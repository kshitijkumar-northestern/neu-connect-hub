/** "online" with a dot, shown only while the other student is online. The palette is neutral, so
 * the word carries the meaning rather than a colour. */
export function PresenceIndicator({ online }: { online: boolean }) {
  if (!online) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
      <span aria-hidden="true" className="size-2 rounded-full bg-foreground" />
      online
    </span>
  );
}
