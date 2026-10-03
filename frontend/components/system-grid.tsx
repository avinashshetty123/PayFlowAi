import { ArrowRight, Banknote, BookOpen, Landmark, Store, Webhook } from "lucide-react";

import { ToneIcon, toneFor } from "@/components/status";
import { cn } from "@/lib/utils";
import type { Snapshot } from "@/types/api";

export const SYSTEMS: { key: keyof Snapshot; label: string; icon: typeof Banknote }[] = [
  { key: "gateway", label: "Gateway", icon: Banknote },
  { key: "bank", label: "Bank", icon: Landmark },
  { key: "merchant", label: "Merchant", icon: Store },
  { key: "ledger", label: "Ledger", icon: BookOpen },
  { key: "webhook", label: "Webhook", icon: Webhook },
];

const TONE_TEXT = {
  good: "text-good",
  warning: "text-warning",
  serious: "text-serious",
  critical: "text-critical",
  info: "text-info",
  neutral: "text-muted",
} as const;

/** Five-system comparison. When `before` is given, changed systems show before → after. */
export function SystemGrid({ current, before, compact }: { current: Snapshot; before?: Snapshot | null; compact?: boolean }) {
  return (
    <div className={cn("grid gap-2", compact ? "grid-cols-5" : "grid-cols-2 sm:grid-cols-5")}>
      {SYSTEMS.map(({ key, label, icon: Icon }) => {
        const value = current[key] ?? "—";
        const previous = before?.[key];
        const changed = previous && previous !== value;
        const tone = toneFor(value);
        return (
          <div
            key={key}
            className={cn(
              "rounded-md border bg-background/60 px-3 py-2.5",
              changed ? "border-good/40" : tone === "critical" ? "border-critical/40" : "border-border",
            )}
          >
            <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-subtle">
              <Icon className="size-3.5" />
              {label}
            </div>
            {changed && (
              <div className="mt-1.5 flex items-center gap-1 text-[11px] text-muted line-through decoration-muted/60">
                {previous}
              </div>
            )}
            <div className={cn("mt-1 flex items-center gap-1.5 font-mono text-[13px] font-semibold", TONE_TEXT[tone])}>
              <ToneIcon tone={tone} />
              {value}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Compact inline "SYSTEM: A → B" change list. */
export function ChangeList({ changes }: { changes: { system: string; from: string; to: string }[] }) {
  if (!changes.length) return <p className="text-xs text-muted">No system state changes.</p>;
  return (
    <ul className="space-y-1.5">
      {changes.map((c) => (
        <li key={c.system} className="flex items-center gap-2 font-mono text-xs">
          <span className="w-20 uppercase text-subtle">{c.system}</span>
          <span className="text-critical">{c.from}</span>
          <ArrowRight className="size-3 text-subtle" />
          <span className="text-good">{c.to}</span>
        </li>
      ))}
    </ul>
  );
}
