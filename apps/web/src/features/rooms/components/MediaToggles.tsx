import { Button } from "@/components/ui/button";

/** Microphone and camera toggles: the accessible name stays put ("Microphone"/"Camera") while
 * `aria-pressed` carries the on/off state, same as any toggle button. Disabled when there's no
 * matching local track to flip (e.g. the student joined receive-only, or only one permission was
 * granted). */
export function MediaToggles({
  micEnabled,
  cameraEnabled,
  micAvailable,
  cameraAvailable,
  onToggleMic,
  onToggleCamera,
}: {
  micEnabled: boolean;
  cameraEnabled: boolean;
  micAvailable: boolean;
  cameraAvailable: boolean;
  onToggleMic: () => void;
  onToggleCamera: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        type="button"
        variant={micEnabled ? "outline" : "destructive"}
        aria-pressed={micEnabled}
        disabled={!micAvailable}
        onClick={onToggleMic}
      >
        Microphone
      </Button>
      <Button
        type="button"
        variant={cameraEnabled ? "outline" : "destructive"}
        aria-pressed={cameraEnabled}
        disabled={!cameraAvailable}
        onClick={onToggleCamera}
      >
        Camera
      </Button>
    </div>
  );
}
