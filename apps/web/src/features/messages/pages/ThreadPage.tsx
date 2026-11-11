import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useParams } from "react-router";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/AuthProvider";
import { useSocket } from "@/features/messages/SocketProvider";
import { PresenceIndicator } from "@/features/messages/components/PresenceIndicator";
import { messagesApi, type Conversation, type Message } from "@/features/messages/api";
import { chatSendErrorMessage } from "@/features/messages/errors";
import { TextareaField } from "@/features/profiles/components/TextareaField";
import { clockTime } from "@/features/messages/time";
import { ApiError } from "@/lib/http";
import type { ChatSendErrorCode } from "@/lib/socket";

type LoadState = "loading" | "ready" | "not-found" | "error";

type PendingState = { status: "sending" } | { status: "failed"; code: ChatSendErrorCode };

interface ViewMessage extends Message {
  pending?: PendingState;
}

const MAX_BODY_LENGTH = 4000;
const COUNTER_THRESHOLD = 3800;

function keyOf(message: Message): string {
  return message.client_msg_id ?? message.id;
}

/** Inserts or replaces a message by `client_msg_id` (falling back to `id`), then re-sorts by
 * `created_at` so an optimistic send and its eventual ack (or a reconnect catch-up) never
 * duplicate a row, however the two arrive. */
function upsertMessage(list: ViewMessage[], incoming: ViewMessage): ViewMessage[] {
  const key = keyOf(incoming);
  const index = list.findIndex((item) => keyOf(item) === key || item.id === incoming.id);
  const next = index === -1 ? [...list, incoming] : list.map((item, i) => (i === index ? incoming : item));
  return next.sort((a, b) => a.created_at.localeCompare(b.created_at));
}

function upsertMessages(list: ViewMessage[], incoming: Message[]): ViewMessage[] {
  return incoming.reduce<ViewMessage[]>((acc, message) => upsertMessage(acc, { ...message }), list);
}

function authorLabel(message: Message, ownUserId: string | undefined, fallbackName: string): string {
  if (ownUserId !== undefined && message.author_id === ownUserId) return "You";
  return message.author_name ?? fallbackName;
}

/** Keyed by the route param so navigating straight from one thread to another remounts the inner
 * component instead of needing to reset its load state imperatively. */
export function ThreadPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  return <Thread key={conversationId ?? "none"} conversationId={conversationId} />;
}

