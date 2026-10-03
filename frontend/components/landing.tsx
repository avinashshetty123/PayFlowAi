"use client";

import {
  ArrowRight,
  BellRing,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  Eye,
  FileClock,
  Gauge,
  GitCompareArrows,
  Lock,
  OctagonPause,
  Scale,
  Search,
  ShieldCheck,
  Sparkles,
  UserCheck,
  Waypoints,
  Workflow,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { INTRO_SEEN_KEY } from "@/lib/intro";
import { cn } from "@/lib/utils";

function markSeen() {
  try {
    localStorage.setItem(INTRO_SEEN_KEY, "1");
  } catch {
    /* storage blocked: the intro may show again, which is harmless */
  }
}

/** Adds `in` to every `.reveal` element as it scrolls into view. */
function useReveal() {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const nodes = root.current?.querySelectorAll<HTMLElement>(".reveal") ?? [];
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && e.target.classList.add("in")),
      { threshold: 0.15 },
    );
    nodes.forEach((n) => observer.observe(n));
    return () => observer.disconnect();
  }, []);
  return root;
}

// ---- hero: live "incident story" animation -------------------------------------------------

const STORY = [
  { label: "Observing 5 systems for every payment", tone: "text-sky", broken: false },
  { label: "Mismatch: ledger FAILED while PayPal captured $50", tone: "text-[#ff8a8a]", broken: true },
  { label: "AI: ledger write failed · policy SYS-FAULT → human approval", tone: "text-[#ffc078]", broken: true },
  { label: "Approved · ledger fixed · all systems verified", tone: "text-[#6ee7a8]", broken: false },
];

const NODES = [
  { name: "PayPal", x: 70, y: 70 },
  { name: "Bank", x: 330, y: 70 },
  { name: "Merchant", x: 40, y: 250 },
  { name: "Ledger", x: 360, y: 250 },
  { name: "Webhook", x: 200, y: 345 },
];
const CORE = { x: 200, y: 190 };

function HeroVisual() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setStep((s) => (s + 1) % STORY.length), 2600);
    return () => clearInterval(id);
  }, []);
  const story = STORY[step];

  return (
    <div className="relative mx-auto w-full max-w-[460px]">
      <svg viewBox="0 0 400 400" className="w-full" aria-label="PayFlow reconciling five payment systems">
        <defs>
          <radialGradient id="coreGlow">
            <stop offset="0%" stopColor="#00BAF2" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#00BAF2" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx={CORE.x} cy={CORE.y} r="120" fill="url(#coreGlow)" className="landing-breathe" />
        {NODES.map((n, i) => {
          const broken = story.broken && n.name === "Ledger";
          const path = `M${n.x},${n.y} L${CORE.x},${CORE.y}`;
          return (
            <g key={n.name}>
              <path d={path} stroke={broken ? "#ff6b6b" : "#2f5b9a"} strokeWidth="2" strokeDasharray="5 6" className="landing-dash" />
              <circle r="4" fill={broken ? "#ff6b6b" : "#00BAF2"}>
                <animateMotion dur={`${1.6 + i * 0.25}s`} repeatCount="indefinite" path={path} />
              </circle>
              <circle cx={n.x} cy={n.y} r="30" fill="#0b2a5e" stroke={broken ? "#ff6b6b" : "#3d6db3"} strokeWidth="2"
                className={cn("transition-all duration-500", broken && "landing-alarm")} />
              <text x={n.x} y={n.y + 50} textAnchor="middle" fill="#cfe0f5" fontSize="13" fontWeight="600">{n.name}</text>
              <text x={n.x} y={n.y + 5} textAnchor="middle" fill={broken ? "#ff8a8a" : "#6ee7a8"} fontSize="15" fontWeight="700">
                {broken ? "✕" : "✓"}
              </text>
            </g>
          );
        })}
        <circle cx={CORE.x} cy={CORE.y} r="44" fill="#00BAF2" />
        <text x={CORE.x} y={CORE.y - 3} textAnchor="middle" fill="#002E6E" fontSize="14" fontWeight="800">PayFlow</text>
        <text x={CORE.x} y={CORE.y + 15} textAnchor="middle" fill="#002E6E" fontSize="12" fontWeight="700">AI</text>
      </svg>
      <div className="mx-auto -mt-2 flex max-w-[420px] items-center gap-2 rounded-full border border-white/15 bg-white/10 px-4 py-2 backdrop-blur">
        <span className={cn("size-2 shrink-0 rounded-full", story.broken ? "bg-[#ff8a8a] animate-pulse" : "bg-[#6ee7a8]")} />
        <span key={step} className={cn("landing-fade text-xs font-medium sm:text-sm", story.tone)}>{story.label}</span>
      </div>
    </div>
  );
}

