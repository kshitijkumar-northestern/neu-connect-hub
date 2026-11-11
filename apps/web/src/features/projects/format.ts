/** "3 places left" / "1 place left" / "No places left", for a role's remaining_slots. */
export function placesLeftLabel(remaining: number): string {
  if (remaining <= 0) return "No places left";
  return `${remaining} place${remaining === 1 ? "" : "s"} left`;
}
