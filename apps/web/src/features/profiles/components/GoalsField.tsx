import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import type { Goal } from "@/features/profiles/api";
import { GOALS, GOAL_LABELS } from "@/features/profiles/constants";

export function GoalsField({
  selected,
  onChange,
}: {
  selected: readonly Goal[];
  onChange: (next: Goal[]) => void;
}) {
  function toggle(goal: Goal, checked: boolean) {
    onChange(checked ? [...selected, goal] : selected.filter((item) => item !== goal));
  }

  return (
    <FieldSet>
      <FieldLegend>What are you looking for?</FieldLegend>
      <div className="flex flex-col gap-2">
        {GOALS.map((goal) => (
          <Field key={goal} orientation="horizontal">
            <Checkbox
              id={`goal-${goal}`}
              checked={selected.includes(goal)}
              onCheckedChange={(checked) => toggle(goal, checked === true)}
            />
            <FieldLabel htmlFor={`goal-${goal}`} className="font-normal">
              {GOAL_LABELS[goal]}
            </FieldLabel>
          </Field>
        ))}
      </div>
    </FieldSet>
  );
}