// ---- content -------------------------------------------------------------------------------

const LIFECYCLE = [
  { icon: Eye, title: "Observe", text: "Every PayPal capture and signed webhook triggers reconciliation across 5 systems." },
  { icon: Search, title: "Investigate", text: "Groq AI + similar past incidents (RAG) explain the root cause and money at risk." },
  { icon: Scale, title: "Decide", text: "A deterministic policy engine allows, asks a human, or denies. The AI only recommends." },
  { icon: Wrench, title: "Act", text: "The fix runs exactly once, protected by idempotency keys." },
  { icon: ShieldCheck, title: "Verify", text: "All five systems are re-read. Only a full match resolves the incident." },
  { icon: GitCompareArrows, title: "Reconcile", text: "Money at risk, break ageing, SLA breaches and auto-fix rate, live." },
  { icon: FileClock, title: "Audit", text: "Every step lands in a SHA-256 hash-chained log nobody can quietly edit." },
];

const OUTCOMES = [
  { title: "ALLOW", text: "Safe and low risk: PayFlow fixes it on its own, then verifies.", cls: "border-good/40 bg-good/10 text-good" },
  { title: "HUMAN APPROVAL", text: "Internal fault, big refund or high risk: waits for you, alerts your phone.", cls: "border-warning/40 bg-warning/10 text-warning" },
  { title: "DENY", text: "Unsafe right now: nothing runs; a person resolves it.", cls: "border-critical/40 bg-critical/10 text-critical" },
];

const FEATURES = [
  { icon: Workflow, title: "LangGraph agent", text: "The incident lifecycle is an explicit state graph; each incident's path is drawn live." },
  { icon: Scale, title: "Policy overrules AI", text: "Guardrail PB-001 replaces a wrong AI fix with the playbook fix, and audits it." },
  { icon: Gauge, title: "Explainable risk 0–100", text: "Every point has a named reason. 75+ always needs a human." },
  { icon: UserCheck, title: "Four-eyes on faults", text: "SYS-FAULT and LEDGER-AMT hold fixes to the books for human sign-off." },
  { icon: BellRing, title: "WhatsApp alerts", text: "P1–P4 routing, delivery receipts, re-escalation if nobody acknowledges." },
  { icon: OctagonPause, title: "Kill switch + breaker", text: "Pause all automation in one click; runaway automation is capped." },
  { icon: Sparkles, title: "Self-healing", text: "If systems agree again before approval, the incident closes by itself." },
  { icon: Lock, title: "Tamper-evident audit", text: "Hash-chained records; the verifier pinpoints the first altered row." },
];

const STACK = ["Next.js 16", "TypeScript", "Tailwind", "FastAPI", "PostgreSQL + pgvector", "Redis", "LangGraph", "Groq", "PayPal Sandbox", "Meta WhatsApp"];

