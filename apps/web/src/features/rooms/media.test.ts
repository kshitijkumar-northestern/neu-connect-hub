import { afterEach, describe, expect, it } from "vitest";

import { requestLocalMedia, setTrackEnabled, stopAllTracks } from "@/features/rooms/media";
import { FakeMediaStream, stubGetUserMedia } from "@/test/fake-webrtc";

afterEach(() => {
  // @ts-expect-error -- test-only cleanup of the stub installed by stubGetUserMedia.
  delete globalThis.navigator.mediaDevices;
});

describe("requestLocalMedia", () => {
  it("asks for both audio and video", async () => {
    const getUserMedia = stubGetUserMedia();
    await requestLocalMedia();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true, video: true });
  });

  it("propagates a rejection (denied permission, no device) instead of swallowing it", async () => {
    const denied = new Error("Permission denied");
    stubGetUserMedia(denied);
    await expect(requestLocalMedia()).rejects.toThrow("Permission denied");
  });
});

describe("setTrackEnabled", () => {
  it("flips only the tracks of the requested kind", () => {
    const stream = new FakeMediaStream();
    setTrackEnabled(stream as unknown as MediaStream, "audio", false);

    expect(stream.getAudioTracks().every((track) => !track.enabled)).toBe(true);
    expect(stream.getVideoTracks().every((track) => track.enabled)).toBe(true);
  });

  it("is a no-op without a stream", () => {
    expect(() => setTrackEnabled(null, "audio", false)).not.toThrow();
  });
});

describe("stopAllTracks", () => {
  it("stops every track", () => {
    const stream = new FakeMediaStream();
    stopAllTracks(stream as unknown as MediaStream);

    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
  });

  it("is a no-op without a stream", () => {
    expect(() => stopAllTracks(null)).not.toThrow();
  });
});
