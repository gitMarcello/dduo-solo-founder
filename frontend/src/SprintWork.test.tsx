import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';
import { I18nProvider } from './i18n';
import { SprintWork } from './SprintWork';
import type { Plan, Sprint, Task } from './types';

const sprint: Sprint = {
  id: 's1',
  project_id: 'p1',
  title: 'Current release',
  objective: 'Finish the release',
  status: 'active',
  version: 1,
  started_at: '2026-01-01',
  archived_at: null,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
};
const planned: Sprint = { ...sprint, id: 's2', title: 'Next release', status: 'planned' };
const task: Task = {
  id: 't1',
  kind: 'task',
  title: 'Current task',
  description: 'Full task description',
  status: 'in_progress',
  priority: 'medium',
  sprint_id: 's1',
  labels: [],
  dependencies: [],
  attachments: [],
  version: 1,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
};
const plan: Plan = {
  id: 'p1-plan',
  title: 'Release design',
  objective: 'Release safely',
  content: 'Full design content',
  status: 'decided',
  labels: [],
  work_item_ids: ['t1'],
  attachments: [],
  version: 1,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
};
let sprints: Sprint[];
let tasks: Task[];
let snapshots: Task[];

function props() {
  return {
    projectId: 'p1',
    tasks: [],
    plans: [],
    onCreate: vi.fn(async (input) => {
      const created = { ...task, ...input, id: 'created' };
      tasks.push(created);
      return created;
    }),
    onUpdate: vi.fn(async (base, input) => {
      const updated = { ...base, ...input, version: base.version + 1 };
      tasks = tasks.map((item) => (item.id === base.id ? updated : item));
      return updated;
    }),
    onCreatePlan: vi.fn(async () => plan),
    onUpdatePlan: vi.fn(async (base, input) => ({ ...base, ...input })),
    onStatus: vi.fn(async () => undefined),
    onUpload: vi.fn(async () => undefined),
    onRemoveAttachment: vi.fn(async () => undefined),
  };
}

