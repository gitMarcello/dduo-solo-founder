import type {
  ActivityEvent,
  BackupRecord,
  BackupStatus,
  ContextEventDetail,
  Health,
  Memory,
  MemoryProvenance,
  MemoryStatus,
  ObservabilityEventsPage,
  ObservabilityRange,
  ObservabilitySummary,
  OperationalManualDraftEnvelope,
  OperationalManualEnvelope,
  Plan,
  PlanCreateInput,
  PlanUpdateInput,
  Project,
  ProjectData,
  SleepJob,
  Sprint,
  SprintClosePreview,
  Task,
  TaskAttachment,
  TaskCreateInput,
  TaskUpdateInput,
  TeamEnvelope,
  TeamInvitationEnvelope,
  TeamOverview,
  WorkPage,
  WorkPlacement,
} from './types';

const API_ROOT = '/api';
const CSRF_STORAGE_PREFIX = 'dduo-solo-founder-csrf:';

function csrfStorageKey(projectId: string): string {
  return `${CSRF_STORAGE_PREFIX}${projectId}`;
}

function projectIdFromPath(path: string): string | null {
  const match = /^\/projects\/([^/]+)(?:\/|$)/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

function csrfToken(projectId: string): string {
  try {
    return localStorage.getItem(csrfStorageKey(projectId))?.trim() ?? '';
  } catch {
    return '';
  }
}

function storeCsrfToken(projectId: string, token: string): void {
  try {
    if (token) localStorage.setItem(csrfStorageKey(projectId), token);
  } catch {
    // A browser can disable storage. Reads remain available; mutations fail
    // closed at the API instead of weakening cross-project request isolation.
  }
}

function clearCsrfToken(projectId: string): void {
  try {
    localStorage.removeItem(csrfStorageKey(projectId));
  } catch {
    // Nothing else can be done when browser storage is unavailable.
  }
}

function requiresCsrf(path: string, init?: RequestInit): boolean {
  const method = (init?.method ?? 'GET').toUpperCase();
  return !['GET', 'HEAD', 'OPTIONS'].includes(method) || path.endsWith('/memories/search');
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  };
  const projectId = projectIdFromPath(path);
  if (projectId && requiresCsrf(path, init)) {
    const token = csrfToken(projectId);
    if (token) headers['X-DDUO-CSRF'] = token;
  }
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: Object.keys(headers).length ? headers : undefined,
  });
  if (!response.ok) {
    if (response.status === 401 && projectId) clearCsrfToken(projectId);
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      payload?.detail ?? `Request failed with status ${response.status}`,
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function attachmentKind(file: File): TaskAttachment['kind'] {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('audio/')) return 'audio';
  if (file.type.startsWith('text/') || file.type === 'application/pdf') return 'document';
  return 'file';
}

async function fileBase64(file: File): Promise<string> {
  if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} exceeds the 10 MiB limit`);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Could not read ${file.name}`));
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
    reader.readAsDataURL(file);
  });
}

function normalizeTask(task: Task, compact = true): Task {
  return {
    ...task,
    description: task.description ?? '',
    dependencies: task.dependencies ?? [],
    attachments: task.attachments ?? [],
    created_at: task.created_at ?? '',
    updated_at: task.updated_at ?? '',
    is_compact: compact,
  };
}

function normalizePlan(plan: Plan, compact = true): Plan {
  return {
    ...plan,
    objective: plan.objective ?? '',
    content: plan.content ?? '',
    labels: plan.labels ?? [],
    work_item_ids: plan.work_item_ids ?? [],
    attachments: plan.attachments ?? [],
    created_at: plan.created_at ?? '',
    updated_at: plan.updated_at ?? '',
    is_compact: compact,
  };
}

function queryString(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  return params.toString();
}

