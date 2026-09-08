import { expect, test, type Page, type Route } from '@playwright/test';
import type { Sprint, Task } from '../src/types';

const project = {
  id: 'project-browser',
  name: 'Browser Project',
  cause: 'Ship a dependable local product memory.',
  principles: ['Keep work recoverable'],
  objectives: ['Release the Android companion'],
  profile_version: 1,
};

const task = {
  id: 'task-browser',
  kind: 'task',
  epic_id: null,
  title: 'Ship Android release',
  description: `## Validation

Complete the **final Android validation**.`,
  status: 'in_progress',
  priority: 'high',
  labels: ['mobile', 'release'],
  objective: 'A verified Android release.',
  next_action: 'Run the real-user flow.',
  due_at: null,
  dependencies: [],
  attachments: [],
  version: 1,
  created_at: '2026-07-18T09:00:00Z',
  updated_at: '2026-07-18T09:00:00Z',
};

const plan = {
  id: 'plan-browser',
  title: 'Android release approach',
  objective: 'Validate the release sequence before execution.',
  content: `## Sequence

Start with the **real-user flow**, then prepare the store release.`,
  status: 'decided',
  labels: ['mobile', 'release'],
  work_item_ids: [task.id],
  attachments: [],
  version: 1,
  created_at: '2026-07-18T09:00:00Z',
  updated_at: '2026-07-18T09:00:00Z',
};

