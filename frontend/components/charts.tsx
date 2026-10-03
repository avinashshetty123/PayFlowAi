"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";

type TipProps = TooltipContentProps<ValueType, NameType>;

import { toneFor } from "@/components/status";
import { humanize, money } from "@/lib/format";
import type { DashboardStats } from "@/types/api";

const AXIS = { stroke: "var(--axis)", fontSize: 11, tickLine: false, axisLine: false } as const;
const STATUS_FILL = {
  good: "var(--good)",
  warning: "var(--warning)",
  serious: "var(--serious)",
  critical: "var(--critical)",
  info: "var(--info)",
  neutral: "var(--subtle)",
} as const;

function TooltipBox({ title, rows }: { title: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="rounded-md border border-border-strong bg-panel-2 px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-medium text-foreground">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4 text-muted">
          <span className="flex items-center gap-1.5">
            {r.color && <span className="inline-block h-0.5 w-3 rounded" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="font-mono text-foreground tabular">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

export function VolumeChart({ data }: { data: DashboardStats["payment_volume"] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -20 }} barCategoryGap={3}>
        <CartesianGrid vertical={false} stroke="var(--grid)" />
        <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" minTickGap={16} />
        <YAxis {...AXIS} allowDecimals={false} width={44} />
        <Tooltip
          cursor={{ fill: "var(--panel-2)" }}
          content={({ active, payload }: TipProps) => {
            if (!active || !payload?.length) return null;
            const point = payload[0].payload as DashboardStats["payment_volume"][number];
            return (
              <TooltipBox
                title={point.label}
                rows={[
                  { label: "Payments", value: String(point.count) },
                  { label: "Volume", value: money(point.amount, "USD") },
                ]}
              />
            );
          }}
        />
        <Bar dataKey="count" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function StatusChart({ data }: { data: DashboardStats["payment_status"] }) {
  const total = data.reduce((sum, d) => sum + d.count, 0) || 1;
  return (
    <ul className="space-y-2.5">
      {data.map((d) => {
        const tone = toneFor(d.status);
        return (
          <li key={d.status}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="font-mono text-muted">{d.status}</span>
              <span className="font-mono text-foreground tabular">
                {d.count} <span className="text-subtle">· {Math.round((d.count / total) * 100)}%</span>
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-panel-2">
              <div
                className="h-1.5 rounded-full"
                style={{ width: `${Math.max((d.count / total) * 100, 1.5)}%`, background: STATUS_FILL[tone] }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function IncidentTypeChart({ data }: { data: DashboardStats["incident_types"] }) {
  const rows = data.map((d) => ({ ...d, label: humanize(d.type) }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(rows.length * 30 + 10, 120)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 28, bottom: 0, left: 0 }} barCategoryGap={4}>
        <XAxis type="number" hide allowDecimals={false} />
        <YAxis type="category" dataKey="label" {...AXIS} width={130} tick={{ fill: "var(--muted)", fontSize: 11 }} />
        <Tooltip
          cursor={{ fill: "var(--panel-2)" }}
          content={({ active, payload }: TipProps) =>
            active && payload?.length ? (
              <TooltipBox title={String(payload[0].payload.label)} rows={[{ label: "Incidents", value: String(payload[0].value) }]} />
            ) : null
          }
        />
        <Bar
          dataKey="count"
          fill="var(--series-1)"
          radius={[0, 4, 4, 0]}
          maxBarSize={16}
          label={{ position: "right", fill: "var(--muted)", fontSize: 11 }}
        >
          {rows.map((r) => (
            <Cell key={r.type} fill="var(--series-1)" />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function RecoveryChart({ data }: { data: DashboardStats["recovery_trend"] }) {
  const series = [
    { key: "detected", label: "Detected", color: "var(--series-1)" },
    { key: "resolved", label: "Resolved", color: "var(--series-2)" },
  ] as const;
  return (
    <div>
      <div className="mb-2 flex gap-4 text-[11px] text-muted">
        {series.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={196}>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" minTickGap={16} />
          <YAxis {...AXIS} allowDecimals={false} width={44} />
          <Tooltip
            cursor={{ stroke: "var(--border-strong)" }}
            content={({ active, payload, label }: TipProps) =>
              active && payload?.length ? (
                <TooltipBox
                  title={String(label)}
                  rows={series.map((s) => ({
                    label: s.label,
                    value: String(payload.find((p) => p.dataKey === s.key)?.value ?? 0),
                    color: s.color,
                  }))}
                />
              ) : null
            }
          />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--panel)" }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
