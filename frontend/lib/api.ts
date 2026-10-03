import type {
  ActionDecision,
  AgentGraph,
  AuditIntegrity,
  ChannelConfig,
  NotificationList,
  PolicyCatalog,
  PolicySimulation,
  ReconSummary,
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

const LOCAL_API = "http://localhost:8000";
// Hosted backend used when the console runs on Vercel (override with NEXT_PUBLIC_API_URL_PRODUCTION).
const PRODUCTION_API = process.env.NEXT_PUBLIC_API_URL_PRODUCTION ?? "https://payflowai.onrender.com";
const isLocalUrl = (url: string) => /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(url);

/**
 * Local console → local API, hosted console (Vercel) → hosted API (Render), automatically.
 * An explicit NEXT_PUBLIC_API_URL wins only when it matches where the console runs, so a
 * localhost value baked into a Vercel build can never point production at a laptop.
 */
function resolveApiUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
  if (typeof window === "undefined") return explicit ?? LOCAL_API;
  const consoleIsLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  if (consoleIsLocal) return explicit && isLocalUrl(explicit) ? explicit : LOCAL_API;
  return explicit && !isLocalUrl(explicit) ? explicit : PRODUCTION_API.replace(/\/$/, "");
}

export const API_URL = resolveApiUrl();
/** Environment label for the console chrome. Browser-only: call after mount to avoid hydration mismatch. */
export const environmentLabel = () => (isLocalUrl(resolveApiUrl()) ? "Local" : "Production");

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

  // ---- notifications ----
  notifications: (p: { status?: string; severity?: string; limit?: number } = {}) =>
    request<NotificationList>(`/api/notifications${qs(p)}`),
  notificationChannels: () => request<ChannelConfig>("/api/notifications/channels"),
  ackNotification: (id: string, by: string) =>
    request<{ ok: boolean }>(`/api/notifications/${id}/ack`, { method: "POST", body: JSON.stringify({ by }) }),
  ackAllNotifications: (by: string) =>
    request<{ ok: boolean; acknowledged: number }>("/api/notifications/ack-all", { method: "POST", body: JSON.stringify({ by }) }),
  testAlert: (by: string) =>
    request<{ ok: boolean; results: { channel: string; target: string; status: string; error?: string }[]; message?: string }>(
      "/api/notifications/test", { method: "POST", body: JSON.stringify({ by }) }),

  // ---- human resolution ----
  acknowledgeIncident: (id: string, by: string) =>
    request<{ ok: boolean }>(`/api/incidents/${encodeURIComponent(id)}/acknowledge`, { method: "POST", body: JSON.stringify({ by }) }),
  retryIncident: (id: string, by: string, note?: string) =>
    request<{ ok: boolean; incident_status: string; message: string }>(
      `/api/incidents/${encodeURIComponent(id)}/retry`, { method: "POST", body: JSON.stringify({ by, note }) }),
  resolveIncident: (id: string, by: string, note: string, acceptRisk: boolean) =>
    request<{ ok: boolean; incident_status: string; resolution: string }>(
      `/api/incidents/${encodeURIComponent(id)}/resolve`,
      { method: "POST", body: JSON.stringify({ by, note, accept_risk: acceptRisk }) }),
  closeIncident: (id: string, by: string, note: string) =>
    request<{ ok: boolean; incident_status: string }>(
      `/api/incidents/${encodeURIComponent(id)}/close`, { method: "POST", body: JSON.stringify({ by, note }) }),

  // ---- policy & controls ----
  policies: () => request<PolicyCatalog>("/api/policies"),
  setKillSwitch: (enabled: boolean, reason: string, by: string) =>
    request<PolicyCatalog["kill_switch"]>("/api/policies/kill-switch", {
      method: "POST", body: JSON.stringify({ enabled, reason, by }) }),
  simulatePolicy: (body: { transaction_id: string; action?: string; human_approved?: boolean }) =>
    request<PolicySimulation>("/api/policies/simulate", { method: "POST", body: JSON.stringify(body) }),
  agentGraph: () => request<AgentGraph>("/api/agent/graph"),
  auditVerify: () => request<AuditIntegrity>("/api/audit/verify"),
  reconSummary: () => request<ReconSummary>("/api/reconciliation/summary"),

  resetDemo: () => request<{ ok: boolean; payments: number; incidents: number; seconds: number }>("/api/demo/reset", { method: "POST" }),
  clearDb: () => request<{ ok: boolean; message: string }>("/api/demo/clear", { method: "POST" }),

  simulatorScenarios: () => request<{ scenario: string; description: string; expects_incident: boolean; systems: Record<string, string> }[]>("/api/simulator/scenarios"),
  simulate: (body: { scenario: string; amount: number; customer_id?: string; sync?: boolean }) =>
    request<SimulationResult>("/api/simulator/payments", { method: "POST", body: JSON.stringify(body) }),
};
