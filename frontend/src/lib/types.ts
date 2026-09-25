/** Wire types, mirroring backend/app/schemas. */

export type Effort = 'none' | 'low' | 'medium' | 'high' | 'xhigh' | 'max' | 'ultra';

export type Scope =
  | 'single_line'
  | 'single_function'
  | 'single_file'
  | 'multi_file'
  | 'service'
  | 'multi_service'
  | 'architecture';

export type EvidenceBand = 'none' | 'weak' | 'moderate' | 'strong';

export type TaskFingerprint = {
  schema_version: string;
  task_family: string;
  task_type: string;
  complexity: number;
  ambiguity: number;
  requirements_clarity: number;
  regression_risk: number;
  architecture_reasoning: number;
  database_reasoning: number;
  concurrency_risk: number;
  security_risk: number;
  repository_understanding: number;
  scope: Scope;
  estimated_files: number;
  frontend: boolean;
  backend: boolean;
  database: boolean;
  infrastructure: boolean;
  tests_required: 'low' | 'medium' | 'high';
  confidence: number;
};

export type Configuration = { provider: string; model: string; effort: Effort };

export type ConfigurationScore = {
  configuration: Configuration;
  provider_display: string;
  model_display: string;
  capability_match: number;
  capability_effective: number;
  prior_reliability: number;
  predicted_reliability: number;
  history_shift: number;
  base_burn: number;
  predicted_burn: number;
  burn_breakdown: Record<string, number>;
  expected_debug_cycles: number;
  eligible: boolean;
  evidence_weight: number;
  evidence_band: EvidenceBand;
  observed_success_rate: number | null;
};

export type RoutingDecision = {
  router_version: string;
  registry_version: string;
  provider: string;
  model: string;
  effort: Effort;
  provider_display: string;
  model_display: string;
  confidence: number;
  required_reliability: number;
  predicted_reliability: number;
  predicted_burn: number;
  risk: {
    difficulty: number;
    difficulty_contributions: Record<string, number>;
    required_reliability: number;
    required_reliability_contributions: Record<string, number>;
    dominant_risks: string[];
  };
  explanation: {
    summary: string;
    why: string;
    why_not_lighter: string;
    why_not_stronger: string;
    evidence_note: string;
    reason_codes: string[];
  };
  reason_codes: string[];
  fallback: Configuration | null;
  fallback_display: string | null;
  rejected_lighter: ConfigurationScore | null;
  rejected_stronger: ConfigurationScore | null;
  evaluated: ConfigurationScore[];
  threshold_met: boolean;
  evidence_band: EvidenceBand;
  evidence_weight: number;
};

export type AnalyzerInfo = {
  provider: string | null;
  model: string | null;
  confidence: number | null;
  repaired: boolean;
  duration_ms: number | null;
  source: string;
  fallback_used: boolean;
  note?: string | null;
};

export type TaskResponse = {
  public_task_id: string;
  title: string;
  original_task: string;
  status: string;
  created_at: string;
  fingerprint: TaskFingerprint | null;
  analyzer: AnalyzerInfo | null;
  recommendation: RoutingDecision | null;
  handoff: string | null;
  execution?: Record<string, unknown> | null;
};

export type HandoffResponse = {
  public_task_id: string;
  handoff: string;
  task_only: string;
  provider: string;
  model: string;
  model_id: string;
  effort: string;
};

export type ImportResultResponse = {
  task_id: string;
  execution_id: number;
  task_family: string;
  recommended: string | null;
  actual: string;
  recommendation_followed: boolean;
  status: string;
  first_pass_success: boolean;
  escalated: boolean;
  debug_cycles: number;
  estimated_effective_burn: number | null;
  usage_source: string;
  replaced_previous: boolean;
  learning_summary: string;
  warnings: string[];
  statistics: Record<string, unknown>;
};

export type HistoryRow = {
  public_task_id: string;
  title: string;
  task_family: string | null;
  created_at: string;
  status: string;
  recommended_display: string | null;
  actual_display: string | null;
  outcome: string | null;
  recommendation_followed: boolean | null;
  first_pass_success: boolean | null;
  escalated: boolean | null;
  debug_cycles: number | null;
  predicted_burn: number | null;
  estimated_effective_burn: number | null;
  usage_source: string | null;
  executed_at: string | null;
};

export type HistoryResponse = {
  rows: HistoryRow[];
  total: number;
  limit: number;
  offset: number;
};

export type Analytics = {
  totals: {
    executions: number;
    successes: number;
    partials: number;
    failures: number;
    first_pass_successes: number;
    escalations: number;
    regressions: number;
    recommendation_followed: number;
    measured_usage_reports: number;
    success_rate: number | null;
    first_pass_rate: number | null;
    failure_rate: number | null;
    escalation_rate: number | null;
    recommendation_followed_rate: number | null;
  };
  by_configuration: Array<{
    task_family: string;
    provider_display: string;
    model_display: string;
    effort: string;
    attempts: number;
    successes: number;
    failures: number;
    first_pass_successes: number;
    escalations: number;
    success_rate: number | null;
    smoothed_success_rate: number;
    avg_debug_cycles: number | null;
    avg_effective_burn: number | null;
  }>;
  by_family: Array<{
    task_family: string;
    executions: number;
    successes: number;
    success_rate: number | null;
  }>;
  findings: Array<{
    kind: string;
    task_family: string;
    configuration: string;
    alternative: string | null;
    detail: string;
    evidence: number;
  }>;
  notes: string[];
};

export type PiStatus = {
  status: string;
  available: boolean;
  analyzer?: string;
  url?: string;
  /** What the analyzer is configured to be... */
  configured_provider?: string | null;
  configured_model?: string | null;
  configured_effort?: string | null;
  configured_from?: Record<string, string> | null;
  authenticated?: boolean;
  /** ...and what a request would actually use. They differ when something is wrong. */
  actual_provider?: string | null;
  actual_model?: string | null;
  provider: string | null;
  model: string | null;
  thinking_level?: string | null;
  bridge_version?: string | null;
  authenticated_providers?: string[];
  alternatives?: Array<{ provider: string; models: string[] }>;
  error_code?: string | null;
  env_file?: string | null;
  note?: string | null;
};

export type PiModelOption = {
  id: string;
  name?: string;
  provider: string;
  reasoning?: boolean;
};

/** Pi thinking levels for the CLASSIFIER, distinct from the router's efforts. */
export const THINKING_LEVELS = ['minimal', 'low', 'medium', 'high', 'xhigh'] as const;
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];

export type SettingsResponse = {
  app_version: string;
  router_version: string;
  registry_version: string;
  config_dir: string;
  database_url: string;
  pi_bridge_url: string;
  pi: PiStatus;
  providers: Array<{
    key: string;
    display_name: string;
    enabled: boolean;
    burn_weight: number;
    burn_reference: string;
    models: Array<{
      key: string;
      display_name: string;
      model_id: string;
      enabled: boolean;
      supported_efforts: string[];
      routable_efforts: string[];
      relative_model_burn: number;
    }>;
  }>;
  task_families: Array<{
    key: string;
    label: string;
    required_reliability_adjustment: number;
  }>;
  policy: Record<string, unknown>;
  preferences: {
    analyzer_source: string;
    allow_analyzer_fallback: boolean;
    analyzer_provider: string | null;
    analyzer_model: string | null;
    analyzer_effort: string | null;
  };
};
