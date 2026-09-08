import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import {
  ActivityView,
  BackupView,
  ConnectionForm,
  MemoryView,
  ObservabilityView,
  ProjectView,
  SetupView,
  Sidebar,
  TeamView,
} from './components';
import type {
  ObservabilitySummary,
  OperationalManual,
  Plan,
  Project,
  Task,
  TeamOverview,
} from './types';
import { WorkView } from './WorkView';

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
const invitePayload = 'eyJpbnZpdGF0aW9uIjoiY2Fub25pY2FsIn0';
const invitePrompt = `Hai ricevuto un invito dDuo.
https://github.com/gitMarcello/dduo-solo-founder
dduo-solo-founder remote-join --invite-payload ${invitePayload} --project-root .
Non aprire Setup e non avviare Docker locale.
dduo-solo-founder dashboard --tab tasks --project-root .
Apri una nuova chat nella stessa root del repository.`;

const tasks: Task[] = [
  {
    id: 'open',
    kind: 'task',
    title: 'Open task',
    description: '',
    status: 'todo',
    priority: 'medium',
    labels: ['mobile'],
    dependencies: [],
    attachments: [],
    version: 1,
    created_at: '',
    updated_at: '',
  },
  {
    id: 'blocked',
    kind: 'task',
    title: 'Blocked task',
    description: '',
    status: 'blocked',
    priority: 'high',
    labels: ['release'],
    dependencies: [],
    attachments: [],
    version: 1,
    created_at: '',
    updated_at: '',
  },
  {
    id: 'done',
    kind: 'task',
    title: 'Done task',
    description: '',
    status: 'done',
    priority: 'low',
    labels: [],
    dependencies: [],
    attachments: [],
    version: 1,
    created_at: '',
    updated_at: '',
  },
  {
    id: 'cancelled',
    kind: 'task',
    title: 'Cancelled task',
    description: '',
    status: 'cancelled',
    priority: 'low',
    labels: [],
    dependencies: [],
    attachments: [],
    version: 1,
    created_at: '',
    updated_at: '',
  },
];

const epic: Task = {
  ...tasks[0],
  id: 'epic',
  kind: 'epic',
  title: 'Android launch',
  labels: ['release'],
};

const attachedTask: Task = {
  ...tasks[0],
  epic_id: epic.id,
  next_action: 'Run device matrix',
  due_at: '2026-07-20T12:00:00Z',
  attachments: [
    {
      id: 'image',
      content_hash: 'image-hash',
      kind: 'image',
      filename: 'screen.png',
      mime_type: 'image/png',
      size_bytes: 2_000,
      source_uri: '',
      summary: '',
      created_at: '',
    },
    {
      id: 'file',
      content_hash: 'file-hash',
      kind: 'file',
      filename: 'notes.txt',
      mime_type: 'text/plain',
      size_bytes: 2_000_000,
      source_uri: '',
      summary: '',
      created_at: '',
    },
  ],
};

const attachedPlan: Plan = {
  id: 'plan-1',
  title: 'Android release approach',
  objective: 'Choose the safe launch path before execution.',
  content: 'Compare beta evidence, release gates, and rollback options.',
  status: 'decided',
  labels: ['release'],
  work_item_ids: ['epic'],
  attachments: [
    {
      id: 'plan-image',
      content_hash: 'plan-image-hash',
      kind: 'image',
      filename: 'release-flow.png',
      mime_type: 'image/png',
      size_bytes: 1_000,
      source_uri: '',
      summary: '',
      created_at: '',
    },
  ],
  version: 1,
  created_at: '',
  updated_at: '',
};

const reportedCoverage = { reported: 1, estimated: 0, unavailable: 0 };
const estimatedCoverage = { reported: 0, estimated: 1, unavailable: 0 };
const mixedCoverage = { reported: 1, estimated: 1, unavailable: 1 };
const unavailableCoverage = { reported: 0, estimated: 0, unavailable: 1 };
const noCoverage = { reported: 0, estimated: 0, unavailable: 0 };

const observability: ObservabilitySummary = {
  period: { range: '7d', from: null, to: '2026-08-25T10:00:00Z', bucket: 'day', timezone: 'UTC' },
  coverage: {
    collection_started_at: '2026-08-20T10:00:00Z',
    events: 3,
    reported: 1,
    estimated: 2,
    unavailable: 0,
  },
  context: {
    coverage: { reported: 0, estimated: 2, unavailable: 0 },
    automatic: {
      coverage: estimatedCoverage,
      injections: 1,
      characters: 40,
      utf8_bytes: 40,
      estimated_tokens: 10,
      budget: {
        measured_injections: 1,
        budget_limit_characters: 9000,
        budgeted_injections: 0,
        fallback_injections: 0,
        inline_expected_injections: 1,
        candidate_characters: 40,
        candidate_utf8_bytes: 40,
        candidate_estimated_tokens: 10,
        avoided_characters: 0,
        avoided_utf8_bytes: 0,
        avoided_estimated_tokens: 0,
        included_items: 2,
        partial_items: 0,
        omitted_items: 0,
        budget_utilization_p50_percent: 0.4,
        budget_utilization_p95_percent: 0.4,
      },
      delivery: {
        measured_injections: 1,
        snapshot_injections: 0,
        delta_injections: 1,
        fallback_injections: 0,
        unknown_injections: 0,
        reused_characters: 1_200,
        reused_utf8_bytes: 1_200,
        reused_estimated_tokens: 300,
      },
    },
    requested: {
      coverage: estimatedCoverage,
      results: 1,
      characters: 20,
      utf8_bytes: 20,
      estimated_tokens: 5,
    },
    by_operation: [],
    timeline: [
      {
        coverage: { reported: 0, estimated: 2, unavailable: 0 },
        start: '2026-08-25T00:00:00Z',
        requests: 2,
        input_tokens_reported: null,
        input_tokens_estimated: 15,
        output_tokens_reported: null,
        output_tokens_estimated: null,
        automatic_estimated_tokens: 10,
        requested_estimated_tokens: 5,
      },
    ],
  },
  agent_usage: {
    coverage: reportedCoverage,
    requests: 1,
    successes: 1,
    failures: 0,
    input_tokens_reported: 1_000,
    input_tokens_estimated: null,
    cached_input_tokens_reported: 200,
    uncached_input_tokens_reported: 800,
    cache_write_input_tokens_reported: null,
    cache_hit_percent: 20,
    output_tokens_reported: 100,
    output_tokens_estimated: null,
    duration_p50_ms: 2_000,
    duration_p95_ms: 2_000,
    by_operation: [],
    equivalent_api_cost_usd: '0.0123',
    equivalent_api_cost_breakdown: {
      uncached_input_usd: '0.004',
      cached_input_usd: '0.0001',
      cache_write_input_usd: '0.0002',
      output_usd: '0.008',
      priced: 1,
      unavailable: 0,
    },
    pricing_coverage: { priced: 1, unavailable: 0 },
    by_provider_model: [
      {
        provider: 'claude',
        model: null,
        coverage: reportedCoverage,
        requests: 1,
        successes: 1,
        failures: 0,
        input_tokens_reported: 1_000,
        input_tokens_estimated: null,
        cached_input_tokens_reported: 200,
        uncached_input_tokens_reported: 800,
        cache_write_input_tokens_reported: 50,
        cache_hit_percent: 20,
        output_tokens_reported: 100,
        output_tokens_estimated: null,
        duration_p50_ms: 2_000,
        duration_p95_ms: 2_000,
        by_operation: [],
        equivalent_api_cost_usd: '0.0123',
        pricing_coverage: { priced: 1, unavailable: 0 },
      },
    ],
    timeline: [
      {
        coverage: reportedCoverage,
        start: '2026-08-25T00:00:00Z',
        requests: 1,
        input_tokens_reported: 1_000,
        input_tokens_estimated: null,
        output_tokens_reported: 100,
        output_tokens_estimated: null,
        equivalent_api_cost_usd: '0.0123',
        pricing_coverage: { priced: 1, unavailable: 0 },
      },
    ],
  },
  sleep: {
    coverage: mixedCoverage,
    requests: 2,
    successes: 2,
    failures: 0,
    input_tokens_reported: 0,
    input_tokens_estimated: 50,
    cached_input_tokens_reported: 0,
    uncached_input_tokens_reported: 0,
    cache_write_input_tokens_reported: 0,
    cache_hit_percent: null,
    output_tokens_reported: 10,
    output_tokens_estimated: null,
    duration_p50_ms: 100,
    duration_p95_ms: 100,
    equivalent_api_cost_usd: '0.0032',
    equivalent_api_cost_breakdown: {
      uncached_input_usd: '0.001',
      cached_input_usd: null,
      cache_write_input_usd: null,
      output_usd: '0.0022',
      priced: 2,
      unavailable: 0,
    },
    pricing_coverage: { priced: 2, unavailable: 0 },
    by_provider_model: [],
    by_operation: [
      {
        coverage: reportedCoverage,
        operation: 'sleep.topic_segmentation',
        requests: 2,
        successes: 1,
        failures: 0,
        input_tokens_reported: 0,
        input_tokens_estimated: null,
        cost_usd: '0',
      },
      {
        coverage: estimatedCoverage,
        operation: 'sleep.memory_action_planning',
        requests: 1,
        successes: 1,
        failures: 0,
        input_tokens_reported: null,
        input_tokens_estimated: 30,
        cost_usd: '0',
      },
    ],
    timeline: [
      {
        coverage: mixedCoverage,
        start: '2026-08-25T00:00:00Z',
        requests: 1,
        input_tokens_reported: 0,
        input_tokens_estimated: 50,
        output_tokens_reported: 10,
        output_tokens_estimated: null,
        equivalent_api_cost_usd: '0.0032',
        pricing_coverage: { priced: 1, unavailable: 0 },
      },
    ],
  },
  embeddings: {
    coverage: reportedCoverage,
    requests: 2,
    successes: 2,
    failures: 0,
    input_tokens_reported: 100,
    input_tokens_estimated: null,
    cached_input_tokens_reported: 0,
    uncached_input_tokens_reported: 100,
    cache_write_input_tokens_reported: 0,
    cache_hit_percent: 0,
    output_tokens_reported: null,
    output_tokens_estimated: null,
    duration_p50_ms: 40,
    duration_p95_ms: 50,
    by_operation: [],
    cost_usd: '0.000013',
    timeline: [
      {
        coverage: reportedCoverage,
        start: '2026-08-25T00:00:00Z',
        requests: 2,
        input_tokens_reported: 100,
        input_tokens_estimated: null,
        output_tokens_reported: null,
        output_tokens_estimated: null,
        cost_usd: '0.000013',
      },
    ],
  },
  reliability: {
    coverage: mixedCoverage,
    requests: 3,
    successes: 3,
    failures: 0,
    input_tokens_reported: 100,
    input_tokens_estimated: 15,
    cached_input_tokens_reported: 0,
    uncached_input_tokens_reported: 100,
    cache_write_input_tokens_reported: 0,
    cache_hit_percent: 0,
    output_tokens_reported: 10,
    output_tokens_estimated: null,
    duration_p50_ms: 50,
    duration_p95_ms: 100,
    by_operation: [],
    retrieval: {
      coverage: unavailableCoverage,
      requests: 1,
      successes: 1,
      failures: 0,
      input_tokens_reported: null,
      input_tokens_estimated: null,
      cached_input_tokens_reported: null,
      uncached_input_tokens_reported: null,
      cache_write_input_tokens_reported: null,
      cache_hit_percent: null,
      output_tokens_reported: null,
      output_tokens_estimated: null,
      duration_p50_ms: 20,
      duration_p95_ms: 20,
      by_operation: [],
    },
  },
};

