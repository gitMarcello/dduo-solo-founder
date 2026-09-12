import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { ApiError, api } from './api';
import type { Plan, ProjectData, Task } from './types';

// Sprint lifecycle and paginated reads have their own component and browser
// tests. Keep these App controller tests focused on the existing Work callbacks.
vi.mock('./SprintWork', async () => {
  const { WorkView } = await import('./WorkView');
  return { SprintWork: WorkView };
});

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ApiError: actual.ApiError,
    api: {
      health: vi.fn(),
      exchangeBrowserSession: vi.fn(),
      logoutBrowserSession: vi.fn(),
      team: vi.fn(),
      operationalManual: vi.fn(),
      createTeamInvitation: vi.fn(),
      revokeTeamMember: vi.fn(),
      updateOperationalManual: vi.fn(),
      createOperationalManualDraft: vi.fn(),
      loadProject: vi.fn(),
      createTask: vi.fn(),
      updateTask: vi.fn(),
      createPlan: vi.fn(),
      updatePlan: vi.fn(),
      uploadTaskAttachment: vi.fn(),
      removeTaskAttachment: vi.fn(),
      taskAttachmentUrl: vi.fn(),
      uploadPlanAttachment: vi.fn(),
      removePlanAttachment: vi.fn(),
      planAttachmentUrl: vi.fn(),
      createBackup: vi.fn(),
      backupDownloadUrl: vi.fn(),
      requestSleep: vi.fn(),
      retrySleep: vi.fn(),
      explainMemory: vi.fn(),
      openSetup: vi.fn(),
      observabilitySummary: vi.fn(),
      observabilityEvents: vi.fn(),
      contextEventDetail: vi.fn(),
    },
  };
});

const task: Task = {
  id: 't1',
  kind: 'task',
  title: 'Release Android',
  description: 'Run final tests',
  status: 'todo',
  priority: 'high',
  labels: ['mobile'],
  dependencies: [],
  attachments: [],
  version: 1,
  created_at: '2026-07-12T10:00:00Z',
  updated_at: '2026-07-12T10:00:00Z',
};
const plan: Plan = {
  id: 'plan-1',
  title: 'Release approach',
  objective: 'Choose the launch sequence',
  content: 'Compare the beta gate and final release path.',
  status: 'decided',
  labels: ['release'],
  work_item_ids: ['t1'],
  attachments: [],
  version: 1,
  created_at: '2026-07-12T10:00:00Z',
  updated_at: '2026-07-12T10:00:00Z',
};
const projectData: ProjectData = {
  project: {
    id: 'p1',
    name: 'Card Project',
    cause: 'Help collectors',
    principles: ['Accurate'],
    objectives: ['Ship Android'],
    profile_version: 1,
  },
  team: {
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
  },
  tasks: [task],
  plans: [],
  events: [
    {
      id: 'e1',
      kind: 'task.create',
      summary: 'Task created',
      actor: 'agent',
      created_at: '2026-07-12T10:00:00Z',
    },
  ],
  memories: [],
  memoryStatus: {
    available: true,
    state: 'updated',
    summary: 'Memory is up to date.',
    jobs: {},
    memories: {},
    latest_job: null,
  },
  sleepJobs: [],
  backup: {
    configured: true,
    configuration_error: '',
    dirty: false,
    automatic_due: false,
    last_backup_at: '2026-07-12T10:00:00Z',
    include_qdrant: true,
    qdrant_collection: 'collection',
    retention: { daily: 7, weekly: 4, monthly: 6 },
    latest: null,
    latest_verified: {
      id: 'b1',
      trigger: 'manual',
      status: 'verified',
      archive_name: 'memory.dduobackup',
      size_bytes: 1200,
      includes_qdrant: true,
      retained: true,
      created_at: '2026-07-12T10:00:00Z',
    },
    items: [],
  },
};

