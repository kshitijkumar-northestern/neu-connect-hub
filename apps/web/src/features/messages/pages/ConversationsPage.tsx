import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useSocket } from "@/features/messages/SocketProvider";
import { messagesApi, type Conversation, type Message } from "@/features/messages/api";
import { PresenceIndicator } from "@/features/messages/components/PresenceIndicator";
import { relativeTime } from "@/features/messages/time";

type LoadState = "loading" | "ready" | "error";

function previewFor(lastMessage: Conversation["last_message"]): string {
  if (!lastMessage) return "No messages yet";
  if (lastMessage.removed) return "Message removed";
  return lastMessage.body ?? "";
}

function byNewestFirst(a: Conversation, b: Conversation): number {
  return b.updated_at.localeCompare(a.updated_at);
}

function ConversationRow({ conversation }: { conversation: Conversation }) {
  const name = conversation.other.display_name ?? "A student";
  return (
    <li className="rounded-2xl border px-4 py-3">
      <Link to={`/messages/${conversation.id}`} className="flex flex-col gap-1">
        <span className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 font-medium">
            {name}
            <PresenceIndicator online={conversation.other.online} />
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">{relativeTime(conversation.updated_at)}</span>
        </span>
        <span className="truncate text-sm text-muted-foreground">{previewFor(conversation.last_message)}</span>
      </Link>
    </li>
  );
}

/** Every conversation the student is in, newest activity first. Updates live as messages arrive
 * and as people come online or go offline. */
export function ConversationsPage() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const { onMessage, onPresence } = useSocket();

  useEffect(() => {
    let cancelled = false;
    messagesApi
      .conversations()
      .then((result) => {
        if (cancelled) return;
        setConversations(result);
        setLoad("ready");
      })
      .catch(() => {
        if (!cancelled) setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () =>
      onMessage((message: Message) => {
        setConversations((current) => {
          if (!current.some((conversation) => conversation.id === message.conversation_id)) return current;
          const next = current.map((conversation) =>
            conversation.id === message.conversation_id
              ? { ...conversation, last_message: message, updated_at: message.created_at }
              : conversation,
          );
          return [...next].sort(byNewestFirst);
        });
      }),
    [onMessage],
  );

  useEffect(
    () =>
      onPresence((event) => {
        setConversations((current) =>
          current.map((conversation) =>
            conversation.other.user_id === event.user_id
              ? { ...conversation, other: { ...conversation.other, online: event.online } }
              : conversation,
          ),
        );
      }),
    [onPresence],
  );

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "error") {
    return <p role="alert">Can't load your messages right now. Check your connection and try again.</p>;
  }

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Messages</h1>
      {conversations.length ? (
        <ul className="flex flex-col gap-2">
          {conversations.map((conversation) => (
            <ConversationRow key={conversation.id} conversation={conversation} />
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">
          You can message a student once you're connected.{" "}
          <Link className="underline underline-offset-4" to="/connections">
            Go to connections
          </Link>
        </p>
      )}
    </section>
  );
}
