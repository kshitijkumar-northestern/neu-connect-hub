import { apiFetch } from "@/lib/http";

// Mirrors apps/api/app/modules/rooms/schemas.py.
export type RoomState = "active" | "ended";

export interface RoomParticipant {
  user_id: string;
  display_name: string | null;
}

export interface Room {
  id: string;
  conversation_id: string;
  /** The conversation's kind: a direct chat, or a project's team chat. */
  kind: "direct" | "project";
  /** The project title for a team call, the other student's name for a direct one. */
  name: string | null;
  project_id: string | null;
  room_state: RoomState;
  /** The most students the call takes at once. */
  capacity: number;
  /** Who is in the call right now, earliest joiner first. */
  participants: RoomParticipant[];
  created_at: string;
  ended_at: string | null;
}

/** An RTCIceServer: STUN entries have only `urls`; TURN entries carry short-lived credentials. */
export interface IceServer {
  urls: string[];
  username?: string;
  credential?: string;
}

export interface IceServers {
  ice_servers: IceServer[];
  /** When the TURN credentials stop working; null when no TURN server is configured. */
  expires_at: string | null;
}

function roomPath(roomId: string, rest = ""): string {
  return `/rooms/${encodeURIComponent(roomId)}${rest}`;
}

export const roomsApi = {
  /** Starts the conversation's call, or returns the one already going (members only). */
  start: (conversationId: string) =>
    apiFetch<Room>("/rooms", { method: "POST", json: { conversation_id: conversationId } }),
  get: (roomId: string) => apiFetch<Room>(roomPath(roomId)),
  /** STUN and TURN servers for this call; fetch just before joining, as TURN credentials expire. */
  iceServers: (roomId: string) => apiFetch<IceServers>(roomPath(roomId, "/ice-servers")),
};
