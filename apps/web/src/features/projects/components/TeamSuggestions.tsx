import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { errorMessage } from "@/features/auth/errors";
import { ConnectButton } from "@/features/connections/components/ConnectButton";
import { listNames } from "@/features/discovery/reasons";
import type { NamedItem } from "@/features/profiles/api";
import { projectsApi, type TeamSuggestion } from "@/features/projects/api";
import { ApiError } from "@/lib/http";

/** "unavailable" is a 404: the project closed or filled since the page loaded, so there's nothing
 * to suggest and a retry can't help. "rate-limited" is a 429, where retrying straight away would
 * only hit the limit again. */
type LoadState =
  | "loading"
  | "ready"
  | "opt-in"
  | "students-only"
  | "availability"
  | "unavailable"
  | "rate-limited"
  | "error";

function failureState(failure: unknown): LoadState {
  if (!(failure instanceof ApiError)) return "error";
  if (failure.status === 404) return "unavailable";
  if (failure.status === 429) return "rate-limited";
  if (failure.status === 403 && failure.code === "discovery_opt_in_required") return "opt-in";
  if (failure.status === 403 && failure.code === "discovery_students_only") return "students-only";
  if (failure.status === 409 && failure.code === "availability_required") return "availability";
  return "error";
}

/** One suggested student: who they are, the role they could fill, the needed skills they'd bring,
 * and a Connect button that works like the one on a Discover match card. */
function SuggestionItem({ suggestion }: { suggestion: TeamSuggestion }) {
  return (
    <li className="flex flex-col gap-3 rounded-2xl border px-4 py-3">
      <div className="flex flex-col gap-1">
        <Link
          to={`/people/${suggestion.user_id}`}
          className="self-start font-medium underline-offset-4 hover:underline"
        >
          {suggestion.display_name}
        </Link>
        {suggestion.headline ? <p className="text-sm text-muted-foreground">{suggestion.headline}</p> : null}
      </div>

      <div className="flex flex-col gap-1 text-sm">
        <p>Could fill: {suggestion.role.title}</p>
        <p className="text-muted-foreground">Brings {listNames(suggestion.covers)}</p>
      </div>

      <ConnectButton userId={suggestion.user_id} displayName={suggestion.display_name} />
    </li>
  );
}

/** For the owner of an open project: students who could bring the skills the team still needs.
 * Suggestions only; nobody is added to the team. */
export function TeamSuggestions({ projectId }: { projectId: string }) {
  const [load, setLoad] = useState<LoadState>("loading");
  const [suggestions, setSuggestions] = useState<TeamSuggestion[]>([]);
  const [uncovered, setUncovered] = useState<NamedItem[]>([]);
  const [rateLimitMessage, setRateLimitMessage] = useState<string>();

  // Tags each call so a stale response (a retry overtaking it, or React's StrictMode replaying the
  // effect in development) can't clobber a newer one's result.
  const requestIdRef = useRef(0);

  // Only registers the promise's continuation when called from the effect: the initial state
  // already covers the mount, and `retry` sets the loading state itself before calling this.
  const runFetch = useCallback(() => {
    const requestId = ++requestIdRef.current;
    projectsApi
      .suggestions(projectId)
      .then((result) => {
        if (requestIdRef.current !== requestId) return;
        setSuggestions(result.suggestions);
        setUncovered(result.uncovered);
        setLoad("ready");
      })
      .catch((failure: unknown) => {
        if (requestIdRef.current !== requestId) return;
        const next = failureState(failure);
        if (next === "rate-limited") setRateLimitMessage(errorMessage(failure));
        setLoad(next);
      });
  }, [projectId]);

  useEffect(() => {
    runFetch();
  }, [runFetch]);

  function retry() {
    setLoad("loading");
    runFetch();
  }

  if (load === "unavailable") return null;

  return (
    <div>
      <h2 className="text-sm font-medium">Suggested teammates</h2>
      <div className="mt-2 flex flex-col gap-3">
        {load === "loading" ? (
          <p role="status" className="text-sm text-muted-foreground">
            Finding teammates…
          </p>
        ) : load === "opt-in" ? (
          <>
            <p className="text-sm text-muted-foreground">Turn on discovery in your profile to see suggestions.</p>
            <Button asChild variant="outline" className="self-start">
              <Link to="/profile">Go to your profile</Link>
            </Button>
          </>
        ) : load === "students-only" ? (
          <p className="text-sm text-muted-foreground">Suggestions are for student accounts.</p>
        ) : load === "availability" ? (
          <>
            <p className="text-sm text-muted-foreground">Add your weekly availability to see suggested teammates.</p>
            <Button asChild variant="outline" className="self-start">
              <Link to="/profile">Add your availability</Link>
            </Button>
          </>
        ) : load === "rate-limited" ? (
          <p role="alert" className="text-sm">
            {rateLimitMessage}
          </p>
        ) : load === "error" ? (
          <>
            <p role="alert" className="text-sm">
              Can't load suggestions right now. Check your connection and try again.
            </p>
            <Button type="button" variant="outline" className="self-start" onClick={retry}>
              Try again
            </Button>
          </>
        ) : suggestions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No one matches the skills your team still needs right now.</p>
        ) : (
          <>
            <ul aria-label="Suggested teammates" className="flex flex-col gap-2">
              {suggestions.map((suggestion) => (
                <SuggestionItem key={suggestion.user_id} suggestion={suggestion} />
              ))}
            </ul>
            {uncovered.length ? (
              <p className="text-sm text-muted-foreground">
                Still uncovered: {uncovered.map((skill) => skill.name).join(", ")}
              </p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
