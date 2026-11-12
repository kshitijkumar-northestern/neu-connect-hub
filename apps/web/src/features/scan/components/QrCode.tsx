import { cn } from "@/lib/utils";

/** The bits of `uqr`'s `encode()` result this component needs. */
export interface QrMatrix {
  size: number;
  data: boolean[][];
}

export interface QrCodeProps {
  matrix: QrMatrix;
  "aria-label": string;
  className?: string;
}

// Modules of quiet (light) space required on every side for a scanner to find the code.
const QUIET_ZONE = 4;

/** A `uqr` matrix as an inline SVG of `<rect>`s (never `dangerouslySetInnerHTML`). The panel is a
 * fixed light background with dark modules in `currentColor`, not the app's theme tokens: a QR
 * code in a dark-mode foreground color loses contrast and stops scanning, so this deliberately
 * ignores the theme and always renders dark-on-light. Never nest the brand mark inside this. */
export function QrCode({ matrix, className, ...props }: QrCodeProps) {
  const ariaLabel = props["aria-label"];
  const dimension = matrix.size + QUIET_ZONE * 2;

  return (
    <svg
      role="img"
      aria-label={ariaLabel}
      viewBox={`0 0 ${dimension} ${dimension}`}
      className={cn("rounded-2xl bg-white text-black", className)}
    >
      <rect data-slot="qr-panel" x={0} y={0} width={dimension} height={dimension} fill="white" />
      <g fill="currentColor">
        {matrix.data.flatMap((row, y) =>
          row.map((dark, x) =>
            dark ? (
              <rect key={`${x}-${y}`} data-slot="qr-module" x={x + QUIET_ZONE} y={y + QUIET_ZONE} width={1} height={1} />
            ) : null,
          ),
        )}
      </g>
    </svg>
  );
}
