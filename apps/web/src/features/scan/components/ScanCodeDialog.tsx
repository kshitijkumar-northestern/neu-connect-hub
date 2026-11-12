import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { FormError } from "@/features/auth/components/FormError";
import { stopAllTracks } from "@/features/rooms/media";
import { decodeImage, type DecodableImage } from "@/features/scan/decode";
import { parseConnectLink } from "@/lib/qr";

export interface ScanCodeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type CameraStatus = "idle" | "active" | "denied";

const INVALID_CODE_MESSAGE = "That isn't a Scan to Connect code.";
const CAMERA_DENIED_MESSAGE = "We can't use your camera. Paste the link or upload a photo of the code instead.";
// Shown for the camera loop or an upload when decoding itself fails (e.g. the on-demand jsqr
// chunk couldn't load) rather than when it simply finds no code — same fallback, either path.
const SCANNER_UNAVAILABLE_MESSAGE = "Couldn't scan that. Paste the link or try the photo again.";
// Polls a few times a second: frequent enough that a scan feels instant, infrequent enough not to
// spend a decode (and battery) on every single animation frame.
const SCAN_INTERVAL_MS = 300;
// Only used before the video has reported real dimensions (no metadata yet, or a test with no
// real camera at all).
const FALLBACK_FRAME_WIDTH = 640;
const FALLBACK_FRAME_HEIGHT = 480;
// Caps the decode canvas's longer side: a QR code decodes just as well from a downscaled frame,
// and reading back a smaller `ImageData` every tick is a lot cheaper than a full-resolution one.
const MAX_FRAME_SIDE = 640;

/** Sizes the (already-created) decode canvas once for the stream's real dimensions — not on every
 * tick, which would reallocate the backing buffer 3+ times a second for no reason — downscaled so
 * its longer side is never more than `MAX_FRAME_SIDE`. */
function sizeCanvasForStream(video: HTMLVideoElement, canvas: HTMLCanvasElement): void {
  const sourceWidth = video.videoWidth || FALLBACK_FRAME_WIDTH;
  const sourceHeight = video.videoHeight || FALLBACK_FRAME_HEIGHT;
  const scale = Math.min(1, MAX_FRAME_SIDE / Math.max(sourceWidth, sourceHeight));
  canvas.width = Math.round(sourceWidth * scale);
  canvas.height = Math.round(sourceHeight * scale);
}

function captureFrame(video: HTMLVideoElement, canvas: HTMLCanvasElement): DecodableImage | null {
  if (!canvas.width || !canvas.height) return null;
  // Hints that this canvas's pixels are read back often, not just painted: without it, a browser
  // may optimise the context for display instead, making every getImageData call slower.
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
  return { data: imageData.data, width: imageData.width, height: imageData.height };
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("image_load_failed"));
    };
    image.src = url;
  });
}

async function decodeUploadedFile(file: File): Promise<string | null> {
  const image = await loadImage(file);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth || FALLBACK_FRAME_WIDTH;
  canvas.height = image.naturalHeight || FALLBACK_FRAME_HEIGHT;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
  return decodeImage({ data: imageData.data, width: imageData.width, height: imageData.height });
}

/** Scan a Scan to Connect code: the camera (requested only once "Start camera" is pressed), a
 * pasted link, or an uploaded photo — all three feed the same candidate-handling path, through
 * `lib/qr.ts`. Props are the contract SharePage and ConnectionsPage rely on to open and close it. */
