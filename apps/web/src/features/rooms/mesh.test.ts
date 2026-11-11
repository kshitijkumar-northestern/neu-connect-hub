import { describe, expect, it, vi } from "vitest";

import { RoomMesh, type MeshOptions } from "@/features/rooms/mesh";
import { FakeMediaStream, FakeMediaStreamTrack, fakePeerConnectionFactory } from "@/test/fake-webrtc";
import type { Peer, RtcSignal } from "@/lib/socket";

const ALICE: Peer = { user_id: "alice", display_name: "Alice" };
const BOB: Peer = { user_id: "bob", display_name: "Bob" };

/** Lets every pending microtask (an `offer()`'s two `await`s) settle before asserting on it. */
async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function makeMesh(localStream: MediaStream | null = null) {
  const { factory, connections } = fakePeerConnectionFactory();
  const sendSignal = vi.fn<MeshOptions["sendSignal"]>();
  const onRemoteStream = vi.fn<MeshOptions["onRemoteStream"]>();
  const onPeerClosed = vi.fn<MeshOptions["onPeerClosed"]>();
  const mesh = new RoomMesh({
    iceServers: [{ urls: ["stun:stun.example.com"] }],
    localStream,
    createPeerConnection: factory,
    sendSignal,
    onRemoteStream,
    onPeerClosed,
  });
  return { mesh, connections, sendSignal, onRemoteStream, onPeerClosed };
}

