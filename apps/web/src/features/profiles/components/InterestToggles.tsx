import { Button } from "@/components/ui/button";
import { FieldDescription, FieldError, FieldLegend, FieldSet } from "@/components/ui/field";
import type { InterestOption } from "@/features/profiles/api";

export function InterestToggles({
  options,
  selected,
  onChange,
  error,
}: {
  options: readonly InterestOption[];
  selected: readonly string[];
  onChange: (next: string[]) => void;
  error?: string;
}) {
  function toggle(slug: string) {
    onChange(selected.includes(slug) ? selected.filter((item) => item !== slug) : [...selected, slug]);
  }

  return (
    <FieldSet>
      <FieldLegend variant="label">Interests</FieldLegend>
      <FieldDescription>Pick the topics you'd like to connect with other students over.</FieldDescription>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const pressed = selected.includes(option.slug);
          return (
            <Button
              key={option.slug}
              type="button"
              size="sm"
              variant={pressed ? "default" : "outline"}
              aria-pressed={pressed}
              onClick={() => toggle(option.slug)}
            >
              {option.name}
            </Button>
          );
        })}
      </div>
      <FieldError>{error}</FieldError>
    </FieldSet>
  );
}
