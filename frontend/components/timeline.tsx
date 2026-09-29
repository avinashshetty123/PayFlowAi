import { cn } from "@/lib/utils";
import { time } from "@/lib/format";
import type { TimelineItem } from "@/types/api";

const DOT = {
  ok: "bg-good",
  warn: "bg-warning",
  error: "bg-critical",
  info: "bg-info",
} as const;

const KIND_LABEL: Record<string, string> = {
  payment: "SYSTEM",
  detection: "DETECT",
  ai: "AI",
  policy: "POLICY",
  action: "ACTION",
  verification: "VERIFY",
  incident: "INCIDENT",
  human: "HUMAN",
};

export function Timeline({ items, live }: { items: TimelineItem[]; live?: boolean }) {
  return (
    <ol className="relative">
      {items.map((item, index) => {
        const last = index === items.length - 1;
        return (
          <li key={`${item.timestamp}-${index}`} className="relative flex gap-3 pb-3.5 last:pb-0">
            {!last && <span className="absolute left-[75px] top-3 h-full w-px bg-border" aria-hidden />}
            <time className="w-[62px] shrink-0 pt-px text-right font-mono text-[11px] text-subtle tabular">
              {time(item.timestamp)}
            </time>
            <span
              className={cn(
                "relative z-10 mt-1 size-2.5 shrink-0 rounded-full ring-4 ring-panel",
                DOT[item.tone],
                live && last && "pulse-ring",
              )}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[13px] font-medium text-foreground">{item.label}</span>
                <span className="rounded border border-border px-1 text-[9px] font-semibold tracking-wider text-subtle">
                  {KIND_LABEL[item.kind] ?? item.kind.toUpperCase()}
                </span>
              </div>
              {item.detail && <p className="mt-0.5 text-xs leading-relaxed text-muted">{item.detail}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
