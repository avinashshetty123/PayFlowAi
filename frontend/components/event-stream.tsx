"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { API_URL } from "@/lib/api";
import type { LiveEvent } from "@/types/api";

type Listener = (event: LiveEvent) => void;

type StreamState = {
  connected: boolean;
  transport: string | null;
  events: LiveEvent[]; // newest last
  subscribe: (listener: Listener) => () => void;
};

const MAX_EVENTS = 150;
const StreamContext = createContext<StreamState>({
  connected: false,
  transport: null,
  events: [],
  subscribe: () => () => undefined,
});

/** One shared Server-Sent Events connection to GET /api/events/stream (auto-reconnects). */
export function EventStreamProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [transport, setTransport] = useState<string | null>(null);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const listeners = useRef(new Set<Listener>());
  const seen = useRef(new Set<string>());

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const connect = () => {
      source = new EventSource(`${API_URL}/api/events/stream?backlog=60`);
      source.addEventListener("hello", (msg) => {
        setConnected(true);
        try {
          setTransport(JSON.parse((msg as MessageEvent).data).transport);
        } catch {
          setTransport(null);
        }
      });
      source.addEventListener("payflow", (msg) => {
        const event = JSON.parse((msg as MessageEvent).data) as LiveEvent;
        if (seen.current.has(event.id)) return;
        seen.current.add(event.id);
        setEvents((prev) => [...prev, event].slice(-MAX_EVENTS));
        if (!event.backlog) listeners.current.forEach((listener) => listener(event));
      });
      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) retry = setTimeout(connect, 2500);
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      source?.close();
    };
  }, []);

  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  return (
    <StreamContext.Provider value={{ connected, transport, events, subscribe }}>{children}</StreamContext.Provider>
  );
}

export function useEventStream() {
  return useContext(StreamContext);
}

/** Refresh page data when a matching live event arrives (debounced). */
export function useLiveRefresh(refresh: () => unknown, match: (event: LiveEvent) => boolean = () => true) {
  const { subscribe } = useEventStream();
  const refreshRef = useRef(refresh);
  const matchRef = useRef(match);
  useEffect(() => {
    refreshRef.current = refresh;
    matchRef.current = match;
  });
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = subscribe((event) => {
      if (!matchRef.current(event)) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => refreshRef.current(), 150);
    });
    return () => {
      unsubscribe();
      if (timer) clearTimeout(timer);
    };
  }, [subscribe]);
}

export const EVENT_LABELS: Record<string, string> = {
  PAYMENT_CREATED: "Payment created",
  PAYMENT_APPROVAL_STARTED: "PayPal order created · awaiting buyer",
  PAYMENT_APPROVED: "Buyer approved in PayPal",
  PAYMENT_CAPTURE_STARTED: "Capture requested",
  PAYMENT_CAPTURE_COMPLETED: "PayPal capture completed",
  PAYMENT_CAPTURE_FAILED: "PayPal capture failed",
  PROVIDER_CALL_FAILED: "PayPal call failed",
  WEBHOOK_RECEIVED: "Webhook received",
  WEBHOOK_VERIFIED: "Webhook verified",
  WEBHOOK_REJECTED: "Webhook rejected",
  WEBHOOK_DUPLICATE: "Duplicate webhook ignored",
  WEBHOOK_DELAYED: "Webhook delayed (injected)",
  WEBHOOK_DROPPED: "Webhook dropped (injected)",
  DOWNSTREAM_UPDATED: "Merchant + ledger updated",
  FAILURE_INJECTED: "Demo failure injected",
  RECONCILIATION_STARTED: "Reconciliation started",
  RECONCILIATION_COMPLETED: "Reconciliation completed",
  MISMATCH_DETECTED: "Mismatch detected",
  INCIDENT_CREATED: "Incident created",
  INVESTIGATION_STARTED: "AI investigation started",
  HISTORICAL_MATCH_FOUND: "Historical match found",
  INVESTIGATION_COMPLETED: "Root cause identified",
  POLICY_EVALUATED: "Policy decision",
  ACTION_CREATED: "Action created",
  ACTION_AWAITING_APPROVAL: "Awaiting human approval",
  ACTION_APPROVED: "Action approved",
  ACTION_REJECTED: "Action rejected",
  ACTION_EXECUTED: "Action executed",
  ACTION_FAILED: "Action failed",
  ACTION_DEDUPLICATED: "Duplicate action ignored",
  VERIFICATION_STARTED: "Verification started",
  VERIFICATION_RETRY: "Verification retry",
  VERIFICATION_COMPLETED: "Verification completed",
  INCIDENT_RESOLVED: "Incident resolved",
  INCIDENT_ESCALATED: "Incident escalated",
  REFUND_REQUESTED: "Refund requested",
  PAYMENT_EVENT_RECEIVED: "Payment event",
  NOTIFICATION_SENT: "Ops notified",
};

export function eventTone(event: LiveEvent): "ok" | "warn" | "error" | "info" {
  const name = event.event;
  if (name === "VERIFICATION_COMPLETED") return event.data.auditEvent === "VERIFICATION_PASSED" ? "ok" : "error";
  if (["PAYMENT_CAPTURE_COMPLETED", "WEBHOOK_VERIFIED", "INCIDENT_RESOLVED", "ACTION_EXECUTED", "PAYMENT_APPROVED",
    "INVESTIGATION_COMPLETED", "ACTION_APPROVED"].includes(name)) return "ok";
  if (["PAYMENT_CAPTURE_FAILED", "WEBHOOK_REJECTED", "MISMATCH_DETECTED", "ACTION_FAILED", "WEBHOOK_DROPPED",
    "PROVIDER_CALL_FAILED", "ACTION_REJECTED"].includes(name)) return "error";
  if (["FAILURE_INJECTED", "WEBHOOK_DUPLICATE", "WEBHOOK_DELAYED", "INCIDENT_CREATED", "INCIDENT_ESCALATED",
    "ACTION_AWAITING_APPROVAL", "VERIFICATION_RETRY", "REFUND_REQUESTED"].includes(name)) return "warn";
  return "info";
}
