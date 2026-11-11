import { ApiError } from "@/lib/http";
import type { RoomErrorCode, RoomRemovedEvent } from "@/lib/socket";

// What students see for each `room.join` (and `rtc.signal`) ack error code (apps/api's realtime
// rooms module), listed exhaustively so a new code can't silently fall through to the fallback.
const MESSAGES: Record<RoomErrorCode, string> = {
  room_full: "This call is full.",
  not_found: "This call isn't available.",
  room_ended: "This call has ended.",
  rate_limited: "Too many attempts. Wait a moment and try again.",
  timeout: "Couldn't reach the server in time.",
  not_in_room: "You're not in this call anymore.",
  peer_not_found: "That student isn't in this call anymore.",
  validation_error: "Something went wrong. Please try again.",
  unauthorized: "Your session isn't valid anymore.",
  internal_error: "Something went wrong. Please try again.",
};

const UNKNOWN_CODE_FALLBACK = "Something went wrong. Please try again.";

/** Never returns `undefined`, even for a runtime code the client doesn't know about yet (acks
 * aren't validated against the TS union at the wire), so an unrecognised code still renders text
 * instead of silently dropping the error. */
export function roomAckErrorMessage(code: RoomErrorCode): string {
  return MESSAGES[code] ?? UNKNOWN_CODE_FALLBACK;
}

/** Copy for `room.removed`, exhaustive over its two reasons. */
const REMOVED_MESSAGES: Record<RoomRemovedEvent["reason"], string> = {
  access_lost: "You no longer have access to this call.",
  joined_elsewhere: "You joined this call in another tab or window.",
};

export function roomRemovedMessage(reason: RoomRemovedEvent["reason"]): string {
  return REMOVED_MESSAGES[reason] ?? UNKNOWN_CODE_FALLBACK;
}

/** Shown when the call socket fails to connect at all (a transport-level `connect_error`, as
 * opposed to a `room.join` ack timeout, which already has its own copy above). */
export const CONNECT_ERROR_MESSAGE = "Couldn't connect to the call. Check your connection and try again.";

const FALLBACK = "Couldn't reach the server right now. Check your connection and try again.";

/** For the room's own HTTP calls (load, ICE servers): kept separate from
 * `features/auth/errors.ts`'s `errorMessage`, whose "not_found" copy ("That account couldn't be
 * found.") is about accounts, not calls. */
export function roomHttpErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "not_found") return "This call isn't available.";
    if (error.code === "room_ended") return "This call has ended.";
    if (error.code === "rate_limited") return "Too many attempts. Wait a moment and try again.";
  }
  return FALLBACK;
}
