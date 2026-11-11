import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Button } from "@/components/ui/button";
import { roomsApi, type Room } from "@/features/rooms/api";
import { Lobby, roomTitle, type DeviceCheckState } from "@/features/rooms/components/Lobby";
import { MediaToggles } from "@/features/rooms/components/MediaToggles";
import { VideoTile } from "@/features/rooms/components/VideoTile";
import { CONNECT_ERROR_MESSAGE, roomAckErrorMessage, roomHttpErrorMessage, roomRemovedMessage } from "@/features/rooms/errors";
import { requestLocalMedia, setTrackEnabled, stopAllTracks } from "@/features/rooms/media";
import { RoomMesh } from "@/features/rooms/mesh";
import { ApiError } from "@/lib/http";
import {
  createSocket,
  emitRoomJoin,
  emitRoomLeave,
  emitRtcSignal,
  type AppSocket,
  type Peer,
  type PeerJoinedEvent,
  type PeerLeftEvent,
  type RoomRemovedEvent,
  type RtcSignalEvent,
} from "@/lib/socket";

type LoadState = "loading" | "ready" | "not-found" | "ended" | "error";
type Phase = "lobby" | "connecting" | "in-call" | "disconnected" | "removed";

/** A `connect` wait longer than this is treated the same as a transport `connect_error`: both
 * show an error and return to the lobby rather than hanging forever. */
export const CONNECT_TIMEOUT_MS = 10_000;

type ConnectResult = { ok: true } | { ok: false; reason: "connect_error" | "timeout" };

/** Resolves once the socket connects, or once it reports `connect_error` or `CONNECT_TIMEOUT_MS`
 * passes — whichever comes first — so a join attempt can never hang on an unreachable server. */
function waitForConnect(socket: AppSocket): Promise<ConnectResult> {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      finish({ ok: false, reason: "timeout" });
    }, CONNECT_TIMEOUT_MS);
    function finish(result: ConnectResult) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.off("connect", handleConnect);
      socket.off("connect_error", handleConnectError);
      resolve(result);
    }
    function handleConnect() {
      finish({ ok: true });
    }
    function handleConnectError() {
      finish({ ok: false, reason: "connect_error" });
    }
    socket.on("connect", handleConnect);
    socket.on("connect_error", handleConnectError);
  });
}

/** Adds every `incoming` peer the current list doesn't already have, by `user_id` — used both for
 * the join ack (which must MERGE with whoever a `room.peer_joined` already added while the ack was
 * still in flight) and for a single new arrival. */
function mergePeers(current: readonly Peer[], incoming: readonly Peer[]): Peer[] {
  const next = [...current];
  for (const peer of incoming) {
    if (!next.some((existing) => existing.user_id === peer.user_id)) next.push(peer);
  }
  return next;
}

function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const next = { ...record };
  delete next[key];
  return next;
}

/** Keyed by the route param so navigating from one room straight to another remounts the inner
 * component instead of needing to reset its load and call state imperatively. */
export function RoomPage() {
  const { roomId } = useParams<{ roomId: string }>();
  return <RoomCall key={roomId ?? "none"} roomId={roomId} />;
}

