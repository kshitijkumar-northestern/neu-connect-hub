// A stand-in for the browser's WebRTC and media-device APIs: jsdom has neither. Pass
// `fakePeerConnectionFactory().factory` as `mesh.ts`'s `createPeerConnection`, and
// `stubGetUserMedia` to make `navigator.mediaDevices.getUserMedia` resolve or reject in a test.
import { vi } from "vitest";

let offerCount = 0;
let answerCount = 0;

export class FakeMediaStreamTrack {
  enabled = true;
  readyState: "live" | "ended" = "live";
  stopCalls = 0;

  constructor(public readonly kind: "audio" | "video") {}

  stop(): void {
    this.stopCalls += 1;
    this.readyState = "ended";
  }
}

/** Enough of `MediaStream` for the page and mesh to render and wire up tracks with. */
export class FakeMediaStream {
  private readonly tracks: FakeMediaStreamTrack[];

  constructor(tracks: FakeMediaStreamTrack[] = [new FakeMediaStreamTrack("audio"), new FakeMediaStreamTrack("video")]) {
    this.tracks = tracks;
  }

  getTracks(): FakeMediaStreamTrack[] {
    return [...this.tracks];
  }

  getAudioTracks(): FakeMediaStreamTrack[] {
    return this.tracks.filter((track) => track.kind === "audio");
  }

  getVideoTracks(): FakeMediaStreamTrack[] {
    return this.tracks.filter((track) => track.kind === "video");
  }
}

interface FakeSessionDescription {
  type: "offer" | "answer";
  sdp: string;
}

interface FakeIceCandidateEvent {
  candidate: { candidate: string; sdpMid: string | null; sdpMLineIndex: number | null; usernameFragment?: string | null } | null;
}

interface FakeTrackEvent {
  streams: FakeMediaStream[];
}

/** A small fake `RTCPeerConnection`: records what `mesh.ts` does to it, and lets a test fire
 * `ontrack`/`onicecandidate` to simulate the network side. */
export class FakeRTCPeerConnection {
  closed = false;
  localDescription: FakeSessionDescription | null = null;
  remoteDescription: FakeSessionDescription | null = null;
  readonly addedTracks: Array<{ track: FakeMediaStreamTrack; stream: FakeMediaStream }> = [];
  readonly addedCandidates: unknown[] = [];
  readonly addedTransceivers: Array<{ kind: string; direction: string | undefined }> = [];
  onicecandidate: ((event: FakeIceCandidateEvent) => void) | null = null;
  ontrack: ((event: FakeTrackEvent) => void) | null = null;

  constructor(public readonly config: RTCConfiguration) {}

  addTrack(track: FakeMediaStreamTrack, stream: FakeMediaStream): void {
    this.addedTracks.push({ track, stream });
  }

  addTransceiver(trackOrKind: unknown, init?: { direction?: string }): unknown {
    const kind = typeof trackOrKind === "string" ? trackOrKind : (trackOrKind as FakeMediaStreamTrack).kind;
    this.addedTransceivers.push({ kind, direction: init?.direction });
    return { mid: null, direction: init?.direction ?? "sendrecv" };
  }

  async createOffer(): Promise<FakeSessionDescription> {
    offerCount += 1;
    return { type: "offer", sdp: `fake-offer-sdp-${offerCount}` };
  }

  async createAnswer(): Promise<FakeSessionDescription> {
    answerCount += 1;
    return { type: "answer", sdp: `fake-answer-sdp-${answerCount}` };
  }

  async setLocalDescription(description: FakeSessionDescription): Promise<void> {
    this.localDescription = description;
  }

  async setRemoteDescription(description: FakeSessionDescription): Promise<void> {
    this.remoteDescription = description;
  }

  async addIceCandidate(candidate: unknown): Promise<void> {
    this.addedCandidates.push(candidate);
  }

  close(): void {
    this.closed = true;
  }
}

/** One factory per test, so each connection it builds is reachable in creation order. */
export function fakePeerConnectionFactory(): {
  factory: (config: RTCConfiguration) => RTCPeerConnection;
  connections: FakeRTCPeerConnection[];
} {
  const connections: FakeRTCPeerConnection[] = [];
  const factory = (config: RTCConfiguration): RTCPeerConnection => {
    const connection = new FakeRTCPeerConnection(config);
    connections.push(connection);
    return connection as unknown as RTCPeerConnection;
  };
  return { factory, connections };
}

/** Makes `navigator.mediaDevices.getUserMedia` resolve with a fresh fake stream, or reject with
 * `outcome` (e.g. a `DOMException`-shaped permission-denied error) when it's an Error. */
export function stubGetUserMedia(
  outcome: Error | (() => FakeMediaStream) = () => new FakeMediaStream(),
): ReturnType<typeof vi.fn> {
  const getUserMedia = vi.fn(() => (outcome instanceof Error ? Promise.reject(outcome) : Promise.resolve(outcome())));
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    value: { getUserMedia },
    configurable: true,
  });
  return getUserMedia;
}

/** Replaces the global `RTCPeerConnection` (jsdom has none) with the fake one, for a page test
 * that exercises `mesh.ts` through its own default factory instead of injecting one. Call the
 * returned `uninstall` in `afterEach`. */
export function installFakeRTCPeerConnection(): { connections: FakeRTCPeerConnection[]; uninstall: () => void } {
  const connections: FakeRTCPeerConnection[] = [];
  const original = (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection;
  function FakeConstructor(this: unknown, config: RTCConfiguration) {
    const connection = new FakeRTCPeerConnection(config);
    connections.push(connection);
    return connection;
  }
  (globalThis as { RTCPeerConnection: unknown }).RTCPeerConnection = FakeConstructor as unknown as typeof RTCPeerConnection;
  return {
    connections,
    uninstall: () => {
      (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = original;
    },
  };
}
