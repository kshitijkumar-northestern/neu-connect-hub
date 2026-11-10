import { Button } from "@/components/ui/button";
import { FieldDescription, FieldError, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { WEEKDAYS } from "@/features/profiles/constants";

export interface AvailabilityRow {
  id: number;
  /** HH:MM, for an <input type="time">. */
  start: string;
  end: string;
}

/** One row list per weekday, Monday (index 0) through Sunday (index 6). */
export type AvailabilityRows = AvailabilityRow[][];

export function emptyAvailabilityRows(): AvailabilityRows {
  return WEEKDAYS.map(() => []);
}

export function AvailabilityEditor({
  rows,
  onChange,
  error,
}: {
  rows: AvailabilityRows;
  onChange: (rows: AvailabilityRows) => void;
  error?: string;
}) {
  function addRow(weekday: number) {
    // Derived from the current rows rather than a counter ref, so ids stay unique even after
    // profile data loads in asynchronously and replaces the rows this component started with.
    const usedIds = rows.flat().map((row) => row.id);
    const id = (usedIds.length ? Math.max(...usedIds) : -1) + 1;
    const row: AvailabilityRow = { id, start: "09:00", end: "17:00" };
    onChange(rows.map((day, index) => (index === weekday ? [...day, row] : day)));
  }

  function updateRow(weekday: number, id: number, patch: Partial<AvailabilityRow>) {
    onChange(
      rows.map((day, index) => (index === weekday ? day.map((row) => (row.id === id ? { ...row, ...patch } : row)) : day)),
    );
  }

  function removeRow(weekday: number, id: number) {
    onChange(rows.map((day, index) => (index === weekday ? day.filter((row) => row.id !== id) : day)));
  }

  return (
    <FieldSet>
      <FieldLegend>Weekly availability</FieldLegend>
      <FieldDescription>Times are in the time zone you chose above.</FieldDescription>
      <div className="flex flex-col gap-4">
        {WEEKDAYS.map((label, weekday) => (
          <div key={label} className="flex flex-col gap-2 sm:flex-row sm:items-start">
            <span className="text-sm font-medium sm:w-28 sm:shrink-0 sm:pt-1.5">{label}</span>
            <div className="flex flex-1 flex-col gap-2">
              {rows[weekday]?.map((row) => (
                <div key={row.id} className="flex flex-wrap items-center gap-2">
                  <label className="sr-only" htmlFor={`availability-${weekday}-${row.id}-start`}>
                    {label} range start time
                  </label>
                  <Input
                    id={`availability-${weekday}-${row.id}-start`}
                    type="time"
                    step={900}
                    className="w-32"
                    value={row.start}
                    onChange={(event) => updateRow(weekday, row.id, { start: event.target.value })}
                  />
                  <span aria-hidden="true" className="text-muted-foreground">
                    to
                  </span>
                  <label className="sr-only" htmlFor={`availability-${weekday}-${row.id}-end`}>
                    {label} range end time
                  </label>
                  <Input
                    id={`availability-${weekday}-${row.id}-end`}
                    type="time"
                    step={900}
                    className="w-32"
                    value={row.end}
                    onChange={(event) => updateRow(weekday, row.id, { end: event.target.value })}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove this ${label} range`}
                    onClick={() => removeRow(weekday, row.id)}
                  >
                    Remove
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="self-start"
                aria-label={`Add a time for ${label}`}
                onClick={() => addRow(weekday)}
              >
                Add a time
              </Button>
            </div>
          </div>
        ))}
      </div>
      <FieldError>{error}</FieldError>
    </FieldSet>
  );
}
