import { useEffect, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { connectionsApi, type Connection } from "@/features/connections/api";
import { ScanCodeDialog } from "@/features/scan/components/ScanCodeDialog";
import { ApiError } from "@/lib/http";

type LoadState = "loading" | "ready" | "error";

function nameFor(connection: Connection): string {
  return connection.display_name ?? "A student";
}

function RequestRow({
  connection,
  onAccept,
  onDecline,
  busy,
}: {
  connection: Connection;
  onAccept: (connection: Connection) => void;
  onDecline: (connection: Connection) => void;
  busy: boolean;
}) {
  const name = nameFor(connection);
  return (
    <li className="flex items-center justify-between gap-4 rounded-2xl border px-4 py-2">
      <Link to={`/people/${connection.user_id}`} className="font-medium underline-offset-4 hover:underline">
        {name}
      </Link>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} aria-label={`Accept ${name}'s request`} onClick={() => onAccept(connection)}>
          Accept
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          aria-label={`Decline ${name}'s request`}
          onClick={() => onDecline(connection)}
        >
          Decline
        </Button>
      </div>
    </li>
  );
}

function SentRow({
  connection,
  onWithdraw,
  busy,
}: {
  connection: Connection;
  onWithdraw: (connection: Connection) => void;
  busy: boolean;
}) {
  const name = nameFor(connection);
  return (
    <li className="flex items-center justify-between gap-4 rounded-2xl border px-4 py-2">
      <Link to={`/people/${connection.user_id}`} className="font-medium underline-offset-4 hover:underline">
        {name}
      </Link>
      <Button
        size="sm"
        variant="outline"
        disabled={busy}
        aria-label={`Withdraw request to ${name}`}
        onClick={() => onWithdraw(connection)}
      >
        Withdraw
      </Button>
    </li>
  );
}

function ConnectedRow({ connection }: { connection: Connection }) {
  return (
    <li className="flex items-center justify-between gap-4 rounded-2xl border px-4 py-2">
      <Link to={`/people/${connection.user_id}`} className="font-medium underline-offset-4 hover:underline">
        {nameFor(connection)}
      </Link>
      {connection.conversation_id ? (
        <Button asChild size="sm" variant="outline">
          <Link to={`/messages/${connection.conversation_id}`}>Message</Link>
        </Button>
      ) : null}
    </li>
  );
}

/** The three connection groups: incoming requests, requests you've sent and your accepted
 * connections. Updates come from the server's response, not optimistic local edits. */
export function ConnectionsPage() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [connections, setConnections] = useState<Connection[]>([]);
  const [error, setError] = useState<string>();
  const [busyId, setBusyId] = useState<string>();
  const [scanOpen, setScanOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    connectionsApi
      .list()
      .then((result) => {
        if (cancelled) return;
        setConnections(result);
        setLoad("ready");
      })
      .catch(() => {
        if (!cancelled) setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Re-fetches the list after a 409 (the connection closed or changed elsewhere), leaving the
   * current rows in place if the reload itself fails. */
  async function reloadConnections() {
    try {
      const result = await connectionsApi.list();
      setConnections(result);
    } catch {
      // The FormError from the action that triggered this already explains the failure.
    }
  }

  async function runAction(connection: Connection, action: () => Promise<void>) {
    setBusyId(connection.id);
    setError(undefined);
    try {
      await action();
    } catch (failure) {
      setError(errorMessage(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        await reloadConnections();
      }
    } finally {
      setBusyId(undefined);
    }
  }

  function accept(connection: Connection) {
    void runAction(connection, async () => {
      const result = await connectionsApi.accept(connection.id);
      setConnections((current) => current.map((candidate) => (candidate.id === connection.id ? result : candidate)));
    });
  }

  function decline(connection: Connection) {
    void runAction(connection, async () => {
      await connectionsApi.decline(connection.id);
      setConnections((current) => current.filter((candidate) => candidate.id !== connection.id));
    });
  }

  function withdraw(connection: Connection) {
    void runAction(connection, async () => {
      await connectionsApi.withdraw(connection.id);
      setConnections((current) => current.filter((candidate) => candidate.id !== connection.id));
    });
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "error") {
    return <p role="alert">Can't load your connections right now. Check your connection and try again.</p>;
  }

  const incoming = connections.filter((connection) => connection.state === "pending" && connection.direction === "incoming");
  const outgoing = connections.filter((connection) => connection.state === "pending" && connection.direction === "outgoing");
  const accepted = connections.filter((connection) => connection.state === "accepted");

  return (
    <section className="flex flex-col gap-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Connections</h1>
        <Button type="button" variant="outline" onClick={() => setScanOpen(true)}>
          Scan a code
        </Button>
      </div>
      <ScanCodeDialog open={scanOpen} onOpenChange={setScanOpen} />
      <FormError>{error}</FormError>

      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Requests for you</h2>
        {incoming.length ? (
          <ul className="flex flex-col gap-2">
            {incoming.map((connection) => (
              <RequestRow
                key={connection.id}
                connection={connection}
                onAccept={accept}
                onDecline={decline}
                busy={busyId === connection.id}
              />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">No one has asked to connect yet.</p>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Sent requests</h2>
        {outgoing.length ? (
          <ul className="flex flex-col gap-2">
            {outgoing.map((connection) => (
              <SentRow key={connection.id} connection={connection} onWithdraw={withdraw} busy={busyId === connection.id} />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">You haven't sent any requests.</p>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Your connections</h2>
        {accepted.length ? (
          <ul className="flex flex-col gap-2">
            {accepted.map((connection) => (
              <ConnectedRow key={connection.id} connection={connection} />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">You don't have any connections yet.</p>
        )}
      </div>
    </section>
  );
}
