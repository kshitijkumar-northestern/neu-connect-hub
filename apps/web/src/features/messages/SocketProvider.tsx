import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useAuth } from "@/features/auth/AuthProvider";
import type { Message } from "@/features/messages/api";
import { createSocket, emitChatSend, type AppSocket, type ChatSendAck, type ChatSendPayload, type PresenceEvent } from "@/lib/socket";

type Unsubscribe = () => void;

interface SocketContextValue {
  /** False while the first connection attempt is in flight, or after a drop, until it recovers. */
  connected: boolean;
  send: (payload: ChatSendPayload) => Promise<ChatSendAck>;
  onMessage: (handler: (message: Message) => void) => Unsubscribe;
  onPresence: (handler: (event: PresenceEvent) => void) => Unsubscribe;
  /** Fires after every reconnect (not the first connect), so a thread can catch up with `after=`. */
  onReconnect: (handler: () => void) => Unsubscribe;
}

const SocketContext = createContext<SocketContextValue | null>(null);

/** Opens the chat socket while signed in and verified, closes it otherwise (sign-out or unmount).
 * Scoped to the /messages route tree: nothing outside it needs a live connection, and keeping the
 * real socket.io-client import out of the rest of the app keeps it out of unrelated tests too. */
export function SocketProvider({ children }: { children: ReactNode }) {
  const { state, refresh } = useAuth();
  const [socket] = useState<AppSocket>(() => createSocket());

  const [connected, setConnected] = useState(false);
  const hasConnectedOnce = useRef(false);
  const messageHandlers = useRef(new Set<(message: Message) => void>());
  const presenceHandlers = useRef(new Set<(event: PresenceEvent) => void>());
  const reconnectHandlers = useRef(new Set<() => void>());

  useEffect(() => {
    function handleConnect() {
      setConnected(true);
      if (hasConnectedOnce.current) {
        for (const handler of reconnectHandlers.current) handler();
      }
      hasConnectedOnce.current = true;
    }
    function handleDisconnect(reason: string) {
      setConnected(false);
      // The server only disconnects a socket this way when the session is gone (expired, revoked
      // or the account got suspended); anything else is a transient drop socket.io will retry.
      if (reason === "io server disconnect") void refresh();
    }
    function handleMessage(message: Message) {
      for (const handler of messageHandlers.current) handler(message);
    }
    function handlePresence(event: PresenceEvent) {
      for (const handler of presenceHandlers.current) handler(event);
    }

    socket.on("connect", handleConnect);
    socket.on("disconnect", handleDisconnect);
    socket.on("chat.message", handleMessage);
    socket.on("presence.changed", handlePresence);
    return () => {
      socket.off("connect", handleConnect);
      socket.off("disconnect", handleDisconnect);
      socket.off("chat.message", handleMessage);
      socket.off("presence.changed", handlePresence);
    };
  }, [socket, refresh]);

  const shouldConnect = state.status === "signed-in" && state.user.verified;
  useEffect(() => {
    if (shouldConnect) {
      socket.connect();
    } else {
      // socket.io-client fires "disconnect" synchronously for an already-open socket, which the
      // handler above turns into `setConnected(false)`; nothing to do here if it was never open.
      socket.disconnect();
      hasConnectedOnce.current = false;
    }
  }, [shouldConnect, socket]);

  useEffect(() => {
    return () => {
      socket.disconnect();
    };
  }, [socket]);

  const send = useCallback((payload: ChatSendPayload) => emitChatSend(socket, payload), [socket]);

  const onMessage = useCallback((handler: (message: Message) => void) => {
    messageHandlers.current.add(handler);
    return () => messageHandlers.current.delete(handler);
  }, []);

  const onPresence = useCallback((handler: (event: PresenceEvent) => void) => {
    presenceHandlers.current.add(handler);
    return () => presenceHandlers.current.delete(handler);
  }, []);

  const onReconnect = useCallback((handler: () => void) => {
    reconnectHandlers.current.add(handler);
    return () => reconnectHandlers.current.delete(handler);
  }, []);

  const value = useMemo(
    () => ({ connected, send, onMessage, onPresence, onReconnect }),
    [connected, send, onMessage, onPresence, onReconnect],
  );

  return <SocketContext.Provider value={value}>{children}</SocketContext.Provider>;
}

export function useSocket(): SocketContextValue {
  const context = useContext(SocketContext);
  if (!context) throw new Error("useSocket must be used inside <SocketProvider>");
  return context;
}
