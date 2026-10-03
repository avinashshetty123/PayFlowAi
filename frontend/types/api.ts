// Mirrors the FastAPI Pydantic response schemas.

export type Snapshot = {
  gateway: string;
  bank: string;
  merchant: string;
  ledger: string;
  webhook: string;
  overall?: string;
};

export type Payment = {
  id: string;
  transaction_id: string;
  customer_id: string;
  amount: number;
  currency: string;
  gateway_status: string;
  bank_status: string;
  merchant_status: string;
  ledger_status: string;
  webhook_status: string;
  overall_status: string;
  source: string;
  scenario: string | null;
  is_simulated: boolean;
  provider: string;
  provider_order_id: string | null;
  provider_capture_id: string | null;
  provider_status: string | null;
  reconciliation_status: string;
  payer: { payer_id?: string; email?: string; name?: string; country?: string } | null;
  inr_equivalent: number | null;
  created_at: string;
  updated_at: string;
};

export type ProviderTransaction = {
  id: string;
  kind: string;
  provider_reference: string | null;
  status: string;
  amount: number | null;
  currency: string | null;
  idempotency_key: string | null;
  debug_id: string | null;
  error: string | null;
  created_at: string;
};

export type WebhookEventRow = {
  id: string;
  provider: string;
  provider_event_id: string;
  event_type: string;
  transaction_id: string | null;
  transmission_id: string | null;
  signature_verified: boolean | null;
  verification_detail: string | null;
  processing_status: string;
  delivery_count: number;
  error: string | null;
  received_at: string;
  processed_at: string | null;
};

export type FailureInjection = {
  id: string;
  scenario: string;
  enabled: boolean;
  injected_by: string;
  created_at: string;
  injected_at: string | null;
  metadata: Record<string, unknown>;
};

export type FailureScenarioInfo = {
  scenario: string;
  label: string;
  stage: string;
  description: string;
  requires_webhooks: boolean;
  before_capture_only: boolean;
  expected_incident: string | null;
  available: boolean;
};

export type FailureCatalog = {
  enabled: boolean;
  label: string;
  note: string;
  webhooks_configured: boolean;
  scenarios: FailureScenarioInfo[];
};

export type LiveEvent = {
  id: string;
  event: string;
  transactionId: string | null;
  incidentId: string | null;
  incidentNumber: string | null;
  timestamp: string;
  data: { auditEvent?: string; actor?: string; reason?: string; result?: Record<string, unknown>; [k: string]: unknown };
  backlog?: boolean;
};

export type CreatedOrder = {
  payment: Payment;
  order_id: string;
  approve_url: string | null;
  armed_failures: string[];
};

export type CaptureResult = { payment: Payment; status: string; message: string; pipeline_mode: string | null };

export type PaymentEvent = {
  id: string;
  source: string;
  event_type: string;
  payload: { label?: string; detail?: string; [key: string]: unknown };
  created_at: string;
};

export type PaymentDetail = {
  payment: Payment;
  events: PaymentEvent[];
  bank_transactions: { id: string; bank_reference: string; amount: number; status: string; settled_at: string | null; created_at: string }[];
  merchant_transactions: { id: string; order_id: string; amount: number; status: string; created_at: string }[];
  ledger_entries: { id: string; amount: number; entry_type: string; status: string; created_at: string; updated_at: string }[];
  incidents: { id: string; incident_number: string; type: string; status: string; severity: string }[];
  provider_transactions: ProviderTransaction[];
  webhook_events: WebhookEventRow[];
  failure_injections: FailureInjection[];
  approve_url: string | null;
};

export type IncidentSummary = {
  id: string;
  incident_number: string;
  transaction_id: string;
  amount: number;
  type: string;
  severity: string;
  status: string;
  root_cause: string | null;
  confidence: number | null;
  recommended_action: string | null;
  requires_human: boolean;
  risk: string | null;
  policy_decision: string | null;
  ai_status: string;
  action_status: string | null;
  created_at: string;
  resolved_at: string | null;
  currency: string;
  provider: string;
  provider_status: string | null;
  failure_source: string | null;
  injected_scenario: string | null;
  risk_score: number | null;
  resolution: string | null;
  acknowledged_by: string | null;
};

