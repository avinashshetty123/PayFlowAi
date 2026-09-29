"use client";

import {
  CreditCard,
  FileClock,
  GitCompareArrows,
  LayoutDashboard,
  Radio,
  ShieldAlert,
  UserCheck,
  Waypoints,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { EventStreamProvider, useEventStream, useLiveRefresh } from "@/components/event-stream";
import { LiveDemoProvider, useLiveDemo } from "@/components/live-demo";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/payments", label: "Payments", icon: CreditCard },
  { href: "/incidents", label: "Incidents", icon: ShieldAlert },
  { href: "/approvals", label: "Approvals", icon: UserCheck },
  { href: "/reconciliation", label: "Reconciliation", icon: GitCompareArrows },
  { href: "/audit", label: "Audit", icon: FileClock },
];

function Sidebar() {
  const pathname = usePathname();
  const { data: stats, refresh } = useApi(api.dashboard, [], 30000);
  useLiveRefresh(refresh, (e) => e.event.startsWith("INCIDENT") || e.event.startsWith("ACTION"));
  return (
    <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-border bg-panel md:flex">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <div className="flex size-7 items-center justify-center rounded-md bg-primary/15 text-primary">
          <Waypoints className="size-4" />
        </div>
        <div>
          <div className="text-sm font-semibold tracking-tight">PayFlow AI</div>
          <div className="text-[10px] uppercase tracking-wider text-subtle">Payment Ops Teammate</div>
        </div>
      </div>
      <nav className="flex-1 space-y-0.5 p-2">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          const count =
            href === "/approvals" ? stats?.pending_approvals : href === "/incidents" ? stats?.active_incidents : undefined;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                active ? "bg-panel-2 text-foreground" : "text-muted hover:bg-panel-2/60 hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              <span className="flex-1">{label}</span>
              {!!count && (
                <span
                  className={cn(
                    "rounded px-1.5 text-[10px] font-semibold tabular",
                    href === "/approvals" ? "bg-warning/15 text-warning" : "bg-panel-2 text-muted",
                  )}
                >
                  {count}
                </span>
              )}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-border p-3 text-[10px] leading-relaxed text-subtle">
        Payments run on PayPal Sandbox (no real money). Failures labelled <b>DEMO FAILURE INJECTION</b> are introduced by
        PayFlow into its own systems, never by PayPal.
      </div>
    </aside>
  );
}

function StreamIndicator() {
  const { connected, transport } = useEventStream();
  return (
    <span className={cn("flex items-center gap-1.5 text-[11px]", connected ? "text-good" : "text-warning")}>
      <span className={cn("size-1.5 rounded-full", connected ? "bg-good pulse-ring" : "bg-warning")} />
      {connected ? `Live · ${transport === "redis" ? "Redis" : "in-process"}` : "Event stream reconnecting"}
    </span>
  );
}

function TopBar() {
  const { open } = useLiveDemo();
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-background/90 px-4 backdrop-blur md:px-6">
      <div className="flex items-center gap-2 md:hidden">
        <Waypoints className="size-4 text-primary" />
        <span className="text-sm font-semibold">PayFlow AI</span>
      </div>
      <div className="hidden items-center gap-3 md:flex">
        <StreamIndicator />
        <span className="text-[11px] text-subtle">Provider · PayPal Sandbox</span>
      </div>
      <div className="flex items-center gap-2">
        <nav className="flex gap-1 md:hidden">
          {NAV.slice(0, 4).map(({ href, icon: Icon }) => (
            <Link key={href} href={href} className={cn("rounded p-2 text-muted", pathname === href && "text-foreground")}>
              <Icon className="size-4" />
            </Link>
          ))}
        </nav>
        <Button size="sm" onClick={() => open()}>
          <Radio />
          START LIVE DEMO
        </Button>
      </div>
    </header>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <EventStreamProvider>
      <LiveDemoProvider>
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <TopBar />
            <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-6">{children}</main>
          </div>
        </div>
      </LiveDemoProvider>
    </EventStreamProvider>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-4 rounded-md border border-critical/40 bg-critical/10 px-4 py-3 text-sm text-[#f87171]">{message}</div>
  );
}
