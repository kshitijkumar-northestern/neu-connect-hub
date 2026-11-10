import { Link } from "react-router";

import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <section className="flex flex-col items-start gap-4">
      <h1 className="text-3xl font-bold tracking-tight">Page not found</h1>
      <p className="text-muted-foreground">The page you asked for does not exist.</p>
      <Button asChild>
        <Link to="/">Go to the home page</Link>
      </Button>
    </section>
  );
}
