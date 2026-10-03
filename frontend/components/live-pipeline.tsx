"use client";

import Link from "next/link";

import { EVENT_LABELS, eventTone, useEventStream } from "@/components/event-stream";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { time } from "@/lib/format";
import { cn } from "@/lib/utils";

const DOT = { ok: "bg-good", warn: "bg-warning", error: "bg-critical", info: "bg-info" } as const;
const QUIET = new Set(["PAYMENT_EVENT_RECEIVED", "NOTIFICATION_SENT"]);

/** LIVE PAYMENT PIPELINE: every event as it happens, newest first. */
export function LivePipeline({ limit = 18 }: { limit?: number }) {
  const { events, connected } = useEventStream();
  const visible = events.filter((e) => !QUIET.has(e.event)).slice(-limit).reverse();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Live payment pipeline</CardTitle>
        <span className={cn("flex items-center gap-1.5 text-[11px]", connected ? "text-good" : "text-warning")}>
          <span className={cn("size-1.5 rounded-full", connected ? "bg-good pulse-ring" : "bg-warning")} />
          {connected ? "streaming" : "reconnecting"}
        </span>
      </CardHeader>
      <CardContent>
        {visible.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted">No events yet. Click START LIVE DEMO.</p>
        ) : (
          <ol className="space-y-1.5">
            {visible.map((event, index) => {
              const tone = eventTone(event);
              return (
                <li key={event.id} className="flex items-start gap-2.5 text-xs">
                  <span className="w-[58px] shrink-0 pt-px font-mono text-[11px] text-subtle tabular">{time(event.timestamp)}</span>
                  <span className={cn("mt-1 size-2 shrink-0 rounded-full", DOT[tone], index === 0 && !event.backlog && "pulse-ring")} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2">
                      <span className="font-medium text-foreground">{EVENT_LABELS[event.event] ?? event.event}</span>
                      {event.transactionId && (
                        <Link href={`/payments/${event.transactionId}`} className="font-mono text-[11px] text-muted hover:text-primary">
                          {event.transactionId}
                        </Link>
                      )}
                      {event.incidentId && (
                        <Link href={`/incidents/${event.incidentId}`} className="text-[11px] text-info hover:underline">
                          incident →
                        </Link>
                      )}
                    </div>
                    {event.data.reason && <p className="truncate text-[11px] text-muted">{String(event.data.reason)}</p>}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
