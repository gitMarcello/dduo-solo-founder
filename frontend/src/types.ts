export type Health = {
  status: 'ok';
  embedding_provider: string;
  embedding_model: string;
};

export type Project = {
  id: string;
  name: string;
  cause: string;
  principles: string[];
  objectives: string[];
  profile_version: number;
};

export type TeamCapability = 'infrastructure_manager' | 'project_member';

export type TeamDevice = {
  id: string;
  device_id: string;
  device_label: string;
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
};

export type TeamMember = {
  id: string;
  project_id: string;
  display_name: string;
  capability: TeamCapability;
  capability_label: string;
  status: 'active' | 'revoked';
  created_at: string;
  updated_at: string;
  devices?: TeamDevice[];
  trusted_local?: boolean;
  access_token_id?: string | null;
};

export type TeamCurrentMember = Pick<
  TeamMember,
  'id' | 'project_id' | 'display_name' | 'capability' | 'capability_label'
> & {
  trusted_local: boolean;
  access_token_id?: string | null;
};

export type TeamCapabilities = {
  manage_infrastructure: boolean;
  participate_in_project: boolean;
};

export type TeamEnvelope = {
  current_member: TeamCurrentMember | null;
  capabilities: TeamCapabilities;
};

export type TeamOverview = TeamEnvelope & {
  members: TeamMember[];
};

export type OperationalManual = {
  content: string;
  version: number;
  updated_by_member_id?: string | null;
  updated_at?: string | null;
  characters: number;
  soft_limit_characters: number;
  hard_limit_characters: number;
  warnings: string[];
};

export type OperationalManualEnvelope = TeamEnvelope & {
  manual: OperationalManual;
  idempotent?: boolean;
};

export type OperationalManualDraft = {
  draft: string;
  based_on_version: number;
  source: 'provider' | 'deterministic_fallback';
  characters: number;
  warnings: string[];
  persisted: false;
};

export type OperationalManualDraftEnvelope = TeamEnvelope & {
  draft: OperationalManualDraft;
};

export type TeamInvitation = {
  id: string;
  display_name: string;
  invitation_code?: string;
  invite_payload: string;
  setup_prompt: string;
  release_version?: string;
  expires_at: string;
  consumed_at?: string | null;
};

export type TeamInvitationEnvelope = TeamEnvelope & {
  invitation: TeamInvitation;
};

export type TaskStatus = 'todo' | 'in_progress' | 'blocked' | 'done' | 'cancelled';
export type TaskPriority = 'low' | 'medium' | 'high' | 'critical';
export type TaskKind = 'task' | 'epic';
export type PlanStatus = 'draft' | 'decided' | 'executing' | 'completed' | 'superseded';

export type Sprint = {
  id: string;
  project_id: string;
  title: string;
  objective: string;
  status: 'planned' | 'active' | 'archived';
  version: number;
  started_at: string | null;
  archived_at: string | null;
  archive_version?: number | null;
  created_at: string;
  updated_at: string;
};

export type WorkPlacement = 'current' | 'backlog' | 'archive' | 'all';
export type WorkPage<T> = { items: T[]; total: number; limit: number; offset: number };
export type SprintClosePreview = {
  sprint: Sprint;
  unfinished_count: number;
  completed_count: number;
  total: number;
};

export type TaskAttachment = {
  id: string;
  content_hash: string;
  kind: 'file' | 'image' | 'document' | 'url' | 'audio' | 'other';
  filename: string;
  mime_type: string;
  size_bytes: number;
  source_uri: string;
  summary: string;
  created_at: string;
};

export type Task = {
  id: string;
  kind: TaskKind;
  epic_id?: string | null;
  sprint_id?: string | null;
  is_compact?: boolean;
  task_counts?: { total: number; completed: number; open: number };
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  labels: string[];
  objective?: string | null;
  next_action?: string;
  due_at?: string | null;
  dependencies: string[];
  rationale?: string | null;
  completion_evidence?: string | null;
  attachments: TaskAttachment[];
  version: number;
  created_at: string;
  updated_at: string;
};

export type Plan = {
  id: string;
  is_compact?: boolean;
  title: string;
  objective: string;
  content: string;
  status: PlanStatus;
  labels: string[];
  work_item_ids: string[];
  attachments: TaskAttachment[];
  version: number;
  created_at: string;
  updated_at: string;
};

export type TaskCreateInput = {
  kind: TaskKind;
  epic_id?: string | null;
  sprint_id?: string | null;
  title: string;
  description?: string;
  status?: TaskStatus;
  priority: TaskPriority;
  labels: string[];
  objective?: string | null;
  next_action?: string | null;
  due_at?: string | null;
};

export type TaskUpdateInput = Partial<Omit<TaskCreateInput, 'title'>> & {
  title?: string;
  completion_evidence?: string | null;
};

