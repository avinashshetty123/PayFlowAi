import { AlertTriangle, CheckCircle2, Circle, Clock, Loader2, MinusCircle, XCircle } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { humanize } from "@/lib/format";

const TONES: Record<string, BadgeTone> = {
  SUCCESS: "good", SETTLED: "good", RECEIVED: "good", RESOLVED: "good", COMPLETED: "good", PASSED: "good",
  ALLOW: "good", REFUNDED: "good", APPROVED: "good", LOW: "good", ANALYZED: "good", ANALYZED_FALLBACK: "good",
  PENDING: "warning", DELAYED: "warning", AWAITING_APPROVAL: "warning", PENDING_APPROVAL: "warning",
  HUMAN_APPROVAL_REQUIRED: "warning", REFUND_PENDING: "warning", REFUND_REQUESTED: "warning", MEDIUM: "warning",
  PROCESSING: "info", OPEN: "info", INVESTIGATING: "info", REMEDIATING: "info", EXECUTING: "info", CREATED: "info",
  ANALYZING: "info", QUEUED: "neutral", NOT_APPLICABLE: "neutral", NOT_FOUND: "neutral",
  ESCALATED: "serious", HIGH: "serious", CLOSED: "neutral", ACKNOWLEDGED: "info", SENT: "good",
  AUTOMATED: "good", HUMAN_APPROVED: "good", MANUAL: "good", ACCEPTED_RISK: "warning", FALSE_POSITIVE: "neutral", SELF_HEALED: "good",
  FAILED: "critical", NOT_RECEIVED: "critical", TIMEOUT: "critical", UNKNOWN: "critical", REFUND_FAILED: "critical",
  DENY: "critical", REJECTED: "critical", CRITICAL: "critical", DUPLICATE: "critical",
};

export function toneFor(status: string | null | undefined): BadgeTone {
  return (status && TONES[status]) || "neutral";
}

export function ToneIcon({ tone, className, spin }: { tone: BadgeTone; className?: string; spin?: boolean }) {
  const cls = cn("size-3.5 shrink-0", className);
  if (spin) return <Loader2 className={cn(cls, "animate-spin")} />;
  switch (tone) {
    case "good":
      return <CheckCircle2 className={cls} />;
    case "warning":
      return <Clock className={cls} />;
    case "serious":
      return <AlertTriangle className={cls} />;
    case "critical":
      return <XCircle className={cls} />;
    case "info":
      return <Circle className={cls} />;
    default:
      return <MinusCircle className={cls} />;
  }
}

const SPINNING = new Set(["INVESTIGATING", "REMEDIATING", "EXECUTING", "ANALYZING"]);

/** Status is always icon + label, never color alone. */
export function StatusBadge({ status, label, className }: { status: string | null | undefined; label?: string; className?: string }) {
  if (!status) return <span className="text-subtle">—</span>;
  const tone = toneFor(status);
  return (
    <Badge tone={tone} className={className}>
      <ToneIcon tone={tone} spin={SPINNING.has(status)} />
      {label ?? status.replaceAll("_", " ")}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const tone: BadgeTone = severity === "CRITICAL" ? "critical" : severity === "HIGH" ? "serious" : severity === "MEDIUM" ? "warning" : "neutral";
  return (
    <Badge tone={tone}>
      <span className={cn("inline-block size-1.5 rounded-full", {
        "bg-critical": tone === "critical",
        "bg-serious": tone === "serious",
        "bg-warning": tone === "warning",
        "bg-muted": tone === "neutral",
      })} />
      {humanize(severity)}
    </Badge>
  );
}

export function TypeLabel({ type }: { type: string }) {
  return <span className="font-mono text-[12px] text-foreground">{type}</span>;
}
