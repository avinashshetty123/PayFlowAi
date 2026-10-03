import { cn } from "@/lib/utils";
import type { RiskBreakdown } from "@/types/api";

const BAND_TONE: Record<string, string> = {
  LOW: "bg-good", MEDIUM: "bg-warning", HIGH: "bg-serious", CRITICAL: "bg-critical",
};
const BAND_TEXT: Record<string, string> = {
  LOW: "text-good", MEDIUM: "text-warning", HIGH: "text-serious", CRITICAL: "text-critical",
};

/** Explainable 0–100 risk score: every point attributed to a named factor. */
export function RiskMeter({ risk }: { risk: RiskBreakdown }) {
  const max = Math.max(...risk.factors.map((f) => f.points), 1);
  return (
    <div>
      <div className="flex items-end justify-between">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-subtle">Risk score</div>
          <div className={cn("text-2xl font-bold", BAND_TEXT[risk.band])}>
            {risk.score}<span className="text-sm font-medium text-subtle"> / 100 · {risk.band}</span>
          </div>
        </div>
        <span className="text-[10px] text-subtle">human required ≥ {risk.threshold}</span>
      </div>
      <div className="relative mt-1.5 h-2 rounded-full bg-panel-2">
        <div className={cn("h-2 rounded-full", BAND_TONE[risk.band])} style={{ width: `${risk.score}%` }} />
        <div className="absolute -top-0.5 h-3 w-px bg-foreground/60" style={{ left: `${risk.threshold}%` }} aria-hidden />
      </div>
      <ul className="mt-3 space-y-1.5">
        {risk.factors.map((f) => (
          <li key={f.name} className="text-xs">
            <div className="flex justify-between">
              <span className="text-foreground">{f.name}</span>
              <span className="font-mono text-muted">+{f.points}</span>
            </div>
            <div className="mt-0.5 h-1 rounded-full bg-panel-2">
              <div className="h-1 rounded-full bg-navy/70" style={{ width: `${(f.points / max) * 100}%` }} />
            </div>
            <div className="mt-0.5 text-[10px] text-subtle">{f.detail}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