function fulfill(route: Route, payload: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

async function mockDashboardApi(
  page: Page,
  opened: { value: number },
  observability?: { summary: number; detail: number },
) {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/health') {
      return fulfill(route, {
        status: 'ok',
        embedding_provider: 'openai',
        embedding_model: 'text-embedding-3-large',
      });
    }
    if (path.endsWith('/observability/summary')) {
      if (observability) observability.summary += 1;
      const coverage = { reported: 0, estimated: 1, unavailable: 0 };
      const metric = {
        coverage,
        requests: 0,
        successes: 0,
        failures: 0,
        input_tokens_reported: null,
        input_tokens_estimated: null,
        cached_input_tokens_reported: null,
        cache_write_input_tokens_reported: null,
        output_tokens_reported: null,
        output_tokens_estimated: null,
        duration_p50_ms: null,
        duration_p95_ms: null,
        by_operation: [],
      };
      return fulfill(route, {
        period: {
          range: '7d',
          from: '2026-08-18T00:00:00Z',
          to: '2026-08-25T00:00:00Z',
          bucket: 'day',
          timezone: 'Europe/Rome',
        },
        coverage: {
          ...coverage,
          collection_started_at: '2026-08-25T10:00:00Z',
          events: 1,
        },
        context: {
          coverage,
          automatic: {
            coverage,
            injections: 1,
            characters: 42,
            utf8_bytes: 47,
            estimated_tokens: 12,
            estimated_tokens_p50: 12,
            estimated_tokens_p95: 12,
            component_bytes: { instructions: 12, memories: 35 },
            budget: {
              measured_injections: 1,
              budget_limit_characters: 9000,
              budgeted_injections: 0,
              fallback_injections: 0,
              inline_expected_injections: 1,
              candidate_characters: 42,
              candidate_utf8_bytes: 47,
              candidate_estimated_tokens: 12,
              avoided_characters: 0,
              avoided_utf8_bytes: 0,
              avoided_estimated_tokens: 0,
              included_items: 2,
              partial_items: 0,
              omitted_items: 0,
              budget_utilization_p50_percent: 0.47,
              budget_utilization_p95_percent: 0.47,
            },
            delivery: {
              measured_injections: 1,
              snapshot_injections: 1,
              delta_injections: 0,
              fallback_injections: 0,
              unknown_injections: 0,
              reused_characters: 0,
              reused_utf8_bytes: 0,
              reused_estimated_tokens: 0,
            },
          },
          requested: {
            coverage: { reported: 0, estimated: 0, unavailable: 0 },
            results: 0,
            characters: null,
            utf8_bytes: null,
            estimated_tokens: null,
          },
          by_operation: [],
          timeline: [],
        },
        agent_usage: {
          ...metric,
          equivalent_api_cost_usd: null,
          pricing_coverage: { priced: 0, unavailable: 0 },
          by_provider_model: [],
          timeline: [],
        },
        sleep: {
          ...metric,
          equivalent_api_cost_usd: null,
          pricing_coverage: { priced: 0, unavailable: 0 },
          by_provider_model: [],
          timeline: [],
        },
        embeddings: { ...metric, cost_usd: null, timeline: [] },
        reliability: { ...metric, requests: 1, successes: 1, retrieval: metric },
      });
    }
    if (path.endsWith('/observability/events')) {
      return fulfill(route, {
        items: [
          {
            id: 'context-browser',
            category: 'context',
            operation: 'context.turn_injection',
            scope: 'automatic',
            status: 'success',
            provider: 'codex',
            attempt: 1,
            measurement_source: 'local_estimate',
            input_tokens: 12,
            characters: 42,
            utf8_bytes: 47,
            duration_ms: null,
            details: {},
            has_content: true,
            occurred_at: '2026-08-25T10:00:00Z',
          },
        ],
        next_cursor: null,
      });
    }
    if (path.endsWith('/observability/context-events/context-browser')) {
      if (observability) observability.detail += 1;
      return fulfill(route, {
        event: {
          id: 'context-browser',
          session_id: 'session-browser',
          turn_id: 'turn-browser',
          retrieval_run_id: 'retrieval-browser',
          category: 'context',
          operation: 'context.turn_injection',
          scope: 'automatic',
          status: 'success',
          provider: 'codex',
          attempt: 1,
          measurement_source: 'local_estimate',
          input_tokens: 12,
          characters: 42,
          utf8_bytes: 47,
          details: {},
          has_content: true,
          occurred_at: '2026-08-25T10:00:00Z',
        },
        content: 'Exact dDuo context\n<script>not markup</script>\nDecisione è',
        content_sha256: 'a'.repeat(64),
        producer_version: '0.1.0-alpha.43',
        render_version: 'hook-context-v1',
        estimator_version: 'utf8_bytes_div_4_v1',
        captured_at: '2026-08-25T10:00:00Z',
        components: [
          {
            name: 'memories',
            utf8_bytes: 35,
            estimated_tokens: 9,
            item_count: 1,
            references: ['memory-browser'],
          },
        ],
        turn: {
          id: 'turn-browser',
          user_prompt: 'Quale decisione avevamo preso?',
          assistant_response: 'Abbiamo riusato la memoria.',
        },
        retrieval: {
          id: 'retrieval-browser',
          status: 'context_ready',
          memories: [
            {
              id: 'memory-browser',
              node_type: 'reusable_fact',
              node_key: 'decisione',
              text: 'Memoria consolidata consegnata.',
              revision: 2,
              score: 0.91,
            },
          ],
        },
        tool_name: null,
      });
    }
    if (path.endsWith('/team/manual')) {
      return fulfill(route, {
        current_member: {
          id: 'manager-browser',
          project_id: project.id,
          display_name: 'Alex',
          capability: 'infrastructure_manager',
          capability_label: 'Gestore dell’infrastruttura',
          status: 'active',
          created_at: '2026-08-28T08:00:00Z',
          updated_at: '2026-08-28T08:00:00Z',
        },
        capabilities: { manage_infrastructure: true, participate_in_project: true },
        manual: {
          content: 'Deploy only after review and a focused smoke test.',
          version: 2,
          characters: 49,
          soft_limit_characters: 4000,
          hard_limit_characters: 100000,
          warnings: [],
        },
      });
    }
    if (path.endsWith('/team')) {
      const member = {
        id: 'manager-browser',
        project_id: project.id,
        display_name: 'Alex',
        capability: 'infrastructure_manager',
        capability_label: 'Gestore dell’infrastruttura',
        status: 'active',
        devices: [],
        created_at: '2026-08-28T08:00:00Z',
        updated_at: '2026-08-28T08:00:00Z',
      };
      return fulfill(route, {
        current_member: member,
        capabilities: { manage_infrastructure: true, participate_in_project: true },
        members: [member],
      });
    }
    if (path.endsWith('/briefing')) return fulfill(route, { project });
    if (path.endsWith('/sprints')) return fulfill(route, { items: [], total: 0, limit: 50, offset: 0 });
    if (path.endsWith('/tasks')) return fulfill(route, { items: new URL(request.url()).searchParams.get('kind') === 'epic' ? [] : [task], total: 1, limit: 50, offset: 0 });
    if (path.endsWith(`/tasks/${task.id}`)) return fulfill(route, { task });
    if (path.endsWith('/plans')) return fulfill(route, { items: [plan], total: 1, limit: 50, offset: 0 });
    if (path.endsWith(`/plans/${plan.id}`)) return fulfill(route, { plan });
    if (path.endsWith('/activity')) return fulfill(route, { items: [] });
    if (path.endsWith('/backups')) {
      return fulfill(route, {
        configured: true,
        configuration_error: '',
        dirty: false,
        automatic_due: false,
        include_qdrant: true,
        qdrant_collection: 'browser',
        retention: { daily: 7, weekly: 4, monthly: 6 },
        latest: null,
        latest_verified: null,
        items: [],
      });
    }
    if (path.endsWith('/memories')) return fulfill(route, { items: [] });
    if (path.endsWith('/memory-status')) {
      return fulfill(route, {
        available: false,
        state: 'connection_required',
        summary: 'Connect Claude to resume waiting memory.',
        provider: 'claude',
        error_kind: 'auth_required',
        retry_at: null,
        jobs: { pending: 0, running: 0, waiting: 1 },
        memories: { active: 0 },
        latest_job: null,
      });
    }
    if (path.endsWith('/sleep-jobs')) {
      return fulfill(route, {
        items: [
          {
            id: 'sleep-browser',
            provider: 'claude',
            trigger: 'idle',
            status: 'waiting',
            input_turn_ids: ['turn-browser'],
            attempts: 1,
            error_kind: 'auth_required',
            retry_at: null,
            result: {},
            created_at: '2026-07-18T09:00:00Z',
          },
        ],
      });
    }
    if (path.endsWith('/setup') && request.method() === 'POST') {
      opened.value += 1;
      return fulfill(route, { opened: true }, 202);
    }
    return fulfill(route, { detail: `Unexpected browser API request: ${path}` }, 404);
  });
}

