"use client";

import { Bell, BellRing, Check, CheckCheck, MessageCircle, Send, Smartphone, Slack, Webhook } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useEventStream, useLiveRefresh } from "@/components/event-stream";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { NotificationItem } from "@/types/api";

export const SEVERITY_STYLE: Record<string, { bar: string; chip: string; label: string }> = {
  P1: { bar: "bg-critical", chip: "bg-critical text-white", label: "P1 · Critical" },
  P2: { bar: "bg-serious", chip: "bg-serious text-white", label: "P2 · High" },
  P3: { bar: "bg-info", chip: "bg-info/10 text-info", label: "P3 · Medium" },
  P4: { bar: "bg-border-strong", chip: "bg-panel-2 text-muted", label: "P4 · Info" },
};

const CHANNEL_ICON: Record<string, typeof Bell> = {
  whatsapp: MessageCircle, telegram: Send, ntfy: Smartphone, slack: Slack, webhook: Webhook,
};

export function DeliveryChips({ item }: { item: NotificationItem }) {
  if (!item.deliveries.length) return <span className="text-[10px] text-subtle">in-app only</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {item.deliveries.map((d, i) => {
        const Icon = CHANNEL_ICON[d.channel] ?? Bell;
        return (
          <span key={`${d.channel}-${i}`} title={`${d.channel} → ${d.target}: ${d.status}${d.error ? ` (${d.error})` : ""}`}
            className={cn("inline-flex items-center gap-0.5 rounded px-1 py-px text-[10px] font-medium",
              d.status === "SENT" ? "bg-good/10 text-good" : d.status === "FAILED" ? "bg-critical/10 text-critical" : "bg-panel-2 text-muted")}>
            <Icon className="size-2.5" /> {d.channel}{d.escalation_level ? ` #${d.escalation_level}` : ""}
          </span>
        );
      })}
    </span>
  );
}

export function NotificationRow({ item, onAck, compact }: { item: NotificationItem; onAck: (id: string) => void; compact?: boolean }) {
  const style = SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.P4;
  return (
    <div className={cn("relative flex gap-3 border-b border-border px-4 py-3 last:border-0",
      item.status === "OPEN" ? "bg-panel" : "bg-panel-2/40")}>
      <span className={cn("absolute inset-y-2 left-0 w-1 rounded-r", style.bar)} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={cn("rounded px-1.5 py-px text-[10px] font-bold tracking-wide", style.chip)}>{item.severity}</span>
          {item.escalation_level > 0 && (
            <span className="rounded bg-critical/10 px-1.5 py-px text-[10px] font-semibold text-critical">
              escalated ×{item.escalation_level}
            </span>
          )}
          <span className="text-[11px] text-subtle">{relative(item.created_at)}</span>
          {item.status !== "OPEN" && (
            <span className="text-[10px] uppercase tracking-wider text-subtle">
              {item.status.toLowerCase()}{item.acknowledged_by ? ` · ${item.acknowledged_by}` : ""}
            </span>
          )}
        </div>
        <Link href={item.link ?? "/notifications"} className="mt-1 block text-[13px] font-semibold text-foreground hover:text-info">
          {item.title}
        </Link>
        <p className={cn("mt-0.5 whitespace-pre-line text-xs text-muted", compact && "line-clamp-2")}>{item.body}</p>
        <div className="mt-1.5"><DeliveryChips item={item} /></div>
      </div>
      {item.status === "OPEN" && (
        <Button variant="outline" size="sm" className="h-7 shrink-0 px-2 text-[11px]" onClick={() => onAck(item.id)}>
          <Check className="size-3" /> Ack
        </Button>
      )}
    </div>
  );
}

function useDesktopAlerts() {
  const { subscribe } = useEventStream();
  const router = useRouter();
  useEffect(() => subscribe((event) => {
    if (event.event !== "NOTIFICATION_CREATED" && event.event !== "NOTIFICATION_ESCALATED") return;
    const severity = String(event.data.severity ?? "");
    if (!["P1", "P2"].includes(severity)) return;
    if (typeof window === "undefined" || !("Notification" in window) || Notification.permission !== "granted") return;
    const note = new Notification(`PayFlow ${severity}: ${String(event.data.title ?? event.event)}`, {
      body: String(event.data.reason ?? ""), tag: `${event.incidentId}-${event.event}`,
    });
    note.onclick = () => {
      window.focus();
      if (event.incidentId) router.push(`/incidents/${event.incidentId}`);
    };
  }), [subscribe, router]);
}

export function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const [, setPermissionTick] = useState(0);
  const panel = useRef<HTMLDivElement>(null);
  const { data, refresh } = useApi(() => api.notifications({ limit: 15 }), [], 30000);
  useLiveRefresh(refresh, (e) => e.event.startsWith("NOTIFICATION") || e.event.startsWith("INCIDENT"));
  useDesktopAlerts();

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  async function ack(id: string) {
    await api.ackNotification(id, "ops.console");
    await refresh();
  }
  async function ackAll() {
    await api.ackAllNotifications("ops.console");
    await refresh();
  }
  async function enableDesktop() {
    if (!("Notification" in window)) return;
    await Notification.requestPermission();
    setPermissionTick((t) => t + 1);
  }
  // Read lazily: the panel only renders after a click, so this never runs during SSR.
  const permission = () => (typeof window !== "undefined" && "Notification" in window ? Notification.permission : "denied");

  const unread = data?.unread ?? 0;
  const urgent = data?.urgent ?? 0;
  return (
    <div className="relative" ref={panel}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn("relative flex size-9 items-center justify-center rounded-full border border-border bg-panel text-muted transition-colors hover:text-foreground",
          urgent > 0 && "border-critical/40 text-critical")}
        aria-label={`Notifications: ${unread} unread`}
      >
        {urgent > 0 ? <BellRing className="size-4 animate-pulse" /> : <Bell className="size-4" />}
        {unread > 0 && (
          <span className={cn("absolute -right-1 -top-1 min-w-[18px] rounded-full px-1 text-center text-[10px] font-bold leading-[18px] text-white",
            urgent > 0 ? "bg-critical" : "bg-info")}>
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="slide-in absolute right-0 z-50 mt-2 w-[420px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-border bg-panel shadow-xl">
          <div className="flex items-center justify-between border-b border-border bg-navy px-4 py-3 text-white">
            <div>
              <div className="text-sm font-semibold">Alerts</div>
              <div className="text-[11px] text-white/70">{unread} open · {urgent} urgent (P1/P2)</div>
            </div>
            <button onClick={ackAll} disabled={!unread}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-white/90 hover:bg-white/10 disabled:opacity-40">
              <CheckCheck className="size-3.5" /> Ack all
            </button>
          </div>
          {permission() === "default" && (
            <button onClick={enableDesktop} className="flex w-full items-center gap-2 border-b border-border bg-sky/10 px-4 py-2 text-left text-[11px] text-info hover:bg-sky/15">
              <BellRing className="size-3.5" /> Enable desktop alerts for P1/P2 incidents
            </button>
          )}
          <div className="max-h-[60vh] overflow-y-auto">
            {!data?.items.length ? (
              <p className="px-4 py-10 text-center text-xs text-muted">All quiet. No alerts yet.</p>
            ) : (
              data.items.map((item) => <NotificationRow key={item.id} item={item} onAck={ack} compact />)
            )}
          </div>
          <Link href="/notifications" onClick={() => setOpen(false)}
            className="block border-t border-border px-4 py-2.5 text-center text-xs font-medium text-info hover:bg-panel-2">
            Open notification centre · channels &amp; delivery receipts →
          </Link>
        </div>
      )}
    </div>
  );
}