export function Landing() {
  const root = useReveal();

  return (
    <div ref={root} className="min-h-screen bg-background text-foreground">
      {/* nav */}
      <header className="fixed inset-x-0 top-0 z-40 border-b border-white/10 bg-navy/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 md:px-6">
          <div className="flex items-center gap-2.5 text-white">
            <div className="flex size-8 items-center justify-center rounded-lg bg-sky text-[#002e6e]"><Waypoints className="size-4" /></div>
            <span className="text-[15px] font-bold tracking-tight">PayFlow<span className="text-sky"> AI</span></span>
          </div>
          <nav className="hidden items-center gap-6 text-sm text-white/75 md:flex">
            <a href="#how" className="hover:text-white">How it works</a>
            <a href="#policy" className="hover:text-white">Policy</a>
            <a href="#features" className="hover:text-white">Features</a>
          </nav>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Link href="/" onClick={markSeen}
              className="hidden items-center gap-1.5 rounded-full bg-sky px-4 py-2 text-sm font-semibold text-[#002e6e] transition-transform hover:scale-[1.03] sm:flex">
              Enter console <ArrowRight className="size-4" />
            </Link>
          </div>
        </div>
      </header>

      {/* hero */}
      <section className="landing-hero relative overflow-hidden bg-navy pt-16 text-white">
        <div className="landing-grid pointer-events-none absolute inset-0 opacity-40" aria-hidden />
        <div className="relative mx-auto grid max-w-6xl items-center gap-10 px-4 py-16 md:grid-cols-[1.1fr_1fr] md:px-6 md:py-24">
          <div>
            <p className="landing-up inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs font-medium text-sky">
              <BrainCircuit className="size-3.5" /> Autonomous payment operations
            </p>
            <h1 className="landing-up mt-5 text-4xl font-bold leading-[1.08] tracking-tight [animation-delay:120ms] sm:text-5xl lg:text-6xl">
              Payments that <span className="landing-shimmer">heal themselves</span>, with a human in control.
            </h1>
            <p className="landing-up mt-5 max-w-xl text-base leading-relaxed text-white/75 [animation-delay:240ms] sm:text-lg">
              PayFlow watches every payment across PayPal, the bank, the merchant, the ledger and webhooks. When they
              disagree, AI explains why, a policy engine decides what is safe, and the fix is applied, verified and audited.
            </p>
            <div className="landing-up mt-8 flex flex-wrap gap-3 [animation-delay:360ms]">
              <Link href="/" onClick={markSeen}
                className="group inline-flex items-center gap-2 rounded-full bg-sky px-6 py-3 text-sm font-semibold text-[#002e6e] shadow-[0_8px_30px_rgba(0,186,242,0.35)] transition-transform hover:scale-[1.03]">
                Enter console <ArrowRight className="size-4 transition-transform group-hover:translate-x-1" />
              </Link>
              <a href="#how" className="inline-flex items-center gap-2 rounded-full border border-white/25 px-6 py-3 text-sm font-semibold text-white hover:bg-white/10">
                How it works <ChevronDown className="size-4" />
              </a>
            </div>
            <div className="landing-up mt-8 flex flex-wrap gap-2 text-[11px] font-medium text-white/70 [animation-delay:480ms]">
              {["AI recommends", "Policy decides", "Humans approve", "Everything is audited"].map((t) => (
                <span key={t} className="rounded-full bg-white/10 px-3 py-1">{t}</span>
              ))}
            </div>
          </div>
          <div className="landing-up [animation-delay:200ms]"><HeroVisual /></div>
        </div>
        <a href="#problem" aria-label="Scroll down" className="relative mx-auto mb-6 flex w-fit text-white/50 hover:text-white">
          <ChevronDown className="size-6 animate-bounce" />
        </a>
      </section>

      {/* problem */}
      <section id="problem" className="mx-auto max-w-6xl px-4 py-20 md:px-6">
        <div className="reveal grid gap-10 md:grid-cols-2 md:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-info">The problem</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">One payment. Five records that must agree.</h2>
            <p className="mt-4 leading-relaxed text-muted">
              A customer pays once, but five systems record it separately. A failed ledger write, a lost webhook or a wrong
              amount makes them drift. Money is captured but the books do not show it, and ops teams usually find out from
              an angry customer.
            </p>
          </div>
          <div className="grid grid-cols-5 gap-2">
            {["PayPal", "Bank", "Merchant", "Ledger", "Webhook"].map((s, i) => (
              <div key={s} className="reveal flex flex-col items-center gap-2 rounded-xl border border-border bg-panel p-3 text-center card-shadow"
                style={{ transitionDelay: `${i * 90}ms` }}>
                <span className={cn("flex size-9 items-center justify-center rounded-full text-sm font-bold",
                  s === "Ledger" ? "bg-critical/15 text-critical landing-alarm-soft" : "bg-good/15 text-good")}>
                  {s === "Ledger" ? "✕" : "✓"}
                </span>
                <span className="text-[11px] font-semibold sm:text-xs">{s}</span>
                <span className={cn("text-[10px]", s === "Ledger" ? "text-critical" : "text-muted")}>{s === "Ledger" ? "FAILED" : "OK"}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* how it works */}
      <section id="how" className="border-y border-border bg-panel-2 py-20">
        <div className="mx-auto max-w-6xl px-4 md:px-6">
          <div className="reveal max-w-2xl">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-info">How it works</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">Seven steps, from payment to proof</h2>
          </div>
          <div className="reveal relative mt-12">
            <div className="absolute left-[22px] top-0 h-full w-0.5 bg-border md:left-0 md:top-[22px] md:h-0.5 md:w-full" aria-hidden />
            <div className="landing-progress absolute left-[22px] top-0 w-0.5 bg-sky md:left-0 md:top-[22px] md:h-0.5" aria-hidden />
            <ol className="relative grid gap-6 md:grid-cols-7 md:gap-3">
              {LIFECYCLE.map(({ icon: Icon, title, text }, i) => (
                <li key={title} className="landing-step flex gap-4 md:flex-col md:gap-3" style={{ animationDelay: `${i * 0.35}s` }}>
                  <span className="relative z-10 flex size-11 shrink-0 items-center justify-center rounded-full border-2 border-sky bg-panel text-info">
                    <Icon className="size-5" />
                  </span>
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wider text-subtle">Step {i + 1}</div>
                    <div className="font-semibold">{title}</div>
                    <p className="mt-1 text-xs leading-relaxed text-muted">{text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      {/* policy */}
      <section id="policy" className="mx-auto max-w-6xl px-4 py-20 md:px-6">
        <div className="reveal text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-info">The policy engine</p>
          <h2 className="mx-auto mt-3 max-w-3xl text-3xl font-bold tracking-tight sm:text-4xl">
            The AI recommends. <span className="text-info">The policy decides.</span>
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-muted">
            Plain, testable rules with no AI inside. The AI&apos;s doubts can only make a decision stricter, never looser,
            and a human approval can never override a safety denial.
          </p>
        </div>
        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {OUTCOMES.map((o, i) => (
            <div key={o.title} className={cn("reveal rounded-2xl border p-6", o.cls)} style={{ transitionDelay: `${i * 120}ms` }}>
              <div className="text-lg font-bold tracking-wide">{o.title}</div>
              <p className="mt-2 text-sm leading-relaxed text-foreground/80">{o.text}</p>
            </div>
          ))}
        </div>
        <div className="reveal mt-6 flex flex-wrap justify-center gap-2">
          {["PB-001", "PRE-*", "SYS-FAULT", "LEDGER-AMT", "LIM-REFUND", "RISK-SCORE", "AI-CONF", "CTL-KILL", "CTL-BREAKER", "HUMAN-OK"].map((r) => (
            <span key={r} className="rounded-md bg-navy px-2 py-1 font-mono text-[11px] text-white">{r}</span>
          ))}
        </div>
      </section>

      {/* features */}
      <section id="features" className="border-t border-border bg-panel-2 py-20">
        <div className="mx-auto max-w-6xl px-4 md:px-6">
          <div className="reveal max-w-2xl">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-info">What makes it different</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">Built like a real fintech ops platform</h2>
          </div>
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map(({ icon: Icon, title, text }, i) => (
              <div key={title} className="reveal group rounded-2xl border border-border bg-panel p-5 card-shadow transition-transform hover:-translate-y-1"
                style={{ transitionDelay: `${(i % 4) * 80}ms` }}>
                <span className="flex size-10 items-center justify-center rounded-xl bg-sky/15 text-info transition-colors group-hover:bg-sky group-hover:text-[#002e6e]">
                  <Icon className="size-5" />
                </span>
                <div className="mt-4 font-semibold">{title}</div>
                <p className="mt-1 text-sm leading-relaxed text-muted">{text}</p>
              </div>
            ))}
          </div>
          <div className="reveal mt-12 flex flex-wrap justify-center gap-2">
            {STACK.map((s) => (
              <span key={s} className="rounded-full border border-border bg-panel px-3 py-1 text-xs font-medium text-muted">{s}</span>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="landing-hero relative overflow-hidden bg-navy py-20 text-white">
        <div className="landing-grid pointer-events-none absolute inset-0 opacity-30" aria-hidden />
        <div className="reveal relative mx-auto max-w-3xl px-4 text-center">
          <CheckCircle2 className="mx-auto size-10 text-sky" />
          <h2 className="mt-4 text-3xl font-bold tracking-tight sm:text-4xl">See an incident go end to end</h2>
          <p className="mx-auto mt-3 max-w-xl text-white/70">
            Make a real PayPal Sandbox payment, inject a failure into PayFlow&apos;s own ledger, get the WhatsApp alert,
            approve the fix, and watch it verify. No real money moves.
          </p>
          <Link href="/" onClick={markSeen}
            className="group mt-8 inline-flex items-center gap-2 rounded-full bg-sky px-8 py-3.5 text-sm font-semibold text-[#002e6e] shadow-[0_8px_30px_rgba(0,186,242,0.35)] transition-transform hover:scale-[1.03]">
            Enter console <ArrowRight className="size-4 transition-transform group-hover:translate-x-1" />
          </Link>
        </div>
      </section>

      <footer className="border-t border-border py-6 text-center text-xs text-muted">
        PayFlow AI · PayPal Sandbox demo · AI recommends, policy decides, humans approve, everything is audited
      </footer>
    </div>
  );
}