describe('components', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    if (originalClipboard) {
      Object.defineProperty(navigator, 'clipboard', originalClipboard);
    } else {
      Reflect.deleteProperty(navigator, 'clipboard');
    }
  });

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('keeps the active mobile navigation label explicit', () => {
    render(
      <Sidebar
        tab="observability"
        openTasks={3}
        memoryState="online"
        canManageInfrastructure={true}
        canOpenLocalSetup={true}
        onTab={vi.fn()}
      />,
    );
    const active = screen.getByRole('button', { name: 'Observability' });
    expect(active).toHaveClass('active');
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(within(active).getByText('Observability')).toHaveClass('nav-label');
  });

  it('renders separated observability metrics and filters recent operations', async () => {
    const user = userEvent.setup();
    const onRange = vi.fn();
    const onFilters = vi.fn();
    render(
      <ObservabilityView
        projectId="p1"
        summary={observability}
        events={[
          {
            id: 'event',
            category: 'embedding',
            operation: 'embedding.manual_search',
            scope: '',
            status: 'success',
            provider: 'openai',
            attempt: 1,
            measurement_source: 'provider_reported',
            input_tokens: 100,
            duration_ms: 20,
            details: {},
            actor_member_id: 'member-1',
            actor_member: {
              id: 'member-1',
              project_id: 'p1',
              display_name: 'Sam',
              capability: 'project_member',
              capability_label: 'Membro del progetto',
              status: 'active',
              created_at: '2026-08-25T09:00:00Z',
              updated_at: '2026-08-25T09:00:00Z',
            },
            occurred_at: '2026-08-25T10:00:00Z',
          },
        ]}
        range="7d"
        onRange={onRange}
        filters={{ category: '', status: '', actor: '' }}
        onFilters={onFilters}
        loading={false}
        eventsLoading={false}
        error=""
        nextCursor={null}
        onLoadMore={vi.fn().mockResolvedValue(undefined)}
        members={[
          {
            id: 'member-1',
            project_id: 'p1',
            display_name: 'Sam',
            capability: 'project_member',
            capability_label: 'Membro del progetto',
            status: 'active',
            created_at: '2026-08-25T09:00:00Z',
            updated_at: '2026-08-25T09:00:00Z',
          },
        ]}
      />,
    );
    expect(screen.getByText('dDuo context emitted')).toBeInTheDocument();
    expect(screen.getByText('Founder brief budget')).toBeInTheDocument();
    const stableReuse = screen.getByText('Stable context reused').parentElement;
    expect(stableReuse).not.toBeNull();
    expect(within(stableReuse as HTMLElement).getByText('300 tokens')).toBeInTheDocument();
    expect(
      within(stableReuse as HTMLElement).getByText('Delta 1 · Snapshot 0 · Fallback 0'),
    ).toBeInTheDocument();
    expect(
      within(stableReuse as HTMLElement).getByText(
        'Already present in the live session · not provider cache',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Interactive agent — API equivalent')).toBeInTheDocument();
    expect(screen.getByText('Observed samples')).toBeInTheDocument();
    expect(screen.queryByText('Observed turns')).not.toBeInTheDocument();
    expect(screen.getByText('User: All users')).toBeInTheDocument();
    expect(screen.getByText('Claude')).toBeInTheDocument();
    expect(screen.getByText('Mixed/unknown')).toBeInTheDocument();
    expect(screen.getByText(/Codex and Claude samples appear/)).toBeInTheDocument();
    expect(screen.getByText('Provider input')).toBeInTheDocument();
    expect(screen.getByText('Uncached input')).toBeInTheDocument();
    expect(screen.getByText('Cache read')).toBeInTheDocument();
    expect(screen.getByText('Cache hit')).toBeInTheDocument();
    expect(screen.getByText('Cache write')).toBeInTheDocument();
    expect(screen.getByText('Output')).toBeInTheDocument();
    expect(screen.getByText('Uncached input cost')).toBeInTheDocument();
    expect(screen.getByText('Cached input cost')).toBeInTheDocument();
    expect(screen.getByText('Cache write cost')).toBeInTheDocument();
    expect(screen.getByText('Output cost')).toBeInTheDocument();
    expect(
      screen.getByText('Cost breakdown coverage: 1 priced · 0 unavailable'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/do not identify the exact text spans served from cache/),
    ).toBeInTheDocument();
    expect(screen.getByText(/may reflect configured pricing/)).toBeInTheDocument();
    expect(screen.getByText(/can reflect configured model pricing/)).toBeInTheDocument();
    expect(
      screen.getByText(/may differ from both the current public list price/),
    ).toBeInTheDocument();
    expect(screen.getByText('Embedding API')).toBeInTheDocument();
    expect(screen.getAllByText('API equivalent estimate')).toHaveLength(2);
    expect(screen.getByText(/subscription access is not billed here/)).toBeInTheDocument();
    expect(screen.getByText('Topic segmentation')).toBeInTheDocument();
    expect(screen.getByText('Memory action planning')).toBeInTheDocument();
    expect(screen.getByText('embedding.manual_search')).toBeInTheDocument();
    expect(screen.getAllByText('Sam').length).toBeGreaterThan(0);
    const sleepSection = screen.getByRole('heading', { name: 'Sleep' }).closest('section');
    expect(sleepSection).not.toBeNull();
    const sleepHeading = sleepSection?.querySelector('.section-heading');
    expect(sleepHeading).not.toBeNull();
    expect(within(sleepHeading as HTMLElement).getByText('Reported')).toBeInTheDocument();
    expect(within(sleepHeading as HTMLElement).getByText('Estimated')).toBeInTheDocument();
    expect(within(sleepHeading as HTMLElement).getByText('Unavailable')).toBeInTheDocument();
    const sleepInput = within(sleepSection as HTMLElement).getByText('Input tokens').parentElement;
    expect(sleepInput).toHaveTextContent('0Reported');
    expect(sleepInput).toHaveTextContent('50Estimated');
    await user.click(screen.getByRole('button', { name: 'All' }));
    expect(onRange).toHaveBeenCalledWith('all');
    await user.selectOptions(screen.getByLabelText('Operation category'), 'embedding');
    expect(onFilters).toHaveBeenCalledWith({ actor: '', category: 'embedding', status: '' });
    await user.selectOptions(screen.getByLabelText('Operation category'), 'agent_usage');
    expect(onFilters).toHaveBeenCalledWith({ actor: '', category: 'agent_usage', status: '' });
    expect(screen.queryByRole('option', { name: /usage guard/i })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Operation status'), 'blocked');
    expect(onFilters).toHaveBeenCalledWith({ actor: '', category: '', status: 'blocked' });
    await user.selectOptions(screen.getByLabelText('Operation user'), 'system');
    expect(onFilters).toHaveBeenCalledWith({ actor: 'system', category: '', status: '' });
  });

  it('keeps a positive sub-microdollar API equivalent visible', () => {
    const summary: ObservabilitySummary = {
      ...observability,
      agent_usage: {
        ...observability.agent_usage,
        equivalent_api_cost_usd: '0.0000004',
        by_provider_model: observability.agent_usage.by_provider_model.map((item) => ({
          ...item,
          equivalent_api_cost_usd: '0.0000004',
        })),
      },
    };
    render(
      <ObservabilityView
        projectId="p1"
        summary={summary}
        events={[]}
        range="7d"
        onRange={vi.fn()}
        filters={{ category: '', status: '' }}
        onFilters={vi.fn()}
        loading={false}
        eventsLoading={false}
        error=""
        nextCursor={null}
        onLoadMore={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByText('<$0.000001')).toBeInTheDocument();
    expect(screen.getByText('<$0.000001 API equivalent')).toBeInTheDocument();
  });

  it('distinguishes measured zero from missing summary and timeline values', () => {
    const summary: ObservabilitySummary = {
      ...observability,
      agent_usage: {
        ...observability.agent_usage,
        coverage: noCoverage,
        requests: 0,
        input_tokens_reported: null,
        input_tokens_estimated: null,
        output_tokens_reported: null,
        output_tokens_estimated: null,
        equivalent_api_cost_usd: null,
        pricing_coverage: { priced: 0, unavailable: 1 },
        by_provider_model: [],
        timeline: [],
      },
      sleep: {
        ...observability.sleep,
        equivalent_api_cost_usd: null,
        pricing_coverage: { priced: 0, unavailable: 2 },
      },
      embeddings: {
        ...observability.embeddings,
        coverage: noCoverage,
        input_tokens_reported: null,
        input_tokens_estimated: null,
        cost_usd: null,
        timeline: [
          {
            ...observability.embeddings.timeline[0],
            coverage: noCoverage,
            input_tokens_reported: null,
            input_tokens_estimated: null,
          },
        ],
      },
    };
    render(
      <ObservabilityView
        projectId="p1"
        summary={summary}
        events={[]}
        range="7d"
        onRange={vi.fn()}
        filters={{ category: '', status: '' }}
        onFilters={vi.fn()}
        loading={false}
        eventsLoading={false}
        error=""
        nextCursor={null}
        onLoadMore={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const sleepSection = screen.getByRole('heading', { name: 'Sleep' }).closest('section');
    const sleepInput = within(sleepSection as HTMLElement).getByText('Input tokens').parentElement;
    expect(sleepInput).toHaveTextContent('0Reported');
    expect(sleepInput).not.toHaveTextContent('—');
    const sleepEquivalent = within(sleepSection as HTMLElement).getByText(
      'API equivalent estimate',
    ).parentElement;
    expect(sleepEquivalent).toHaveTextContent('—');
    expect(sleepEquivalent).toHaveTextContent('Unavailable');

    const agentSection = screen
      .getByRole('heading', { name: 'Interactive agent — API equivalent' })
      .closest('section');
    const agentEquivalent = within(agentSection as HTMLElement).getByText(
      'API equivalent estimate',
    ).parentElement;
    expect(agentEquivalent).toHaveTextContent('—');
    expect(agentEquivalent).toHaveTextContent('Unavailable');
    expect(agentSection).toHaveTextContent('No interactive provider/model usage in this period');

    const embeddingSection = screen
      .getByRole('heading', { name: 'Embedding API' })
      .closest('section');
    const embeddingInput = within(embeddingSection as HTMLElement).getByText(
      'Input tokens',
    ).parentElement;
    const embeddingCost = within(embeddingSection as HTMLElement).getByText(
      'Estimated attributable cost',
    ).parentElement;
    expect(embeddingInput).toHaveTextContent('—');
    expect(
      within(embeddingSection?.querySelector('.section-heading') as HTMLElement).getByText(
        'No data',
      ),
    ).toBeInTheDocument();
    expect(embeddingCost).toHaveTextContent('—');
    expect(
      within(embeddingSection as HTMLElement).getByText('No measurements in this period'),
    ).toBeInTheDocument();
  });

  it('loads exact emitted context lazily and keeps turn text outside usage', async () => {
    const user = userEvent.setup();
    const exactContent =
      '  Private turn context follows\n<script>alert("no")</script>\nDecisione è';
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const detail = vi.spyOn(api, 'contextEventDetail').mockResolvedValue({
      event: {
        id: 'context-event',
        session_id: 'session-1',
        turn_id: 'turn-1',
        retrieval_run_id: 'retrieval-1',
        category: 'context',
        operation: 'context.turn_injection',
        scope: 'automatic',
        status: 'success',
        provider: 'codex',
        attempt: 1,
        measurement_source: 'local_estimate',
        input_tokens: 12,
        characters: 46,
        utf8_bytes: 48,
        details: {
          component_memories: 24,
          budget_limit_characters: 9000,
          client_character_units: 46,
          candidate_estimated_tokens: 30,
          included_items: 1,
          partial_items: 1,
          omitted_items: 1,
          budget_outcome: 'budgeted',
          delivery_expectation: 'inline_expected',
        },
        has_content: true,
        occurred_at: '2026-08-25T10:00:00Z',
      },
      content: exactContent,
      content_sha256: 'a'.repeat(64),
      producer_version: '0.1.0-alpha.43',
      render_version: 'hook-context-v1',
      estimator_version: 'utf8_bytes_div_4_v1',
      captured_at: '2026-08-25T10:00:00Z',
      components: [
        {
          name: 'memories',
          utf8_bytes: 24,
          estimated_tokens: 6,
          item_count: 1,
          candidate_item_count: 2,
          partial_item_count: 1,
          omitted_item_count: 1,
          references: ['memory-1'],
          omitted_references: ['memory-2'],
        },
      ],
      turn: {
        id: 'turn-1',
        user_prompt: 'Quale decisione avevamo preso?',
        assistant_response: 'Abbiamo mantenuto la decisione.',
      },
      retrieval: {
        id: 'retrieval-1',
        status: 'context_ready',
        memories: [
          {
            id: 'memory-1',
            node_type: 'reusable_fact',
            node_key: 'decision',
            text: 'La decisione consolidata.',
            revision: 2,
            score: 0.912,
          },
        ],
      },
      tool_name: null,
    });
    render(
      <div className="shell">
        <ObservabilityView
          projectId="p1"
          summary={{
            ...observability,
            context: {
              ...observability.context,
              automatic: {
                ...observability.context.automatic,
                estimated_tokens_p50: 12,
                estimated_tokens_p95: 18,
                component_bytes: { instructions: 24, memories: 24 },
              },
            },
          }}
          events={[
            {
              id: 'context-event',
              category: 'context',
              operation: 'context.turn_injection',
              scope: 'automatic',
              status: 'success',
              provider: 'codex',
              attempt: 1,
              measurement_source: 'local_estimate',
              input_tokens: 12,
              utf8_bytes: 48,
              details: {},
              has_content: true,
              occurred_at: '2026-08-25T10:00:00Z',
            },
          ]}
          range="7d"
          onRange={vi.fn()}
          filters={{ category: '', status: '' }}
          onFilters={vi.fn()}
          loading={false}
          eventsLoading={false}
          error=""
          nextCursor={null}
          onLoadMore={vi.fn().mockResolvedValue(undefined)}
        />
      </div>,
    );

    expect(detail).not.toHaveBeenCalled();
    expect(screen.getByText('Automatic context by component')).toBeInTheDocument();
    const inspectButton = screen.getByRole('button', { name: 'Inspect emitted context' });
    await user.click(inspectButton);
    const dialog = await screen.findByRole('dialog', { name: 'Context emitted by dDuo' });
    expect(dialog).toBeVisible();
    const shell = document.querySelector('.shell');
    expect(shell).toHaveAttribute('inert');
    expect(shell).toHaveAttribute('aria-hidden', 'true');
    expect(detail).toHaveBeenCalledWith('p1', 'context-event', expect.any(AbortSignal));
    expect(screen.getByText('Quale decisione avevamo preso?')).toBeInTheDocument();
    expect(screen.getByText('La decisione consolidata.')).toBeInTheDocument();
    expect(screen.getByText(/Private turn context follows/)).toBeInTheDocument();
    expect(screen.getByText(/not provider-reported consumption/)).toBeInTheDocument();
    expect(screen.getByText('References: memory-1')).toBeInTheDocument();
    expect(screen.getByText('Founder brief selection')).toBeInTheDocument();
    expect(screen.getByText(/1 emitted of 2 candidates/)).toBeInTheDocument();
    expect(screen.getByText('Omitted references: memory-2')).toBeInTheDocument();
    expect(screen.getByText('utf8_bytes_div_4_v1')).toBeInTheDocument();
    const raw = dialog.querySelector('pre');
    expect(raw).not.toBeNull();
    expect(raw?.textContent).toBe(exactContent);
    expect(raw?.querySelector('script')).toBeNull();
    const closeButton = screen.getByRole('button', { name: 'Close context inspector' });
    await waitFor(() => expect(closeButton).toHaveFocus());
    await user.keyboard('{Shift>}{Tab}{/Shift}');
    expect(screen.getByRole('button', { name: 'Copy' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith(exactContent);
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument();
    await user.tab();
    expect(closeButton).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(shell).not.toHaveAttribute('inert');
    expect(shell).not.toHaveAttribute('aria-hidden');
    await waitFor(() => expect(inspectButton).toHaveFocus());
  });

  it('aborts a pending inspector when the project changes and reports detail failures', async () => {
    const user = userEvent.setup();
    let pendingSignal: AbortSignal | undefined;
    const detail = vi
      .spyOn(api, 'contextEventDetail')
      .mockImplementationOnce((_projectId, _eventId, signal) => {
        pendingSignal = signal;
        return new Promise(() => undefined);
      })
      .mockRejectedValueOnce(new Error('Context payload is no longer available'));
    const events = [
      {
        id: 'context-current',
        category: 'context',
        operation: 'context.turn_injection',
        scope: 'automatic',
        status: 'success',
        provider: 'codex',
        attempt: 1,
        measurement_source: 'local_estimate',
        input_tokens: 12,
        utf8_bytes: 48,
        details: {},
        has_content: true,
        occurred_at: '2026-08-25T10:00:00Z',
      },
      {
        id: 'context-legacy',
        category: 'context',
        operation: 'context.session_start',
        scope: 'automatic',
        status: 'success',
        provider: 'codex',
        attempt: 1,
        measurement_source: 'local_estimate',
        input_tokens: 4,
        utf8_bytes: 16,
        details: {},
        occurred_at: '2026-08-24T10:00:00Z',
      },
    ] satisfies Parameters<typeof ObservabilityView>[0]['events'];
    const common = {
      summary: observability,
      events,
      range: '7d' as const,
      onRange: vi.fn(),
      filters: { category: '', status: '' },
      onFilters: vi.fn(),
      loading: false,
      eventsLoading: false,
      error: '',
      nextCursor: null,
      onLoadMore: vi.fn().mockResolvedValue(undefined),
    };
    const { rerender } = render(<ObservabilityView projectId="p1" {...common} />);

    expect(screen.getByText('Content unavailable for this historical event')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Inspect emitted context' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Loading emitted context');
    rerender(<ObservabilityView projectId="p2" {...common} />);
    await waitFor(() => expect(pendingSignal?.aborted).toBe(true));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Inspect emitted context' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Context payload is no longer available',
    );
    expect(detail).toHaveBeenLastCalledWith('p2', 'context-current', expect.any(AbortSignal));
  });

  it('identifies requested MCP context and reports a rejected clipboard write', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error('Clipboard denied'));
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    vi.spyOn(api, 'contextEventDetail').mockResolvedValue({
      event: {
        id: 'mcp-context',
        session_id: null,
        turn_id: null,
        retrieval_run_id: null,
        category: 'context',
        operation: 'context.mcp_tool_result',
        scope: 'requested',
        status: 'success',
        provider: 'other',
        attempt: 1,
        measurement_source: 'local_estimate',
        input_tokens: 4,
        characters: 13,
        utf8_bytes: 14,
        details: {},
        has_content: true,
        occurred_at: '2026-08-25T10:00:00Z',
      },
      content: '{"result":"è"}',
      content_sha256: 'b'.repeat(64),
      producer_version: '0.1.0-alpha.43',
      render_version: 'mcp-result-v1',
      estimator_version: 'utf8_bytes_div_4_v1',
      captured_at: '2026-08-25T10:00:00Z',
      components: [{ name: 'result', utf8_bytes: 14, estimated_tokens: 4 }],
      turn: null,
      retrieval: null,
      tool_name: 'search_memory',
    });
    render(
      <ObservabilityView
        projectId="p1"
        summary={observability}
        events={[
          {
            id: 'mcp-context',
            category: 'context',
            operation: 'context.mcp_tool_result',
            scope: 'requested',
            status: 'success',
            provider: 'other',
            attempt: 1,
            measurement_source: 'local_estimate',
            input_tokens: 4,
            utf8_bytes: 14,
            details: {},
            has_content: true,
            occurred_at: '2026-08-25T10:00:00Z',
          },
        ]}
        range="7d"
        onRange={vi.fn()}
        filters={{ category: '', status: '' }}
        onFilters={vi.fn()}
        loading={false}
        eventsLoading={false}
        error=""
        nextCursor={null}
        onLoadMore={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Inspect emitted context' }));
    expect(await screen.findByText('search_memory')).toBeInTheDocument();
    expect(
      screen.queryByText('Turn orientation — excluded from dDuo usage'),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Copy' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Copy failed');
  });

  it('renders empty, unavailable, loading, and paginated observability states', async () => {
    const user = userEvent.setup();
    const onFilters = vi.fn();
    const onLoadMore = vi.fn().mockResolvedValue(undefined);
    const sparse: ObservabilitySummary = {
      ...observability,
      coverage: { ...observability.coverage, collection_started_at: null, unavailable: 1 },
      context: { ...observability.context, timeline: [] },
      sleep: {
        ...observability.sleep,
        duration_p50_ms: null,
        duration_p95_ms: null,
        by_operation: [],
        timeline: [],
      },
      embeddings: { ...observability.embeddings, cost_usd: '0', timeline: [] },
      reliability: {
        ...observability.reliability,
        duration_p50_ms: null,
        duration_p95_ms: null,
        retrieval: {
          ...observability.reliability.retrieval,
          duration_p50_ms: null,
          duration_p95_ms: null,
        },
      },
    };
    const { rerender } = render(
      <ObservabilityView
        projectId="p1"
        summary={null}
        events={[]}
        range="7d"
        onRange={vi.fn()}
        filters={{ category: '', status: '' }}
        onFilters={onFilters}
        loading
        eventsLoading={false}
        error="Observability endpoint unavailable"
        nextCursor={null}
        onLoadMore={onLoadMore}
      />,
    );
    expect(screen.getByText('Loading observability…')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Observability endpoint unavailable');
    expect(screen.queryByText('No operations in this period')).not.toBeInTheDocument();

    rerender(
      <ObservabilityView
        projectId="p1"
        summary={sparse}
        events={[
          {
            id: 'unavailable',
            category: 'retrieval',
            operation: 'retrieval.pipeline',
            scope: '',
            status: 'degraded',
            provider: null,
            model: null,
            attempt: 2,
            measurement_source: 'unavailable',
            utf8_bytes: 1_500,
            duration_ms: 1_500,
            details: {},
            occurred_at: '2026-08-25T10:00:00Z',
          },
        ]}
        range="7d"
        onRange={vi.fn()}
        filters={{ category: '', status: '' }}
        onFilters={onFilters}
        loading={false}
        eventsLoading={false}
        error=""
        nextCursor="next-page"
        onLoadMore={onLoadMore}
      />,
    );
    expect(screen.getByText(/Metrics start when this version is installed/)).toBeInTheDocument();
    expect(screen.getAllByText('No measurements in this period')).toHaveLength(3);
    expect(screen.getByText('No sleep passes in this period')).toBeInTheDocument();
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0);
    expect(screen.getByText('retrieval.pipeline')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Operation status'), 'degraded');
    expect(onFilters).toHaveBeenCalledWith({ category: '', status: 'degraded' });
    await user.click(screen.getByRole('button', { name: 'Load more' }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it('opens Board by default and keeps only the useful work views', async () => {
    const user = userEvent.setup();
    render(
      <WorkView
        projectId="p1"
        tasks={tasks}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );
    expect(screen.getByText('Open task')).toBeInTheDocument();
    expect(screen.getByText('Done task')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Work view' })).toHaveValue('board');
    expect(screen.queryByRole('option', { name: 'Focus' })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'list');
    expect(screen.getByText('Done task')).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'epics');
    expect(screen.getByText('No epics yet')).toBeInTheDocument();
  });

  it('opens exact Work deep links and keeps the URL human handoff stable', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '/?project=p1&tab=tasks&ticket=preserved&work=open');
    render(
      <WorkView
        projectId="p1"
        tasks={tasks}
        plans={[attachedPlan]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Open task' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit Task' })).toBeInTheDocument();
    expect(window.location.search).toContain('work=open');
    expect(window.location.search).toContain('ticket=preserved');
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(window.location.search).not.toContain('work=');
    expect(window.location.search).toContain('ticket=preserved');
  });

  it('renders Work Markdown for reading and preserves its source for editing', async () => {
    const user = userEvent.setup();
    const description = `## Implementation

Use the **safe path** and read the [release notes](https://example.com/release).

- Verify iOS
- Verify Android`;
    const markdownTask: Task = {
      ...tasks[0],
      id: 'markdown-task',
      title: 'Markdown task',
      description,
    };
    render(
      <WorkView
        projectId="p1"
        tasks={[markdownTask]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );

    await user.click(screen.getByText('Markdown task'));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveClass('drawer-reading');
    expect(
      within(dialog).getByRole('heading', { name: 'Implementation', level: 2 }),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('safe path')).toHaveProperty('tagName', 'STRONG');
    expect(within(dialog).getByRole('link', { name: 'release notes' })).toHaveAttribute(
      'href',
      'https://example.com/release',
    );
    expect(within(dialog).queryByLabelText('Description')).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole('button', { name: 'Edit Task' }));
    expect(dialog).toHaveClass('drawer-editing');
    expect(within(dialog).getByLabelText('Description')).toHaveValue(description);
  });

  it('keeps keyboard focus inside a Work drawer and restores its opener', async () => {
    const user = userEvent.setup();
    render(
      <WorkView
        projectId="p1"
        tasks={tasks}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );
    const opener = screen.getByText('Open task').closest('button') as HTMLButtonElement;

    await user.click(opener);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveFocus();

    await user.keyboard('{Shift>}{Tab}{/Shift}');
    expect(within(dialog).getByRole('button', { name: 'Edit Task' })).toHaveFocus();

    await user.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(opener).toHaveFocus();
  });

  it('renders Plan Markdown for reading and preserves its source for editing', async () => {
    const user = userEvent.setup();
    const content = `## Decision

Prefer the **smallest safe change**.

1. Review
2. Ship`;
    const markdownPlan: Plan = {
      ...attachedPlan,
      id: 'markdown-plan',
      title: 'Markdown plan',
      content,
      work_item_ids: [tasks[0].id],
    };
    render(
      <WorkView
        projectId="p1"
        tasks={tasks}
        plans={[markdownPlan]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );

    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'plans');
    await user.click(screen.getByText('Markdown plan'));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Decision', level: 2 })).toBeInTheDocument();
    expect(within(dialog).getByText('smallest safe change')).toHaveProperty('tagName', 'STRONG');
    expect(within(dialog).getByText('Open task').closest('.plan-work-option')).toHaveClass(
      'plan-work-option-readonly',
    );

    await user.click(within(dialog).getByRole('button', { name: 'Edit plan' }));
    expect(within(dialog).getByLabelText('Plan content')).toHaveValue(content);
  });

  it.each([
    { item: epic, title: 'Android launch' },
    { item: tasks[2], title: 'Done task' },
    { item: tasks[3], title: 'Cancelled task' },
  ])('opens an exact deep link for $title even outside the active board', ({ item, title }) => {
    window.history.replaceState(null, '', `/?project=p1&tab=tasks&work=${item.id}`);
    render(
      <WorkView
        projectId="p1"
        tasks={[epic, ...tasks]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: title })).toBeInTheDocument();
  });

  it('opens Plan deep links and safely drops an unknown project item', async () => {
    window.history.replaceState(null, '', '/?project=p1&tab=tasks&view=plans&plan=plan-1');
    const { unmount } = render(
      <WorkView
        projectId="p1"
        tasks={tasks}
        plans={[attachedPlan]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Android release approach' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit plan' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Work view' })).toHaveValue('plans');

    unmount();
    window.history.replaceState(null, '', '/?project=p1&tab=tasks&work=another-project-item');
    render(
      <WorkView
        projectId="p1"
        tasks={tasks}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );
    await waitFor(() => expect(window.location.search).not.toContain('work='));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('Open task')).toBeInTheDocument();

    cleanup();
    window.history.replaceState(null, '', '/?project=p1&tab=tasks&work=open&plan=plan-1');
    render(
      <WorkView
        projectId="p1"
        tasks={tasks}
        plans={[attachedPlan]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(window.location.search).not.toContain('work=');
      expect(window.location.search).not.toContain('plan=');
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('creates a labelled epic and uploads its files', async () => {
    const user = userEvent.setup();
    const created = { ...tasks[0], id: 'epic', kind: 'epic' as const, title: 'Launch' };
    const onCreate = vi.fn().mockResolvedValue(created);
    const onUpload = vi.fn().mockResolvedValue(undefined);
    render(
      <WorkView
        projectId="p1"
        tasks={[]}
        onCreate={onCreate}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={onUpload}
        onRemoveAttachment={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'New' }));
    await user.click(screen.getByRole('button', { name: 'New task' }));
    await user.click(screen.getByRole('button', { name: 'Epic' }));
    await user.type(screen.getByLabelText('Title'), 'Launch');
    await user.type(screen.getByLabelText('Add label'), 'investors{Enter}');
    const file = new File(['deck'], 'deck.pdf', { type: 'application/pdf' });
    await user.upload(screen.getByLabelText('Add images or files'), file);
    await user.click(screen.getByRole('button', { name: 'Create epic' }));
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'epic', title: 'Launch', labels: ['investors'] }),
    );
    expect(onUpload).toHaveBeenCalledWith(created, [file]);
  });

  it('moves board cards and edits complete task details with attachments', async () => {
    const onStatus = vi.fn().mockResolvedValue(undefined);
    const updated = { ...attachedTask, title: 'Test Android release' };
    const onUpdate = vi.fn().mockResolvedValue(updated);
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onRemoveAttachment = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(api, 'taskAttachmentUrl').mockReturnValue('/api/attachment');
    render(
      <WorkView
        projectId="p1"
        tasks={[epic, attachedTask, tasks[1], tasks[2], tasks[3]]}
        onCreate={vi.fn()}
        onUpdate={onUpdate}
        onStatus={onStatus}
        onUpload={onUpload}
        onRemoveAttachment={onRemoveAttachment}
      />,
    );

    const card = screen.getByText('Open task').closest('article');
    expect(card).not.toBeNull();
    fireEvent.dragStart(card as HTMLElement);
    fireEvent.dragOver(screen.getByRole('group', { name: 'Done' }));
    fireEvent.drop(screen.getByRole('group', { name: 'Done' }));
    await waitFor(() => expect(onStatus).toHaveBeenCalledWith(attachedTask, 'done'));

    fireEvent.click(screen.getByText('Open task'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByAltText('screen.png')).toHaveAttribute('src', '/api/attachment');
    expect(screen.getByRole('link', { name: /screen.png/ })).not.toHaveAttribute('download');
    expect(screen.getByRole('link', { name: /notes.txt/ })).toHaveAttribute(
      'download',
      'notes.txt',
    );
    expect(screen.getByText('1.9 MB')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Task' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Test Android release' } });
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'in_progress' } });
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'critical' } });
    fireEvent.change(screen.getByLabelText('Epic'), { target: { value: epic.id } });
    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'Run the complete release flow' },
    });
    fireEvent.change(screen.getByLabelText('Next action'), { target: { value: 'Test checkout' } });
    fireEvent.change(screen.getByLabelText('Due'), { target: { value: '2026-07-25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove mobile' }));
    fireEvent.change(screen.getByLabelText('Add label'), { target: { value: 'qa, android' } });
    fireEvent.click(screen.getByTitle('Add label'));
    fireEvent.click(screen.getByTitle('Remove notes.txt'));
    expect(onRemoveAttachment).toHaveBeenCalledWith(attachedTask, 'file');

    const pending = new File(['deck'], 'release.pdf', { type: 'application/pdf' });
    const removable = new File(['preview'], 'preview.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('Add images or files'), {
      target: { files: [pending, removable] },
    });
    fireEvent.click(screen.getByTitle('Remove preview.png'));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(onUpdate).toHaveBeenCalledWith(
        attachedTask,
        expect.objectContaining({
          title: 'Test Android release',
          status: 'in_progress',
          priority: 'critical',
          labels: ['qa', 'android'],
          due_at: expect.stringContaining('2026-07-25'),
        }),
      ),
    );
    expect(onUpload).toHaveBeenCalledWith(updated, [pending]);
  });

  it('edits a versioned Plan, links Work, and manages its attachments', async () => {
    const user = userEvent.setup();
    const updated = {
      ...attachedPlan,
      status: 'executing' as const,
      content: 'The beta gate is clear. Execute the real-user test first.',
      work_item_ids: ['epic', 'open'],
    };
    const onUpdatePlan = vi.fn().mockResolvedValue(updated);
    const onUploadPlan = vi.fn().mockResolvedValue(undefined);
    const onRemovePlanAttachment = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(api, 'planAttachmentUrl').mockReturnValue('/api/plan-attachment');
    render(
      <WorkView
        projectId="p1"
        tasks={[epic, tasks[0]]}
        plans={[attachedPlan]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onCreatePlan={vi.fn()}
        onUpdatePlan={onUpdatePlan}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadPlan={onUploadPlan}
        onRemovePlanAttachment={onRemovePlanAttachment}
      />,
    );

    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'plans');
    expect(screen.getByText('Android release approach')).toBeInTheDocument();
    expect(screen.getByText('Decided')).toBeInTheDocument();
    await user.click(screen.getByText('Android release approach'));
    expect(screen.getByAltText('release-flow.png')).toHaveAttribute('src', '/api/plan-attachment');
    await user.click(screen.getByRole('button', { name: 'Edit plan' }));
    await user.click(screen.getByTitle('Remove release-flow.png'));
    await waitFor(() =>
      expect(onRemovePlanAttachment).toHaveBeenCalledWith(attachedPlan, 'plan-image'),
    );
    await user.clear(screen.getByLabelText('Plan content'));
    await user.type(
      screen.getByLabelText('Plan content'),
      'The beta gate is clear. Execute the real-user test first.',
    );
    await user.selectOptions(screen.getByLabelText('Plan status'), 'executing');
    await user.click(screen.getByRole('checkbox', { name: /Open task/ }));
    await user.type(screen.getByLabelText('Add plan label'), 'ios{Enter}');
    const file = new File(['decision'], 'decision.pdf', { type: 'application/pdf' });
    await user.upload(screen.getByLabelText('Add plan images or files'), file);
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(onUpdatePlan).toHaveBeenCalledWith(
        attachedPlan,
        expect.objectContaining({
          status: 'executing',
          content: 'The beta gate is clear. Execute the real-user test first.',
          labels: ['release', 'ios'],
          work_item_ids: ['epic', 'open'],
        }),
      ),
    );
    expect(onUploadPlan).toHaveBeenCalledWith(updated, [file]);
  });

  it('keeps a plan attachment failure inside the editor', async () => {
    const user = userEvent.setup();
    render(
      <WorkView
        projectId="p1"
        tasks={[epic]}
        plans={[attachedPlan]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onCreatePlan={vi.fn()}
        onUpdatePlan={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onUploadPlan={vi.fn()}
        onRemovePlanAttachment={vi.fn().mockRejectedValue(new Error('Plan file is unavailable'))}
      />,
    );
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'plans');
    await user.click(screen.getByText('Android release approach'));
    await user.click(screen.getByRole('button', { name: 'Edit plan' }));
    await user.click(screen.getByTitle('Remove release-flow.png'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Plan file is unavailable');
  });

  it('surfaces attachment removal failures inside the editor', async () => {
    const user = userEvent.setup();
    render(
      <WorkView
        projectId="p1"
        tasks={[attachedTask]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn().mockRejectedValue(new Error('Could not remove file'))}
      />,
    );
    await user.click(screen.getByText('Open task'));
    await user.click(screen.getByRole('button', { name: 'Edit Task' }));
    await user.click(screen.getByTitle('Remove notes.txt'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not remove file');
  });

  it('closes a newly-created task when a later attachment upload fails', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(tasks[0]);
    const onUpload = vi.fn().mockRejectedValue(new Error('Upload failed'));
    render(
      <WorkView
        projectId="p1"
        tasks={[]}
        onCreate={onCreate}
        onUpdate={vi.fn()}
        onStatus={vi.fn()}
        onUpload={onUpload}
        onRemoveAttachment={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'New' }));
    await user.click(screen.getByRole('button', { name: 'New task' }));
    await user.type(screen.getByLabelText('Title'), 'Open task');
    const file = new File(['notes'], 'notes.txt', { type: 'text/plain' });
    await user.upload(screen.getByLabelText('Add images or files'), file);
    await user.click(screen.getByRole('button', { name: 'Create task' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onUpload).toHaveBeenCalledWith(tasks[0], [file]);
  });

  it('filters labels and search while showing epic progress', async () => {
    const user = userEvent.setup();
    const doneChild = { ...tasks[2], epic_id: epic.id, labels: ['release'] };
    render(
      <WorkView
        projectId="p1"
        tasks={[epic, attachedTask, doneChild, tasks[3]]}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onStatus={vi.fn().mockResolvedValue(undefined)}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'epics');
    expect(screen.getByText('1/2')).toBeInTheDocument();
    expect(screen.getByText('Run device matrix')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Filters' }));
    await user.click(screen.getByRole('checkbox', { name: 'mobile' }));
    expect(screen.getByText('0/1')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'list');
    await user.type(screen.getByPlaceholderText('Search work'), 'does-not-exist');
    expect(screen.getByText('No matching work')).toBeInTheDocument();
    await user.click(screen.getByTitle('Clear search'));
    expect(screen.getByText('Cancelled task')).toBeInTheDocument();
  });

  it('contains editor failures, oversize files, cancellation, and escape', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockRejectedValue('Could not save');
    render(
      <WorkView
        projectId="p1"
        tasks={[]}
        onCreate={onCreate}
        onUpdate={vi.fn()}
        onStatus={vi.fn().mockRejectedValue(new Error('status failed'))}
        onUpload={vi.fn()}
        onRemoveAttachment={vi.fn()}
      />,
    );
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'epics');
    await user.click(screen.getByRole('button', { name: 'New' }));
    await user.click(screen.getByRole('button', { name: 'New epic' }));
    await user.click(screen.getByRole('button', { name: 'Task' }));
    await user.type(screen.getByLabelText('Title'), 'Will fail');
    const oversized = new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'large.bin');
    await user.upload(screen.getByLabelText('Add images or files'), oversized);
    expect(screen.getByRole('alert')).toHaveTextContent('exceeds the 10 MiB limit');
    await user.click(screen.getByRole('button', { name: 'Create task' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'New' }));
    await user.click(screen.getByRole('button', { name: 'New task' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders empty profile and activity states', () => {
    const { rerender } = render(
      <ProjectView
        project={{
          id: 'p',
          name: 'P',
          cause: '',
          principles: [],
          objectives: [],
          profile_version: 1,
        }}
      />,
    );
    expect(screen.getByText('Waiting for onboarding')).toBeInTheDocument();
    expect(screen.getAllByText(/No .* yet/)).toHaveLength(2);
    rerender(<ActivityView events={[]} />);
    expect(screen.getByText('No activity yet')).toBeInTheDocument();
  });

  it('does not connect an empty identifier', async () => {
    const user = userEvent.setup();
    const connect = vi.fn();
    render(<ConnectionForm onConnect={connect} />);
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled();
    await user.keyboard('{Enter}');
    expect(connect).not.toHaveBeenCalled();
  });

  it('renders backup protection, failures, and the unconfigured state', async () => {
    const user = userEvent.setup();
    const onBackup = vi.fn().mockResolvedValue(undefined);
    const onOpenSetup = vi.fn().mockResolvedValue(undefined);
    const status = {
      configured: true,
      configuration_error: '',
      dirty: true,
      automatic_due: true,
      last_backup_at: '2026-07-12T10:00:00Z',
      include_qdrant: true,
      qdrant_collection: 'collection',
      retention: { daily: 7, weekly: 4, monthly: 6 },
      latest: {
        id: 'failed',
        trigger: 'automatic' as const,
        status: 'failed' as const,
        includes_qdrant: false,
        retained: true,
        error: 'Disk unavailable',
        created_at: '2026-07-12T11:00:00Z',
      },
      latest_verified: {
        id: 'verified',
        trigger: 'manual' as const,
        status: 'verified' as const,
        archive_name: 'project.dduobackup',
        size_bytes: 2_000_000,
        includes_qdrant: false,
        retained: true,
        created_at: '2026-07-12T10:00:00Z',
      },
      items: [],
    };
    const { rerender } = render(
      <BackupView
        projectId="p1"
        status={status}
        backingUp={false}
        canOpenLocalSetup={true}
        onBackup={onBackup}
        onOpenSetup={onOpenSetup}
      />,
    );
    expect(screen.getByText('New changes pending')).toBeInTheDocument();
    expect(screen.getByText('Latest backup failed')).toBeInTheDocument();
    expect(screen.getByText('Rebuild on restore')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download archive' })).toHaveAttribute(
      'href',
      '/api/projects/p1/backups/verified/download',
    );
    await user.click(screen.getByRole('button', { name: 'Create now' }));
    expect(onBackup).toHaveBeenCalled();

    rerender(
      <BackupView
        projectId="p1"
        status={{
          ...status,
          configured: false,
          configuration_error: 'No folder',
          latest: null,
        }}
        backingUp={false}
        canOpenLocalSetup={true}
        onBackup={onBackup}
        onOpenSetup={onOpenSetup}
      />,
    );
    expect(screen.getByText('Automatic backup is not enabled')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Enable backup' }));
    expect(onOpenSetup).toHaveBeenCalled();
  });

  it('hands remote backup setup to an authorized chat without opening local Setup', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const onOpenSetup = vi.fn().mockResolvedValue(undefined);
    render(
      <BackupView
        projectId="project-remote"
        status={{
          configured: false,
          configuration_error: 'No folder',
          dirty: true,
          automatic_due: false,
          include_qdrant: true,
          qdrant_collection: 'collection',
          retention: { daily: 7, weekly: 4, monthly: 6 },
          latest: null,
          latest_verified: null,
          items: [],
        }}
        backingUp={false}
        canOpenLocalSetup={false}
        onBackup={vi.fn().mockResolvedValue(undefined)}
        onOpenSetup={onOpenSetup}
      />,
    );

    expect(screen.getByText('Backup must be enabled on the VPS')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Enable backup' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Copy setup request' }));
    expect(onOpenSetup).not.toHaveBeenCalled();
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('project-remote'));
    const request = writeText.mock.calls[0][0];
    expect(request).toContain('Infrastructure manager');
    expect(request).toContain('do not ask me to run terminal commands');
    expect(request).not.toContain('ssh ');
    expect(request).not.toContain('token=');
    expect(screen.getByRole('button', { name: 'Request copied' })).toBeInTheDocument();
  });

  it('surfaces sleep failures and expands memory provenance', async () => {
    const user = userEvent.setup();
    const memory = {
      id: 'm1',
      node_type: 'reusable_fact' as const,
      node_key: 'release-channel',
      text: 'Android is the **next release**.',
      status: 'active' as const,
      memory_group_id: 'g1',
      revision: 2,
      source_turn_ids: ['t1'],
      source_artifact_ids: [],
      updated_at: '2026-07-12T10:00:00Z',
    };
    vi.spyOn(api, 'explainMemory').mockResolvedValue({
      memory,
      revisions: [memory],
      sources: [
        {
          turn_id: 't1',
          user_prompt: 'Release Android next',
          assistant_response: 'Noted',
          created_at: '2026-07-12T10:00:00Z',
        },
      ],
    });
    const onSleep = vi.fn().mockResolvedValue(undefined);
    const onRetry = vi.fn().mockResolvedValue(undefined);
    const onOpenSetup = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryView
        projectId="p1"
        memories={[memory]}
        status={{
          available: false,
          state: 'connection_required',
          summary: 'Connect Claude in Setup to resume memory consolidation.',
          retry_at: null,
          provider: 'claude',
          error_kind: 'auth_required',
          jobs: { waiting: 1 },
          providers: {
            codex: {
              available: true,
              state: 'updated',
              summary: 'Memory is up to date.',
              provider: 'codex',
              jobs: {},
            },
            claude: {
              available: false,
              state: 'connection_required',
              summary: 'Connect Claude in Setup to resume memory consolidation.',
              provider: 'claude',
              error_kind: 'auth_required',
              jobs: { waiting: 1 },
            },
          },
          memories: { active: 1 },
          latest_job: null,
        }}
        jobs={[
          {
            id: 'j1',
            provider: 'claude',
            trigger: 'idle',
            status: 'waiting',
            input_turn_ids: ['t1'],
            attempts: 1,
            error_kind: 'auth_required',
            retry_at: null,
            message: 'Connect Claude in Setup to resume memory consolidation.',
            result: {},
            created_at: '2026-07-12T10:00:00Z',
          },
        ]}
        onSleep={onSleep}
        onRetry={onRetry}
        onOpenSetup={onOpenSetup}
        schedulingSleep={false}
      />,
    );
    const providers = screen.getByLabelText('Memory providers');
    expect(within(providers).getByText('Codex')).toBeInTheDocument();
    expect(within(providers).getByText('Up to date')).toBeInTheDocument();
    expect(within(providers).getByText('Claude')).toBeInTheDocument();
    expect(within(providers).getByText('1 waiting')).toBeInTheDocument();
    expect(screen.getByText('next release')).toHaveProperty('tagName', 'STRONG');
    expect(screen.getByRole('heading', { name: 'Connection required' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Open Setup' }));
    await user.click(screen.getByTitle('Open memory setup'));
    await user.click(screen.getByRole('button', { name: /release-channel/ }));
    expect(onSleep).not.toHaveBeenCalled();
    expect(onRetry).not.toHaveBeenCalled();
    expect(onOpenSetup).toHaveBeenCalledTimes(2);
    expect(await screen.findByText('Release Android next')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /release-channel/ }));
    expect(screen.queryByText('Release Android next')).not.toBeInTheDocument();
  });

  it('opens the separate local Setup surface on demand', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn().mockResolvedValue(undefined);
    render(<SetupView onOpen={onOpen} opening={false} />);
    await user.click(screen.getByRole('button', { name: 'Open setup' }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/managed by the infrastructure manager/i)).toBeInTheDocument();
  });

  it('keeps team roles neutral and requires an explicit manager action for manual drafts', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const project: Project = {
      id: 'project-1',
      name: "TeamApp founder's",
      cause: '',
      principles: [],
      objectives: [],
      profile_version: 1,
    };
    const team: TeamOverview = {
      current_member: {
        id: 'manager-1',
        project_id: 'project-1',
        display_name: 'Alex',
        capability: 'infrastructure_manager',
        capability_label: 'Gestore dell’infrastruttura',
        trusted_local: false,
      },
      capabilities: { manage_infrastructure: true, participate_in_project: true },
      members: [
        {
          id: 'manager-1',
          project_id: 'project-1',
          display_name: 'Alex',
          capability: 'infrastructure_manager',
          capability_label: 'Gestore dell’infrastruttura',
          status: 'active',
          devices: [],
          created_at: '2026-08-28T08:00:00Z',
          updated_at: '2026-08-28T08:00:00Z',
        },
        {
          id: 'member-1',
          project_id: 'project-1',
          display_name: 'Sam',
          capability: 'project_member',
          capability_label: 'Membro del progetto',
          status: 'active',
          devices: [],
          created_at: '2026-08-28T09:00:00Z',
          updated_at: '2026-08-28T09:00:00Z',
        },
      ],
    };
    const manual: OperationalManual = {
      content: '# Release\nDeploy after review.',
      version: 3,
      updated_by_member_id: 'manager-1',
      updated_at: '2026-08-28T10:00:00Z',
      characters: 30,
      soft_limit_characters: 4_000,
      hard_limit_characters: 100_000,
      warnings: [],
    };
    const onInvite = vi.fn().mockResolvedValue(undefined);
    const onRevokeMember = vi.fn().mockResolvedValue(undefined);
    const onSaveManual = vi.fn().mockResolvedValue(undefined);
    const onCreateDraft = vi.fn().mockResolvedValue(undefined);
    render(
      <TeamView
        project={project}
        team={team}
        manual={manual}
        draft={{
          draft: '# Compact release',
          based_on_version: 3,
          source: 'provider',
          characters: 17,
          warnings: [],
          persisted: false,
        }}
        invitation={{
          id: 'invite-1',
          display_name: 'Sam',
          invite_payload: invitePayload,
          setup_prompt: invitePrompt,
          expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        }}
        loading={false}
        saving={false}
        drafting={false}
        inviting={false}
        revokingMemberId=""
        error=""
        onInvite={onInvite}
        onRevokeMember={onRevokeMember}
        onSaveManual={onSaveManual}
        onCreateDraft={onCreateDraft}
        onDraft={vi.fn()}
      />,
    );
    expect(screen.getByText('Project member')).toBeInTheDocument();
    expect(screen.getByText('Updated by Alex')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Release', level: 1 })).toBeInTheDocument();
    expect(screen.queryByText('# Release')).not.toBeInTheDocument();
    expect(onSaveManual).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Accept as new version' }));
    expect(onSaveManual).toHaveBeenCalledWith('# Compact release', 3);

    await user.click(screen.getByRole('button', { name: 'Edit manual' }));
    const editor = screen.getByLabelText('Manual content');
    expect(editor).toHaveValue('# Release\nDeploy after review.');
    await user.clear(editor);
    await user.type(editor, '# Release\nShip after QA.');
    await user.click(screen.getByRole('button', { name: 'Save new version' }));
    expect(onSaveManual).toHaveBeenCalledWith('# Release\nShip after QA.');

    await user.type(screen.getByLabelText('Member name'), 'Nuovo membro');
    await user.selectOptions(screen.getByLabelText('Valid for'), '72');
    await user.click(screen.getByRole('button', { name: 'Create invitation' }));
    expect(onInvite).toHaveBeenCalledWith('Nuovo membro', 72);
    await user.click(screen.getByRole('button', { name: 'Copy prompt' }));
    expect(writeText).toHaveBeenCalledWith(invitePrompt);
    const copiedPrompt = writeText.mock.calls[0][0];
    expect(copiedPrompt).toContain(`--invite-payload ${invitePayload}`);
    expect(copiedPrompt).not.toContain('--project-id');
    expect(copiedPrompt).not.toContain('--name');
    expect(copiedPrompt).not.toContain('--api-url');
    expect(copiedPrompt).not.toContain('--dashboard-url');
    expect(copiedPrompt).not.toContain('--invitation-code');
    expect(copiedPrompt).not.toContain('dduo_inv_');
    expect(copiedPrompt).toContain('--project-root .');
    expect(copiedPrompt).not.toContain('--replace-existing');
    expect(copiedPrompt).toContain('Non aprire Setup e non avviare Docker locale.');
    expect(copiedPrompt).toContain('dduo-solo-founder dashboard --tab tasks --project-root .');
    expect(copiedPrompt).toContain('una nuova chat nella stessa root del repository');
    expect(copiedPrompt).not.toContain('/?project=');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await user.click(screen.getByRole('button', { name: 'Revoke access for Sam' }));
    expect(onRevokeMember).toHaveBeenCalledWith('member-1');
  });

  it('shows a retryable manual read error instead of an endless loading state', async () => {
    const user = userEvent.setup();
    const onRetryManual = vi.fn().mockResolvedValue(undefined);
    render(
      <TeamView
        project={{
          id: 'p1',
          name: 'Shared',
          cause: '',
          principles: [],
          objectives: [],
          profile_version: 1,
        }}
        team={null}
        manual={null}
        draft={null}
        invitation={null}
        loading={false}
        manualLoading={false}
        saving={false}
        drafting={false}
        inviting={false}
        revokingMemberId=""
        error=""
        manualError="Manual unavailable"
        onInvite={vi.fn()}
        onRevokeMember={vi.fn()}
        onSaveManual={vi.fn()}
        onCreateDraft={vi.fn()}
        onDraft={vi.fn()}
        onRetryManual={onRetryManual}
      />,
    );

    expect(screen.queryByText('Loading operational manual…')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Manual unavailable');
    await user.click(screen.getByRole('button', { name: 'Retry manual' }));
    expect(onRetryManual).toHaveBeenCalledTimes(1);
  });

  it('never exposes or copies an expired or consumed invitation prompt', () => {
    const project: Project = {
      id: 'p1',
      name: 'Shared',
      cause: '',
      principles: [],
      objectives: [],
      profile_version: 1,
    };
    const remoteTeam: TeamOverview = {
      current_member: {
        id: 'manager-1',
        project_id: 'p1',
        display_name: 'Alex',
        capability: 'infrastructure_manager',
        capability_label: 'Gestore dell’infrastruttura',
        trusted_local: false,
      },
      capabilities: { manage_infrastructure: true, participate_in_project: true },
      members: [],
    };
    const baseProps: Parameters<typeof TeamView>[0] = {
      project,
      team: remoteTeam,
      manual: {
        content: '',
        version: 0,
        characters: 0,
        soft_limit_characters: 4_000,
        hard_limit_characters: 100_000,
        warnings: ['manual_empty'],
      },
      draft: null,
      invitation: {
        id: 'expired',
        display_name: 'Sam',
        invite_payload: invitePayload,
        setup_prompt: invitePrompt,
        expires_at: '2000-01-01T00:00:00Z',
      },
      loading: false,
      saving: false,
      drafting: false,
      inviting: false,
      revokingMemberId: '',
      error: '',
      onInvite: vi.fn(),
      onRevokeMember: vi.fn(),
      onSaveManual: vi.fn(),
      onCreateDraft: vi.fn(),
      onDraft: vi.fn(),
    };
    const { rerender } = render(<TeamView {...baseProps} />);

    expect(screen.getByText('Invitation expired')).toBeInTheDocument();
    expect(screen.queryByLabelText('Invitation setup prompt')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Prompt unavailable' })).toBeDisabled();

    rerender(
      <TeamView
        {...baseProps}
        invitation={{
          id: 'consumed',
          display_name: 'Sam',
          invite_payload: invitePayload,
          setup_prompt: invitePrompt,
          expires_at: new Date(Date.now() + 86_400_000).toISOString(),
          consumed_at: '2026-08-28T10:00:00Z',
        }}
      />,
    );
    expect(screen.getByText('Invitation already consumed')).toBeInTheDocument();
    expect(screen.queryByLabelText('Invitation setup prompt')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Prompt unavailable' })).toBeDisabled();
  });

  it('requires promotion before a trusted local owner can invite project members', () => {
    render(
      <TeamView
        project={{
          id: 'p1',
          name: 'Local project',
          cause: '',
          principles: [],
          objectives: [],
          profile_version: 1,
        }}
        team={{
          current_member: {
            id: 'trusted-local-owner',
            project_id: 'p1',
            display_name: 'Local owner',
            capability: 'infrastructure_manager',
            capability_label: 'Gestore dell’infrastruttura',
            trusted_local: true,
          },
          capabilities: { manage_infrastructure: true, participate_in_project: true },
          members: [],
        }}
        manual={{
          content: '',
          version: 0,
          characters: 0,
          soft_limit_characters: 4_000,
          hard_limit_characters: 100_000,
          warnings: ['manual_empty'],
        }}
        draft={null}
        invitation={null}
        loading={false}
        saving={false}
        drafting={false}
        inviting={false}
        revokingMemberId=""
        error=""
        onInvite={vi.fn()}
        onRevokeMember={vi.fn()}
        onSaveManual={vi.fn()}
        onCreateDraft={vi.fn()}
        onDraft={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText('Member name')).not.toBeInTheDocument();
    expect(screen.getByText(/persistent disk-backed swap/i)).toBeInTheDocument();
  });

  it('shows the shared manual without team-management controls to a project member', () => {
    render(
      <TeamView
        project={{
          id: 'p1',
          name: 'Shared',
          cause: '',
          principles: [],
          objectives: [],
          profile_version: 1,
        }}
        team={{
          current_member: null,
          capabilities: { manage_infrastructure: false, participate_in_project: true },
          members: [],
        }}
        manual={{
          content: 'Always test the deployment.',
          version: 1,
          characters: 27,
          soft_limit_characters: 4_000,
          hard_limit_characters: 100_000,
          warnings: [],
        }}
        draft={null}
        invitation={null}
        loading={false}
        saving={false}
        drafting={false}
        inviting={false}
        revokingMemberId=""
        error=""
        onInvite={vi.fn()}
        onRevokeMember={vi.fn()}
        onSaveManual={vi.fn()}
        onCreateDraft={vi.fn()}
        onDraft={vi.fn()}
      />,
    );
    expect(screen.getByText('Always test the deployment.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit manual' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create invitation' })).not.toBeInTheDocument();
    expect(screen.getByText(/Only the Infrastructure manager/)).toBeInTheDocument();
  });
});