describe('App', () => {
  afterEach(() => {
    cleanup();
    document.documentElement.lang = 'en';
  });

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.history.replaceState(null, '', '/');
    vi.mocked(api.health).mockResolvedValue({
      status: 'ok',
      embedding_provider: 'openai',
      embedding_model: 'large',
    });
    vi.mocked(api.exchangeBrowserSession).mockResolvedValue({
      authenticated: true,
      csrf_token: 'csrf-default',
      current_member: null,
      capabilities: { manage_infrastructure: false, participate_in_project: true },
    });
    vi.mocked(api.logoutBrowserSession).mockResolvedValue({
      logged_out: true,
      current_member: null,
      capabilities: { manage_infrastructure: false, participate_in_project: true },
    });
    vi.mocked(api.team).mockResolvedValue({
      current_member: null,
      capabilities: { manage_infrastructure: false, participate_in_project: true },
      members: [],
    });
    vi.mocked(api.revokeTeamMember).mockResolvedValue({
      current_member: null,
      capabilities: { manage_infrastructure: true, participate_in_project: true },
      members: [],
    });
    vi.mocked(api.operationalManual).mockResolvedValue({
      current_member: null,
      capabilities: { manage_infrastructure: false, participate_in_project: true },
      manual: {
        content: '',
        version: 0,
        characters: 0,
        soft_limit_characters: 4_000,
        hard_limit_characters: 100_000,
        warnings: ['manual_empty'],
      },
    });
    vi.mocked(api.loadProject).mockResolvedValue(projectData);
    vi.mocked(api.createTask).mockResolvedValue({ ...task, id: 't2', title: 'Prepare listing' });
    vi.mocked(api.updateTask).mockResolvedValue({ ...task, status: 'done', version: 2 });
    vi.mocked(api.createPlan).mockResolvedValue(plan);
    vi.mocked(api.updatePlan).mockResolvedValue({ ...plan, status: 'decided', version: 2 });
    vi.mocked(api.uploadTaskAttachment).mockResolvedValue({
      id: 'a2',
      content_hash: 'new-hash',
      kind: 'document',
      filename: 'brief.txt',
      mime_type: 'text/plain',
      size_bytes: 5,
      source_uri: '',
      summary: '',
      created_at: '2026-07-12T10:00:00Z',
      created: true,
      linked: true,
    });
    vi.mocked(api.removeTaskAttachment).mockResolvedValue(undefined);
    vi.mocked(api.taskAttachmentUrl).mockReturnValue('/api/attachment');
    vi.mocked(api.uploadPlanAttachment).mockResolvedValue({
      id: 'pa2',
      content_hash: 'plan-hash',
      kind: 'document',
      filename: 'plan.txt',
      mime_type: 'text/plain',
      size_bytes: 5,
      source_uri: '',
      summary: '',
      created_at: '2026-07-12T10:00:00Z',
      created: true,
      linked: true,
    });
    vi.mocked(api.removePlanAttachment).mockResolvedValue(undefined);
    vi.mocked(api.planAttachmentUrl).mockReturnValue('/api/plan-attachment');
    vi.mocked(api.createBackup).mockResolvedValue({
      id: 'b2',
      trigger: 'manual',
      status: 'verified',
      includes_qdrant: true,
      retained: true,
      created_at: '2026-07-12T11:00:00Z',
    });
    vi.mocked(api.backupDownloadUrl).mockImplementation(
      (projectId, backupId) => `/api/projects/${projectId}/backups/${backupId}/download`,
    );
    vi.mocked(api.requestSleep).mockResolvedValue({ scheduled: 0, items: [] });
    vi.mocked(api.openSetup).mockResolvedValue({ opened: true });
    vi.mocked(api.observabilitySummary).mockResolvedValue(null as never);
    vi.mocked(api.observabilityEvents).mockResolvedValue({ items: [], next_cursor: null });
  });

  it('opens a project and task view directly from a dashboard deep link', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'another-project');
    window.history.replaceState(null, '', '/?project=p1&tab=tasks');
    render(<App />);
    expect(await screen.findByText('Release Android')).toBeInTheDocument();
    expect(api.loadProject).toHaveBeenCalledWith('p1');
    expect(screen.queryByRole('heading', { name: 'Connect a project' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Work/ })).toHaveAttribute('aria-current', 'page');
  });

  it('uses the browser language when no preference has been saved', async () => {
    const browserLanguage = vi.spyOn(window.navigator, 'language', 'get').mockReturnValue('it-IT');
    window.history.replaceState(null, '', '/?project=p1&tab=tasks');
    render(<App />);
    await screen.findByText('Release Android');
    expect(screen.getByRole('button', { name: 'Lavoro' })).toHaveAttribute('aria-current', 'page');
    browserLanguage.mockRestore();
  });

  it('switches the dashboard language without translating project content and persists it', async () => {
    const user = userEvent.setup();
    localStorage.setItem('dduo.dashboard.language', 'en');
    window.history.replaceState(null, '', '/?project=p1&tab=tasks');
    render(<App />);
    await screen.findByText('Release Android');

    await user.click(screen.getByRole('button', { name: /Italian/ }));
    expect(screen.getByRole('button', { name: 'Lavoro' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Panoramica' })).toBeInTheDocument();
    expect(screen.getByText('Release Android')).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('it');
    expect(localStorage.getItem('dduo.dashboard.language')).toBe('it');

    cleanup();
    render(<App />);
    await screen.findByText('Release Android');
    expect(screen.getByRole('button', { name: 'Lavoro' })).toHaveAttribute('aria-current', 'page');
  });

  it('exchanges a one-time browser ticket before loading protected project data', async () => {
    let completeExchange: (() => void) | undefined;
    vi.mocked(api.exchangeBrowserSession).mockImplementation(
      () =>
        new Promise((resolve) => {
          completeExchange = () =>
            resolve({
              authenticated: true,
              csrf_token: 'csrf-ticket',
              current_member: null,
              capabilities: { manage_infrastructure: false, participate_in_project: true },
            });
        }),
    );
    window.history.replaceState(null, '', '/?project=p1&tab=tasks&ticket=dduo_web_secret');
    render(<App />);
    await waitFor(() =>
      expect(api.exchangeBrowserSession).toHaveBeenCalledWith('p1', 'dduo_web_secret'),
    );
    expect(window.location.search).not.toContain('ticket=');
    expect(api.loadProject).not.toHaveBeenCalled();
    completeExchange?.();
    expect(await screen.findByText('Release Android')).toBeInTheDocument();
    expect(api.exchangeBrowserSession).toHaveBeenCalledTimes(1);
  });

  it.each([
    'Retry browser access',
    'Refresh current view',
  ])('retries a transient browser-ticket exchange through %s without restoring the secret to the URL', async (retryButton) => {
    const user = userEvent.setup();
    vi.mocked(api.exchangeBrowserSession)
      .mockRejectedValueOnce(new Error('Gateway temporarily unavailable'))
      .mockResolvedValueOnce({
        authenticated: true,
        csrf_token: 'csrf-retry',
        current_member: null,
        capabilities: { manage_infrastructure: false, participate_in_project: true },
      });
    window.history.replaceState(null, '', '/?project=p1&tab=tasks&ticket=dduo_web_retry');
    render(<App />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The memory server could not be reached',
    );
    expect(window.location.search).not.toContain('ticket=');
    expect(api.loadProject).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: retryButton }));

    expect(await screen.findByText('Release Android')).toBeInTheDocument();
    expect(api.exchangeBrowserSession).toHaveBeenNthCalledWith(2, 'p1', 'dduo_web_retry');
    expect(window.location.search).not.toContain('ticket=');
  });

  it.each([
    'work=t1',
    'plan=plan-1',
  ])('automatically reuses the same seven-day link in new mounts and preserves %s destination', async (destination) => {
    const token = `dduo_link_${'a'.repeat(43)}`;
    const url = `/?project=p1&tab=tasks&${destination}&access_token=${token}`;
    vi.mocked(api.loadProject).mockResolvedValue({ ...projectData, plans: [plan] });
    for (let visit = 1; visit <= 2; visit += 1) {
      window.history.replaceState(null, '', url);
      const view = render(<App />);
      await waitFor(() => expect(api.exchangeBrowserSession).toHaveBeenCalledTimes(visit));
      expect(api.exchangeBrowserSession).toHaveBeenLastCalledWith('p1', token);
      expect(await screen.findByRole('heading', { name: 'Card Project' })).toBeInTheDocument();
      expect(window.location.search).toBe(`?project=p1&tab=tasks&${destination}`);
      expect(screen.queryByRole('button', { name: /confirm/i })).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it.each([
    '?project=p1&project=p2&ticket=dduo_web_secret',
    '?project=p1&ticket=a&ticket=b',
    `?project=p1&access_token=dduo_link_${'a'.repeat(43)}&ticket=dduo_web_secret`,
    '?project=p1&access_token=permanent-token',
    '?access_token=dduo_link_missing_project',
    '?project=p1&project=p2',
  ])('rejects ambiguous or malformed access without loading another project: %s', async (query) => {
    localStorage.setItem('dduo-solo-founder-project', 'unrelated-project');
    window.history.replaceState(null, '', `/${query}`);
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('incomplete or ambiguous');
    expect(api.exchangeBrowserSession).not.toHaveBeenCalled();
    expect(api.loadProject).not.toHaveBeenCalled();
    expect(api.health).not.toHaveBeenCalled();
    expect(window.location.search).not.toContain('access_token=');
    expect(window.location.search).not.toContain('ticket=');
    expect(screen.getByRole('button', { name: 'Work' }).querySelector('small')).toBeNull();
    expect(screen.getByText('Memory status not verified')).toBeInTheDocument();
  });

  it('keeps a valid existing project cookie usable when the supplied link has expired', async () => {
    vi.mocked(api.exchangeBrowserSession).mockRejectedValueOnce(new ApiError('expired', 401));
    window.history.replaceState(
      null,
      '',
      `/?project=p1&tab=tasks&access_token=dduo_link_${'a'.repeat(43)}`,
    );
    render(<App />);
    expect(await screen.findByText('Release Android')).toBeInTheDocument();
    expect(api.team).toHaveBeenCalledWith('p1');
    expect(api.exchangeBrowserSession).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it.each([
    ['access_token', `dduo_link_${'a'.repeat(43)}`, 'Access has expired or is missing'],
    ['ticket', 'dduo_web_expired', 'This older access link has expired or was already used'],
  ])('explains expired %s in IT/EN without retrying rejected credentials', async (parameter, token, expected) => {
    const user = userEvent.setup();
    vi.mocked(api.exchangeBrowserSession).mockRejectedValue(
      new ApiError('secret-error-do-not-display', 401),
    );
    vi.mocked(api.team).mockRejectedValue(new ApiError('unauthorized', 401));
    window.history.replaceState(null, '', `/?project=p1&tab=tasks&work=t1&${parameter}=${token}`);
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent(expected);
    expect(api.loadProject).not.toHaveBeenCalled();
    expect(api.exchangeBrowserSession).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Retry browser access' })).not.toBeInTheDocument();
    expect(screen.queryByText('Memory online')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Italian' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Chiedi al tuo assistente un nuovo link');
    expect(api.exchangeBrowserSession).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe('?project=p1&tab=tasks&work=t1');
    expect(document.body.textContent).not.toContain('secret-error-do-not-display');
  });

  it('handles an unauthorized ordinary deep link without showing an empty project or online memory', async () => {
    vi.mocked(api.loadProject).mockRejectedValueOnce(new ApiError('unauthorized', 401));
    window.history.replaceState(null, '', '/?project=p1&tab=tasks&work=t1');
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Ask your assistant for a new link to this page',
    );
    expect(screen.queryByText('Memory online')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Connect a project' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Work' }).querySelector('small')).toBeNull();
    expect(window.location.search).toBe('?project=p1&tab=tasks&work=t1');
    expect(api.exchangeBrowserSession).not.toHaveBeenCalled();
  });

  it.each([
    'team',
    'observability',
  ])('refreshes the exact project after authorization failure on the %s tab', async (tab) => {
    const user = userEvent.setup();
    vi.mocked(api.loadProject).mockRejectedValueOnce(new ApiError('expired', 401));
    window.history.replaceState(null, '', `/?project=p1&tab=${tab}&work=t1`);
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Access has expired or is missing');
    expect(api.team).not.toHaveBeenCalled();
    expect(api.observabilitySummary).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Refresh current view' }));
    await waitFor(() => expect(api.loadProject).toHaveBeenCalledTimes(2));
    expect(api.loadProject).toHaveBeenLastCalledWith('p1');
    expect(await screen.findByRole('heading', { name: 'Card Project' })).toBeInTheDocument();
    expect(vi.mocked(api.team).mock.calls.every(([projectId]) => projectId === 'p1')).toBe(true);
    expect(
      vi.mocked(api.observabilitySummary).mock.calls.every(([projectId]) => projectId === 'p1'),
    ).toBe(true);
    expect(window.location.search).toBe(`?project=p1&tab=${tab}&work=t1`);
  });

  it('lazily loads observability from a dashboard deep link', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '/?project=p1&tab=observability');
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Card Project' })).toBeInTheDocument();
    await waitFor(() => expect(api.observabilitySummary).toHaveBeenCalledTimes(1));
    expect(api.observabilityEvents).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ range: '7d' }),
    );
    expect(screen.getByRole('button', { name: /Observability/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await user.click(screen.getByTitle('Refresh'));
    await waitFor(() => expect(api.observabilitySummary).toHaveBeenCalledTimes(2));
    expect(api.observabilityEvents).toHaveBeenCalledTimes(2);
    expect(api.observabilityEvents).toHaveBeenLastCalledWith(
      'p1',
      expect.objectContaining({ range: '7d', cursor: undefined }),
    );
  });

  it('renders observability chrome in Italian without changing project content', async () => {
    localStorage.setItem('dduo.dashboard.language', 'it');
    window.history.replaceState(null, '', '/?project=p1&tab=observability');
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Card Project' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Osservabilità/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(
      screen.getByText(/Le metriche iniziano con l'installazione di questa versione/),
    ).toBeInTheDocument();
    expect(screen.getByTitle('Aggiorna')).toBeInTheDocument();
  });

  it('keeps the project team snapshot visible while refreshing it on Team entry', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '/?project=p1&tab=tasks');
    render(<App />);
    await screen.findByText('Release Android');
    expect(api.team).not.toHaveBeenCalled();
    expect(api.operationalManual).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Team' }));
    expect(await screen.findByRole('heading', { name: 'Project team' })).toBeInTheDocument();
    await waitFor(() => expect(api.team).toHaveBeenCalledWith('p1'));
    await waitFor(() => expect(api.operationalManual).toHaveBeenCalledWith('p1'));
    expect(screen.getByText(/Only the Infrastructure manager/)).toBeInTheDocument();
  });

  it('connects a project and navigates shared project data', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText('Project UUID'), 'p1');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    expect(window.location.search).toContain('project=p1');
    expect(await screen.findByRole('heading', { name: 'Card Project' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Overview' }));
    expect(screen.getByText('Help collectors')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    await user.click(screen.getByRole('button', { name: 'Consolidate now' }));
    await waitFor(() => expect(api.requestSleep).toHaveBeenCalledWith('p1'));
    await user.click(screen.getByRole('button', { name: /Work/ }));
    expect(screen.getByText('Release Android')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Activity' }));
    expect(screen.getByText('Task created')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Backup' }));
    expect(screen.getByText('Memory protected')).toBeInTheDocument();
  });

  it('creates and completes tasks through versioned API calls', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    await user.click(screen.getByRole('button', { name: 'New' }));
    await user.click(screen.getByRole('button', { name: 'New task' }));
    await user.type(screen.getByLabelText('Title'), 'Prepare listing');
    await user.selectOptions(screen.getByLabelText('Priority'), 'critical');
    await user.click(screen.getByRole('button', { name: 'Create task' }));
    await waitFor(() =>
      expect(api.createTask).toHaveBeenCalledWith('p1', {
        kind: 'task',
        epic_id: null,
        sprint_id: null,
        title: 'Prepare listing',
        description: '',
        next_action: null,
        status: 'todo',
        priority: 'critical',
        labels: [],
        due_at: null,
      }),
    );
    expect(await screen.findByText('Prepare listing')).toBeInTheDocument();
    const releaseCard = screen.getByText('Release Android').closest('article');
    expect(releaseCard).not.toBeNull();
    fireEvent.dragStart(releaseCard as HTMLElement);
    fireEvent.dragOver(screen.getByRole('group', { name: 'Done' }));
    fireEvent.drop(screen.getByRole('group', { name: 'Done' }));
    await waitFor(() =>
      expect(api.updateTask).toHaveBeenCalledWith('p1', task, {
        status: 'done',
        completion_evidence: 'Completed from dDuo Solo Founder UI',
      }),
    );
  });

  it('creates a Plan before execution and links it to Work', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    await user.click(screen.getByRole('button', { name: 'New' }));
    await user.click(screen.getByRole('button', { name: 'New plan' }));
    await user.type(screen.getByLabelText('Plan title'), 'Release approach');
    await user.type(screen.getByLabelText('Plan outcome'), 'Choose the launch sequence');
    await user.type(
      screen.getByLabelText('Plan content'),
      'Compare the beta gate and final release path.',
    );
    await user.selectOptions(screen.getByLabelText('Plan status'), 'decided');
    await user.click(screen.getByRole('checkbox', { name: /Release Android/ }));
    await user.click(screen.getByRole('button', { name: 'Create plan' }));
    await waitFor(() =>
      expect(api.createPlan).toHaveBeenCalledWith('p1', {
        title: 'Release approach',
        objective: 'Choose the launch sequence',
        content: 'Compare the beta gate and final release path.',
        status: 'decided',
        labels: [],
        work_item_ids: ['t1'],
      }),
    );
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'plans');
    expect(screen.getByText('Release approach')).toBeInTheDocument();
    expect(screen.getByText('Decided')).toBeInTheDocument();
  });

  it('updates a Plan and persists its attachments in project state', async () => {
    const attachment = {
      id: 'plan-file',
      content_hash: 'plan-file-hash',
      kind: 'document' as const,
      filename: 'release-notes.txt',
      mime_type: 'text/plain',
      size_bytes: 5,
      source_uri: '',
      summary: '',
      created_at: '2026-07-12T10:00:00Z',
    };
    vi.mocked(api.loadProject).mockResolvedValue({
      ...projectData,
      plans: [{ ...plan, attachments: [attachment] }],
    });
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'plans');
    await user.click(screen.getByText('Release approach'));
    await user.click(screen.getByRole('button', { name: 'Edit plan' }));
    await user.click(screen.getByTitle('Remove release-notes.txt'));
    await waitFor(() =>
      expect(api.removePlanAttachment).toHaveBeenCalledWith('p1', 'plan-1', 'plan-file'),
    );
    await user.clear(screen.getByLabelText('Plan content'));
    await user.type(screen.getByLabelText('Plan content'), 'The beta gate is ready.');
    const file = new File(['plan'], 'new-plan.txt', { type: 'text/plain' });
    await user.upload(screen.getByLabelText('Add plan images or files'), file);
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(api.updatePlan).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ id: 'plan-1' }),
        expect.objectContaining({ content: 'The beta gate is ready.' }),
      ),
    );
    await waitFor(() =>
      expect(api.uploadPlanAttachment).toHaveBeenCalledWith('p1', 'plan-1', file),
    );
  });

  it('keeps Plan creation and edit failures readable without losing Work context', async () => {
    vi.mocked(api.loadProject).mockResolvedValue({ ...projectData, plans: [plan] });
    vi.mocked(api.createPlan).mockRejectedValue(new Error('Plan could not be created'));
    vi.mocked(api.updatePlan).mockRejectedValue(new Error('Plan version conflict'));
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    await user.click(screen.getByRole('button', { name: 'New' }));
    await user.click(screen.getByRole('button', { name: 'New plan' }));
    await user.type(screen.getByLabelText('Plan title'), 'Failed plan');
    await user.click(screen.getByRole('button', { name: 'Create plan' }));
    expect(
      (await screen.findAllByRole('alert')).some((alert) =>
        alert.textContent?.includes('Plan could not be created'),
      ),
    ).toBe(true);
    await user.click(screen.getByTitle('Close'));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Work view' }), 'plans');
    await user.click(screen.getByText('Release approach'));
    await user.click(screen.getByRole('button', { name: 'Edit plan' }));
    await user.clear(screen.getByLabelText('Plan content'));
    await user.type(screen.getByLabelText('Plan content'), 'Changed approach');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(
      (await screen.findAllByRole('alert')).some((alert) =>
        alert.textContent?.includes('Plan version conflict'),
      ),
    ).toBe(true);
    expect(screen.getAllByText('Release Android').length).toBeGreaterThan(0);
  });

  it('uploads and removes attachments through project state', async () => {
    const attachment = {
      id: 'a1',
      content_hash: 'hash',
      kind: 'document' as const,
      filename: 'old.txt',
      mime_type: 'text/plain',
      size_bytes: 3,
      source_uri: '',
      summary: '',
      created_at: '2026-07-12T10:00:00Z',
    };
    vi.mocked(api.loadProject).mockResolvedValue({
      ...projectData,
      tasks: [{ ...task, attachments: [attachment] }],
    });
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('Release Android');
    await user.click(screen.getByText('Release Android').closest('button') as HTMLButtonElement);
    await user.click(screen.getByRole('button', { name: 'Edit Task' }));
    await user.click(screen.getByTitle('Remove old.txt'));
    await waitFor(() => expect(api.removeTaskAttachment).toHaveBeenCalledWith('p1', 't1', 'a1'));
    const file = new File(['brief'], 'brief.txt', { type: 'text/plain' });
    await user.upload(screen.getByLabelText('Add images or files'), file);
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(api.uploadTaskAttachment).toHaveBeenCalledWith('p1', 't1', file));
  });

  it('shows retrieval failures', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'missing');
    vi.mocked(api.loadProject).mockRejectedValue(new ApiError('project not found', 404));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('project not found');
  });

  it('creates an encrypted backup and refreshes its status', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    await user.click(screen.getByRole('button', { name: 'Backup' }));
    await user.click(screen.getByRole('button', { name: 'Create now' }));
    await waitFor(() => expect(api.createBackup).toHaveBeenCalledWith('p1'));
    await waitFor(() => expect(api.loadProject).toHaveBeenCalledTimes(2));
  });

  it('keeps backup metadata and infrastructure setup outside a project member dashboard', async () => {
    vi.mocked(api.loadProject).mockResolvedValue({
      ...projectData,
      team: {
        current_member: {
          id: 'member-1',
          project_id: 'p1',
          display_name: 'Sam',
          capability: 'project_member',
          capability_label: 'Membro del progetto',
          trusted_local: false,
        },
        capabilities: { manage_infrastructure: false, participate_in_project: true },
        members: [],
      },
      backup: null,
    });
    window.history.replaceState(null, '', '/?project=p1&tab=backup');
    render(<App />);
    expect(await screen.findByText('Release Android')).toBeInTheDocument();
    expect(window.location.search).toContain('tab=tasks');
    expect(screen.queryByRole('button', { name: 'Backup' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Setup' })).not.toBeInTheDocument();
    expect(api.createBackup).not.toHaveBeenCalled();
    expect(api.openSetup).not.toHaveBeenCalled();
  });

  it('keeps host-local Setup outside a remote infrastructure manager dashboard', async () => {
    vi.mocked(api.loadProject).mockResolvedValue({
      ...projectData,
      team: {
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
      },
    });
    window.history.replaceState(null, '', '/?project=p1&tab=setup');
    render(<App />);
    expect(await screen.findByText('Release Android')).toBeInTheDocument();
    expect(window.location.search).toContain('tab=tasks');
    expect(screen.getByRole('button', { name: 'Backup' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Setup' })).not.toBeInTheDocument();
    expect(api.openSetup).not.toHaveBeenCalled();
  });

  it('opens protected Setup without exposing a local URL to the dashboard', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    await user.click(screen.getByRole('button', { name: 'Setup' }));
    await user.click(screen.getByRole('button', { name: 'Open setup' }));
    await waitFor(() => expect(api.openSetup).toHaveBeenCalledWith('p1'));
    expect(window.location.href).not.toContain('token=');
  });

  it('keeps a Setup failure readable in the dashboard', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    vi.mocked(api.openSetup).mockRejectedValue(
      new Error('Local Setup is temporarily unavailable.'),
    );
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    await user.click(screen.getByRole('button', { name: 'Setup' }));
    await user.click(screen.getByRole('button', { name: 'Open setup' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Local Setup is temporarily unavailable.',
    );
  });

  it('refreshes, reports offline health, and switches project', async () => {
    localStorage.setItem('dduo-solo-founder-project', 'p1');
    vi.mocked(api.health).mockRejectedValue(new Error('offline'));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Card Project' });
    expect(screen.getByText('Memory unavailable')).toBeInTheDocument();
    await user.click(screen.getByTitle('Refresh'));
    await waitFor(() => expect(api.loadProject).toHaveBeenCalledTimes(2));
    await user.click(screen.getByTitle('Close browser session and switch project'));
    await waitFor(() => expect(api.logoutBrowserSession).toHaveBeenCalledWith('p1'));
    expect(screen.getByRole('heading', { name: 'Connect a project' })).toBeInTheDocument();
    expect(localStorage.getItem('dduo-solo-founder-project')).toBeNull();
    expect(window.location.search).toBe('');
  });
});
