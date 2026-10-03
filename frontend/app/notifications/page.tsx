"use client";

import { BellRing, CheckCheck, CircleCheck, CircleDashed, Send } from "lucide-react";
import { useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { useLiveRefresh } from "@/components/event-stream";
import { NotificationRow, SEVERITY_STYLE } from "@/components/notification-center";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUSES = [
  { key: "", label: "All" },
  { key: "OPEN", label: "Open" },
  { key: "ACKNOWLEDGED", label: "Acknowledged" },
  { key: "RESOLVED", label: "Resolved" },
];

export default function NotificationsPage() {
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const { data, error, refresh } = useApi(() => api.notifications({ status, severity, limit: 100 }), [status, severity], 30000);
  const { data: channels } = useApi(api.notificationChannels, [], undefined);
  useLiveRefresh(refresh, (e) => e.event.startsWith("NOTIFICATION") || e.event.startsWith("INCIDENT"));
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  async function ack(id: string) {
    await api.ackNotification(id, "ops.console");
    await refresh();
  }
  async function sendTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.testAlert("ops.console");
      setTestResult(r.message ?? r.results.map((x) => `${x.channel}: ${x.status}${x.error ? ` (${x.error})` : ""}`).join(" · "));
    } catch (err) {
      setTestResult(err instanceof Error ? err.message : String(err));
    } finally {
      setTesting(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Alert centre"
        description="Every incident alert with severity routing, delivery receipts per channel, acknowledgement and automatic re-escalation of unacknowledged P1/P2 alerts."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={sendTest} disabled={testing}>
              <Send /> {testing ? "Sending…" : "Send test alert"}
            </Button>
            <Button size="sm" onClick={async () => { await api.ackAllNotifications("ops.console"); await refresh(); }}>
              <CheckCheck /> Acknowledge all
            </Button>
          </>
        }
      />
      {error && <ErrorBanner message={error} />}
      {testResult && <p className="mb-4 rounded-lg border border-border bg-panel px-3 py-2 text-xs text-muted">{testResult}</p>}

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        {(["P1", "P2", "P3", "P4"] as const).map((sev) => {
          const count = data?.items.filter((i) => i.severity === sev && i.status === "OPEN").length ?? 0;
          return (
            <button key={sev} onClick={() => setSeverity(severity === sev ? "" : sev)}
              className={cn("rounded-xl border bg-panel px-4 py-3 text-left card-shadow transition-colors",
                severity === sev ? "border-brand" : "border-border hover:border-border-strong")}>
              <div className="flex items-center justify-between">
                <span className={cn("rounded px-1.5 py-px text-[10px] font-bold", SEVERITY_STYLE[sev].chip)}>{sev}</span>
                <span className="text-[11px] text-subtle">open</span>
              </div>
              <div className="mt-2 text-2xl font-bold text-foreground">{count}</div>
              <div className="text-[11px] text-muted">{SEVERITY_STYLE[sev].label}</div>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2 overflow-hidden">
          <CardHeader>
            <CardTitle>Alerts</CardTitle>
            <div className="flex gap-1">
              {STATUSES.map((s) => (
                <button key={s.key} onClick={() => setStatus(s.key)}
                  className={cn("rounded-full px-2.5 py-1 text-[11px] font-medium",
                    status === s.key ? "bg-navy text-white" : "text-muted hover:bg-panel-2")}>
                  {s.label}
                </button>
              ))}
            </div>
          </CardHeader>
          {!data ? (
            <Skeleton className="m-5 h-64" />
          ) : data.items.length === 0 ? (
            <p className="px-5 pb-10 pt-4 text-center text-sm text-muted">No alerts match this filter.</p>
          ) : (
            <div className="border-t border-border">{data.items.map((item) => <NotificationRow key={item.id} item={item} onAck={ack} />)}</div>
          )}
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-1.5"><BellRing className="size-3.5" /> Delivery channels</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5">
              {channels?.channels.map((c) => (
                <div key={c.channel} className="rounded-lg border border-border px-3 py-2">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
                      {c.configured ? <CircleCheck className="size-3.5 text-good" /> : <CircleDashed className="size-3.5 text-subtle" />}
                      {c.label}
                    </span>
                    <span className="text-[10px] text-subtle">{c.severities.join(" · ") || "—"}</span>
                  </div>
                  <div className="mt-0.5 font-mono text-[10px] text-muted">
                    {c.configured ? `→ ${c.target}` : `set ${c.env}`}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Routing & escalation policy</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-xs text-muted">
              {channels && Object.entries(channels.routes).map(([sev, list]) => (
                <div key={sev} className="flex items-start gap-2">
                  <span className={cn("rounded px-1.5 py-px text-[10px] font-bold", SEVERITY_STYLE[sev].chip)}>{sev}</span>
                  <span>{list.length ? list.join(", ") : "in-app only"}</span>
                </div>
              ))}
              <p className="border-t border-border pt-2">
                Unacknowledged P1/P2 alerts are re-sent every {channels?.escalation_minutes ?? 5} min (max 3 times).
                Alerts are deduplicated per incident and auto-resolve when the incident resolves. Delivery happens from a
                transactional outbox, so a channel outage never blocks payment processing.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