export const api = {
  health: () => request<Health>('/health'),

  renameProject: (projectId: string, name: string, expectedVersion: number) =>
    request<Project>(`/projects/${encodeURIComponent(projectId)}/rename`, {
      method: 'POST',
      body: JSON.stringify({ name, expected_version: expectedVersion }),
    }),

  async exchangeBrowserSession(projectId: string, ticket: string) {
    const response = await request<TeamEnvelope & { authenticated: true; csrf_token: string }>(
      `/projects/${encodeURIComponent(projectId)}/auth/browser-session`,
      { method: 'POST', body: JSON.stringify({ ticket }) },
    );
    storeCsrfToken(projectId, response.csrf_token);
    return response;
  },

  async logoutBrowserSession(projectId: string) {
    try {
      return await request<TeamEnvelope & { logged_out: true }>(
        `/projects/${encodeURIComponent(projectId)}/auth/logout`,
        { method: 'POST' },
      );
    } finally {
      clearCsrfToken(projectId);
    }
  },

  team(projectId: string) {
    return request<TeamOverview>(`/projects/${encodeURIComponent(projectId)}/team`);
  },

  operationalManual(projectId: string) {
    return request<OperationalManualEnvelope>(
      `/projects/${encodeURIComponent(projectId)}/team/manual`,
    );
  },

  createTeamInvitation(
    projectId: string,
    input: { display_name: string; expires_in_hours: number; language?: 'en' | 'it' },
  ) {
    return request<TeamInvitationEnvelope>(
      `/projects/${encodeURIComponent(projectId)}/team/invites`,
      {
        method: 'POST',
        body: JSON.stringify({
          ...input,
          api_url: `${window.location.origin}/api`,
          dashboard_url: window.location.origin,
        }),
      },
    );
  },

  revokeTeamMember(projectId: string, memberId: string) {
    return request<TeamOverview>(
      `/projects/${encodeURIComponent(projectId)}/team/members/${encodeURIComponent(memberId)}`,
      { method: 'DELETE' },
    );
  },

  updateOperationalManual(
    projectId: string,
    input: { content: string; expected_version: number; idempotency_key: string },
  ) {
    return request<OperationalManualEnvelope>(
      `/projects/${encodeURIComponent(projectId)}/team/manual`,
      { method: 'PATCH', body: JSON.stringify(input) },
    );
  },

  createOperationalManualDraft(projectId: string, expectedVersion: number) {
    return request<OperationalManualDraftEnvelope>(
      `/projects/${encodeURIComponent(projectId)}/team/manual/compact-draft`,
      { method: 'POST', body: JSON.stringify({ expected_version: expectedVersion }) },
    );
  },

  openSetup(projectId: string) {
    return request<{ opened: true }>(`/projects/${encodeURIComponent(projectId)}/setup`, {
      method: 'POST',
    });
  },

  async loadProject(projectId: string): Promise<ProjectData> {
    const encoded = encodeURIComponent(projectId);
    const teamRequest = request<TeamOverview>(`/projects/${encoded}/team`);
    const backupRequest = teamRequest.then((team) =>
      team.capabilities.manage_infrastructure
        ? request<BackupStatus>(`/projects/${encoded}/backups`)
        : null,
    );
    const [team, briefing, tasks, plans, activity, memories, memoryStatus, sleepJobs, backup] =
      await Promise.all([
        teamRequest,
        request<{ project: Project }>(`/projects/${encoded}/briefing`),
        request<{ items: Task[]; total?: number }>(
          `/projects/${encoded}/tasks?detail=compact&scope=active&kind=task&limit=50&offset=0`,
        ),
        request<{ items: Plan[] }>(`/projects/${encoded}/plans?detail=compact&limit=50&offset=0`),
        request<{ items: ActivityEvent[] }>(`/projects/${encoded}/activity`),
        request<{ items: Memory[] }>(`/projects/${encoded}/memories?status=active`),
        request<MemoryStatus>(`/projects/${encoded}/memory-status`),
        request<{ items: SleepJob[] }>(`/projects/${encoded}/sleep-jobs?limit=20`),
        backupRequest,
      ]);
    return {
      project: briefing.project,
      team,
      tasks: tasks.items.map((task) => normalizeTask(task)),
      openTaskCount: tasks.total,
      plans: plans.items.map((plan) => normalizePlan(plan)),
      events: activity.items,
      backup,
      memories: memories.items,
      memoryStatus,
      sleepJobs: sleepJobs.items,
    };
  },

  createTask(projectId: string, input: TaskCreateInput) {
    return request<Task>(`/projects/${encodeURIComponent(projectId)}/tasks`, {
      method: 'POST',
      body: JSON.stringify(input),
    });
  },

  async getTask(projectId: string, taskId: string) {
    const result = await request<{ task: Task }>(
      `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}?detail=full`,
    );
    return normalizeTask(result.task, false);
  },

  async getPlan(projectId: string, planId: string) {
    const result = await request<{ plan: Plan }>(
      `/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}`,
    );
    return normalizePlan(result.plan, false);
  },

  async listWork(
    projectId: string,
    options: {
      placement?: WorkPlacement;
      sprint_id?: string;
      scope?: 'active' | 'completed' | 'all';
      kind?: 'task' | 'epic';
      epic_id?: string;
      limit?: number;
      offset?: number;
      q?: string;
    } = {},
  ) {
    const page = await request<WorkPage<Task>>(
      `/projects/${encodeURIComponent(projectId)}/tasks?${queryString({
        detail: 'compact',
        limit: 50,
        offset: 0,
        ...options,
      })}`,
    );
    return { ...page, items: page.items.map((task) => normalizeTask(task)) };
  },

  async listPlans(
    projectId: string,
    options: { limit?: number; offset?: number; q?: string } = {},
  ) {
    const page = await request<WorkPage<Plan>>(
      `/projects/${encodeURIComponent(projectId)}/plans?${queryString({
        detail: 'compact',
        limit: 50,
        offset: 0,
        ...options,
      })}`,
    );
    return { ...page, items: page.items.map((plan) => normalizePlan(plan)) };
  },

  listSprints(
    projectId: string,
    options: { status?: Sprint['status']; limit?: number; offset?: number } = {},
  ) {
    return request<WorkPage<Sprint>>(
      `/projects/${encodeURIComponent(projectId)}/sprints?${queryString({ limit: 50, offset: 0, ...options })}`,
    );
  },

  getSprint(projectId: string, sprintId: string) {
    return request<Sprint>(
      `/projects/${encodeURIComponent(projectId)}/sprints/${encodeURIComponent(sprintId)}`,
    );
  },

  createSprint(
    projectId: string,
    input: { title: string; objective?: string; idempotency_key: string },
  ) {
    return request<Sprint>(`/projects/${encodeURIComponent(projectId)}/sprints`, {
      method: 'POST',
      body: JSON.stringify(input),
    });
  },

  startSprint(projectId: string, sprint: Sprint, idempotencyKey: string) {
    return request<Sprint>(
      `/projects/${encodeURIComponent(projectId)}/sprints/${encodeURIComponent(sprint.id)}/start`,
      {
        method: 'POST',
        body: JSON.stringify({ expected_version: sprint.version, idempotency_key: idempotencyKey }),
      },
    );
  },

  closeSprintPreview(projectId: string, sprintId: string) {
    return request<SprintClosePreview>(
      `/projects/${encodeURIComponent(projectId)}/sprints/${encodeURIComponent(sprintId)}/close-preview`,
    );
  },

  archiveSprint(
    projectId: string,
    sprint: Sprint,
    input: {
      unfinished_destination: 'backlog' | 'sprint';
      destination_sprint_id?: string;
      idempotency_key: string;
    },
  ) {
    return request<Sprint>(
      `/projects/${encodeURIComponent(projectId)}/sprints/${encodeURIComponent(sprint.id)}/archive`,
      {
        method: 'POST',
        body: JSON.stringify({ expected_version: sprint.version, ...input }),
      },
    );
  },

  reopenSprint(projectId: string, sprint: Sprint, idempotencyKey: string) {
    return request<Sprint>(
      `/projects/${encodeURIComponent(projectId)}/sprints/${encodeURIComponent(sprint.id)}/reopen`,
      {
        method: 'POST',
        body: JSON.stringify({ expected_version: sprint.version, idempotency_key: idempotencyKey }),
      },
    );
  },

  async archivedSprintTasks(
    projectId: string,
    sprintId: string,
    options: { limit?: number; offset?: number; q?: string } = {},
  ) {
    const page = await request<WorkPage<Task>>(
      `/projects/${encodeURIComponent(projectId)}/sprints/${encodeURIComponent(sprintId)}/tasks?${queryString({ limit: 50, offset: 0, ...options })}`,
    );
    return { ...page, items: page.items.map((task) => normalizeTask(task)) };
  },

  importSprintHistory(
    projectId: string,
    input: { title: string; task_ids: string[]; idempotency_key: string },
  ) {
    return request<Sprint>(`/projects/${encodeURIComponent(projectId)}/sprints/history`, {
      method: 'POST',
      body: JSON.stringify(input),
    });
  },

  updateTask(projectId: string, task: Task, input: TaskUpdateInput) {
    return request<Task>(
      `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(task.id)}`,
      { method: 'PATCH', body: JSON.stringify({ ...input, expected_version: task.version }) },
    );
  },

  createPlan(projectId: string, input: PlanCreateInput) {
    return request<Plan>(`/projects/${encodeURIComponent(projectId)}/plans`, {
      method: 'POST',
      body: JSON.stringify(input),
    });
  },

  updatePlan(projectId: string, plan: Plan, input: PlanUpdateInput) {
    return request<Plan>(
      `/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(plan.id)}`,
      { method: 'PATCH', body: JSON.stringify({ ...input, expected_version: plan.version }) },
    );
  },

  async uploadTaskAttachment(projectId: string, taskId: string, file: File) {
    const content_base64 = await fileBase64(file);
    return request<TaskAttachment & { created: boolean; linked: boolean }>(
      `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/attachments`,
      {
        method: 'POST',
        body: JSON.stringify({
          kind: attachmentKind(file),
          filename: file.name,
          mime_type: file.type || 'application/octet-stream',
          content_base64,
        }),
      },
    );
  },

  removeTaskAttachment(projectId: string, taskId: string, artifactId: string) {
    return request<void>(
      `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/attachments/${encodeURIComponent(artifactId)}`,
      { method: 'DELETE' },
    );
  },

  taskAttachmentUrl(projectId: string, taskId: string, artifactId: string) {
    return `${API_ROOT}/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/attachments/${encodeURIComponent(artifactId)}/content`;
  },

  async uploadPlanAttachment(projectId: string, planId: string, file: File) {
    const content_base64 = await fileBase64(file);
    return request<TaskAttachment & { created: boolean; linked: boolean }>(
      `/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}/attachments`,
      {
        method: 'POST',
        body: JSON.stringify({
          kind: attachmentKind(file),
          filename: file.name,
          mime_type: file.type || 'application/octet-stream',
          content_base64,
        }),
      },
    );
  },

  removePlanAttachment(projectId: string, planId: string, artifactId: string) {
    return request<void>(
      `/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}/attachments/${encodeURIComponent(artifactId)}`,
      { method: 'DELETE' },
    );
  },

  planAttachmentUrl(projectId: string, planId: string, artifactId: string) {
    return `${API_ROOT}/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}/attachments/${encodeURIComponent(artifactId)}/content`;
  },

  createBackup(projectId: string) {
    return request<BackupRecord>(`/projects/${encodeURIComponent(projectId)}/backups`, {
      method: 'POST',
    });
  },

  backupDownloadUrl(projectId: string, backupId: string) {
    return `${API_ROOT}/projects/${encodeURIComponent(projectId)}/backups/${encodeURIComponent(backupId)}/download`;
  },

  requestSleep(projectId: string) {
    return request<{ scheduled: number; items: SleepJob[] }>(
      `/projects/${encodeURIComponent(projectId)}/sleep`,
      { method: 'POST', body: JSON.stringify({ trigger: 'manual' }) },
    );
  },

  retrySleep(projectId: string, jobId: string) {
    return request<SleepJob>(
      `/projects/${encodeURIComponent(projectId)}/sleep-jobs/${encodeURIComponent(jobId)}/retry`,
      { method: 'POST' },
    );
  },

  explainMemory(projectId: string, memoryId: string) {
    return request<MemoryProvenance>(
      `/projects/${encodeURIComponent(projectId)}/memories/${encodeURIComponent(memoryId)}/provenance`,
    );
  },

  forgetMemory(projectId: string, memoryId: string, rationale: string) {
    return request<{ forgotten: number }>(
      `/projects/${encodeURIComponent(projectId)}/memories/forget`,
      { method: 'POST', body: JSON.stringify({ memory_id: memoryId, rationale }) },
    );
  },

  observabilitySummary(
    projectId: string,
    range: ObservabilityRange,
    timezone: string,
    actor?: string,
  ) {
    const query = new URLSearchParams({ range, timezone });
    if (actor) query.set('actor', actor);
    return request<ObservabilitySummary>(
      `/projects/${encodeURIComponent(projectId)}/observability/summary?${query.toString()}`,
    );
  },

  observabilityEvents(
    projectId: string,
    options: {
      range: ObservabilityRange;
      category?: string;
      status?: string;
      actor?: string;
      cursor?: string;
    },
  ) {
    const query = new URLSearchParams({ range: options.range });
    if (options.category) query.set('category', options.category);
    if (options.status) query.set('status', options.status);
    if (options.actor) query.set('actor', options.actor);
    if (options.cursor) query.set('cursor', options.cursor);
    return request<ObservabilityEventsPage>(
      `/projects/${encodeURIComponent(projectId)}/observability/events?${query.toString()}`,
    );
  },

  contextEventDetail(projectId: string, eventId: string, signal?: AbortSignal) {
    return request<ContextEventDetail>(
      `/projects/${encodeURIComponent(projectId)}/observability/context-events/${encodeURIComponent(eventId)}`,
      { signal },
    );
  },
};
