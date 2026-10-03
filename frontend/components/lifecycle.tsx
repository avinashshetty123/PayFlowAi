import { Check } from "lucide-react";

import { cn } from "@/lib/utils";
import type { IncidentDetail } from "@/types/api";

const STAGES = ["Observe", "Investigate", "Decide", "Act", "Verify", "Reconcile", "Audit"] as const;

/** How far the OBSERVE → AUDIT lifecycle has progressed for an incident. */
export function lifecycleProgress(incident: IncidentDetail): { done: number; blocked?: string } {
  const action = incident.actions.at(-1);
  const verification = action?.result?.verification;
  if (incident.status === "RESOLVED") return { done: 7 };
  if (incident.status === "CLOSED") return { done: 7, blocked: "Closed by a human as false positive" };
  if (incident.status === "ESCALATED") {
    if (verification?.status === "FAILED") return { done: 4, blocked: "Verification failed" };
    if (action?.status === "REJECTED") return { done: 3, blocked: "Rejected by operator" };
    return { done: action?.status === "COMPLETED" ? 4 : 3, blocked: "Escalated to on-call" };
  }
  if (incident.status === "AWAITING_APPROVAL") return { done: 3, blocked: "Awaiting human approval" };
  if (incident.status === "REMEDIATING") return { done: action?.status === "COMPLETED" ? 4 : 3 };
  if (incident.investigation) return { done: incident.policy_decision ? 3 : 2 };
  if (incident.status === "INVESTIGATING") return { done: 1 };
  return { done: 1 };
}

export function LifecycleStepper({ incident }: { incident: IncidentDetail }) {
  const { done, blocked } = lifecycleProgress(incident);
  const running = !blocked && done < 7 && ["INVESTIGATING", "REMEDIATING"].includes(incident.status);
  return (
    <div>
      <ol className="grid grid-cols-7 gap-1.5">
        {STAGES.map((stage, index) => {
          const complete = index < done;
          const current = index === done && done < 7;
          return (
            <li key={stage} className="min-w-0">
              <div
                className={cn(
                  "h-1 rounded-full",
                  complete ? "bg-good" : current ? (blocked ? "bg-warning" : "bg-info") : "bg-border",
                  current && running && "animate-pulse",
                )}
              />
              <div className={cn("mt-2 flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider",
                complete ? "text-foreground" : current ? (blocked ? "text-warning" : "text-info") : "text-subtle")}
              >
                {complete && <Check className="size-3 text-good" />}
                <span className="truncate">{stage}</span>
              </div>
            </li>
          );
        })}
      </ol>
      {blocked && <p className="mt-2 text-xs text-warning">{blocked}</p>}
    </div>
  );
}
