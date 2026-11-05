import { useId, type ComponentProps, type ReactNode } from "react";

import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

/** Label, input, hint and error wired together for screen readers. */
export function TextField({
  label,
  description,
  error,
  ...input
}: { label: string; description?: ReactNode; error?: string } & ComponentProps<typeof Input>) {
  const id = useId();
  const descriptionId = `${id}-description`;
  const errorId = `${id}-error`;
  const describedBy = [description ? descriptionId : null, error ? errorId : null].filter(Boolean).join(" ");

  return (
    <Field data-invalid={error ? true : undefined}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} aria-invalid={error ? true : undefined} aria-describedby={describedBy || undefined} {...input} />
      {description ? <FieldDescription id={descriptionId}>{description}</FieldDescription> : null}
      {error ? <FieldError id={errorId}>{error}</FieldError> : null}
    </Field>
  );
}
