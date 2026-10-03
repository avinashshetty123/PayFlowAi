"use client";

import {
  Bell,
  CreditCard,
  FileClock,
  GitCompareArrows,
  Info,
  LayoutDashboard,
  OctagonPause,
  Radio,
  Scale,
  ShieldAlert,
  UserCheck,
  Waypoints,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";

import { EventStreamProvider, useEventStream, useLiveRefresh } from "@/components/event-stream";
import { LiveDemoProvider, useLiveDemo } from "@/components/live-demo";
import { INTRO_SEEN_KEY } from "@/lib/intro";
import { NotificationCenter } from "@/components/notification-center";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { ThemeToggle } from "@/components/theme-toggle";
import { api, environmentLabel } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV_GROUPS = [
  {
    title: "Overview",
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard },
      { href: "/payments", label: "Payments", icon: CreditCard },
      { href: "/welcome", label: "About PayFlow", icon: Info },
    ],
  },
  {
    title: "Operations",
    items: [
      { href: "/incidents", label: "Incidents", icon: ShieldAlert },
      { href: "/approvals", label: "Approvals", icon: UserCheck },
      { href: "/notifications", label: "Alerts", icon: Bell },
    ],
  },
  {
    title: "Controls",
    items: [
      { href: "/reconciliation", label: "Reconciliation", icon: GitCompareArrows },
      { href: "/policies", label: "Policies & Agent", icon: Scale },
      { href: "/audit", label: "Audit Log", icon: FileClock },
    ],
  },
];
const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function Sidebar() {
  const pathname = usePathname();
  const { data: stats, refresh } = useApi(api.dashboard, [], 30000);
  const { data: alerts, refresh: refreshAlerts } = useApi(() => api.notifications({ limit: 1 }), [], 30000);
  useLiveRefresh(() => {
    refresh();
    refreshAlerts();
  }, (e) => e.event.startsWith("INCIDENT") || e.event.startsWith("ACTION") || e.event.startsWith("NOTIFICATION"));

  const badges: Record<string, number | undefined> = {
    "/approvals": stats?.pending_approvals,
    "/incidents": stats?.active_incidents,
    "/notifications": alerts?.unread,
  };

  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-navy text-white md:flex">
      <div className="flex h-16 items-center gap-2.5 px-5">
        <div className="flex size-8 items-center justify-center rounded-lg bg-sky text-[#002e6e]">
          <Waypoints className="size-4" />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-bold tracking-tight">
            PayFlow<span className="text-sky"> AI</span>
          </div>
          <div className="text-[10px] uppercase tracking-[0.14em] text-white/55">for Business</div>
        </div>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pt-2">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/45">{group.title}</div>
            <div className="space-y-0.5">
              {group.items.map(({ href, label, icon: Icon }) => {
                const active = isActive(pathname, href);
                const badge = badges[href];
                return (
                  <Link key={href} href={href}
                    className={cn(
                      "relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                      active ? "bg-white/15 font-semibold text-white" : "text-white/75 hover:bg-white/10 hover:text-white",
                    )}>
                    {active && <span className="absolute inset-y-1.5 left-0 w-1 rounded-r bg-sky" aria-hidden />}
                    <Icon className="size-4 shrink-0" />
                    <span className="flex-1">{label}</span>
                    {!!badge && (
                      <span className={cn("rounded-full px-1.5 py-px text-[10px] font-bold tabular",
                        href === "/approvals" ? "bg-warning text-white" : href === "/notifications" ? "bg-sky text-[#002e6e]" : "bg-white/20 text-white")}>
                        {badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="m-3 rounded-lg bg-white/10 p-3 text-[11px] leading-relaxed text-white/70">
        <div className="mb-0.5 font-semibold text-white">PayPal Sandbox</div>
        No real money moves. Demo failures are injected into PayFlow only.
      </div>
    </aside>
  );
}

function KillSwitchBanner() {
  const { data, refresh } = useApi(api.policies, [], 30000);
  useLiveRefresh(refresh, (e) => e.transactionId === "SYSTEM");
  if (!data?.kill_switch.enabled) return null;
  return (
    <Link href="/policies" className="flex items-center gap-2 bg-critical px-4 py-1.5 text-xs font-medium text-white md:px-6">
      <OctagonPause className="size-3.5" />
      Automation paused by {data.kill_switch.updated_by}: {data.kill_switch.reason}. Every financial action needs human approval.
    </Link>
  );
}

function StreamStatus() {
  const { connected } = useEventStream();
  return (
    <span className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
      connected ? "border-good/30 bg-good/5 text-good" : "border-warning/30 bg-warning/5 text-warning")}>
      <span className={cn("size-1.5 rounded-full", connected ? "bg-good pulse-ring" : "bg-warning animate-pulse")} />
      {connected ? "Live" : "Reconnecting"}
    </span>
  );
}

const noopSubscribe = () => () => {};

/** Rendered from the browser location only after hydration, so SSR and client markup always match. */
function EnvironmentChip() {
  const label = useSyncExternalStore(noopSubscribe, environmentLabel, () => null);
  return (
    <span className="hidden rounded-full border border-border bg-panel-2 px-2.5 py-1 text-[11px] font-medium text-muted lg:inline">
      {label ? `${label} · ` : ""}PayPal Sandbox
    </span>
  );
}

function TopBar() {
  const { open } = useLiveDemo();
  const pathname = usePathname();
  const current = ALL_ITEMS.find((i) => isActive(pathname, i.href));
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-panel/95 backdrop-blur">
      <KillSwitchBanner />
      <div className="flex h-16 items-center justify-between gap-3 px-4 md:px-8">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 md:hidden">
            <Waypoints className="size-4 text-brand" />
            <span className="text-sm font-bold text-brand">PayFlow AI</span>
          </div>
          <div className="hidden md:block">
            <div className="text-[10px] uppercase tracking-[0.14em] text-subtle">Merchant console</div>
            <div className="text-sm font-semibold text-foreground">{current?.label ?? "PayFlow AI"}</div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <EnvironmentChip />
          <StreamStatus />
          <nav className="flex gap-0.5 md:hidden">
            {ALL_ITEMS.slice(0, 4).map(({ href, icon: Icon }) => (
              <Link key={href} href={href}
                className={cn("rounded-md p-2 text-muted", isActive(pathname, href) && "text-brand")}>
                <Icon className="size-4" />
              </Link>
            ))}
          </nav>
          <ThemeToggle />
          <NotificationCenter />
          <Button size="sm" onClick={() => open()} className="rounded-full px-4">
            <Radio className="size-3.5" />
            Live Demo
          </Button>
        </div>
      </div>
    </header>
  );
}

/** First visit to the console goes to the intro page; its "Enter console" button marks it seen. */
function useFirstVisitIntro(pathname: string) {
  const router = useRouter();
  useEffect(() => {
    if (pathname !== "/") return;
    try {
      if (!localStorage.getItem(INTRO_SEEN_KEY)) router.replace("/welcome");
    } catch {
      /* storage blocked: stay on the dashboard */
    }
  }, [pathname, router]);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  useFirstVisitIntro(pathname);
  // The intro page is a full-screen marketing page without the console chrome.
  if (pathname.startsWith("/welcome")) return <>{children}</>;
  return (
    <EventStreamProvider>
      <LiveDemoProvider>
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <TopBar />
            <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-8">{children}</main>
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
        <h1 className="text-2xl font-bold tracking-tight text-foreground">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-4 rounded-xl border border-critical/30 bg-critical/5 px-4 py-3 text-sm text-critical">{message}</div>
  );
}
