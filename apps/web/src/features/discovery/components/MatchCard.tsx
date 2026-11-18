import { useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { ConnectButton } from "@/features/connections/components/ConnectButton";
import { discoveryApi, type Match } from "@/features/discovery/api";
import { describeReason } from "@/features/discovery/reasons";

export interface MatchCardProps {
  match: Match;
  /** The card hides itself for this visit; the page owns the list and the focus move that follows. */
  onNotNow: (userId: string) => void;
  /** Registers this card's name link so the page can move focus here once a sibling is dismissed. */
  headingRef?: (element: HTMLAnchorElement | null) => void;
}

/** One suggested collaborator: their name, headline, the plain-language reasons they were
 * suggested, and the Connect / Not now actions. */
export function MatchCard({ match, onNotNow, headingRef }: MatchCardProps) {
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);

  function handleSent() {
    setSent(true);
    void discoveryApi.feedback(match.user_id, "connected").catch(() => {
      // Best-effort signal for ranking; a failure here shouldn't undo a request that succeeded.
    });
  }

  function handleNotNow() {
    onNotNow(match.user_id);
    void discoveryApi.feedback(match.user_id, "dismissed").catch(() => {
      // Same best-effort signal; the card is already gone from view either way.
    });
  }

  return (
    <li>
      <Card>
        <CardHeader>
          <Link
            ref={headingRef}
            to={`/people/${match.user_id}`}
            className="text-lg font-medium underline-offset-4 hover:underline"
          >
            {match.display_name}
          </Link>
          {match.headline ? <p className="text-sm text-muted-foreground">{match.headline}</p> : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
            {match.reasons.map((reason, index) => (
              <li key={index}>{describeReason(reason)}</li>
            ))}
          </ul>

          <ConnectButton
            userId={match.user_id}
            displayName={match.display_name}
            onSent={handleSent}
            onPendingChange={setPending}
          >
            {/* Gone once the request is sent and locked while it's on its way: a dismissal then
                would contradict it. */}
            {sent ? null : (
              <Button
                variant="outline"
                disabled={pending}
                aria-label={`Not now: ${match.display_name}`}
                onClick={handleNotNow}
              >
                Not now
              </Button>
            )}
          </ConnectButton>
        </CardContent>
      </Card>
    </li>
  );
}
