import { useTheme } from "@/app/providers/ThemeProvider";
import logoOnDark from "@/assets/brand/logo-dark.svg";
import logoOnLight from "@/assets/brand/logo-light.svg";
import { cn } from "@/lib/utils";

/**
 * The NU Connect Hub mark, shown directly on its surface: never inside a tile, border or card.
 * Light surfaces get the dark mark (primary); dark surfaces get the white mark (foreground).
 */
export function BrandMark({ className }: { className?: string }) {
  const { resolvedTheme } = useTheme();
  return (
    <img
      src={resolvedTheme === "dark" ? logoOnDark : logoOnLight}
      alt="NU Connect Hub"
      width={32}
      height={32}
      className={cn("size-8 shrink-0 select-none", className)}
      draggable={false}
    />
  );
}