function Thread({ conversationId }: { conversationId: string | undefined }) {
  const { state, refresh } = useAuth();
  const ownUserId = state.status === "signed-in" ? state.user.id : undefined;
  const { connected, send, onMessage, onPresence, onReconnect } = useSocket();

  const [load, setLoad] = useState<LoadState>("loading");
  const [conversation, setConversation] = useState<Conversation>();
  const [messages, setMessages] = useState<ViewMessage[]>([]);
  const [beforeCursor, setBeforeCursor] = useState<string | null>(null);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [draft, setDraft] = useState("");

  const afterCursorRef = useRef<string | null>(null);
  const offlinePendingRef = useRef(new Map<string, string>());
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollAdjustRef = useRef<number | null>(null);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    messagesApi
      .messages(conversationId)
      .then((page) => {
        if (cancelled) return;
        setMessages(page.messages);
        setBeforeCursor(page.before);
        afterCursorRef.current = page.after;
        setLoad("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoad(error instanceof ApiError && error.status === 404 ? "not-found" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    // Best-effort: the header's name and presence. A failure here doesn't block the thread.
    messagesApi
      .conversation(conversationId)
      .then((result) => {
        if (!cancelled) setConversation(result);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  useEffect(
    () =>
      onPresence((event) => {
        setConversation((current) =>
          current && current.other && current.other.user_id === event.user_id
            ? { ...current, other: { ...current.other, online: event.online } }
            : current,
        );
      }),
    [onPresence],
  );

  const attemptSend = useCallback(
    async (clientMsgId: string, body: string) => {
      if (!conversationId) return;
      const ack = await send({ conversation_id: conversationId, client_msg_id: clientMsgId, body });
      if (ack.ok) {
        offlinePendingRef.current.delete(clientMsgId);
        setMessages((current) => upsertMessage(current, { ...ack.message }));
        return;
      }
      if (ack.error.code === "unauthorized") {
        void refresh();
        return;
      }
      offlinePendingRef.current.delete(clientMsgId);
      setMessages((current) =>
        current.map((message) =>
          message.client_msg_id === clientMsgId
            ? { ...message, pending: { status: "failed", code: ack.error.code } }
            : message,
        ),
      );
    },
    [conversationId, send, refresh],
  );

  useEffect(
    () =>
      onMessage((message: Message) => {
        if (message.conversation_id !== conversationId) return;
        setMessages((current) => upsertMessage(current, { ...message }));
      }),
    [onMessage, conversationId],
  );

  useEffect(
    () =>
      onReconnect(() => {
        if (!conversationId) return;
        void (async () => {
          try {
            const query = afterCursorRef.current ? { after: afterCursorRef.current } : {};
            const page = await messagesApi.messages(conversationId, query);
            setMessages((current) => upsertMessages(current, page.messages));
            if (page.after) afterCursorRef.current = page.after;
          } catch {
            // The live feed and the next successful fetch will fill any gap this missed.
          }
          for (const [clientMsgId, body] of offlinePendingRef.current) {
            void attemptSend(clientMsgId, body);
          }
        })();
      }),
    [onReconnect, conversationId, attemptSend],
  );

  useLayoutEffect(() => {
    const container = scrollRef.current;
    if (container && scrollAdjustRef.current !== null) {
      container.scrollTop = container.scrollHeight - scrollAdjustRef.current;
      scrollAdjustRef.current = null;
    }
  }, [messages]);

  async function loadEarlier() {
    if (!beforeCursor || loadingEarlier || !conversationId) return;
    setLoadingEarlier(true);
    const container = scrollRef.current;
    if (container) scrollAdjustRef.current = container.scrollHeight - container.scrollTop;
    try {
      const page = await messagesApi.messages(conversationId, { before: beforeCursor });
      setMessages((current) => upsertMessages(current, page.messages));
      setBeforeCursor(page.before);
    } catch {
      // Leave the button in place so the student can try again.
    } finally {
      setLoadingEarlier(false);
    }
  }

  function handleSend() {
    const trimmed = draft.trim();
    if (!trimmed || !conversationId) return;
    const clientMsgId = crypto.randomUUID();
    setDraft("");
    setMessages((current) =>
      upsertMessage(current, {
        id: clientMsgId,
        conversation_id: conversationId,
        author_id: ownUserId ?? null,
        author_name: null,
        body: trimmed,
        client_msg_id: clientMsgId,
        created_at: new Date().toISOString(),
        removed: false,
        pending: { status: "sending" },
      }),
    );
    if (!connected) {
      offlinePendingRef.current.set(clientMsgId, trimmed);
      return;
    }
    void attemptSend(clientMsgId, trimmed);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  }

  function retry(message: ViewMessage) {
    const clientMsgId = message.client_msg_id;
    if (!clientMsgId) return;
    setMessages((current) =>
      current.map((item) => (item.client_msg_id === clientMsgId ? { ...item, pending: { status: "sending" } } : item)),
    );
    void attemptSend(clientMsgId, message.body ?? "");
  }

  if (!conversationId) {
    return <p>This conversation isn't available.</p>;
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "not-found") {
    return <p>This conversation isn't available.</p>;
  }

  if (load === "error") {
    return <p role="alert">Can't load this conversation right now. Check your connection and try again.</p>;
  }

  const isProject = conversation?.kind === "project";
  const otherName = conversation?.other?.display_name ?? "A student";
  // Multi-author project chats fall back to "A student" per message; a direct thread falls back
  // to the one other student whose name the header already shows.
  const fallbackAuthorName = isProject ? "A student" : otherName;
  const trimmedDraft = draft.trim();

  return (
    <section className="flex h-[calc(100svh-14rem)] min-h-[28rem] flex-col gap-4">
      <header className="flex flex-col gap-1 border-b pb-3">
        <div className="flex items-center justify-between gap-3">
          {isProject ? (
            <Link
              to={`/projects/${conversation.project_id}`}
              className="font-medium underline-offset-4 hover:underline"
            >
              {conversation.name ?? "Team chat"}
            </Link>
          ) : conversation?.other ? (
            <Link to={`/people/${conversation.other.user_id}`} className="font-medium underline-offset-4 hover:underline">
              {otherName}
            </Link>
          ) : (
            <span className="font-medium">{otherName}</span>
          )}
          {!isProject && conversation?.other ? <PresenceIndicator online={conversation.other.online} /> : null}
        </div>
        {!connected ? <p role="status" className="text-xs text-muted-foreground">Reconnecting…</p> : null}
      </header>

      <div ref={scrollRef} role="log" aria-live="polite" className="flex flex-1 flex-col gap-3 overflow-y-auto rounded-2xl border p-4">
        {beforeCursor ? (
          <Button variant="outline" size="sm" className="self-center" disabled={loadingEarlier} onClick={() => void loadEarlier()}>
            {loadingEarlier ? "Loading…" : "Load earlier messages"}
          </Button>
        ) : null}

        {messages.map((message) => (
          <div key={keyOf(message)} className="flex flex-col gap-0.5">
            <p className="text-xs text-muted-foreground">
              {authorLabel(message, ownUserId, fallbackAuthorName)} · {clockTime(message.created_at)}
            </p>
            <p className={message.removed ? "text-muted-foreground italic" : "whitespace-pre-wrap break-words"}>
              {message.removed ? "Message removed" : message.body}
            </p>
            {message.pending?.status === "sending" ? (
              <span role="status" className="text-xs text-muted-foreground">
                Sending…
              </span>
            ) : null}
            {message.pending?.status === "failed" ? (
              <div className="flex items-center gap-2 text-xs">
                <span role="alert" className="text-destructive">
                  Not sent
                </span>
                <span className="sr-only">{chatSendErrorMessage(message.pending.code)}</span>
                <button type="button" className="underline underline-offset-4" onClick={() => retry(message)}>
                  Retry
                </button>
              </div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-2">
        <TextareaField
          label="Message"
          value={draft}
          maxLength={MAX_BODY_LENGTH}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          description={draft.length >= COUNTER_THRESHOLD ? `${draft.length}/${MAX_BODY_LENGTH}` : undefined}
        />
        <Button type="button" className="self-end" disabled={!trimmedDraft} onClick={handleSend}>
          Send
        </Button>
      </div>
    </section>
  );
}