beforeEach(() => {
  sprints = [{ ...sprint }, { ...planned }];
  tasks = [
    { ...task },
    { ...task, id: 'backlog', title: 'Blocked without sprint', sprint_id: null, status: 'blocked' },
    { ...task, id: 'old', title: 'Previous completed work', sprint_id: null, status: 'done' },
  ];
  snapshots = [];
  vi.spyOn(api, 'listSprints').mockImplementation(async (_project, options = {}) => {
    const items = sprints.filter((item) => !options.status || item.status === options.status);
    return {
      items: items.slice(options.offset ?? 0, (options.offset ?? 0) + 50),
      total: items.length,
      limit: 50,
      offset: options.offset ?? 0,
    };
  });
  vi.spyOn(api, 'getSprint').mockImplementation(async (_project, id) => {
    const found = sprints.find((item) => item.id === id);
    if (!found) throw new Error('Sprint not found');
    return found;
  });
  vi.spyOn(api, 'listWork').mockImplementation(async (_project, options = {}) => {
    const items = tasks.filter(
      (item) =>
        (!options.kind || item.kind === options.kind) &&
        (!options.sprint_id || item.sprint_id === options.sprint_id) &&
        (options.placement !== 'backlog' ||
          (!item.sprint_id && !['done', 'cancelled'].includes(item.status))) &&
        (options.placement !== 'archive' || ['done', 'cancelled'].includes(item.status)) &&
        (!options.q || item.title.toLowerCase().includes(options.q.toLowerCase())),
    );
    const offset = options.offset ?? 0;
    return {
      items: items
        .slice(offset, offset + 50)
        .map((item) => ({ ...item, description: '', is_compact: true })),
      total: items.length,
      limit: 50,
      offset,
    };
  });
  vi.spyOn(api, 'getTask').mockImplementation(async (_project, id) => {
    const found = tasks.find((item) => item.id === id);
    if (!found) throw new Error('Task not found');
    return found;
  });
  vi.spyOn(api, 'listPlans').mockResolvedValue({
    items: [{ ...plan, content: '', is_compact: true }],
    total: 1,
    limit: 50,
    offset: 0,
  });
  vi.spyOn(api, 'getPlan').mockResolvedValue(plan);
  vi.spyOn(api, 'createSprint').mockImplementation(async (_project, input) => {
    const created = { ...planned, id: 's3', title: input.title, objective: input.objective ?? '' };
    sprints.push(created);
    return created;
  });
  vi.spyOn(api, 'startSprint').mockImplementation(async (_project, current) => {
    const updated = { ...current, status: 'active' as const };
    sprints = sprints.map((item) => (item.id === current.id ? updated : item));
    return updated;
  });
  vi.spyOn(api, 'closeSprintPreview').mockResolvedValue({
    sprint: { ...sprint, version: 8 },
    unfinished_count: 1,
    completed_count: 100,
    total: 101,
  });
  vi.spyOn(api, 'archiveSprint').mockImplementation(async (_project, current, input) => {
    snapshots = tasks.filter((item) => item.sprint_id === current.id).map((item) => ({ ...item }));
    tasks = tasks.map((item) =>
      item.sprint_id === current.id && !['done', 'cancelled'].includes(item.status)
        ? { ...item, sprint_id: input.destination_sprint_id ?? null }
        : item,
    );
    const archived = { ...current, status: 'archived' as const, archive_version: current.version };
    sprints = sprints.map((item) => (item.id === current.id ? archived : item));
    return archived;
  });
  vi.spyOn(api, 'archivedSprintTasks').mockImplementation(async () => ({
    items: snapshots,
    total: snapshots.length,
    limit: 50,
    offset: 0,
  }));
  vi.spyOn(api, 'reopenSprint').mockImplementation(async (_project, current) => {
    const updated = { ...current, status: 'planned' as const };
    sprints = sprints.map((item) => (item.id === current.id ? updated : item));
    return updated;
  });
  vi.spyOn(api, 'importSprintHistory').mockImplementation(async (_project, input) => {
    const archived = { ...sprint, id: 'imported', title: input.title, status: 'archived' as const };
    snapshots = tasks.filter((item) => input.task_ids.includes(item.id));
    sprints.push(archived);
    return archived;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '/');
  localStorage.clear();
});

describe('Sprint Work', () => {
  it('keeps sprint objectives collapsed and renders their Markdown on demand', async () => {
    sprints[0].objective = 'Ship **safely** with this sprint.';
    render(<SprintWork {...props()} />);
    const disclosure = await screen.findByRole('button', { name: 'Sprint details' });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('safely')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Work scope')).toHaveValue('current');
    fireEvent.click(disclosure);
    const objective = screen.getByText('safely');
    expect(disclosure).toHaveAttribute('aria-expanded', 'true');
    expect(objective).toBeVisible();
    expect(objective.tagName).toBe('STRONG');
  });

  it('closes the objective when changing the selected sprint', async () => {
    sprints[1].objective = 'Prepare **next steps**.';
    render(<SprintWork {...props()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sprint details' }));
    expect(screen.getByText('Finish the release')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Work scope'), { target: { value: 'current:s2' } });
    expect(await screen.findByRole('button', { name: 'Sprint details' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(screen.queryByText('next steps')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Work scope')).toHaveDisplayValue('Next release');
    fireEvent.click(screen.getByRole('button', { name: 'Sprint details' }));
    expect(screen.getByText('next steps')).toBeVisible();
  });

  it('avoids duplicate scope headings and keeps page counts next to the work summary', async () => {
    window.history.replaceState(null, '', '/?placement=all');
    const { container } = render(<SprintWork {...props()} />);
    await screen.findByText('Current task');
    expect(screen.getByLabelText('Work scope')).toHaveValue('all');
    expect(screen.queryByRole('heading', { name: 'All work' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sprint details' })).not.toBeInTheDocument();
    expect(container.querySelector('.work-summary-group')).toHaveTextContent('1–3 of 3 items');
    expect(container.querySelector('.work-summary-group')).toHaveTextContent(
      'Task counts on this page.',
    );
    expect(screen.queryByRole('button', { name: 'New sprint' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(screen.getByRole('button', { name: 'New sprint' })).toBeVisible();
  });

  it('keeps no-sprint projects usable and treats blocked unassigned tasks as backlog', async () => {
    sprints = [];
    render(<SprintWork {...props()} />);
    expect(await screen.findByText('Blocked without sprint')).toBeInTheDocument();
    expect(
      screen.getByText('No active sprint. Your unfinished work remains in the backlog.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Previous completed work')).not.toBeInTheDocument();
    expect(api.listWork).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ placement: 'backlog' }),
    );
  });

  it('paginates the whole inventory beyond one hundred and searches unloaded work', async () => {
    tasks = Array.from({ length: 123 }, (_, index) => ({
      ...task,
      id: `t${index}`,
      title: `Item ${String(index).padStart(3, '0')}`,
      sprint_id: null,
      status: 'done',
    }));
    render(<SprintWork {...props()} />);
    fireEvent.change(screen.getByLabelText('Work scope'), { target: { value: 'all' } });
    expect(await screen.findByText('Item 000')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(await screen.findByText('Item 050')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(await screen.findByText('Item 122')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    await screen.findByText('Item 050');
    fireEvent.change(screen.getByLabelText('Search work'), { target: { value: 'Item 122' } });
    await waitFor(() =>
      expect(api.listWork).toHaveBeenLastCalledWith(
        'p1',
        expect.objectContaining({ q: 'Item 122', offset: 0 }),
      ),
    );
    expect(await screen.findByText('Item 122')).toBeInTheDocument();
  });

  it('creates a planned sprint with an optional objective and starts it explicitly', async () => {
    sprints = [];
    render(<SprintWork {...props()} />);
    await screen.findByText('Blocked without sprint');
    fireEvent.click(screen.getByRole('button', { name: 'New' }));
    fireEvent.click(screen.getByRole('button', { name: 'New sprint' }));
    const dialog = screen.getByRole('dialog', { name: 'New sprint' });
    fireEvent.change(within(dialog).getByLabelText('Sprint name'), {
      target: { value: 'Autumn release' },
    });
    fireEvent.change(within(dialog).getByLabelText('Objective (optional)'), {
      target: { value: 'Ship sharing' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create sprint' }));
    await waitFor(() =>
      expect(api.createSprint).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({
          title: 'Autumn release',
          objective: 'Ship sharing',
          idempotency_key: expect.any(String),
        }),
      ),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Sprint details' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start sprint' }));
    await waitFor(() => expect(api.startSprint).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: 'Sprint details' }));
    expect(await screen.findByRole('button', { name: 'Close and archive' })).toBeInTheDocument();
  });

  it('requires explicit carryover and archives using the preview version', async () => {
    render(<SprintWork {...props()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sprint details' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close and archive' }));
    const dialog = await screen.findByRole('dialog', { name: 'Close and archive' });
    expect(
      within(dialog).getByText('100 completed · 1 unfinished · 101 total'),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Archive sprint' })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Move unfinished tasks to'), {
      target: { value: 's2' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive sprint' }));
    await waitFor(() =>
      expect(api.archiveSprint).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ version: 8 }),
        expect.objectContaining({ unfinished_destination: 'sprint', destination_sprint_id: 's2' }),
      ),
    );
    await waitFor(() => expect(api.archivedSprintTasks).toHaveBeenCalled());
    expect(tasks.find((item) => item.id === 't1')?.status).toBe('in_progress');
    fireEvent.click(await screen.findByRole('button', { name: 'Sprint details' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reopen as planned' }));
    await waitFor(() => expect(api.reopenSprint).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('Work scope'), { target: { value: 'archive:s1' } });
    await waitFor(() =>
      expect(api.archivedSprintTasks).toHaveBeenLastCalledWith('p1', 's1', expect.any(Object)),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Sprint details' }));
    expect(screen.queryByRole('button', { name: 'Start sprint' })).not.toBeInTheDocument();
  });

  it('fetches an exact archived task deep link even outside the loaded page', async () => {
    window.history.replaceState(null, '', '/?placement=archive&work=old');
    vi.mocked(api.listWork).mockResolvedValue({ items: [], total: 123, limit: 50, offset: 0 });
    render(<SprintWork {...props()} />);
    const dialog = await screen.findByRole('dialog', { name: 'Previous completed work' });
    expect(within(dialog).getByText('Full task description')).toBeInTheDocument();
    expect(api.getTask).toHaveBeenCalledWith('p1', 'old');
    expect(window.location.search).toContain('work=old');
  });

  it('moves backlog work into a selected sprint with a versioned field patch', async () => {
    const handlers = props();
    render(<SprintWork {...handlers} />);
    fireEvent.change(screen.getByLabelText('Work scope'), { target: { value: 'backlog' } });
    fireEvent.click(await screen.findByText('Blocked without sprint'));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Task' }));
    fireEvent.change(screen.getByLabelText('Sprint'), { target: { value: 's2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(handlers.onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'backlog', version: 1 }),
        { sprint_id: 's2' },
      ),
    );
  });

  it('groups only explicitly selected completed legacy work', async () => {
    render(<SprintWork {...props()} />);
    fireEvent.change(screen.getByLabelText('Work scope'), { target: { value: 'archive' } });
    await screen.findByText('Previous completed work');
    fireEvent.click(screen.getByRole('button', { name: 'Group selected past work' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Previous completed work' }));
    fireEvent.change(screen.getByLabelText('Historical collection name'), {
      target: { value: 'Earlier development' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Archive 1 selected tasks' }));
    await waitFor(() =>
      expect(api.importSprintHistory).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ title: 'Earlier development', task_ids: ['old'] }),
      ),
    );
  });

  it('does not offer historical task grouping for epics or stale epic pages', async () => {
    tasks.push({
      ...task,
      id: 'completed-epic',
      title: 'Completed project epic',
      kind: 'epic',
      status: 'done',
      sprint_id: null,
    });
    render(<SprintWork {...props()} />);
    fireEvent.change(screen.getByLabelText('Work scope'), { target: { value: 'archive' } });
    await screen.findByText('Previous completed work');
    fireEvent.click(screen.getByRole('button', { name: 'Group selected past work' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Work view' }), {
      target: { value: 'epics' },
    });
    await screen.findAllByText('Completed project epic');
    expect(screen.getByRole('combobox', { name: 'Work scope' })).toBeDisabled();
    expect(
      screen.getByText('Epics and plans belong to the whole project and can span several sprints.'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Group selected past work' }),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Work view' }), {
      target: { value: 'board' },
    });
    expect(screen.getByRole('combobox', { name: 'Work scope' })).toBeEnabled();
    expect(
      screen.queryByRole('checkbox', { name: 'Completed project epic' }),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByRole('checkbox', { name: 'Previous completed work' }),
    ).toBeInTheDocument();
    expect(api.importSprintHistory).not.toHaveBeenCalled();
  });

  it('loads plans separately and fetches their full content only when opened', async () => {
    render(<SprintWork {...props()} />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Work view' }), {
      target: { value: 'plans' },
    });
    expect(screen.getByRole('combobox', { name: 'Work scope' })).toBeDisabled();
    fireEvent.click(await screen.findByText('Release design'));
    const dialog = await screen.findByRole('dialog', { name: 'Release design' });
    expect(within(dialog).getByText('Full design content')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit plan' }));
    expect(within(dialog).getByLabelText('Plan status')).toContainHTML('value="completed"');
  });

  it('reports failed loads and recovers on retry', async () => {
    vi.mocked(api.listSprints).mockRejectedValueOnce(new Error('Offline'));
    render(<SprintWork {...props()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Offline');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Current task')).toBeInTheDocument();
  });

  it('preserves archive errors and does not silently retry a conflicting closure', async () => {
    vi.mocked(api.archiveSprint).mockRejectedValue(new ApiError('Sprint version conflict', 409));
    render(<SprintWork {...props()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sprint details' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close and archive' }));
    const dialog = await screen.findByRole('dialog', { name: 'Close and archive' });
    fireEvent.change(within(dialog).getByLabelText('Move unfinished tasks to'), {
      target: { value: 'backlog' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive sprint' }));
    await waitFor(() =>
      expect(within(dialog).getByRole('alert')).toHaveTextContent('Sprint version conflict'),
    );
    expect(api.archiveSprint).toHaveBeenCalledTimes(1);
  });

  it('provides the lifecycle controls in Italian', async () => {
    localStorage.setItem('dduo.dashboard.language', 'it');
    render(
      <I18nProvider>
        <SprintWork {...props()} />
      </I18nProvider>,
    );
    expect(await screen.findByRole('option', { name: 'Sprint corrente' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Archivio' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Tutto il lavoro' })).toBeInTheDocument();
  });
});