describe("RoomMesh", () => {
  it("offers to every peer the join ack listed", async () => {
    const { mesh, connections, sendSignal } = makeMesh();
    mesh.offerTo([ALICE, BOB]);
    await flush();

    expect(connections).toHaveLength(2);
    expect(connections[0]?.localDescription?.type).toBe("offer");
    expect(connections[1]?.localDescription?.type).toBe("offer");
    expect(sendSignal).toHaveBeenCalledWith("alice", { type: "offer", sdp: connections[0]?.localDescription?.sdp });
    expect(sendSignal).toHaveBeenCalledWith("bob", { type: "offer", sdp: connections[1]?.localDescription?.sdp });
  });

  it("does not offer to a peer it only waits for (an existing peer when someone joins)", async () => {
    const { mesh, connections, sendSignal } = makeMesh();
    mesh.waitFor(ALICE);
    await flush();

    expect(connections).toHaveLength(1);
    expect(connections[0]?.localDescription).toBeNull();
    expect(sendSignal).not.toHaveBeenCalledWith("alice", expect.objectContaining({ type: "offer" }));
  });

  it("answers an offer from a peer it's waiting for", async () => {
    const { mesh, connections, sendSignal } = makeMesh();
    mesh.waitFor(ALICE);

    await mesh.handleSignal("alice", { type: "offer", sdp: "remote-offer-sdp" });

    expect(connections[0]?.remoteDescription).toEqual({ type: "offer", sdp: "remote-offer-sdp" });
    expect(connections[0]?.localDescription?.type).toBe("answer");
    expect(sendSignal).toHaveBeenCalledWith("alice", { type: "answer", sdp: connections[0]?.localDescription?.sdp });
  });

  it("applies an answer to the connection it offered on", async () => {
    const { mesh, connections } = makeMesh();
    mesh.offerTo([ALICE]);
    await flush();

    await mesh.handleSignal("alice", { type: "answer", sdp: "remote-answer-sdp" });

    expect(connections[0]?.remoteDescription).toEqual({ type: "answer", sdp: "remote-answer-sdp" });
  });

  it("buffers candidates until the remote description is set, then flushes them in order", async () => {
    const { mesh, connections } = makeMesh();
    mesh.waitFor(ALICE);

    const first: RtcSignal = { type: "candidate", candidate: { candidate: "c1", sdpMid: "0", sdpMLineIndex: 0 } };
    const second: RtcSignal = { type: "candidate", candidate: { candidate: "c2", sdpMid: "0", sdpMLineIndex: 0 } };
    await mesh.handleSignal("alice", first);
    await mesh.handleSignal("alice", second);
    expect(connections[0]?.addedCandidates).toHaveLength(0);

    await mesh.handleSignal("alice", { type: "offer", sdp: "remote-offer-sdp" });

    expect(connections[0]?.addedCandidates).toEqual([first.candidate, second.candidate]);
  });

  it("applies a candidate immediately once the remote description is already set", async () => {
    const { mesh, connections } = makeMesh();
    mesh.waitFor(ALICE);
    await mesh.handleSignal("alice", { type: "offer", sdp: "remote-offer-sdp" });

    const candidate = { candidate: "c3", sdpMid: "0", sdpMLineIndex: 0 };
    await mesh.handleSignal("alice", { type: "candidate", candidate });

    expect(connections[0]?.addedCandidates).toEqual([candidate]);
  });

  it("relays onicecandidate as a candidate signal to that peer", async () => {
    const { mesh, connections, sendSignal } = makeMesh();
    mesh.waitFor(ALICE);

    connections[0]?.onicecandidate?.({
      candidate: { candidate: "local-candidate", sdpMid: "0", sdpMLineIndex: 0, usernameFragment: "frag" },
    });

    expect(sendSignal).toHaveBeenCalledWith("alice", {
      type: "candidate",
      candidate: { candidate: "local-candidate", sdpMid: "0", sdpMLineIndex: 0, usernameFragment: "frag" },
    });
  });

  it("ignores the end-of-candidates null marker", () => {
    const { mesh, connections, sendSignal } = makeMesh();
    mesh.waitFor(ALICE);

    connections[0]?.onicecandidate?.({ candidate: null });

    expect(sendSignal).not.toHaveBeenCalled();
  });

  it("adds every local track to each new connection", () => {
    const localStream = new FakeMediaStream();
    const { mesh, connections } = makeMesh(localStream as unknown as MediaStream);
    mesh.offerTo([ALICE]);

    expect(connections[0]?.addedTracks).toHaveLength(localStream.getTracks().length);
  });

  it("adds a recvonly transceiver for both kinds when joining with no local stream at all", () => {
    const { mesh, connections } = makeMesh(null);
    mesh.offerTo([ALICE]);

    expect(connections[0]?.addedTransceivers).toEqual(
      expect.arrayContaining([
        { kind: "audio", direction: "recvonly" },
        { kind: "video", direction: "recvonly" },
      ]),
    );
    expect(connections[0]?.addedTransceivers).toHaveLength(2);
  });

  it("adds a recvonly transceiver only for the kind missing from an audio-only local stream", () => {
    const localStream = new FakeMediaStream([new FakeMediaStreamTrack("audio")]);
    const { mesh, connections } = makeMesh(localStream as unknown as MediaStream);
    mesh.offerTo([ALICE]);

    expect(connections[0]?.addedTransceivers).toEqual([{ kind: "video", direction: "recvonly" }]);
  });

  it("adds no recvonly transceivers when the local stream already has both kinds", () => {
    const localStream = new FakeMediaStream();
    const { mesh, connections } = makeMesh(localStream as unknown as MediaStream);
    mesh.offerTo([ALICE]);

    expect(connections[0]?.addedTransceivers).toEqual([]);
  });

  it("adds recvonly transceivers on the answering side too (waitFor), not just the offering side", () => {
    const { mesh, connections } = makeMesh(null);
    mesh.waitFor(ALICE);

    expect(connections[0]?.addedTransceivers).toEqual(
      expect.arrayContaining([
        { kind: "audio", direction: "recvonly" },
        { kind: "video", direction: "recvonly" },
      ]),
    );
  });

  it("surfaces a peer's remote stream once a track arrives", () => {
    const { mesh, connections, onRemoteStream } = makeMesh();
    mesh.waitFor(ALICE);
    const remoteStream = new FakeMediaStream();

    connections[0]?.ontrack?.({ streams: [remoteStream] });

    expect(onRemoteStream).toHaveBeenCalledWith("alice", remoteStream);
  });

  it("removePeer closes that connection and reports it closed, leaving others untouched", async () => {
    const { mesh, connections, onPeerClosed } = makeMesh();
    mesh.offerTo([ALICE, BOB]);
    await flush();

    mesh.removePeer("alice");

    expect(connections[0]?.closed).toBe(true);
    expect(connections[1]?.closed).toBe(false);
    expect(onPeerClosed).toHaveBeenCalledWith("alice");
    expect(onPeerClosed).not.toHaveBeenCalledWith("bob");
  });

  it("removePeer is a no-op for a peer that was never connected", () => {
    const { mesh, onPeerClosed } = makeMesh();
    mesh.removePeer("nobody");
    expect(onPeerClosed).not.toHaveBeenCalled();
  });

  it("close closes every connection", async () => {
    const { mesh, connections, onPeerClosed } = makeMesh();
    mesh.offerTo([ALICE, BOB]);
    await flush();

    mesh.close();

    expect(connections.every((connection) => connection.closed)).toBe(true);
    expect(onPeerClosed).toHaveBeenCalledWith("alice");
    expect(onPeerClosed).toHaveBeenCalledWith("bob");
  });

  it("a second connectTo for the same peer (e.g. a duplicate peer_joined) is ignored", async () => {
    const { mesh, connections } = makeMesh();
    mesh.waitFor(ALICE);
    mesh.waitFor(ALICE);
    await flush();

    expect(connections).toHaveLength(1);
  });

  it("an unexpected offer from an unknown peer still connects and answers", async () => {
    const { mesh, connections, sendSignal } = makeMesh();

    await mesh.handleSignal("alice", { type: "offer", sdp: "surprise-offer" });

    expect(connections).toHaveLength(1);
    expect(sendSignal).toHaveBeenCalledWith("alice", { type: "answer", sdp: connections[0]?.localDescription?.sdp });
  });

  it("hasPeer reports whether a connection already exists for that peer", () => {
    const { mesh } = makeMesh();
    expect(mesh.hasPeer("alice")).toBe(false);

    mesh.waitFor(ALICE);

    expect(mesh.hasPeer("alice")).toBe(true);
    expect(mesh.hasPeer("bob")).toBe(false);
  });

  it("a signal from a peer that was never connected and isn't an offer is dropped safely", async () => {
    const { mesh, connections } = makeMesh();
    await expect(
      mesh.handleSignal("ghost", { type: "candidate", candidate: { candidate: "c", sdpMid: null, sdpMLineIndex: null } }),
    ).resolves.toBeUndefined();
    expect(connections).toHaveLength(0);
  });
});
