import { Button } from "@/components/ui/button";
import { FormError } from "@/features/auth/components/FormError";
import type { Room } from "@/features/rooms/api";
import { MediaToggles } from "@/features/rooms/components/MediaToggles";
import { VideoTile } from "@/features/rooms/components/VideoTile";

export type DeviceCheckState = "idle" | "checking" | "ready" | "denied";

/** The room's display title: also used for the in-call header, so both show the same name. */
export function roomTitle(room: Room): string {
  if (room.name) return room.name;
  return room.kind === "project" ? "Team call" : "Call";
}

/** The device-check step: media is requested only once the student presses "Check camera and
 * microphone", never before. A denied permission (or no device) still lets them join, listening
 * only. */
export function Lobby({
  room,
  deviceCheck,
  localStream,
  micEnabled,
  cameraEnabled,
  onCheckDevices,
  onToggleMic,
  onToggleCamera,
  onJoin,
  joining,
  joinError,
}: {
  room: Room;
  deviceCheck: DeviceCheckState;
  localStream: MediaStream | null;
  micEnabled: boolean;
  cameraEnabled: boolean;
  onCheckDevices: () => void;
  onToggleMic: () => void;
  onToggleCamera: () => void;
  onJoin: () => void;
  joining: boolean;
  joinError: string | undefined;
}) {
  const micAvailable = Boolean(localStream?.getAudioTracks().length);
  const cameraAvailable = Boolean(localStream?.getVideoTracks().length);

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{roomTitle(room)}</h1>
        <p className="text-sm text-muted-foreground">
          {room.participants.length} of {room.capacity} in the call
        </p>
      </div>

      <div className="flex flex-col gap-3">
        <Button
          type="button"
          variant="outline"
          disabled={deviceCheck === "checking"}
          onClick={onCheckDevices}
          className="self-start"
        >
          {deviceCheck === "checking" ? "Checking…" : "Check camera and microphone"}
        </Button>

        {deviceCheck === "checking" ? (
          <p role="status" className="text-sm text-muted-foreground">
            Checking…
          </p>
        ) : null}

        {deviceCheck === "denied" ? (
          <p role="alert" className="text-sm text-destructive">
            We can't use your camera or microphone. You can still join and listen.
          </p>
        ) : null}

        {localStream ? (
          <>
            <div className="max-w-xs">
              <VideoTile label="You" stream={localStream} muted />
            </div>
            <MediaToggles
              micEnabled={micEnabled}
              cameraEnabled={cameraEnabled}
              micAvailable={micAvailable}
              cameraAvailable={cameraAvailable}
              onToggleMic={onToggleMic}
              onToggleCamera={onToggleCamera}
            />
          </>
        ) : null}
      </div>

      <FormError>{joinError}</FormError>
      {joining ? (
        <p role="status" className="text-sm text-muted-foreground">
          Connecting…
        </p>
      ) : null}
      <Button type="button" disabled={joining} onClick={onJoin} className="self-start">
        Join call
      </Button>
    </section>
  );
}
