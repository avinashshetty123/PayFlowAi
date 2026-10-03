"use client";

import { useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { useLiveRefresh } from "@/components/event-stream";
import { IncidentTable } from "@/components/incident-table";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "AWAITING_APPROVAL", label: "Awaiting approval" },
  { key: "ESCALATED", label: "Escalated" },
  { key: "RESOLVED", label: "Resolved" },
  { key: "CLOSED", label: "Closed" },
] as const;

export default function IncidentsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const { data, error, refresh } = useApi(
    () =>
      api.incidents({
        limit: 200,
        active: filter === "active" ? true : undefined,
        status: filter !== "all" && filter !== "active" ? filter : undefined,
      }),
    [filter],
    20000,
  );
  useLiveRefresh(refresh, (e) => e.event.startsWith("INCIDENT") || e.event.startsWith("ACTION") || e.event.startsWith("INVESTIGATION"));

  return (
    <div>
      <PageHeader
        title="Incidents"
        description="Opened by the deterministic reconciliation engine, investigated by AI, remediated under policy. PayPal provider failures and PayFlow demo injections are labelled separately."
      />
      {error && <ErrorBanner message={error} />}
      <div className="mb-3 flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs",
              filter === f.key ? "bg-panel-2 text-foreground" : "text-muted hover:text-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
        {data && <span className="ml-auto self-center text-xs text-subtle">{data.total} incidents</span>}
      </div>
      <Card>{data ? <IncidentTable incidents={data.items} /> : <Skeleton className="m-5 h-64" />}</Card>
    </div>
  );
}
