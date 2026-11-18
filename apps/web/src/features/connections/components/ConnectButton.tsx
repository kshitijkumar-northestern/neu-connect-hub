import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { connectionsApi } from "@/features/connections/api";
import { ApiError } from "@/lib/http";

type ConnectState = "idle" | "pending" | "sent" | "error";

const ALREADY_CONNECTED_MESSAGE = "Already connected or requested";

export interface ConnectButtonProps {
  userId: string;
  displayName: string;
  /** Called once the request has been sent. */
  onSent?: () => void;
  /** True when the request goes out, false once it settles either way. */
  onPendingChange?: (pending: boolean) => void;
  /** Other actions shown beside the button, such as "Not now". */
  children?: ReactNode;
}

/** Sends a connection request: any error above, the button (and `children`) in a row, and a live
 * region below announcing the request once it's sent. Rendered as siblings so the caller's
 * layout spaces them. */
export function ConnectButton({ userId, displayName, onSent, onPendingChange, children }: ConnectButtonProps) {
  const [connect, setConnect] = useState<ConnectState>("idle");
  const [error, setError] = useState<string>();

  async function handleConnect() {
    setConnect("pending");
    setError(undefined);
    onPendingChange?.(true);
    try {
      await connectionsApi.request(userId);
      setConnect("sent");
      onPendingChange?.(false);
      onSent?.();
    } catch (failure) {
      setConnect("error");
      setError(failure instanceof ApiError && failure.status === 409 ? ALREADY_CONNECTED_MESSAGE : errorMessage(failure));
      onPendingChange?.(false);
    }
  }

  const sent = connect === "sent";

  return (
    <>
      <FormError>{error}</FormError>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          aria-busy={connect === "pending"}
          disabled={connect === "pending" || sent}
          // The accessible name starts with the visible label, whichever it is.
          aria-label={sent ? `Request sent to ${displayName}` : `Connect with ${displayName}`}
          onClick={() => void handleConnect()}
        >
          {sent ? "Request sent" : "Connect"}
        </Button>
        {children}
      </div>

      {/* Always mounted, so screen readers are already watching it when the message arrives.
          Visually hidden while empty, never display:none, which would drop it from the
          accessibility tree. */}
      <p role="status" className={sent ? "text-sm text-muted-foreground" : "sr-only"}>
        {sent ? `Connection request sent to ${displayName}` : null}
      </p>
    </>
  );
}
