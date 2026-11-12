/** The minimum a decoder needs of a video frame or a decoded upload: raw RGBA pixels and their
 * dimensions, matching what `CanvasRenderingContext2D.getImageData` returns. */
export interface DecodableImage {
  data: Uint8ClampedArray;
  width: number;
  height: number;
}

/** Sync or async: the default decoder is async (it lazily loads `jsqr`), but an injected test
 * decoder can just return a value. */
export type QrDecoder = (image: DecodableImage) => string | null | Promise<string | null>;

// `jsqr` (~250 KiB) is only ever needed once a student opens the scan dialog, so it's its own
// chunk, fetched on first use rather than bundled into every page. Cached so every decode after
// the first reuses the same in-flight or settled import instead of re-requesting the chunk.
let jsQRModule: Promise<typeof import("jsqr")> | undefined;

function loadJsQR(): Promise<typeof import("jsqr")> {
  jsQRModule ??= import("jsqr");
  return jsQRModule;
}

/** The real decoder: a thin wrapper over `jsqr`, called through `decodeImage` rather than
 * directly, so ScanCodeDialog's tests can mock the `jsqr` package instead of needing a real QR
 * bitmap in jsdom. */
export const decodeQrCode: QrDecoder = async (image) => {
  const { default: jsQR } = await loadJsQR();
  return jsQR(image.data, image.width, image.height)?.data ?? null;
};

/** Decodes one image — a captured camera frame or an uploaded photo, both reduced to the same
 * `DecodableImage` shape by the caller — with an injectable decoder (defaulting to `jsqr`), so
 * this function is testable on its own with a fake decoder and no DOM at all. */
export async function decodeImage(image: DecodableImage, decoder: QrDecoder = decodeQrCode): Promise<string | null> {
  return await decoder(image);
}
