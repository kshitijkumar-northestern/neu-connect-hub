import { useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { connectionsApi } from "@/features/connections/api";
import { discoveryApi, type Match } from "@/features/discovery/api";
import { describeReason } from "@/features/discovery/reasons";
import { ApiError } from "@/lib/http";

type ConnectState = "idle" | "pending" | "sent" | "error";

const ALREADY_CONNECTED_MESSAGE = "Already connected or requested";

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
  const [connect, setConnect] = useState<ConnectState>("idle");
  const [error, setError] = useState<string>();

  async function handleConnect() {
    setConnect("pending");
    setError(undefined);
    try {
      await connectionsApi.request(match.user_id);
      setConnect("sent");
      void discoveryApi.feedback(match.user_id, "connected").catch(() => {
        // Best-effort signal for ranking; a failure here shouldn't undo a request that succeeded.
      });
    } catch (failure) {
      setConnect("error");
      setError(failure instanceof ApiError && failure.status === 409 ? ALREADY_CONNECTED_MESSAGE : errorMessage(failure));
    }
  }

  function handleNotNow() {
    onNotNow(match.user_id);
    void discoveryApi.feedback(match.user_id, "dismissed").catch(() => {
      // Same best-effort signal; the card is already gone from view either way.
    });
  }

  const sent = connect === "sent";

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

          <FormError>{error}</FormError>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              aria-busy={connect === "pending"}
              disabled={connect === "pending" || sent}
              // The accessible name starts with the visible label, whichever it is.
              aria-label={sent ? `Request sent to ${match.display_name}` : `Connect with ${match.display_name}`}
              onClick={() => void handleConnect()}
            >
              {sent ? "Request sent" : "Connect"}
            </Button>
            {/* Gone once the request is sent and locked while it's on its way: a dismissal then
                would contradict it. */}
            {sent ? null : (
              <Button
                variant="outline"
                disabled={connect === "pending"}
                aria-label={`Not now: ${match.display_name}`}
                onClick={handleNotNow}
              >
                Not now
              </Button>
            )}
          </div>

          {/* Always mounted, so screen readers are already watching it when the message arrives.
              Visually hidden while empty, never display:none, which would drop it from the
              accessibility tree. */}
          <p role="status" className={sent ? "text-sm text-muted-foreground" : "sr-only"}>
            {sent ? `Connection request sent to ${match.display_name}` : null}
          </p>
        </CardContent>
      </Card>
    </li>
  );
}
