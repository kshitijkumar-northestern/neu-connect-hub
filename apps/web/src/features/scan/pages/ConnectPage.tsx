import { useEffect, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { errorMessage } from "@/features/auth/errors";
import { useHashToken } from "@/features/auth/useHashToken";
import { FormError } from "@/features/auth/components/FormError";
import { connectionsApi } from "@/features/connections/api";
import { scanApi, type ConnectPreview } from "@/features/scan/api";
import { ApiError } from "@/lib/http";

type LoadState = "loading" | "ready" | "invalid" | "error";

const INVALID_CODE_MESSAGE = "This code isn't valid. Ask them for a new one.";

/** The action for each `connection_state`, from the viewer's side. The server never creates a
 * connection on its own: sending the invitation is this page's own, explicit `POST /connections`. */
function ConnectAction({ preview }: { preview: ConnectPreview }) {
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string>();

  async function sendInvitation() {
    setSending(true);
    setError(undefined);
    try {
      await connectionsApi.request(preview.user_id);
      setSent(true);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setSending(false);
    }
  }

  switch (preview.connection_state) {
    case "none":
      if (sent) return <p role="status">Invitation sent</p>;
      return (
        <div className="flex flex-col items-start gap-2">
          <FormError>{error}</FormError>
          <Button disabled={sending} onClick={() => void sendInvitation()}>
            Send invitation
          </Button>
        </div>
      );
    case "pending_outgoing":
      return <p role="status">Invitation sent</p>;
    case "pending_incoming":
      return (
        <p>
          They&apos;ve invited you.{" "}
          <Link className="underline underline-offset-4" to="/connections">
            Respond on your connections page
          </Link>
        </p>
      );
    case "connected":
      return (
        <p>
          You&apos;re connected.{" "}
          <Link className="underline underline-offset-4" to={`/people/${preview.user_id}`}>
            View their profile
          </Link>
        </p>
      );
    default: {
      // Exhaustiveness check: a new connection_state value fails the build here, not silently.
      const neverState: never = preview.connection_state;
      return neverState;
    }
  }
}

/** Where a scanned or pasted Scan to Connect link lands. Reads the token from the fragment (which
 * clears it from the address bar), shows the minimum the API reveals, and never acts without the
 * student's press. */
export function ConnectPage() {
  const token = useHashToken();
  const [load, setLoad] = useState<LoadState>(token ? "loading" : "invalid");
  const [preview, setPreview] = useState<ConnectPreview>();
  const [errorText, setErrorText] = useState<string>();

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    scanApi
      .resolve(token)
      .then((result) => {
        if (cancelled) return;
        setPreview(result);
        setLoad("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        if (failure instanceof ApiError && failure.status === 404) {
          setLoad("invalid");
        } else {
          setErrorText(errorMessage(failure));
          setLoad("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "invalid") {
    return <p>{INVALID_CODE_MESSAGE}</p>;
  }

  if (load === "error" || !preview) {
    return <p role="alert">{errorText ?? "Something went wrong. Please try again."}</p>;
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{preview.display_name}</h1>
        {preview.headline ? <p className="text-muted-foreground">{preview.headline}</p> : null}
      </div>
      <ConnectAction preview={preview} />
    </section>
  );
}
