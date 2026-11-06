import type { ReactNode } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";

/** A form-level error, announced when it appears (Alert has role="alert"). */
export function FormError({ children }: { children?: ReactNode }) {
  if (!children) return null;
  return (
    <Alert variant="destructive">
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}