test('local Setup keeps secrets local, delegates Codex hook trust, and installs or repairs Claude telemetry', async ({
  page,
}) => {
  let embeddingsReady = false;
  const savedKeys: string[] = [];
  const connectedProviders: string[] = [];
  let telemetryChanges = 0;
  let claudeTelemetry = {
    ready: false,
    installed: false,
    reason: 'restore_state_missing',
  };

  await page.route('**/v1/setup/status**', (route) =>
    fulfill(route, {
      docker: { ready: true, installed: true },
      embeddings: { ready: embeddingsReady },
      clients: {
        claude: { ready: false, setup_state: 'idle' },
        codex: { ready: false, setup_state: 'idle' },
      },
      codex_hooks: { ready: false },
      project: { ready: false, name: '' },
      claude_telemetry: claudeTelemetry,
    }),
  );
  await page.route('**/v1/setup/openai**', async (route) => {
    savedKeys.push(JSON.parse(route.request().postData() ?? '{}').key);
    embeddingsReady = true;
    await fulfill(route, { saved: true });
  });
  await page.route('**/v1/setup/auth**', async (route) => {
    connectedProviders.push(JSON.parse(route.request().postData() ?? '{}').provider);
    await fulfill(route, { status: 'starting' }, 202);
  });
  await page.route('**/v1/setup/claude-telemetry**', async (route) => {
    telemetryChanges += 1;
    claudeTelemetry =
      telemetryChanges === 1
        ? { ready: false, installed: true, reason: 'project_adapter_missing' }
        : { ready: true, installed: true, reason: 'ready' };
    await fulfill(route, claudeTelemetry);
  });
  await page.route('**/v1/setup/activate**', (route) => fulfill(route, {}));

  const ticketResponse = await page.request.post('http://127.0.0.1:18888/v1/setup/ticket', {
    headers: { Authorization: 'Bearer browser-test-token' },
    data: { project_root: '/tmp' },
  });
  expect(ticketResponse.ok()).toBe(true);
  const { ticket } = (await ticketResponse.json()) as { ticket: string };
  await page.addInitScript(() => {
    Object.defineProperty(window.navigator, 'languages', {
      configurable: true,
      get: () => ['it-IT', 'en-US'],
    });
  });
  await page.goto(`http://127.0.0.1:18888/setup?ticket=${encodeURIComponent(ticket)}`);
  await expect(page).not.toHaveURL(/[?&](?:ticket|token)=/);
  await expect(page.getByRole('heading', { name: 'dDuo Solo Founder' })).toBeVisible();
  await expect(page.locator('#intro')).toContainText('un solo abbonamento supportato per il sonno');
  await expect(page.getByText('Telemetria di utilizzo Claude', { exact: true })).toBeVisible();
  await expect(page.locator('#claude-telemetry').getByRole('button')).toHaveText(
    'Installa telemetria',
  );
  await expect(page.getByRole('button', { name: 'Italiano' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );

  await page.getByRole('button', { name: 'English' }).click();
  await expect(page.locator('#intro')).toContainText('one supported subscription for sleep');
  await page.reload();
  await expect(page.getByRole('button', { name: 'English' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.getByText('Codex lifecycle hooks')).toBeVisible();
  await expect(page.locator('#hooks').getByText('Review required')).toBeVisible();
  await expect(page.locator('#hooks').getByRole('button')).toHaveCount(0);

  const claudeTelemetryRow = page.locator('#claude-telemetry');
  await expect(claudeTelemetryRow.getByText('Claude usage telemetry', { exact: true })).toBeVisible();
  await claudeTelemetryRow.getByRole('button', { name: 'Install telemetry' }).click();
  await expect.poll(() => telemetryChanges).toBe(1);
  await expect(claudeTelemetryRow.getByRole('button', { name: 'Repair telemetry' })).toBeVisible();
  await claudeTelemetryRow.getByRole('button', { name: 'Repair telemetry' }).click();
  await expect.poll(() => telemetryChanges).toBe(2);
  await expect(claudeTelemetryRow.getByText('Claude usage telemetry is ready.')).toBeVisible();
  await expect(claudeTelemetryRow.getByText('Connected', { exact: true })).toBeVisible();

  await page.locator('#openai-key').fill('sk-browser-only');
  await page.waitForTimeout(2_700);
  await expect(page.locator('#openai-key')).toHaveValue('sk-browser-only');
  await page.getByRole('button', { name: 'Save key' }).click();
  await expect.poll(() => savedKeys).toEqual(['sk-browser-only']);
  await expect(page.getByRole('button', { name: 'Activate project' })).toBeVisible();

  await page.locator('#claude').getByRole('button', { name: 'Connect' }).click();
  await expect.poll(() => connectedProviders).toEqual(['claude']);
  await expect(page.getByRole('status')).toContainText('official window');

  await page.getByRole('button', { name: 'Activate project' }).click();
  await expect(page.getByRole('status')).toContainText('Open a new chat in this project folder');

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('Setup reconnects a failed sleep account explicitly and keeps retry usable after reload', async ({ page }) => {
  let logins = 0;
  let resumes = 0;
  let client = { ready: false, reason: 'auth_required', setup_state: 'idle', resume_ready: false, resume_failed: false };
  let memoryState = 'connection_required';
  await page.route('**/v1/setup/status**', (route) => fulfill(route, {
    docker: { ready: true, installed: true },
    embeddings: { ready: true },
    clients: { codex: client, claude: { ready: false, reason: 'not_installed', setup_state: 'idle' } },
    codex_hooks: { ready: true },
    project: { ready: true, name: 'Browser Project' },
    memory_status: { state: memoryState },
    backup: { configured: true },
  }));
  await page.route('**/v1/setup/auth**', (route) => {
    logins += 1;
    client = { ...client, setup_state: 'waiting' };
    return fulfill(route, { status: 'waiting' });
  });
  await page.route('**/v1/setup/resume**', (route) => {
    resumes += 1;
    client = { ...client, resume_ready: false, resume_failed: resumes === 1 };
    if (resumes === 1) return fulfill(route, { resumed: false, error: 'resume_failed' });
    memoryState = 'updating';
    client = { ...client, ready: true, reason: 'authenticated' };
    return fulfill(route, { resumed: true });
  });
  const ticketResponse = await page.request.post('http://127.0.0.1:18888/v1/setup/ticket', {
    headers: { Authorization: 'Bearer browser-test-token' },
    data: { project_root: '/tmp' },
  });
  expect(ticketResponse.ok()).toBe(true);
  const { ticket } = (await ticketResponse.json()) as { ticket: string };
  await page.goto(`http://127.0.0.1:18888/setup?ticket=${encodeURIComponent(ticket)}`);
  await page.getByRole('button', { name: 'Italiano' }).click();
  await expect(page.locator('#codex').getByRole('button', { name: 'Ricollega', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator('#codex').getByRole('button')).toHaveText('Ricollega');
  expect(logins).toBe(0);
  expect(resumes).toBe(0);
  await page.locator('#codex').getByRole('button').click();
  await expect.poll(() => logins).toBe(1);
  client = { ...client, setup_state: 'connected', resume_ready: true };
  await page.reload();
  await expect.poll(() => resumes).toBe(1);
  await expect(page.locator('#codex').getByRole('button')).toHaveText('Riprova consolidamento');
  await page.reload();
  await expect(page.locator('#codex').getByRole('button')).toHaveText('Riprova consolidamento');
  expect(resumes).toBe(1);
  await page.locator('#codex').getByRole('button').click();
  await expect.poll(() => resumes).toBe(2);
  await expect(page.locator('#memory')).toContainText('consolidamento');
  await expect(page.locator('#memory').getByText('Connesso', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'English' }).click();
  await expect(page.locator('#memory')).toContainText('consolidation');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('Setup does not require a second subscription when the other sleep provider is ready', async ({ page }) => {
  let logins = 0;
  await page.route('**/v1/setup/status**', (route) => fulfill(route, {
    docker: { ready: true, installed: true },
    embeddings: { ready: true },
    clients: {
      codex: { ready: false, reason: 'auth_required', setup_state: 'idle' },
      claude: { ready: true, setup_state: 'idle' },
    },
    codex_hooks: { ready: true },
    project: { ready: true, name: 'Browser Project' },
    memory_status: { state: 'updated' },
    backup: { configured: true },
  }));
  await page.route('**/v1/setup/auth**', (route) => {
    logins += 1;
    return fulfill(route, { status: 'waiting' });
  });
  const response = await page.request.post('http://127.0.0.1:18888/v1/setup/ticket', {
    headers: { Authorization: 'Bearer browser-test-token' },
    data: { project_root: '/tmp' },
  });
  expect(response.ok()).toBe(true);
  const { ticket } = (await response.json()) as { ticket: string };
  await page.goto(`http://127.0.0.1:18888/setup?ticket=${encodeURIComponent(ticket)}`);
  await page.getByRole('button', { name: 'Italiano' }).click();
  await expect(page.locator('#codex')).toContainText('Non necessario');
  await expect(page.locator('#codex').getByRole('button')).toHaveCount(0);
  await expect(page.locator('#memory')).toContainText('La memoria è aggiornata');
  await page.reload();
  await expect(page.locator('#codex')).toContainText('Non necessario');
  expect(logins).toBe(0);
});

test('project rename is compact, explicit and usable on mobile in Italian and English', async ({ page }, testInfo) => {
  await mockDashboardApi(page, { value: 0 });
  let name = project.name;
  await page.route('**/api/projects/project-browser/briefing', (route) => fulfill(route, { project: { ...project, name } }));
  await page.route('**/api/projects/project-browser/rename', (route) => {
    expect(route.request().postDataJSON()).toEqual({ name: 'Orchard', expected_version: 1 });
    name = 'Orchard';
    return fulfill(route, { ...project, name, profile_version: 2 });
  });
  await page.goto('/?project=project-browser');
  await page.getByRole('button', { name: 'English', exact: true }).click();
  const title = page.getByRole('button', { name: `${project.name} — Rename project`, exact: true });
  await expect(title.locator('svg')).toHaveCount(0);
  await title.focus();
  await page.keyboard.press('Enter');
  const input = page.getByRole('textbox', { name: 'Project name', exact: true });
  await expect(input).toBeFocused();
  await expect(input).toHaveValue(project.name);
  await page.keyboard.press('Escape');
  await expect(title).toBeFocused();
  await page.keyboard.press('Space');
  await expect(input).toBeFocused();
  await input.fill('Orchard');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Orchard');
  await expect(page.getByRole('button', { name: 'Orchard — Rename project' })).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath('rename-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Italian', exact: true }).click();
  await page.getByRole('button', { name: 'Orchard — Rinomina progetto', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Nome del progetto' })).toHaveValue('Orchard');
  await expect(page.getByRole('button', { name: 'Salva nome' })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('rename-mobile.png') });
  await page.getByRole('button', { name: 'Annulla', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Orchard — Rinomina progetto' })).toBeFocused();
});

test('Work opens first and a connection-required sleep state opens protected Setup', async ({ page }) => {
  const opened = { value: 0 };
  await mockDashboardApi(page, opened);

  await page.goto('/?project=project-browser');
  await expect(page.getByText('Ship Android release')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Work' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('combobox', { name: 'Work view' })).toHaveValue('board');
  await expect(page.getByRole('tab', { name: 'Focus' })).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Work view' }).selectOption('plans');
  await expect(page.getByText('Android release approach')).toBeVisible();
  await expect(page).toHaveURL(/view=plans/);

  await page.getByRole('button', { name: 'Memory' }).click();
  await expect(page.getByRole('heading', { name: 'Connection required' })).toBeVisible();
  await page.getByRole('button', { name: 'Open Setup' }).click();
  await expect.poll(() => opened.value).toBe(1);
  await expect(page).not.toHaveURL(/token=/);
});

test('exact Work links open the human item and preserve unrelated navigation state', async ({
  page,
}) => {
  await mockDashboardApi(page, { value: 0 });

  await page.goto(
    '/?project=project-browser&tab=tasks&source=assistant&work=task-browser',
  );
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Ship Android release' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Validation', level: 2 })).toBeVisible();
  await expect(page.getByText('final Android validation', { exact: true })).toHaveCSS(
    'font-weight',
    '700',
  );
  const desktopDrawer = await page.getByRole('dialog').boundingBox();
  expect(desktopDrawer?.width ?? 0).toBeGreaterThanOrEqual(700);
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page).not.toHaveURL(/(?:\?|&)work=/);
  await expect(page).toHaveURL(/(?:\?|&)source=assistant(?:&|$)/);

  await page.goto('/?project=project-browser&tab=tasks&view=plans&plan=plan-browser');
  await expect(page.getByRole('combobox', { name: 'Work view' })).toHaveValue('plans');
  await expect(page.getByRole('heading', { name: 'Android release approach' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Sequence', level: 2 })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit plan' })).toBeVisible();
  await expect(
    page.getByRole('dialog').locator('.plan-work-option-readonly').getByText('Ship Android release'),
  ).toBeVisible();

  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('/?project=project-browser&tab=tasks&work=task-browser');
  const mobileDrawer = page.getByRole('dialog');
  await expect(mobileDrawer).toBeVisible();
  await expect(mobileDrawer.getByRole('heading', { name: 'Validation', level: 2 })).toBeVisible();
  const mobileBox = await mobileDrawer.boundingBox();
  expect(mobileBox).not.toBeNull();
  expect(mobileBox?.x).toBe(0);
  expect(mobileBox?.width).toBe(320);
  expect(mobileBox?.height).toBeLessThanOrEqual(800);
  await expect(mobileDrawer.getByRole('button', { name: 'Edit Task' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
});

test('Observability loads exact dDuo context lazily and keeps its inspector inside a mobile viewport', async ({
  page,
}) => {
  const opened = { value: 0 };
  const observed = { summary: 0, detail: 0 };
  await mockDashboardApi(page, opened, observed);
  await page.setViewportSize({ width: 320, height: 800 });

  await page.goto('/?project=project-browser&tab=observability');
  await expect(page.getByRole('heading', { name: 'dDuo context emitted' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Interactive agent — API equivalent' })).toBeVisible();
  await expect(page.getByText('No interactive provider/model usage in this period.')).toBeVisible();
  await expect.poll(() => observed.summary).toBe(1);
  expect(observed.detail).toBe(0);

  await page.getByRole('button', { name: 'Inspect emitted context' }).click();
  const inspector = page.getByRole('dialog', { name: 'Context emitted by dDuo' });
  await expect(inspector).toBeVisible();
  await expect.poll(() => observed.detail).toBe(1);
  await expect(inspector.getByText('Memoria consolidata consegnata.')).toBeVisible();
  await expect(inspector.locator('pre')).toHaveText(
    'Exact dDuo context\n<script>not markup</script>\nDecisione è',
  );
  await expect(inspector.locator('script')).toHaveCount(0);
  const box = await inspector.boundingBox();
  expect(box).not.toBeNull();
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(320);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);

  await page.keyboard.press('Escape');
  await expect(inspector).toBeHidden();
});

test('Team deep links keep the shared manual and neutral roles usable on mobile', async ({ page }) => {
  await mockDashboardApi(page, { value: 0 });
  await page.setViewportSize({ width: 320, height: 800 });

  await page.goto('/?project=project-browser&tab=team');
  await expect(page.getByRole('heading', { name: 'Project team' })).toBeVisible();
  await expect(page.getByText('Infrastructure manager').first()).toBeVisible();
  await expect(page.getByText('Deploy only after review and a focused smoke test.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create invitation' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
});

async function mockSprintWorkflow(
  page: Page,
  sprintContent: Partial<Pick<Sprint, 'title' | 'objective'>> = {},
) {
  await mockDashboardApi(page, { value: 0 });
  const active: Sprint = { id: 'cycle-current', project_id: project.id, title: 'Sharing release', objective: 'Finish sharing and begin the next development cycle.', status: 'active', version: 5, started_at: '2026-08-01', archived_at: null, created_at: '2026-08-01', updated_at: '2026-08-24', ...sprintContent };
  let cycles: Sprint[] = [active, { ...active, id: 'cycle-next', title: 'Next development', status: 'planned', version: 1 }];
  let records: Task[] = [
    { ...task, sprint_id: active.id } as Task,
    ...Array.from({ length: 100 }, (_, index) => ({ ...task, id: `completed-${index}`, title: `Completed release task ${index}`, labels: index % 2 === 0 ? ['mobile', 'release'] : ['release'], sprint_id: active.id, status: 'done' as const }) as Task),
    ...Array.from({ length: 123 }, (_, index) => ({ ...task, id: `legacy-${index}`, title: `Earlier task ${String(index).padStart(3, '0')}`, sprint_id: null, status: 'done' as const }) as Task),
    { ...task, id: 'backlog-blocked', title: 'Blocked work without sprint', sprint_id: null, status: 'blocked' } as Task,
  ];
  let snapshot: Task[] = [];
  const patches: Array<{ expected_version: number; title?: string; description?: string }> = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? 50);
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const pageOf = <T,>(items: T[]) => ({ items: items.slice(offset, offset + limit), total: items.length, offset, limit });
    if (path.endsWith('/sprints')) {
      if (request.method() === 'POST') {
        const input = request.postDataJSON();
        const created: Sprint = { ...active, id: 'cycle-created', title: input.title, objective: input.objective ?? '', status: 'planned', version: 1 };
        cycles.push(created); return fulfill(route, created);
      }
      const status = url.searchParams.get('status');
      return fulfill(route, pageOf(cycles.filter((cycle) => !status || cycle.status === status)));
    }
    const cycleMatch = /\/sprints\/([^/]+)(?:\/([^/]+))?$/.exec(path);
    if (cycleMatch) {
      const current = cycles.find((cycle) => cycle.id === cycleMatch[1]);
      if (!current) return fulfill(route, { detail: 'Sprint not found' }, 404);
      const action = cycleMatch[2];
      if (action === 'close-preview') {
        const items = records.filter((item) => item.sprint_id === current.id);
        const completed = items.filter((item) => ['done', 'cancelled'].includes(item.status)).length;
        return fulfill(route, { sprint: current, completed_count: completed, unfinished_count: items.length - completed, total: items.length });
      }
      if (action === 'tasks') return fulfill(route, pageOf(snapshot.filter((item) => !q || item.title.toLowerCase().includes(q))));
      if (request.method() === 'POST') {
        if (action === 'archive') {
          const input = request.postDataJSON();
          snapshot = records.filter((item) => item.sprint_id === current.id).map((item) => ({ ...item }));
          records = records.map((item) => item.sprint_id === current.id && !['done', 'cancelled'].includes(item.status) ? { ...item, sprint_id: input.destination_sprint_id ?? null } : item);
        }
        const updated = { ...current, version: current.version + 1, status: action === 'archive' ? 'archived' as const : action === 'start' ? 'active' as const : 'planned' as const };
        cycles = cycles.map((item) => item.id === current.id ? updated : item);
        return fulfill(route, updated);
      }
      return fulfill(route, current);
    }
    if (path.endsWith('/tasks')) {
      const placement = url.searchParams.get('placement');
      const sprintId = url.searchParams.get('sprint_id');
      const items = records.filter((item) =>
        url.searchParams.get('kind') !== 'epic' &&
        (!sprintId || item.sprint_id === sprintId) &&
        (url.searchParams.get('scope') !== 'active' || !['done', 'cancelled'].includes(item.status)) &&
        (placement !== 'backlog' || (!item.sprint_id && !['done', 'cancelled'].includes(item.status))) &&
        (placement !== 'archive' || ['done', 'cancelled'].includes(item.status)) &&
        (!q || item.title.toLowerCase().includes(q)));
      return fulfill(route, pageOf(items.map((item) => ({ ...item, description: '', attachments: [] }))));
    }
    const taskMatch = /\/tasks\/([^/]+)$/.exec(path);
    if (taskMatch) {
      const current = records.find((item) => item.id === taskMatch[1]);
      if (!current) return fulfill(route, { detail: 'Task not found' }, 404);
      if (request.method() === 'PATCH') {
        const input = request.postDataJSON(); patches.push(input);
        if (input.expected_version !== current.version) return fulfill(route, { detail: 'Task version conflict' }, 409);
        const updated = { ...current, ...input, version: current.version + 1 };
        records = records.map((item) => item.id === current.id ? updated : item);
        return fulfill(route, updated);
      }
      return fulfill(route, { task: current });
    }
    return route.fallback();
  });
  return { patches, remoteEdit() { records = records.map((item) => item.id === task.id ? { ...item, description: 'Remote description that must survive', version: item.version + 1 } : item); } };
}

test('Sprint Work archives 100 completed tasks, carries unfinished work and remains usable on mobile', async ({ page }) => {
  await mockSprintWorkflow(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?project=project-browser&tab=tasks');
  await expect(page.getByLabel('Work scope')).toHaveValue('current');
  await expect(page.getByText('1–50 of 101 items')).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('work-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: test.info().outputPath('work-mobile-board.png') });
  await page.getByRole('button', { name: 'Sprint details' }).click();
  await page.getByRole('button', { name: 'Close and archive' }).click();
  const dialog = page.getByRole('dialog', { name: 'Close and archive' });
  await expect(dialog.getByText('100 completed · 1 unfinished · 101 total')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Archive sprint', exact: true })).toBeDisabled();
  await dialog.getByLabel('Move unfinished tasks to').selectOption('backlog');
  await page.screenshot({ path: test.info().outputPath('work-mobile-archive.png') });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await dialog.getByRole('button', { name: 'Archive sprint', exact: true }).click();
  await expect(page.getByLabel('Work scope')).toHaveValue('archive:cycle-current');
  await page.getByLabel('Work scope').selectOption('backlog');
  await expect(page.getByText('Ship Android release', { exact: true })).toBeVisible();
  await expect(page.getByText('Blocked work without sprint', { exact: true })).toBeVisible();
  await page.getByLabel('Work scope').selectOption('all');
  await page.getByLabel('Search work').fill('Earlier task 122');
  await expect(page.getByText('Earlier task 122', { exact: true })).toBeVisible();
  await expect(page.getByText('1–1 of 1 items')).toBeVisible();
  await page.goto('/?project=project-browser&tab=tasks&placement=archive&work=legacy-122');
  await expect(page.getByRole('dialog').getByRole('heading', { name: 'Earlier task 122' })).toBeVisible();
  await expect(page.getByRole('dialog').getByText('The drawer shows the current task. The archived sprint keeps its closing snapshot.')).toBeVisible();
});

test('Sprint Work uses one minimal toolbar and accessible dropdowns across desktop and mobile', async ({ page }) => {
  const title = 'A long sprint title that describes the complete release and its validation across every supported platform';
  const objective = `Deliver **focused results** without crowding the board.\n\n${'A deliberately long objective stays available when requested. '.repeat(45)}`;
  await mockSprintWorkflow(page, { title, objective });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?project=project-browser&tab=tasks&placement=all');
  await expect(page.getByText('1–50 of 225 items')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'All work' })).toHaveCount(0);
  const scope = page.getByLabel('Work scope');
  const toolbar = page.locator('.work-toolbar');
  await expect(toolbar.locator('button:visible, select:visible, input:visible')).toHaveCount(5);
  const scopeBounds = await scope.boundingBox();
  const boardBounds = await page.locator('.board-column').first().boundingBox();
  expect(scopeBounds).not.toBeNull();
  expect(boardBounds).not.toBeNull();
  expect(boardBounds!.y - scopeBounds!.y).toBeLessThanOrEqual(170);
  expect(boardBounds!.y).toBeLessThanOrEqual(300);
  await page.screenshot({ path: test.info().outputPath('minimal-work-desktop.png') });
  for (const width of [1024, 800]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(page.getByRole('button', { name: 'New', exact: true })).toBeVisible();
    await expect(toolbar.locator('button:visible, select:visible, input:visible')).toHaveCount(5);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  await page.screenshot({ path: test.info().outputPath('minimal-work-tablet.png') });
  await page.setViewportSize({ width: 1440, height: 1000 });

  await scope.selectOption('current');
  await expect(scope).toHaveValue('current');
  const details = page.getByRole('button', { name: 'Sprint details' });
  const objectiveText = page.getByText('focused results');
  await expect(details).toHaveAttribute('aria-expanded', 'false');
  await expect(objectiveText).toHaveCount(0);
  await details.focus();
  await page.keyboard.press('Enter');
  await expect(objectiveText).toBeVisible();
  await expect(objectiveText).toHaveJSProperty('tagName', 'STRONG');
  await expect(page.getByRole('heading', { name: title })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(objectiveText).toHaveCount(0);
  await expect(details).toBeFocused();

  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await page.getByRole('checkbox', { name: 'mobile', exact: true }).check();
  await page.getByRole('checkbox', { name: 'release', exact: true }).check();
  await expect(page.getByRole('button', { name: /Filters.*2/ })).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('Completed release task 0', { exact: true })).toBeVisible();
  await expect(page.getByText('Completed release task 1', { exact: true })).toHaveCount(0);
  await page.getByLabel('Search work').click();
  await expect(page.getByRole('checkbox', { name: 'mobile', exact: true })).toHaveCount(0);
  await page.getByLabel('Search work').fill('Completed release task 0');
  await expect(page.getByText('1–1 of 1 items')).toBeVisible();
  await expect(page.getByText('Completed release task 0', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Filters.*2/ }).click();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await page.keyboard.press('Escape');
  await page.getByLabel('Search work').fill('');
  await expect(page.getByText('1–50 of 101 items')).toBeVisible();

  await page.getByRole('button', { name: 'New', exact: true }).click();
  await expect(page.getByRole('button', { name: 'New sprint', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'New task', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.getByRole('button', { name: 'New', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await scope.selectOption('all');
    await expect(scope).toHaveValue('all');
    await expect(toolbar.locator('button:visible, select:visible, input:visible')).toHaveCount(5);
    await scope.selectOption('current');
    await details.click();
    await expect(objectiveText).toBeVisible();
    const panelBounds = await page.locator('.work-dropdown-panel').boundingBox();
    expect(panelBounds).not.toBeNull();
    expect(panelBounds!.x).toBeGreaterThanOrEqual(0);
    expect(panelBounds!.x + panelBounds!.width).toBeLessThanOrEqual(width);
    expect(panelBounds!.height).toBeLessThanOrEqual(844);
    await page.getByRole('button', { name: 'Close and archive' }).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button', { name: 'Close and archive' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'New', exact: true }).click();
    const newPanelBounds = await page.locator('.work-dropdown-panel').boundingBox();
    expect(newPanelBounds!.x).toBeGreaterThanOrEqual(0);
    expect(newPanelBounds!.x + newPanelBounds!.width).toBeLessThanOrEqual(width);
    await expect(page.getByRole('button', { name: 'New sprint', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await page.screenshot({ path: test.info().outputPath(`minimal-work-mobile-${width}.png`) });
  }

  await page.getByRole('button', { name: 'Italian', exact: true }).click();
  await expect(page.getByLabel('Ambito del lavoro')).toHaveValue('current');
  await expect(page.getByRole('combobox', { name: 'Vista lavoro' })).toHaveValue('board');
  await page.getByRole('button', { name: 'Nuovo', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Nuovo sprint', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Dettagli sprint' }).click();
  await expect(page.getByRole('button', { name: 'Chiudi e archivia' })).toBeVisible();
  await page.keyboard.press('Escape');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  const italianCaption = await page.locator('.work-page-caption').boundingBox();
  expect(italianCaption!.x).toBeGreaterThanOrEqual(0);
  expect(italianCaption!.x + italianCaption!.width).toBeLessThanOrEqual(320);
  await page.screenshot({ path: test.info().outputPath('minimal-work-mobile-it.png') });
});

test('projects without a sprint keep only essential controls and show their backlog directly', async ({ page }) => {
  await mockDashboardApi(page, { value: 0 });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?project=project-browser&tab=tasks');
  await expect(page.getByText('Ship Android release', { exact: true })).toBeVisible();
  await expect(page.getByText('No active sprint. Your unfinished work remains in the backlog.')).toBeVisible();
  await expect(page.locator('.work-toolbar').locator('button:visible, select:visible, input:visible')).toHaveCount(5);
  await expect(page.getByRole('button', { name: 'Sprint details' })).toHaveCount(0);
  const boardBounds = await page.locator('.board-column').first().boundingBox();
  expect(boardBounds!.y).toBeLessThanOrEqual(300);
  await page.screenshot({ path: test.info().outputPath('minimal-work-no-sprint-desktop.png') });
});

test('background refresh cannot advance an open task draft version or overwrite remote content', async ({ page }) => {
  const fixture = await mockSprintWorkflow(page);
  await page.goto('/?project=project-browser&tab=tasks&work=task-browser');
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Edit Task' }).click();
  await dialog.getByLabel('Title', { exact: true }).fill('Locally edited title');
  fixture.remoteEdit();
  const refreshedDetail = page.waitForResponse((response) => response.url().includes('/tasks/task-browser?detail=full'));
  // Trigger the same refresh handler as the background controller, without clicking
  // through the modal backdrop (which would intentionally close the drawer).
  await page.getByRole('button', { name: 'Refresh current view' }).dispatchEvent('click');
  expect((await (await refreshedDetail).json()).task.version).toBe(2);
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Task version conflict');
  expect(fixture.patches[0]).toEqual({ title: 'Locally edited title', expected_version: 1 });
  await dialog.getByRole('button', { name: 'Reapply my changes' }).click();
  await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue('Remote description that must survive');
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  await expect(dialog).not.toBeVisible();
  expect(fixture.patches[1]).toEqual({ title: 'Locally edited title', expected_version: 2 });
});

test('background refresh preserves a plan draft and remote ordered links until explicit reapply', async ({ page }) => {
  await mockDashboardApi(page, { value: 0 });
  let current = { ...plan };
  const patches: Record<string, unknown>[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/plans')) return fulfill(route, { items: [{ ...current, content: '', is_compact: true }], total: 1, limit: 50, offset: 0 });
    if (!path.endsWith(`/plans/${plan.id}`)) return route.fallback();
    if (request.method() === 'PATCH') {
      const input = request.postDataJSON();
      patches.push(input);
      if (input.expected_version !== current.version) return fulfill(route, { detail: 'Plan version conflict' }, 409);
      current = { ...current, ...input, version: current.version + 1 };
      return fulfill(route, current);
    }
    return fulfill(route, { plan: current });
  });
  await page.goto('/?project=project-browser&tab=tasks&plan=plan-browser');
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Edit plan' }).click();
  await dialog.getByLabel('Plan title', { exact: true }).fill('Locally edited plan');
  current = { ...current, content: 'Remote design survives', work_item_ids: ['remote-first', task.id], version: 2 };
  const refreshedDetail = page.waitForResponse((response) => response.url().includes(`/plans/${plan.id}`));
  await page.getByRole('button', { name: 'Refresh current view' }).dispatchEvent('click');
  expect((await (await refreshedDetail).json()).plan.version).toBe(2);
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Plan version conflict');
  expect(patches[0]).toEqual({ title: 'Locally edited plan', expected_version: 1 });
  await dialog.getByRole('button', { name: 'Reapply my changes' }).click();
  await expect(dialog.getByRole('textbox', { name: 'Plan content', exact: true })).toHaveValue('Remote design survives');
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  await expect(dialog).not.toBeVisible();
  expect(patches[1]).toEqual({ title: 'Locally edited plan', expected_version: 2 });
  expect(current.work_item_ids).toEqual(['remote-first', task.id]);
});