export type HistoricalMatch = {
  id: string;
  title: string;
  incident_type: string;
  similarity: number;
  root_cause: string;
  resolution: string;
  severity: string;
  retrieval: string;
  reference: string | null;
  resolution_mode: string | null;
  resolved_in_seconds: number | null;
};

export type Recommendation = {
  impact?: { customerImpact?: string; financialExposure?: string; blastRadius?: string; urgency?: string } | null;
  contributingFactors?: string[];
  remediationPlan?: string[];
  preventiveMeasures?: string[];
  anomalies?: string[];
  confidenceRationale?: string | null;
  incidentType: string;
  rootCause: string;
  confidence: number;
  recommendedAction: string;
  risk: string;
  requiresHuman: boolean;
  evidence: string[];
  summary: string;
};

export type Investigation = {
  id: string;
  incident_id: string;
  model: string;
  used_fallback: boolean;
  summary: string;
  evidence: { facts?: string[]; tool_calls?: string[]; fallback_reason?: string | null; [key: string]: unknown };
  historical_matches: HistoricalMatch[];
  recommendation: Recommendation;
  latency_ms: number | null;
  created_at: string;
};

export type PolicyCheck = { name: string; passed: boolean; detail: string };

export type Verification = {
  status: "PASSED" | "FAILED" | "NOT_APPLICABLE";
  checks: { name: string; expected: string; actual: string; passed: boolean }[];
  snapshot: Snapshot;
};

export type Action = {
  id: string;
  incident_id: string;
  action_type: string;
  idempotency_key: string;
  status: string;
  requested_by: string;
  approved_by: string | null;
  reason: string | null;
  policy: {
    decision?: string;
    label?: string;
    reasons?: string[];
    checks?: PolicyCheck[];
    approval_recheck?: { decision: string; label: string; reasons: string[]; checks: PolicyCheck[] };
    fired_rules?: string[];
    risk_score?: number | null;
    policy_version?: string | null;
    overridden_ai_action?: string | null;
  };
  result: {
    summary?: string;
    changes?: { system: string; from: string; to: string }[];
    verification?: Verification;
    error?: string;
    [key: string]: unknown;
  } | null;
  created_at: string;
  completed_at: string | null;
};

export type IncidentDetail = IncidentSummary & {
  ai_summary: string | null;
  initial_snapshot: Snapshot;
  final_snapshot: Snapshot | null;
  current_snapshot: Snapshot;
  detection_findings: string[];
  payment: Payment;
  investigation: Investigation | null;
  actions: Action[];
  failure_injections: FailureInjection[];
  webhook_events: WebhookEventRow[];
  provider_transactions: ProviderTransaction[];
  risk_factors: RiskBreakdown | null;
  acknowledged_at: string | null;
  resolution_note: string | null;
  agent_trace: { node: string; at: string; outcome: string }[];
};

export type TimelineItem = {
  timestamp: string;
  source: string;
  label: string;
  detail: string | null;
  kind: string;
  tone: "ok" | "warn" | "error" | "info";
};

export type AuditLog = {
  id: string;
  transaction_id: string;
  incident_id: string | null;
  actor: string;
  event: string;
  reason: string;
  evidence: Record<string, unknown>;
  result: Record<string, unknown>;
  created_at: string;
};

export type ApprovalItem = {
  action: Action;
  incident_id: string;
  incident_number: string;
  incident_type: string;
  severity: string;
  transaction_id: string;
  amount: number;
  risk: string | null;
  confidence: number | null;
  root_cause: string | null;
  ai_summary: string | null;
  recommended_action: string | null;
  snapshot: Snapshot;
  currency: string;
  provider: string;
  provider_capture_id: string | null;
};

export type DashboardStats = {
  total_payments: number;
  successful_payments: number;
  failed_payments: number;
  refunded_payments: number;
  total_volume: number;
  active_incidents: number;
  auto_recovered: number;
  human_recovered: number;
  pending_approvals: number;
  amount_recovered: number;
  success_rate: number;
  reconciliation_rate: number;
  paypal_payments: number;
  currency: string;
  payment_volume: { date: string; label: string; count: number; amount: number }[];
  payment_status: { status: string; count: number }[];
  incident_types: { type: string; count: number }[];
  recovery_trend: { date: string; label: string; detected: number; resolved: number }[];
  recent_incidents: IncidentSummary[];
};

