import type {
  ActionDecision,
  CaptureResult,
  CreatedOrder,
  FailureCatalog,
  ApprovalItem,
  AuditLog,
  DashboardStats,
  Health,
  IncidentDetail,
  IncidentSummary,
  Payment,
  PaymentDetail,
  ReconciliationMatrix,
  SimulationResult,
  TimelineItem,
} from "@/types/api";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Cannot reach PayFlow API at ${API_URL}. Is the backend running?`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  health: () => request<Health>("/api/health"),
  dashboard: () => request<DashboardStats>("/api/dashboard/stats"),

  payments: (p: { limit?: number; offset?: number; status?: string; search?: string; provider?: string } = {}) =>
    request<{ items: Payment[]; total: number }>(`/api/payments${qs(p)}`),
  payment: (transactionId: string) => request<PaymentDetail>(`/api/payments/${encodeURIComponent(transactionId)}`),

  liveDemo: (body: {
    demo: string;
    amount: number;
    failure_scenario?: string | null;
    verification_timeout?: boolean;
    negative_test?: string | null;
  }) => request<CreatedOrder>("/api/demo/live", { method: "POST", body: JSON.stringify(body) }),
  capture: (body: { transaction_id?: string; order_id?: string }) =>
    request<CaptureResult>("/api/payments/paypal/capture", { method: "POST", body: JSON.stringify(body) }),
  refundRequest: (transactionId: string, reason: string) =>
    request<{ ok: boolean; requested: boolean; message: string }>(
      `/api/payments/${encodeURIComponent(transactionId)}/refund-request`,
      { method: "POST", body: JSON.stringify({ reason, requested_by: "ops-console" }) },
    ),
  failureScenarios: () => request<FailureCatalog>("/api/failures/scenarios"),
  injectFailure: (transactionId: string, scenario: string) =>
    request<{ ok: boolean; state: string; message?: string }>("/api/failures/inject", {
      method: "POST",
      body: JSON.stringify({ transaction_id: transactionId, scenario, injected_by: "ops-console" }),
    }),

  incidents: (p: { status?: string; active?: boolean; limit?: number } = {}) =>
    request<{ items: IncidentSummary[]; total: number }>(`/api/incidents${qs(p)}`),
  incident: (id: string) => request<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}`),
  timeline: (id: string) => request<TimelineItem[]>(`/api/incidents/${encodeURIComponent(id)}/timeline`),
  incidentAudit: (id: string) => request<AuditLog[]>(`/api/incidents/${encodeURIComponent(id)}/audit`),
  investigate: (id: string) =>
    request<{ status: string; pipeline_mode: string; message: string }>(
      `/api/incidents/${encodeURIComponent(id)}/investigate`,
      { method: "POST" },
    ),

  approvals: (view: "pending" | "decided" = "pending") => request<ApprovalItem[]>(`/api/actions${qs({ view })}`),
  approve: (actionId: string, approver: string, note?: string) =>
    request<ActionDecision>(`/api/actions/${actionId}/approve`, {
      method: "POST",
      body: JSON.stringify({ approver, note }),
    }),
  reject: (actionId: string, approver: string, reason: string) =>
    request<ActionDecision>(`/api/actions/${actionId}/reject`, {
      method: "POST",
      body: JSON.stringify({ approver, reason }),
    }),

  audit: (p: { limit?: number; offset?: number; event?: string; search?: string } = {}) =>
    request<{ items: AuditLog[]; total: number }>(`/api/audit${qs(p)}`),

  reconciliation: () => request<ReconciliationMatrix>("/api/reconciliation"),
  runReconciliation: () =>
    request<{ checked: number; new_incidents: { id: string; incident_number: string; type: string }[] }>(
      "/api/reconciliation/run",
      { method: "POST" },
    ),

  resetDemo: () => request<{ ok: boolean; payments: number; incidents: number; seconds: number }>("/api/demo/reset", { method: "POST" }),

  simulatorScenarios: () => request<{ scenario: string; description: string; expects_incident: boolean; systems: Record<string, string> }[]>("/api/simulator/scenarios"),
  simulate: (body: { scenario: string; amount: number; customer_id?: string; sync?: boolean }) =>
    request<SimulationResult>("/api/simulator/payments", { method: "POST", body: JSON.stringify(body) }),
};
