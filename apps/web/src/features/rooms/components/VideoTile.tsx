import { useEffect, useRef } from "react";

/** One tile in the call grid: a named video (the local preview, or one peer's). No stream yet
 * (receive-only, or a peer whose track hasn't arrived) shows the name on its own. */
export function VideoTile({ label, stream, muted = false }: { label: string; stream: MediaStream | null; muted?: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (video) video.srcObject = stream;
  }, [stream]);

  return (
    <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-2xl bg-muted">
      {stream ? (
        // Live peer-to-peer video has no caption track to offer (nothing is ever recorded).
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <video ref={videoRef} autoPlay playsInline muted={muted} className="h-full w-full object-cover" />
      ) : (
        <span className="text-sm text-muted-foreground">No video</span>
      )}
      <span className="absolute bottom-2 left-2 rounded-full bg-background/80 px-2 py-0.5 text-xs font-medium">{label}</span>
    </div>
  );
}
