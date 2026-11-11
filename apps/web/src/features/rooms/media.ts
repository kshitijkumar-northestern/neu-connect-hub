/** Requests the mic and camera together. Call this only from the lobby's own "Check camera and
 * microphone" button, never on mount: a call page must never ask for media before the student
 * presses for it. Rejects (denied permission, no device, etc.) so the lobby can fall back to a
 * receive-only join; it never catches the rejection itself. */
export async function requestLocalMedia(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({ audio: true, video: true });
}

/** Flips every track of one kind (`track.enabled`, the only thing a toggle may touch: muting a
 * track locally, never stopping it). A no-op without a stream (receive-only joined). */
export function setTrackEnabled(stream: MediaStream | null, kind: "audio" | "video", enabled: boolean): void {
  if (!stream) return;
  const tracks = kind === "audio" ? stream.getAudioTracks() : stream.getVideoTracks();
  for (const track of tracks) track.enabled = enabled;
}

/** Stops every track so the camera/mic indicator turns off; call on Leave and on unmount. */
export function stopAllTracks(stream: MediaStream | null): void {
  if (!stream) return;
  for (const track of stream.getTracks()) track.stop();
}