export type PlanCreateInput = {
  title: string;
  objective?: string;
  content?: string;
  status?: PlanStatus;
  labels?: string[];
  work_item_ids?: string[];
};

export type PlanUpdateInput = Partial<PlanCreateInput>;

export type ActivityEvent = {
  id: string;
  kind: string;
  summary: string;
  actor: string;
  created_at: string;
};

export type Memory = {
  id: string;
  node_type: 'episode' | 'reusable_fact' | 'heuristic';
  node_key: string;
  text: string;
  status: 'active' | 'superseded' | 'inactive';
  memory_group_id: string;
  revision: number;
  source_turn_ids: string[];
  source_artifact_ids: string[];
  updated_at: string;
};

export type SleepJob = {
  id: string;
  provider: 'codex' | 'claude';
  trigger: string;
  status: 'pending' | 'running' | 'waiting' | 'completed' | 'cancelled';
  input_turn_ids: string[];
  attempts: number;
  error_kind?:
    | 'auth_required'
    | 'rate_limited'
    | 'bridge_unavailable'
    | 'dependency_unavailable'
    | 'invalid_model_output';
  retry_at?: string | null;
  message?: string;
  result: Record<string, number>;
  created_at: string;
  completed_at?: string;
};

export type MemoryProviderStatus = {
  available: boolean;
  state: 'updated' | 'updating' | 'waiting' | 'limited' | 'connection_required';
  summary: string;
  retry_at?: string | null;
  provider?: 'codex' | 'claude' | null;
  error_kind?: SleepJob['error_kind'];
  jobs: Record<string, number>;
};

export type MemoryStatus = MemoryProviderStatus & {
  memories: Record<string, number>;
  latest_job: SleepJob | null;
  providers?: Record<'codex' | 'claude', MemoryProviderStatus>;
};