export type SimulationResult = {
  payment: Payment;
  related_payment: Payment | null;
  reconciliation: {
    consistent: boolean;
    incident_type: string | null;
    severity: string | null;
    summary: string;
    findings: string[];
    snapshot: Snapshot;
  };
  incident_id: string | null;
  incident_number: string | null;
  incident_status: string | null;
  pipeline_mode: string | null;
};

export type ReconciliationRow = {
  transaction_id: string;
  amount: number;
  currency: string;
  provider: string;
  provider_status: string | null;
  created_at: string;
  snapshot: Snapshot;
  consistent: boolean;
  mismatch_type: string | null;
  summary: string;
  incident_id: string | null;
  incident_number: string | null;
  incident_status: string | null;
};

export type ReconciliationMatrix = {
  summary: { checked: number; consistent: number; mismatched: number; remediated: number };
  rows: ReconciliationRow[];
};

export type Health = {
  status: string;
  paypal: string;
  paypal_environment: string;
  paypal_api: string;
  webhook: string;
  webhook_url: string | null;
  database: string;
  redis: string;
  event_bus: string;
  celery_workers: boolean;
  pipeline_mode: string;
  groq: string;
  ai: string;
  rag: string;
  failure_injection: boolean;
  negative_testing: boolean;
};

export type ActionDecision = {
  action: Action;
  incident_status: string;
  verification: Verification | null;
  deduplicated: boolean;
  message: string;
};


// ---- operations: alerts, policy, agent, audit integrity, reconciliation analytics ----

export type NotificationItem = {
  id: string;
  incident_id: string | null;
  transaction_id: string | null;
  severity: "P1" | "P2" | "P3" | "P4";
  category: string;
  title: string;
  body: string;
  link: string | null;
  status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
  escalation_level: number;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  created_at: string;
  deliveries: { channel: string; target: string; status: string; attempts: number; escalation_level: number; error: string | null; sent_at: string | null }[];
};

export type NotificationList = { items: NotificationItem[]; unread: number; urgent: number };

export type ChannelConfig = {
  channels: { channel: string; label: string; configured: boolean; target: string | null; env: string; severities: string[] }[];
  routes: Record<string, string[]>;
  escalation_minutes: number;
};

export type PolicyRule = { id: string; name: string; effect: string; description: string };

export type PolicyCatalog = {
  version: string;
  rules: PolicyRule[];
  playbook: Record<string, string[]>;
  thresholds: Record<string, number>;
  kill_switch: { enabled: boolean; reason: string | null; updated_by: string | null; updated_at: string | null };
  circuit_breaker: { count: number; limit: number; window_minutes: number; tripped: boolean };
};

export type RiskBreakdown = {
  score: number;
  band: string;
  threshold: number;
  factors: { name: string; points: number; detail: string }[];
};

export type PolicySimulation = {
  transaction_id: string;
  incident_number: string;
  incident_type: string;
  evaluation: {
    action_type: string;
    decision: string;
    label: string;
    reasons: string[];
    checks: PolicyCheck[];
    fired_rules: string[];
    risk_score: number | null;
    policy_version: string | null;
  };
  risk: RiskBreakdown;
};

export type AgentGraph = {
  engine: string;
  nodes: { id: string; label: string; kind: string }[];
  edges: { from: string; to: string; label?: string }[];
  mermaid: string | null;
};

export type AuditIntegrity = {
  verified: boolean;
  sealed: number;
  unsealed: number;
  checked: number;
  broken_at: { chain_index: number; audit_id: string; event: string; transaction_id: string } | null;
  head: string;
};

export type ReconSummary = {
  open_breaks: number;
  exposure_by_currency: Record<string, number>;
  exposure_by_type: { type: string; count: number; exposure: number }[];
  aging: { bucket: string; count: number }[];
  sla_breaches: {
    incident_id: string; incident_number: string; transaction_id: string; type: string; severity: string;
    age_minutes: number; sla_minutes: number; status: string; exposure: number; currency: string;
  }[];
  sla_policy: Record<string, number>;
  match_rate: number;
  auto_heal_rate: number;
  mttr_seconds: number | null;
  resolved_incidents: number;
  runs_last_24h: number;
};
