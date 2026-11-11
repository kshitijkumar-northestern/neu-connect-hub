import type { Peer, RtcSignal } from "@/lib/socket";

/** Builds one `RTCPeerConnection` per peer; injectable so it's unit-testable in jsdom, which has
 * no WebRTC of its own. */
export type PeerConnectionFactory = (config: RTCConfiguration) => RTCPeerConnection;

/** Sends one signal to one peer over the call socket. Fire-and-forget: a dropped `rtc.signal` ack
 * isn't actionable here (the page already shows a "Connection lost." banner on a socket drop, and
 * SDP/candidates are never logged, so there's nothing safe to report on failure). */
export type SignalSender = (to: string, signal: RtcSignal) => void;

export interface MeshOptions {
  iceServers: RTCIceServer[];
  /** The caller's own outgoing tracks, added to every connection; null joins receive-only. */
  localStream: MediaStream | null;
  sendSignal: SignalSender;
  /** Fired whenever a peer's remote stream arrives (once we have `event.streams[0]`). */
  onRemoteStream: (userId: string, stream: MediaStream) => void;
  /** Fired once a peer's connection is torn down, by `removePeer` or `close`. */
  onPeerClosed: (userId: string) => void;
  createPeerConnection?: PeerConnectionFactory;
}

interface PeerEntry {
  connection: RTCPeerConnection;
  remoteDescriptionSet: boolean;
  bufferedCandidates: RTCIceCandidateInit[];
}

const defaultFactory: PeerConnectionFactory = (config) => new RTCPeerConnection(config);

/** The call's peer-connection mesh: a full mesh, one `RTCPeerConnection` per other student in the
 * room. The joiner offers to every peer the join ack listed; a student already in the room only
 * answers a newcomer's offer, never initiates one of its own. ICE candidates that arrive before
 * the remote description is set are buffered and flushed right after it's applied. */
export class RoomMesh {
  private readonly peers = new Map<string, PeerEntry>();
  private readonly createPeerConnection: PeerConnectionFactory;

  constructor(private readonly options: MeshOptions) {
    this.createPeerConnection = options.createPeerConnection ?? defaultFactory;
  }

  /** The peers the join ack listed: create a connection for each and send it an offer. */
  offerTo(peers: readonly Peer[]): void {
    for (const peer of peers) this.connectTo(peer.user_id, true);
  }

  /** A student who joined after us (`room.peer_joined`): create a connection and wait for their
   * offer; we never offer to a peer who was already in the room before us. */
  waitFor(peer: Peer): void {
    this.connectTo(peer.user_id, false);
  }

  /** Whether this peer already has a connection — a `room.peer_joined` (or an offer) can arrive
   * before the join ack does, so the ack must only offer to the peers it listed that aren't
   * already known, instead of offering to (or replacing) one already being waited on. */
  hasPeer(userId: string): boolean {
    return this.peers.has(userId);
  }

  /** Applies an offer, answer or (possibly early) candidate relayed from `fromUserId`. */
  async handleSignal(fromUserId: string, signal: RtcSignal): Promise<void> {
    let entry = this.peers.get(fromUserId);
    if (!entry) {
      // An offer can arrive before we've registered the peer (a `room.peer_joined` we missed, or
      // one that raced the signal); anything else from an unknown peer has nowhere to go.
      if (signal.type !== "offer") return;
      this.connectTo(fromUserId, false);
      entry = this.peers.get(fromUserId);
    }
    if (!entry) return;
    const { connection } = entry;

    switch (signal.type) {
      case "offer": {
        await connection.setRemoteDescription({ type: "offer", sdp: signal.sdp });
        entry.remoteDescriptionSet = true;
        await this.flushCandidates(entry);
        const answer = await connection.createAnswer();
        await connection.setLocalDescription(answer);
        this.options.sendSignal(fromUserId, { type: "answer", sdp: answer.sdp ?? "" });
        return;
      }
      case "answer": {
        await connection.setRemoteDescription({ type: "answer", sdp: signal.sdp });
        entry.remoteDescriptionSet = true;
        await this.flushCandidates(entry);
        return;
      }
      case "candidate": {
        if (entry.remoteDescriptionSet) {
          await connection.addIceCandidate(signal.candidate);
        } else {
          entry.bufferedCandidates.push(signal.candidate);
        }
        return;
      }
    }
  }

  /** Closes and drops one peer's connection (`room.peer_left`, or a cleanup before a rejoin). */
  removePeer(userId: string): void {
    const entry = this.peers.get(userId);
    if (!entry) return;
    entry.connection.close();
    this.peers.delete(userId);
    this.options.onPeerClosed(userId);
  }

  /** Closes every connection (leaving the call, or unmounting the page). */
  close(): void {
    for (const [userId] of this.peers) this.removePeer(userId);
  }

  private connectTo(userId: string, asOfferer: boolean): void {
    if (this.peers.has(userId)) return;
    const connection = this.createPeerConnection({ iceServers: this.options.iceServers });
    const entry: PeerEntry = { connection, remoteDescriptionSet: false, bufferedCandidates: [] };
    this.peers.set(userId, entry);

    connection.onicecandidate = (event) => {
      const candidate = event.candidate;
      if (!candidate) return;
      this.options.sendSignal(userId, {
        type: "candidate",
        candidate: {
          candidate: candidate.candidate,
          sdpMid: candidate.sdpMid ?? null,
          sdpMLineIndex: candidate.sdpMLineIndex ?? null,
          usernameFragment: candidate.usernameFragment ?? null,
        },
      });
    };

    connection.ontrack = (event) => {
      const [stream] = event.streams;
      if (stream) this.options.onRemoteStream(userId, stream);
    };

    const localStream = this.options.localStream;
    if (localStream) {
      for (const track of localStream.getTracks()) {
        connection.addTrack(track, localStream);
      }
    }
    // Without this, a receive-only joiner's offer (or an all-receive-only peer's answer) carries
    // no media section for whatever kind it isn't sending, so the OTHER side's matching track
    // never has anywhere to attach and nothing renegotiates: "listen only" would really mean
    // "hear nothing". A recvonly transceiver for each missing kind keeps that kind negotiated on
    // both the offering and the answering side, regardless of what the other side sends.
    if (!localStream?.getAudioTracks().length) connection.addTransceiver("audio", { direction: "recvonly" });
    if (!localStream?.getVideoTracks().length) connection.addTransceiver("video", { direction: "recvonly" });

    if (asOfferer) void this.offer(userId, connection);
  }

  private async offer(userId: string, connection: RTCPeerConnection): Promise<void> {
    const offer = await connection.createOffer();
    await connection.setLocalDescription(offer);
    this.options.sendSignal(userId, { type: "offer", sdp: offer.sdp ?? "" });
  }

  private async flushCandidates(entry: PeerEntry): Promise<void> {
    const buffered = entry.bufferedCandidates;
    entry.bufferedCandidates = [];
    for (const candidate of buffered) await entry.connection.addIceCandidate(candidate);
  }
}
