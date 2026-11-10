import type { ChatSendErrorCode } from "@/lib/socket";

// Separate from src/features/auth/errors.ts: these come from a `chat.send` ack, not an ApiError,
// and a couple of codes (e.g. "not_found") mean something different here than they do for an
// account or a profile, so sharing that Map would show a misleading message.
const MESSAGES = new Map<ChatSendErrorCode, string>([
  ["not_found", "This conversation isn't available anymore."],
  ["validation_error", "That message couldn't be sent."],
  ["rate_limited", "Too many messages. Wait a moment and try again."],
  ["unauthorized", "Your session isn't valid anymore."],
  ["timeout", "Couldn't reach the server in time."],
]);

const FALLBACK = "Something went wrong. Please try again.";

export function chatSendErrorMessage(code: ChatSendErrorCode): string {
  return MESSAGES.get(code) ?? FALLBACK;
}
