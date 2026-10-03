"use client";

import { ArrowRight, Bot, CheckCircle2, Hand, Play, Scale, ShieldCheck, Siren } from "lucide-react";

import { cn } from "@/lib/utils";

type Trace = { node: string; at: string; outcome: string }[];

const NODES = {
  investigate: { label: "AI investigate", icon: Bot, hint: "Groq + read-only tools + RAG" },
  decide: { label: "Policy decide", icon: Scale, hint: "Deterministic rules, risk score" },
  human_gate: { label: "Human approval", icon: Hand, hint: "Pauses the graph" },
  execute: { label: "Execute", icon: Play, hint: "Idempotent, policy-authorised" },
  verify: { label: "Verify", icon: ShieldCheck, hint: "Re-reads PayPal + all systems" },
  reconcile: { label: "Reconcile", icon: CheckCircle2, hint: "Five-way match, close" },
  escalate: { label: "Escalate", icon: Siren, hint: "Human resolution" },
} as const;
type NodeId = keyof typeof NODES;

function Node({ id, trace }: { id: NodeId; trace: Trace }) {
  const steps = trace.filter((t) => t.node === id || (id === "escalate" && t.node === "human_resolution"));
  const last = steps.at(-1);
  const visited = steps.length > 0;
  const failed = !!last && /FAILED|denied/i.test(last.outcome);
  const meta = NODES[id];
  const Icon = meta.icon;
  return (
    <div className={cn("min-w-[118px] flex-1 rounded-xl border px-3 py-2.5 transition-colors",
      visited ? (failed ? "border-critical/40 bg-critical/5" : "border-sky/60 bg-sky/10") : "border-dashed border-border bg-panel")}>
      <div className={cn("flex items-center gap-1.5 text-xs font-semibold",
        visited ? (failed ? "text-critical" : "text-brand") : "text-subtle")}>
        <Icon className="size-3.5" /> {meta.label}
      </div>
      <div className="mt-0.5 text-[10px] leading-snug text-muted">{last ? last.outcome : meta.hint}</div>
    </div>
  );
}

const Arrow = () => <ArrowRight className="size-4 shrink-0 self-center text-subtle" />;

/** LangGraph topology with the path this incident actually took highlighted. */
export function AgentGraph({ trace = [], engine }: { trace?: Trace; engine?: string }) {
  const tookHuman = trace.some((t) => t.node === "human_gate");
  const escalated = trace.some((t) => t.node === "escalate" || t.node === "human_resolution");
  return (
    <div>
      <div className="flex flex-col gap-2 lg:flex-row lg:items-stretch">
        <Node id="investigate" trace={trace} />
        <Arrow />
        <Node id="decide" trace={trace} />
        <Arrow />
        <div className="flex flex-1 flex-col gap-2">
          <div className={cn("flex gap-2", !tookHuman && trace.length > 0 && "opacity-60")}><Node id="human_gate" trace={trace} /></div>
          <div className={cn("flex gap-2", !escalated && trace.length > 0 && "opacity-60")}><Node id="escalate" trace={trace} /></div>
        </div>
        <Arrow />
        <Node id="execute" trace={trace} />
        <Arrow />
        <Node id="verify" trace={trace} />
        <Arrow />
        <Node id="reconcile" trace={trace} />
      </div>
      {engine && (
        <p className="mt-2 text-[10px] uppercase tracking-wider text-subtle">
          Orchestrated by {engine === "langgraph" ? "LangGraph StateGraph" : "sequential fallback"} · no LLM inside the graph can move money
        </p>
      )}
    </div>
  );
}
