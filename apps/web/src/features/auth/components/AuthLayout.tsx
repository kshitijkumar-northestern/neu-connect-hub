import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

/** The centred card every auth page shares. */
export function AuthLayout({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-sm">
      <Card>
        <CardHeader>
          <CardTitle>
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          </CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </CardHeader>
        <CardContent>{children}</CardContent>
        {footer ? <CardFooter className="flex-col items-start gap-2 text-sm">{footer}</CardFooter> : null}
      </Card>
    </div>
  );
}
