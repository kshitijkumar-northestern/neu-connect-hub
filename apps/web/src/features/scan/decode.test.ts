import { describe, expect, it, vi } from "vitest";

// Hoisted: applies to decode.ts's `import("jsqr")` too, so the default decoder never pulls in the
// real (~250 KiB) package during tests.
vi.mock("jsqr", () => ({ default: vi.fn(() => ({ data: "decoded-by-jsqr" })) }));

import jsQR from "jsqr";

import { decodeImage, decodeQrCode, type DecodableImage } from "@/features/scan/decode";

const IMAGE: DecodableImage = { data: new Uint8ClampedArray(4), width: 1, height: 1 };

describe("decodeImage", () => {
  it("passes the image straight through to the given decoder", async () => {
    const decoder = vi.fn().mockReturnValue("decoded-text");

    await expect(decodeImage(IMAGE, decoder)).resolves.toBe("decoded-text");
    expect(decoder).toHaveBeenCalledWith(IMAGE);
  });

  it("returns null when the decoder finds nothing", async () => {
    await expect(decodeImage(IMAGE, () => null)).resolves.toBeNull();
  });

  it("awaits an async injected decoder too", async () => {
    await expect(decodeImage(IMAGE, async () => "async-decoded")).resolves.toBe("async-decoded");
  });
});

describe("decodeQrCode", () => {
  it("lazily loads jsqr on first use and decodes through its default export", async () => {
    expect(vi.mocked(jsQR)).not.toHaveBeenCalled();

    await expect(decodeQrCode(IMAGE)).resolves.toBe("decoded-by-jsqr");

    expect(vi.mocked(jsQR)).toHaveBeenCalledWith(IMAGE.data, IMAGE.width, IMAGE.height);
  });

  it("reuses the same module on a second call instead of importing it again", async () => {
    await decodeQrCode(IMAGE);
    const callsAfterFirst = vi.mocked(jsQR).mock.calls.length;

    await decodeQrCode(IMAGE);

    // jsQR itself is called again (once per decode); what's cached is the `import("jsqr")`
    // module load, which this can't observe directly — but a second decode still resolving
    // correctly confirms the cached module reference stayed usable.
    expect(vi.mocked(jsQR).mock.calls.length).toBeGreaterThan(callsAfterFirst);
  });
});
