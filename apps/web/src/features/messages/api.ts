import { apiFetch } from "@/lib/http";

/** Mirrors MessageResponse in apps/api/app/modules/messages/schemas.py. `removed: true` means
 * the author deleted their account: `body` and `author_id` are null. */
export interface Message {
  id: string;
  conversation_id: string;
  author_id: string | null;
  body: string | null;
  client_msg_id: string | null;
  created_at: string;
  removed: boolean;
}

/** `user_id` and `display_name` are always the OTHER student in the conversation. */
export interface ConversationParticipant {
  user_id: string;
  display_name: string | null;
  online: boolean;
}

/** Mirrors ConversationResponse in apps/api/app/modules/messages/schemas.py. Only conversations
 * the student is an active member of are ever returned; a block makes one disappear. */
export interface Conversation {
  id: string;
  kind: "direct";
  other: ConversationParticipant;
  last_message: Message | null;
  updated_at: string;
}

export interface MessagesPage {
  /** Oldest to newest. */
  messages: Message[];
  /** A cursor for `before`, or null when nothing older remains. */
  before: string | null;
  /** A cursor for `after` (the newest message returned), or null for an empty page. */
  after: string | null;
}

export interface MessagesQuery {
  /** Pages older messages. Mutually exclusive with `after`. */
  before?: string;
  /** Catches up on messages newer than this cursor, e.g. after a reconnect. */
  after?: string;
  /** 1-100; the API defaults to 50. */
  limit?: number;
}

export const messagesApi = {
  /** Every conversation the student is in, newest activity first. */
  conversations: () => apiFetch<Conversation[]>("/conversations"),
  conversation: (id: string) => apiFetch<Conversation>(`/conversations/${encodeURIComponent(id)}`),
  messages: (conversationId: string, query: MessagesQuery = {}) => {
    const params = new URLSearchParams();
    if (query.before) params.set("before", query.before);
    if (query.after) params.set("after", query.after);
    if (query.limit) params.set("limit", String(query.limit));
    const search = params.toString();
    return apiFetch<MessagesPage>(`/conversations/${conversationId}/messages${search ? `?${search}` : ""}`);
  },
};