export type MemoryProvenance = {
  memory: Memory;
  revisions: Memory[];
  sources: Array<{
    turn_id: string;
    user_prompt: string;
    assistant_response: string;
    created_at: string;
  }>;
  source_messages?: Array<{
    message_id: string;
    turn_id: string;
    event_type: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
  source_artifacts?: Array<{
    artifact_id: string;
    kind: string;
    filename: string;
    mime_type: string;
    source_uri: string;
    summary: string;
    content_hash: string;
    created_at: string;
  }>;
};

export type BackupRecord = {
  id: string;
  trigger: 'manual' | 'automatic' | 'update' | 'uninstall' | 'restore';
  status: 'scheduled' | 'running' | 'verified' | 'failed';
  archive_name?: string;
  size_bytes?: number;
  includes_qdrant: boolean;
  retained: boolean;
  error?: string;
  created_at: string;
  completed_at?: string;
  verified_at?: string;
};

export type BackupStatus = {
  configured: boolean;
  configuration_error: string;
  dirty: boolean;
  automatic_due: boolean;
  last_backup_at?: string;
  include_qdrant: boolean;
  qdrant_collection: string;
  retention: { daily: number; weekly: number; monthly: number };
  latest: BackupRecord | null;
  latest_verified: BackupRecord | null;
  items: BackupRecord[];
};

export type ProjectData = {
  project: Project;
  team: TeamOverview;
  tasks: Task[];
  openTaskCount?: number;
  plans: Plan[];
  events: ActivityEvent[];
  memories: Memory[];
  memoryStatus: MemoryStatus;
  sleepJobs: SleepJob[];
  backup: BackupStatus | null;
};

export type ObservabilityRange = '24h' | '7d' | '30d' | 'all';
export type MeasurementSource = 'provider_reported' | 'local_estimate' | 'unavailable';
export type MeasurementCoverage = {
  reported: number;
  estimated: number;
  unavailable: number;
};

export type ObservabilityEvent = {
  id: string;
  session_id?: string | null;
  turn_id?: string | null;
  retrieval_run_id?: string | null;
  actor_member_id?: string | null;
  actor_member?: TeamMember | null;
  category: string;
  operation: string;
  scope: string;
  status: string;
  provider?: string | null;
  model?: string | null;
  attempt: number;
  measurement_source: MeasurementSource;
  input_tokens?: number | null;
  cached_input_tokens?: number | null;
  cache_write_input_tokens?: number | null;
  output_tokens?: number | null;
  characters?: number | null;
  utf8_bytes?: number | null;
  duration_ms?: number | null;
  cost_usd?: string | null;
  details: Record<string, string | number | boolean>;
  has_content?: boolean;
  occurred_at: string;
};

export type ContextComponent = {
  name: string;
  utf8_bytes: number;
  estimated_tokens: number;
  item_count?: number | null;
  candidate_item_count?: number | null;
  partial_item_count?: number;
  omitted_item_count?: number;
  references?: string[];
  omitted_references?: string[];
};

export type DeliveredMemory = {
  id: string;
  node_type: Memory['node_type'];
  node_key: string;
  text: string;
  revision: number;
  score?: number | null;
};

export type ContextEventDetail = {
  event: ObservabilityEvent;
  content: string;
  content_sha256: string;
  producer_version: string;
  render_version: string;
  estimator_version: string;
  captured_at: string;
  components: ContextComponent[];
  turn?: {
    id: string;
    user_prompt: string;
    assistant_response: string;
  } | null;
  retrieval?: {
    id: string;
    status: string;
    memories: DeliveredMemory[];
  } | null;
  tool_name?: string | null;
};

export type MetricBreakdown = {
  coverage: MeasurementCoverage;
  operation: string;
  requests: number;
  successes: number;
  failures: number;
  input_tokens_reported: number | null;
  input_tokens_estimated: number | null;
  cost_usd?: string | null;
  equivalent_api_cost_usd?: string | null;
};

export type MetricBlock = {
  coverage: MeasurementCoverage;
  requests: number;
  successes: number;
  failures: number;
  input_tokens_reported: number | null;
  input_tokens_estimated: number | null;
  cached_input_tokens_reported: number | null;
  uncached_input_tokens_reported: number | null;
  cache_write_input_tokens_reported: number | null;
  cache_hit_percent: number | null;
  output_tokens_reported: number | null;
  output_tokens_estimated: number | null;
  duration_p50_ms: number | null;
  duration_p95_ms: number | null;
  by_operation: MetricBreakdown[];
};

export type TimelinePoint = {
  coverage: MeasurementCoverage;
  start: string;
  requests: number;
  input_tokens_reported: number | null;
  input_tokens_estimated: number | null;
  output_tokens_reported: number | null;
  output_tokens_estimated: number | null;
  automatic_estimated_tokens?: number | null;
  requested_estimated_tokens?: number | null;
  cost_usd?: string | null;
  equivalent_api_cost_usd?: string | null;
  pricing_coverage?: PricingCoverage;
};

export type PricingCoverage = {
  priced: number;
  unavailable: number;
};

export type ApiEquivalentMetricBlock = MetricBlock & {
  equivalent_api_cost_usd: string | null;
  equivalent_api_cost_breakdown: {
    uncached_input_usd: string | null;
    cached_input_usd: string | null;
    cache_write_input_usd: string | null;
    output_usd: string | null;
    priced: number;
    unavailable: number;
  };
  pricing_coverage: PricingCoverage;
  by_provider_model: Array<
    MetricBlock & {
      provider: string | null;
      model: string | null;
      equivalent_api_cost_usd: string | null;
      pricing_coverage: PricingCoverage;
    }
  >;
  timeline: TimelinePoint[];
};

export type ObservabilitySummary = {
  period: {
    range: ObservabilityRange;
    from: string | null;
    to: string;
    bucket: 'hour' | 'day' | 'month';
    timezone: string;
  };
  coverage: MeasurementCoverage & {
    collection_started_at: string | null;
    events: number;
  };
  context: {
    coverage: MeasurementCoverage;
    automatic: {
      coverage: MeasurementCoverage;
      injections: number;
      characters: number | null;
      utf8_bytes: number | null;
      estimated_tokens: number | null;
      estimated_tokens_p50?: number | null;
      estimated_tokens_p95?: number | null;
      component_bytes?: Record<string, number>;
      budget: {
        measured_injections: number;
        budget_limit_characters: number | null;
        budgeted_injections: number;
        fallback_injections: number;
        inline_expected_injections: number;
        candidate_characters: number | null;
        candidate_utf8_bytes: number | null;
        candidate_estimated_tokens: number | null;
        avoided_characters: number | null;
        avoided_utf8_bytes: number | null;
        avoided_estimated_tokens: number | null;
        included_items: number | null;
        partial_items: number | null;
        omitted_items: number | null;
        budget_utilization_p50_percent: number | null;
        budget_utilization_p95_percent: number | null;
      };
      delivery: {
        measured_injections: number;
        snapshot_injections: number;
        delta_injections: number;
        fallback_injections: number;
        unknown_injections: number;
        reused_characters: number | null;
        reused_utf8_bytes: number | null;
        reused_estimated_tokens: number | null;
      };
    };
    requested: {
      coverage: MeasurementCoverage;
      results: number;
      characters: number | null;
      utf8_bytes: number | null;
      estimated_tokens: number | null;
    };
    by_operation: MetricBreakdown[];
    timeline: TimelinePoint[];
  };
  agent_usage: ApiEquivalentMetricBlock;
  sleep: ApiEquivalentMetricBlock;
  embeddings: MetricBlock & { cost_usd: string | null; timeline: TimelinePoint[] };
  reliability: MetricBlock & { retrieval: MetricBlock };
};

export type ObservabilityEventsPage = {
  items: ObservabilityEvent[];
  next_cursor: string | null;
};