export function ScanCodeDialog({ open, onOpenChange }: ScanCodeDialogProps) {
  const navigate = useNavigate();

  const [cameraStatus, setCameraStatus] = useState<CameraStatus>("idle");
  const [starting, setStarting] = useState(false);
  const [codeError, setCodeError] = useState<string>();
  const [pasteValue, setPasteValue] = useState("");

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const intervalRef = useRef<number | undefined>(undefined);
  const decodingRef = useRef(false);
  const metadataHandlerRef = useRef<(() => void) | null>(null);
  // Bumped by every stopCamera and every new start attempt, so a startCamera() call that resumes
  // after an abandoned getUserMedia await (the dialog closed, or a newer attempt replaced it) can
  // tell it's stale and must stop the stream it just received instead of adopting it.
  const attemptIdRef = useRef(0);
  // Checked synchronously (unlike React state, which a second click might race ahead of before the
  // "Start camera" button re-renders disabled) so a rapid double click can only ever start one
  // getUserMedia request.
  const startingRef = useRef(false);

  function stopCamera() {
    attemptIdRef.current += 1;
    startingRef.current = false;
    setStarting(false);
    if (intervalRef.current !== undefined) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = undefined;
    }
    if (videoRef.current && metadataHandlerRef.current) {
      videoRef.current.removeEventListener("loadedmetadata", metadataHandlerRef.current);
    }
    metadataHandlerRef.current = null;
    stopAllTracks(streamRef.current);
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraStatus((current) => (current === "active" ? "idle" : current));
  }

  // Unmounting stops every camera track the same way closing does (below); closing itself (Escape,
  // an overlay click, the dialog's own close button, or a successful scan) always goes through
  // handleOpenChange or handleCandidate, both of which call stopCamera before anyone is notified.
  useEffect(() => () => stopCamera(), []);

  function handleOpenChange(next: boolean) {
    if (!next) stopCamera();
    onOpenChange(next);
  }

  function handleCandidate(text: string) {
    const token = parseConnectLink(text);
    if (!token) {
      setCodeError(INVALID_CODE_MESSAGE);
      return;
    }
    setCodeError(undefined);
    stopCamera();
    onOpenChange(false);
    navigate(`/connect#token=${token}`);
  }

  async function tick() {
    // Guards against overlapping decodes: the first tick's decode may still be awaiting the
    // on-demand jsqr chunk when the next interval fires.
    if (decodingRef.current) return;
    if (!videoRef.current || !canvasRef.current) return;
    // Nothing real to decode yet (no frame has arrived) — avoid wasting a decode on a blank canvas.
    if (videoRef.current.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
    const frame = captureFrame(videoRef.current, canvasRef.current);
    if (!frame) return;
    decodingRef.current = true;
    try {
      const text = await decodeImage(frame);
      if (text !== null) handleCandidate(text);
    } catch {
      // A transient decode failure (e.g. the chunk failed to load): say so, but keep scanning
      // rather than leaving the camera loop stuck or the student with no feedback at all.
      setCodeError(SCANNER_UNAVAILABLE_MESSAGE);
    } finally {
      decodingRef.current = false;
    }
  }

  async function startCamera() {
    if (startingRef.current || cameraStatus === "active") return;
    startingRef.current = true;
    setStarting(true);
    const attemptId = ++attemptIdRef.current;
    setCodeError(undefined);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      if (attemptIdRef.current !== attemptId) {
        // The dialog closed (stopCamera already bumped the attempt id) or a newer attempt started
        // while this one was waiting on permission: stop the stream this attempt just opened
        // rather than leaving the camera light on with nothing using it.
        stopAllTracks(stream);
        return;
      }
      streamRef.current = stream;
      if (videoRef.current && canvasRef.current) {
        videoRef.current.srcObject = stream;
        sizeCanvasForStream(videoRef.current, canvasRef.current);
        const handleLoadedMetadata = () => {
          if (videoRef.current && canvasRef.current) sizeCanvasForStream(videoRef.current, canvasRef.current);
        };
        metadataHandlerRef.current = handleLoadedMetadata;
        videoRef.current.addEventListener("loadedmetadata", handleLoadedMetadata);
      }
      setCameraStatus("active");
      intervalRef.current = window.setInterval(() => void tick(), SCAN_INTERVAL_MS);
    } catch {
      if (attemptIdRef.current === attemptId) setCameraStatus("denied");
    } finally {
      if (attemptIdRef.current === attemptId) {
        startingRef.current = false;
        setStarting(false);
      }
    }
  }

  function handlePasteSubmit(event: FormEvent) {
    event.preventDefault();
    const text = pasteValue.trim();
    if (!text) return;
    handleCandidate(text);
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // lets the student re-select the same file after a failed attempt
    if (!file) return;
    try {
      const text = await decodeUploadedFile(file);
      if (text === null) {
        setCodeError(INVALID_CODE_MESSAGE);
        return;
      }
      handleCandidate(text);
    } catch {
      // The photo failed to load, or decoding it failed (e.g. the on-demand jsqr chunk didn't
      // load) — distinct from a clean decode that simply found no code (INVALID_CODE_MESSAGE).
      setCodeError(SCANNER_UNAVAILABLE_MESSAGE);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Scan a code</DialogTitle>
          <DialogDescription>Scan a Scan to Connect code to see who it is.</DialogDescription>
        </DialogHeader>

        <FormError>{codeError}</FormError>

        <div className="flex flex-col gap-3">
          {cameraStatus !== "active" ? (
            <Button
              type="button"
              variant="outline"
              disabled={starting}
              onClick={() => void startCamera()}
              className="self-start"
            >
              {starting ? "Starting camera…" : "Start camera"}
            </Button>
          ) : null}

          {cameraStatus === "denied" ? (
            <p role="alert" className="text-sm text-destructive">
              {CAMERA_DENIED_MESSAGE}
            </p>
          ) : null}

          <div className={cameraStatus === "active" ? "flex flex-col gap-2" : "hidden"}>
            <video
              ref={videoRef}
              muted
              autoPlay
              playsInline
              aria-label="Camera preview"
              className="aspect-video w-full rounded-2xl bg-muted object-cover"
            />
            <canvas ref={canvasRef} className="hidden" />
          </div>
        </div>

        <form onSubmit={handlePasteSubmit} className="flex flex-col gap-2">
          <Field>
            <FieldLabel htmlFor="scan-paste-link">Paste a link</FieldLabel>
            <div className="flex gap-2">
              <Input
                id="scan-paste-link"
                value={pasteValue}
                onChange={(event) => setPasteValue(event.target.value)}
                placeholder="https://..."
              />
              <Button type="submit" variant="outline">
                Open
              </Button>
            </div>
          </Field>
        </form>

        <Field>
          <FieldLabel htmlFor="scan-upload-photo">Upload a photo of a code</FieldLabel>
          <Input id="scan-upload-photo" type="file" accept="image/*" onChange={(event) => void handleUpload(event)} />
        </Field>
      </DialogContent>
    </Dialog>
  );
}
