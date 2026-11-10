import { Cancel01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useId, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { SkillLevel, SkillOption } from "@/features/profiles/api";
import { LEVELS, LEVEL_LABELS } from "@/features/profiles/constants";

export interface SkillChoice {
  slug: string;
  level?: SkillLevel;
}

function groupByCategory(options: readonly SkillOption[]): Map<string, SkillOption[]> {
  const groups = new Map<string, SkillOption[]>();
  for (const option of options) {
    const group = groups.get(option.category);
    if (group) group.push(option);
    else groups.set(option.category, [option]);
  }
  return groups;
}

/** A skill the student has (with a level) or is looking for (without one), added from a picker
 * and shown as removable badges. Shared by "Skills you have" and "Skills you're looking for". */
export function SkillField({
  legend,
  description,
  options,
  selected,
  withLevel,
  onChange,
  error,
}: {
  legend: string;
  description?: string;
  options: readonly SkillOption[];
  selected: readonly SkillChoice[];
  withLevel: boolean;
  onChange: (next: SkillChoice[]) => void;
  error?: string;
}) {
  const id = useId();
  const [slug, setSlug] = useState("");
  const [level, setLevel] = useState<SkillLevel>("beginner");
  const grouped = useMemo(() => groupByCategory(options), [options]);
  const available = useMemo(
    () => options.filter((option) => !selected.some((choice) => choice.slug === option.slug)),
    [options, selected],
  );
  const nameOf = (candidate: string) => options.find((option) => option.slug === candidate)?.name ?? candidate;

  function add() {
    if (!slug) return;
    onChange([...selected, withLevel ? { slug, level } : { slug }]);
    setSlug("");
    setLevel("beginner");
  }

  function remove(target: string) {
    onChange(selected.filter((choice) => choice.slug !== target));
  }

  return (
    <FieldSet>
      <FieldLegend variant="label">{legend}</FieldLegend>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      <div className="flex flex-wrap items-end gap-2">
        <Field className="min-w-40 flex-1">
          <FieldLabel htmlFor={`${id}-skill`}>Skill</FieldLabel>
          <Select value={slug} onValueChange={setSlug}>
            <SelectTrigger id={`${id}-skill`} className="w-full">
              <SelectValue placeholder="Choose a skill" />
            </SelectTrigger>
            <SelectContent>
              {[...grouped.entries()].map(([category, items]) => {
                const choices = items.filter((item) => available.includes(item));
                if (!choices.length) return null;
                return (
                  <SelectGroup key={category}>
                    <SelectLabel>{category}</SelectLabel>
                    {choices.map((item) => (
                      <SelectItem key={item.slug} value={item.slug}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                );
              })}
            </SelectContent>
          </Select>
        </Field>
        {withLevel ? (
          <Field className="w-36">
            <FieldLabel htmlFor={`${id}-level`}>Level</FieldLabel>
            <Select value={level} onValueChange={(next) => setLevel(next as SkillLevel)}>
              <SelectTrigger id={`${id}-level`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LEVELS.map((item) => (
                  <SelectItem key={item} value={item}>
                    {LEVEL_LABELS[item]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        ) : null}
        <Button type="button" variant="outline" onClick={add} disabled={!slug}>
          Add
        </Button>
      </div>
      {selected.length ? (
        <ul className="flex flex-wrap gap-2">
          {selected.map((choice) => (
            <li key={choice.slug}>
              <Badge variant="secondary" className="gap-1.5 py-1">
                {nameOf(choice.slug)}
                {choice.level ? ` · ${LEVEL_LABELS[choice.level]}` : ""}
                <button
                  type="button"
                  onClick={() => remove(choice.slug)}
                  className="-mr-1 inline-flex rounded-full p-0.5 outline-none hover:bg-foreground/10 focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  <HugeiconsIcon icon={Cancel01Icon} aria-hidden="true" className="size-3" />
                  <span className="sr-only">Remove {nameOf(choice.slug)}</span>
                </button>
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
      <FieldError>{error}</FieldError>
    </FieldSet>
  );
}
