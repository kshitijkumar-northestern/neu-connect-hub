import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { errorMessage } from "@/features/auth/errors";
import { discoveryApi, type Match } from "@/features/discovery/api";
import { MatchCard } from "@/features/discovery/components/MatchCard";
import { ScanCodeDialog } from "@/features/scan/components/ScanCodeDialog";
import { ApiError } from "@/lib/http";

/** "rate-limited" is a 429, where retrying straight away would only hit the limit again. */
type LoadState = "loading" | "opt-in" | "students-only" | "rate-limited" | "error" | "ready";

/** Where focus goes next after a card is dismissed: another card's name link, or the page
 * heading when none remain. */
type FocusTarget = { kind: "card"; userId: string } | { kind: "heading" };

export function DiscoverPage() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [matches, setMatches] = useState<Match[]>([]);
  const [rateLimitMessage, setRateLimitMessage] = useState<string>();
  const [scanOpen, setScanOpen] = useState(false);

  const headingRef = useRef<HTMLHeadingElement>(null);
  const cardRefs = useRef<Record<string, HTMLAnchorElement | null>>({});
  const pendingFocusRef = useRef<FocusTarget | undefined>(undefined);

  // Tags each call so a stale response (an abandoned request, or React's StrictMode replaying this
  // effect once in development) can't clobber a newer one's result.
  const requestIdRef = useRef(0);

  // Not called synchronously from the effect body (only registers the promise's continuation
  // there): the loading state is set by whoever triggers a fetch — the initial state already
  // covers the mount, and `retry` sets it explicitly before calling this again.
  const runFetch = useCallback(() => {
    const requestId = ++requestIdRef.current;
    discoveryApi
      .matches()
      .then((result) => {
        if (requestIdRef.current !== requestId) return;
        setMatches(result.matches);
        setLoad("ready");
      })
      .catch((failure: unknown) => {
        if (requestIdRef.current !== requestId) return;
        if (failure instanceof ApiError && failure.status === 403 && failure.code === "discovery_opt_in_required") {
          setLoad("opt-in");
        } else if (failure instanceof ApiError && failure.status === 403 && failure.code === "discovery_students_only") {
          setLoad("students-only");
        } else if (failure instanceof ApiError && failure.status === 429) {
          setRateLimitMessage(errorMessage(failure));
          setLoad("rate-limited");
        } else {
          setLoad("error");
        }
      });
  }, []);

  useEffect(() => {
    runFetch();
  }, [runFetch]);

  function retry() {
    setLoad("loading");
    runFetch();
  }

  // Moves focus once the dismissed card has actually left the DOM (ref no longer points to it),
  // rather than racing the render that removes it.
  useEffect(() => {
    const target = pendingFocusRef.current;
    if (!target) return;
    pendingFocusRef.current = undefined;
    if (target.kind === "heading") {
      headingRef.current?.focus();
    } else {
      cardRefs.current[target.userId]?.focus();
    }
  }, [matches]);

  function handleNotNow(userId: string) {
    const index = matches.findIndex((match) => match.user_id === userId);
    const next = matches.filter((match) => match.user_id !== userId);
    // The card that slid into its place, else the one before it, else the heading.
    const neighbour = next[index] ?? next[index - 1];
    pendingFocusRef.current = neighbour ? { kind: "card", userId: neighbour.user_id } : { kind: "heading" };
    delete cardRefs.current[userId];
    setMatches(next);
  }

  const scanEntryPoint = (
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" onClick={() => setScanOpen(true)}>
        Scan a code
      </Button>
      <Button asChild variant="outline">
        <Link to="/share">Show your code</Link>
      </Button>
    </div>
  );

  const heading = (
    <div className="flex flex-col gap-1">
      <h1 ref={headingRef} tabIndex={-1} className="text-2xl font-semibold tracking-tight">
        Discover
      </h1>
      <p className="text-muted-foreground">Students you might work well with, based on what you both share.</p>
    </div>
  );

  if (load === "loading") {
    return (
      <section className="flex flex-col gap-6">
        {heading}
        <p role="status" className="text-muted-foreground">
          Finding matches…
        </p>
      </section>
    );
  }

  if (load === "opt-in") {
    return (
      <section className="flex flex-col gap-6">
        {heading}
        <p className="text-muted-foreground">Turn on discovery in your profile to see suggestions.</p>
        <Button asChild variant="outline" className="self-start">
          <Link to="/profile">Go to your profile</Link>
        </Button>
      </section>
    );
  }

  if (load === "students-only") {
    return (
      <section className="flex flex-col gap-6">
        {heading}
        <p className="text-muted-foreground">Discovery is for student accounts.</p>
      </section>
    );
  }

  if (load === "rate-limited") {
    return (
      <section className="flex flex-col gap-6">
        {heading}
        <p role="alert">{rateLimitMessage}</p>
      </section>
    );
  }

  if (load === "error") {
    return (
      <section className="flex flex-col gap-6">
        {heading}
        <p role="alert">Can't load suggestions right now. Check your connection and try again.</p>
        <Button type="button" variant="outline" className="self-start" onClick={retry}>
          Try again
        </Button>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        {heading}
        {scanEntryPoint}
      </div>
      <ScanCodeDialog open={scanOpen} onOpenChange={setScanOpen} />

      {matches.length === 0 ? (
        <div className="flex flex-col gap-3">
          <p className="text-muted-foreground">No suggestions right now</p>
          <ul className="list-disc pl-5 text-sm text-muted-foreground">
            <li>Add skills you're looking for to your profile</li>
            <li>Add your weekly availability</li>
          </ul>
          <Button asChild variant="outline" className="self-start">
            <Link to="/projects">Browse projects</Link>
          </Button>
        </div>
      ) : (
        <ul aria-label="Suggested collaborators" className="flex flex-col gap-4">
          {matches.map((match) => (
            <MatchCard
              key={match.user_id}
              match={match}
              onNotNow={handleNotNow}
              headingRef={(element) => {
                cardRefs.current[match.user_id] = element;
              }}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
