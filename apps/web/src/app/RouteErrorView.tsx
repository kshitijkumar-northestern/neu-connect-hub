import { Link } from "react-router";

import { Button } from "@/components/ui/button";

/** Shown when a route throws. Deliberately generic: error details never reach the page. */
export function RouteErrorView() {
  return (
    <main className="mx-auto flex min-h-svh max-w-3xl flex-col items-start justify-center gap-4 px-4 py-16">
      <div role="alert" className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold tracking-tight">Something went wrong</h1>
        <p className="text-muted-foreground">Please reload the page. If the problem continues, try again later.</p>
      </div>
      <Button asChild variant="outline">
        <Link to="/">Go to the home page</Link>
      </Button>
    </main>
  );
}
