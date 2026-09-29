"use client";

import {
  Activity,
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

import { EventStreamProvider, useEventStream } from "@/components/event-stream";
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
  { href: "/audit", label: "Audit Log", icon: FileClock },
];

function Sidebar() {
  const pathname = usePathname();
  const { data: stats } = useApi(api.dashboard, [], 30000);
  return (
    <aside className="sticky top-0 hidden h-screen w-52 shrink-0 flex-col border-r border-border bg-panel md:flex">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2.5 border-b border-border px-4">
        <div className="flex size-7 items-center justify-center rounded-lg bg-primary/20 text-primary">
          <Waypoints className="size-4" />
        </div>
        <div>
          <div className="text-sm font-semibold">PayFlow AI</div>
          <div className="text-[10px] text-subtle">Payment Operations</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 p-2 pt-3">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          const badge =
            href === "/approvals" ? stats?.pending_approvals :
            href === "/incidents" ? stats?.active_incidents : undefined;
          return (
            <Link key={href} href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted hover:bg-panel-2 hover:text-foreground",
              )}>
              <Icon className="size-4 shrink-0" />
              <span className="flex-1">{label}</span>
              {!!badge && (
                <span className={cn(
                  "rounded-full px-1.5 py-px text-[10px] font-semibold tabular",
                  href === "/approvals" ? "bg-warning/20 text-warning" : "bg-serious/20 text-serious",
                )}>
                  {badge}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer note */}
      <div className="border-t border-border p-3">
        <div className="flex items-center gap-1.5 text-[10px] text-subtle">
          <Activity className="size-3 shrink-0" />
          PayPal Sandbox · no real money
        </div>
      </div>
    </aside>
  );
}

function StreamDot() {
  const { connected } = useEventStream();
  return (
    <span className={cn("flex items-center gap-1.5 text-[11px]", connected ? "text-good" : "text-warning")}>
      <span className={cn("size-1.5 rounded-full", connected ? "bg-good pulse-ring" : "bg-warning animate-pulse")} />
      {connected ? "Live" : "Reconnecting"}
    </span>
  );
}

function TopBar() {
  const { open } = useLiveDemo();
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:px-6">
      {/* Mobile logo */}
      <div className="flex items-center gap-2 md:hidden">
        <Waypoints className="size-4 text-primary" />
        <span className="text-sm font-semibold">PayFlow AI</span>
      </div>

      {/* Desktop left */}
      <div className="hidden items-center gap-4 md:flex">
        <StreamDot />
        <span className="text-[11px] text-subtle">PayPal Sandbox</span>
      </div>

      {/* Right */}
      <div className="flex items-center gap-2">
        {/* Mobile nav icons */}
        <nav className="flex gap-0.5 md:hidden">
          {NAV.slice(0, 5).map(({ href, icon: Icon }) => (
            <Link key={href} href={href}
              className={cn("rounded-md p-2 text-muted transition-colors hover:text-foreground",
                (href === "/" ? pathname === "/" : pathname.startsWith(href)) && "text-primary")}>
              <Icon className="size-4" />
            </Link>
          ))}
        </nav>
        <Button size="sm" onClick={() => open()}>
          <Radio className="size-3.5" />
          Live Demo
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
            <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-6">
              {children}
            </main>
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
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-4 rounded-lg border border-critical/30 bg-critical/8 px-4 py-3 text-sm text-[#f87171]">
      {message}
    </div>
  );
}