function RoomCall({ roomId }: { roomId: string | undefined }) {
  const navigate = useNavigate();

  const [load, setLoad] = useState<LoadState>("loading");
  const [room, setRoom] = useState<Room>();

  const [deviceCheck, setDeviceCheck] = useState<DeviceCheckState>("idle");
  const [localStream, setLocalStreamState] = useState<MediaStream | null>(null);
  const [micEnabled, setMicEnabled] = useState(true);
  const [cameraEnabled, setCameraEnabled] = useState(true);

  const [phase, setPhase] = useState<Phase>("lobby");
  const [joinError, setJoinError] = useState<string>();
  const [removedReason, setRemovedReason] = useState<RoomRemovedEvent["reason"]>();
  const [peers, setPeers] = useState<Peer[]>([]);
  const [remoteStreams, setRemoteStreams] = useState<Record<string, MediaStream>>({});

  const localStreamRef = useRef<MediaStream | null>(null);
  const socketRef = useRef<AppSocket | null>(null);
  const meshRef = useRef<RoomMesh | null>(null);
  const detachRef = useRef<(() => void) | null>(null);
  /** Bumped by every `startCall` and by every teardown, so an attempt that resumes after an
   * abandoning `await` (a newer attempt, Leave, or unmount) can tell it's stale and must only
   * clean up what it personally created, never touch the current refs. */
  const attemptIdRef = useRef(0);

  function setLocalStream(stream: MediaStream | null) {
    localStreamRef.current = stream;
    setLocalStreamState(stream);
  }

  useEffect(() => {
    if (!roomId) return;
    let cancelled = false;
    roomsApi
      .get(roomId)
      .then((result) => {
        if (cancelled) return;
        setRoom(result);
        setLoad(result.room_state === "ended" ? "ended" : "ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoad(error instanceof ApiError && error.status === 404 ? "not-found" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, [roomId]);

  /** Tears down the CURRENTLY COMMITTED call (mesh + socket, via the refs). Never touches local
   * media tracks: those outlive a rejoin, and only Leave, `room.removed` and unmounting stop them
   * for good. Safe to call even when nothing is live. */
  const cleanupConnections = useCallback(() => {
    detachRef.current?.();
    detachRef.current = null;
    meshRef.current?.close();
    meshRef.current = null;
    socketRef.current?.disconnect();
    socketRef.current = null;
  }, []);

  /** Leaving and unmounting both emit `room.leave`, close every connection and stop every local
   * track; only an explicit Leave also navigates (there's nowhere to navigate on unmount). Also
   * invalidates any join attempt still in flight, so it abandons itself instead of resurrecting a
   * call nothing will ever disconnect. */
  const teardownCall = useCallback(() => {
    attemptIdRef.current += 1;
    if (roomId && socketRef.current) void emitRoomLeave(socketRef.current, { room_id: roomId });
    cleanupConnections();
    stopAllTracks(localStreamRef.current);
  }, [roomId, cleanupConnections]);

  useEffect(() => () => teardownCall(), [teardownCall]);

  async function checkDevices() {
    // A second press must stop the stream from the first before replacing it: otherwise its
    // tracks (and the camera/mic they're holding open) leak for good.
    stopAllTracks(localStreamRef.current);
    setLocalStream(null);
    setDeviceCheck("checking");
    try {
      const stream = await requestLocalMedia();
      setLocalStream(stream);
      setMicEnabled(true);
      setCameraEnabled(true);
      setDeviceCheck("ready");
    } catch {
      setDeviceCheck("denied");
    }
  }

  function toggleMic() {
    const next = !micEnabled;
    setMicEnabled(next);
    setTrackEnabled(localStreamRef.current, "audio", next);
  }

  function toggleCamera() {
    const next = !cameraEnabled;
    setCameraEnabled(next);
    setTrackEnabled(localStreamRef.current, "video", next);
  }

  function attachSocketListeners(socket: AppSocket, mesh: RoomMesh): () => void {
    function handlePeerJoined(event: PeerJoinedEvent) {
      if (event.room_id !== roomId) return;
      setPeers((current) => mergePeers(current, [event.peer]));
      mesh.waitFor(event.peer);
    }
    function handlePeerLeft(event: PeerLeftEvent) {
      if (event.room_id !== roomId) return;
      setPeers((current) => current.filter((peer) => peer.user_id !== event.user_id));
      setRemoteStreams((current) => withoutKey(current, event.user_id));
      mesh.removePeer(event.user_id);
    }
    function handleRtcSignal(event: RtcSignalEvent) {
      if (event.room_id !== roomId) return;
      void mesh.handleSignal(event.from, event.signal);
    }
    function handleDisconnect() {
      // Close the mesh at once so nothing keeps transmitting; the local tracks (and the socket,
      // which Rejoin replaces anyway) are left for Rejoin to reuse.
      mesh.close();
      setPhase("disconnected");
    }
    function handleRoomRemoved(event: RoomRemovedEvent) {
      if (event.room_id !== roomId) return;
      cleanupConnections();
      stopAllTracks(localStreamRef.current);
      setLocalStream(null);
      setPeers([]);
      setRemoteStreams({});
      setRemovedReason(event.reason);
      setPhase("removed");
    }

    socket.on("room.peer_joined", handlePeerJoined);
    socket.on("room.peer_left", handlePeerLeft);
    socket.on("rtc.signal", handleRtcSignal);
    socket.on("disconnect", handleDisconnect);
    socket.on("room.removed", handleRoomRemoved);

    return () => {
      socket.off("room.peer_joined", handlePeerJoined);
      socket.off("room.peer_left", handlePeerLeft);
      socket.off("rtc.signal", handleRtcSignal);
      socket.off("disconnect", handleDisconnect);
      socket.off("room.removed", handleRoomRemoved);
    };
  }

  async function startCall() {
    if (!roomId) return;
    // Invalidate (and clean up) any attempt still in flight, so only one attempt's socket/mesh is
    // ever live, and a stale one knows to abandon itself rather than resurface later.
    attemptIdRef.current += 1;
    const attemptId = attemptIdRef.current;
    const isCurrent = () => attemptIdRef.current === attemptId;
    cleanupConnections();
    setJoinError(undefined);
    setPhase("connecting");

    // This attempt's own objects, tracked locally (not just via the refs): if it's abandoned
    // partway through, it must close exactly what IT created, never whatever the refs point to by
    // then (a newer attempt may already have replaced them).
    let socket: AppSocket | undefined;
    let mesh: RoomMesh | undefined;
    let detach: (() => void) | undefined;

    try {
      const ice = await roomsApi.iceServers(roomId);
      if (!isCurrent()) return; // abandoned before anything was created; nothing to clean up

      socket = createSocket();
      mesh = new RoomMesh({
        iceServers: ice.ice_servers,
        localStream: localStreamRef.current,
        sendSignal: (to, signal) => {
          void emitRtcSignal(socket as AppSocket, { room_id: roomId, to, signal });
        },
        onRemoteStream: (userId, stream) => {
          setRemoteStreams((current) => ({ ...current, [userId]: stream }));
        },
        onPeerClosed: (userId) => {
          setRemoteStreams((current) => withoutKey(current, userId));
        },
      });
      // Created before `room.join` is even sent, so a `room.peer_joined` or an offer that arrives
      // before the ack still has somewhere to land.
      detach = attachSocketListeners(socket, mesh);

      // Commit now: Leave, unmount or a newer attempt can all already reach this attempt through
      // the refs from this point on.
      socketRef.current = socket;
      meshRef.current = mesh;
      detachRef.current = detach;

      socket.connect();
      const connected = await waitForConnect(socket);
      if (!isCurrent()) {
        detach();
        mesh.close();
        socket.disconnect();
        return;
      }
      if (!connected.ok) {
        cleanupConnections();
        setJoinError(connected.reason === "timeout" ? roomAckErrorMessage("timeout") : CONNECT_ERROR_MESSAGE);
        setPhase("lobby");
        return;
      }

      const ack = await emitRoomJoin(socket, { room_id: roomId });
      if (!isCurrent()) {
        detach();
        mesh.close();
        socket.disconnect();
        return;
      }
      if (!ack.ok) {
        cleanupConnections();
        setJoinError(roomAckErrorMessage(ack.error.code));
        setPhase("lobby");
        return;
      }

      setPeers((current) => mergePeers(current, ack.peers));
      // `mesh` is always assigned by this point; the `?.` just satisfies control-flow analysis
      // across the `setPeers` state-updater closure above.
      const newPeers = ack.peers.filter((peer) => !mesh?.hasPeer(peer.user_id));
      mesh.offerTo(newPeers);
      setPhase("in-call");
    } catch (error) {
      if (!isCurrent()) return;
      cleanupConnections();
      setJoinError(roomHttpErrorMessage(error));
      setPhase("lobby");
    }
  }

  function leaveCall() {
    if (!roomId || !room) return;
    teardownCall();
    setLocalStream(null);
    setPeers([]);
    setRemoteStreams({});
    navigate(`/messages/${room.conversation_id}`);
  }

  function rejoin() {
    cleanupConnections();
    setPeers([]);
    setRemoteStreams({});
    void startCall();
  }

  if (!roomId) {
    return <p>This call isn't available.</p>;
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "not-found") {
    return <p>This call isn't available.</p>;
  }

  if (load === "ended") {
    return (
      <div className="flex flex-col gap-3">
        <p>This call has ended.</p>
        {room ? (
          <Link to={`/messages/${room.conversation_id}`} className="underline underline-offset-4">
            Back to the conversation
          </Link>
        ) : null}
      </div>
    );
  }

  if (load === "error" || !room) {
    return <p role="alert">Can't load this call right now. Check your connection and try again.</p>;
  }

  if (phase === "removed") {
    return (
      <div className="flex flex-col gap-3">
        <p role="alert">{removedReason ? roomRemovedMessage(removedReason) : null}</p>
        <Link to={`/messages/${room.conversation_id}`} className="underline underline-offset-4 self-start">
          Back to the conversation
        </Link>
      </div>
    );
  }

  if (phase === "disconnected") {
    return (
      <div className="flex flex-col gap-3">
        <p role="status">Connection lost.</p>
        <div className="flex flex-wrap gap-3">
          <Button type="button" onClick={rejoin} className="self-start">
            Rejoin
          </Button>
          <Button type="button" variant="destructive" onClick={leaveCall} className="self-start">
            Leave call
          </Button>
        </div>
      </div>
    );
  }

  if (phase === "in-call") {
    const micAvailable = Boolean(localStream?.getAudioTracks().length);
    const cameraAvailable = Boolean(localStream?.getVideoTracks().length);
    return (
      <section className="flex flex-col gap-6">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">{roomTitle(room)}</h1>
          <p className="text-sm text-muted-foreground">
            {/* The live count (peers plus yourself), not the snapshot from when the page loaded:
                room.participants never updates while the call is in progress. */}
            {peers.length + 1} of {room.capacity} in the call
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <VideoTile label="You" stream={localStream} muted />
          {peers.map((peer) => (
            <VideoTile key={peer.user_id} label={peer.display_name ?? "A student"} stream={remoteStreams[peer.user_id] ?? null} />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <MediaToggles
            micEnabled={micEnabled}
            cameraEnabled={cameraEnabled}
            micAvailable={micAvailable}
            cameraAvailable={cameraAvailable}
            onToggleMic={toggleMic}
            onToggleCamera={toggleCamera}
          />
          <Button type="button" variant="destructive" onClick={leaveCall}>
            Leave call
          </Button>
        </div>
      </section>
    );
  }

  return (
    <Lobby
      room={room}
      deviceCheck={deviceCheck}
      localStream={localStream}
      micEnabled={micEnabled}
      cameraEnabled={cameraEnabled}
      onCheckDevices={() => void checkDevices()}
      onToggleMic={toggleMic}
      onToggleCamera={toggleCamera}
      onJoin={() => void startCall()}
      joining={phase === "connecting"}
      joinError={joinError}
    />
  );
}
