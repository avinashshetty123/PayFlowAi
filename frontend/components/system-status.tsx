"use client";

import { useEventStream } from "@/components/event-stream";
import { ToneIcon } from "@/components/status";
import type { BadgeTone } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const TONE_TEXT: Record<BadgeTone, string> = {
  good: "text-good", warning: "text-warning", serious: "text-serious", critical: "text-[#f87171]", info: "text-[#8fb0f5]",
  neutral: "text-muted",
};

function Row({ label, value, tone, detail }: { label: string; value: string; tone: BadgeTone; detail?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-2 last:border-0">
      <div>
        <div className="text-xs text-foreground">{label}</div>
        {detail && <div className="text-[10px] text-subtle">{detail}</div>}
      </div>
      <span className={cn("flex items-center gap-1.5 font-mono text-[11px] font-semibold", TONE_TEXT[tone])}>
        <ToneIcon tone={tone} /> {value}
      </span>
    </div>
  );
}

export function SystemStatus() {
  const { data, error } = useApi(api.health, [], 20000);
  const { connected, transport } = useEventStream();
  if (error) {
    return (
      <Card>
        <CardHeader><CardTitle>System status</CardTitle></CardHeader>
        <CardContent><Row label="PayFlow API" value="OFFLINE" tone="critical" detail={error} /></CardContent>
      </Card>
    );
  }
  const paypal = data?.paypal ?? "…";
  const webhook = data?.webhook ?? "…";
  return (
    <Card>
      <CardHeader>
        <CardTitle>System status</CardTitle>
        <span className="text-[10px] uppercase tracking-wider text-subtle">{data?.pipeline_mode ?? ""} pipeline</span>
      </CardHeader>
      <CardContent>
        <Row label="PayPal Sandbox" value={paypal} tone={paypal === "CONNECTED" ? "good" : paypal === "NOT_CONFIGURED" ? "warning" : "critical"}
          detail="api-m.sandbox.paypal.com · OAuth2" />
        <Row label="PostgreSQL" value={data?.database ?? "…"} tone={data?.database === "HEALTHY" ? "good" : "critical"}
          detail={data ? `RAG: ${data.rag}` : undefined} />
        <Row label="Redis" value={data?.redis ?? "…"} tone={data?.redis === "HEALTHY" ? "good" : "warning"}
          detail={data?.celery_workers ? "Celery workers online" : "in-process worker fallback"} />
        <Row label="Groq AI" value={data?.groq === "CONFIGURED" ? "CONNECTED" : "NOT CONFIGURED"} tone={data?.groq === "CONFIGURED" ? "good" : "warning"}
          detail={data?.groq === "CONFIGURED" ? `${data?.ai ?? "llama-3.3-70b-versatile"} · JSON mode` : "Using deterministic fallback investigator"} />
        <Row label="Webhook" value={webhook}
          tone={webhook === "VERIFIED" ? "good" : webhook === "VERIFICATION_FAILED" ? "critical" : webhook === "NOT_CONFIGURED" ? "neutral" : "info"}
          detail={data?.webhook_url ?? "set PAYPAL_WEBHOOK_ID + public tunnel"} />
        <Row label="Event stream" value={connected ? "CONNECTED" : "RECONNECTING"} tone={connected ? "good" : "warning"}
          detail={transport ? `SSE via ${transport}` : undefined} />
      </CardContent>
    </Card>
  );
}
