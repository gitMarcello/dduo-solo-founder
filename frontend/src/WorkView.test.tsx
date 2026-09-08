import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';
import type { Plan, Task } from './types';
import { WorkView } from './WorkView';

const task: Task = {
  id: 'task-1',
  kind: 'task',
  title: 'Task before editing',
  description: '\nOriginal task text  \n',
  status: 'todo',
  priority: 'medium',
  labels: [],
  dependencies: [],
  attachments: [],
  version: 1,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
};
const plan: Plan = {
  id: 'plan-1',
  title: 'Plan before editing',
  objective: 'Original objective',
  content: '\nOriginal plan text  \n',
  status: 'draft',
  labels: [],
  work_item_ids: ['task-1'],
  attachments: [],
  version: 1,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
};
const callbacks = () => ({
  onCreate: vi.fn(),
  onUpdate: vi.fn().mockResolvedValue(task),
  onCreatePlan: vi.fn(),
  onUpdatePlan: vi.fn().mockResolvedValue(plan),
  onStatus: vi.fn(),
  onUpload: vi.fn(),
  onRemoveAttachment: vi.fn(),
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('Work edit concurrency', () => {
  it('can select a project epic beyond the loaded page without overwriting other fields', async () => {
    window.history.replaceState(null, '', '/?work=task-1');
    const props = callbacks();
    const epic: Task = { ...task, id: 'distant-epic', kind: 'epic', title: 'Older project epic' };
    vi.spyOn(api, 'listWork').mockResolvedValue({ items: [epic], total: 1, offset: 0, limit: 25 });
    render(<WorkView projectId="p1" tasks={[task]} loadTask={async () => task} {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Task' }));
    fireEvent.click(screen.getByRole('button', { name: 'Find another epic' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Older project epic' }));
    expect(screen.getByLabelText('Epic')).toHaveValue(epic.id);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(props.onUpdate).toHaveBeenCalledWith(task, { epic_id: epic.id }));
  });

  it('adds distant work to a plan while retaining existing ordered off-page links', async () => {
    window.history.replaceState(null, '', '/?plan=plan-1');
    const props = callbacks();
    const linkedPlan = { ...plan, work_item_ids: ['missing-first', 'task-1'] };
    const distant = { ...task, id: 'distant', title: 'Distant task' };
    vi.spyOn(api, 'listWork').mockResolvedValue({
      items: [distant],
      total: 1,
      offset: 0,
      limit: 25,
    });
    render(
      <WorkView
        projectId="p1"
        tasks={[task]}
        plans={[linkedPlan]}
        loadTask={async () => task}
        {...props}
      />,
    );
    expect(screen.getByRole('button', { name: /missing-first/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit plan' }));
    fireEvent.click(screen.getByRole('button', { name: 'Find more work' }));
    const picker = screen.getByRole('region', { name: 'Choose project work' });
    fireEvent.click(await within(picker).findByRole('button', { name: 'Distant task' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(props.onUpdatePlan).toHaveBeenCalledWith(linkedPlan, {
        work_item_ids: ['missing-first', 'task-1', 'distant'],
      }),
    );
  });

  it('keeps cancelled items visible on the board and preserves the selected mode in links', () => {
    render(<WorkView projectId="p1" tasks={[{ ...task, status: 'cancelled' }]} {...callbacks()} />);
    expect(screen.getByRole('heading', { name: 'Cancelled' })).toBeInTheDocument();
    expect(screen.getByText(task.title)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Work view' }), {
      target: { value: 'list' },
    });
    expect(new URLSearchParams(window.location.search).get('view')).toBe('list');
  });

  it('keeps the task base version and only patches edited fields after a refresh', async () => {
    window.history.replaceState(null, '', '/?work=task-1');
    const props = callbacks();
    const view = render(<WorkView projectId="p1" tasks={[task]} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Task' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'My task title' } });
    view.rerender(
      <WorkView
        projectId="p1"
        tasks={[{ ...task, description: 'Remote task text', version: 2 }]}
        {...props}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(props.onUpdate).toHaveBeenCalledWith(task, { title: 'My task title' }),
    );
  });

  it('takes the latest task snapshot when editing begins, after a reading refresh', async () => {
    window.history.replaceState(null, '', '/?work=task-1');
    const props = callbacks();
    const latest = { ...task, description: 'New text before editing', version: 2 };
    const view = render(<WorkView projectId="p1" tasks={[task]} {...props} />);
    view.rerender(<WorkView projectId="p1" tasks={[latest]} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Task' }));
    expect(screen.getByLabelText('Description')).toHaveValue(latest.description);
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'My title' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(props.onUpdate).toHaveBeenCalledWith(latest, { title: 'My title' }));
  });

  it('keeps the plan base version and ordered links through a refresh', async () => {
    window.history.replaceState(null, '', '/?plan=plan-1');
    const props = callbacks();
    const view = render(<WorkView projectId="p1" tasks={[task]} plans={[plan]} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit plan' }));
    fireEvent.change(screen.getByLabelText('Plan title'), { target: { value: 'My plan title' } });
    view.rerender(
      <WorkView
        projectId="p1"
        tasks={[task]}
        plans={[{ ...plan, content: 'Remote plan text', work_item_ids: [], version: 2 }]}
        {...props}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(props.onUpdatePlan).toHaveBeenCalledWith(plan, { title: 'My plan title' }),
    );
  });

  it('reapplies a task draft on explicit request without resubmitting until reviewed', async () => {
    window.history.replaceState(null, '', '/?work=task-1');
    const props = callbacks();
    props.onUpdate.mockRejectedValueOnce(new ApiError('task version conflict', 409));
    const latest = { ...task, description: 'Remote task text', version: 2 };
    vi.spyOn(api, 'getTask').mockResolvedValue(latest);
    render(<WorkView projectId="p1" tasks={[task]} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Task' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'My title' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reapply my changes' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Description')).toHaveValue('Remote task text'),
    );
    expect(screen.getByLabelText('Title')).toHaveValue('My title');
    expect(props.onUpdate).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(props.onUpdate).toHaveBeenLastCalledWith(latest, { title: 'My title' }),
    );
  });

  it('reloads a plan after a conflict only on explicit request', async () => {
    window.history.replaceState(null, '', '/?plan=plan-1');
    const props = callbacks();
    props.onUpdatePlan.mockRejectedValueOnce(new ApiError('plan version conflict', 409));
    const latest = { ...plan, content: 'Remote plan text', version: 2 };
    vi.spyOn(api, 'getPlan').mockResolvedValue(latest);
    render(<WorkView projectId="p1" tasks={[task]} plans={[plan]} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit plan' }));
    fireEvent.change(screen.getByLabelText('Plan title'), { target: { value: 'Unsaved title' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reload latest' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Plan content')).toHaveValue('Remote plan text'),
    );
    expect(screen.getByLabelText('Plan title')).toHaveValue(plan.title);
    expect(props.onUpdatePlan).toHaveBeenCalledTimes(1);
  });
});

describe('Minimal Work toolbar', () => {
  it('groups creation actions behind one button and keeps focus in the opened editor', async () => {
    const user = userEvent.setup();
    const onCreateSprint = vi.fn();
    render(
      <WorkView projectId="p1" tasks={[task]} {...callbacks()} onCreateSprint={onCreateSprint} />,
    );
    const trigger = screen.getByRole('button', { name: 'New' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'New task' })).not.toBeInTheDocument();
    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const panelId = trigger.getAttribute('aria-controls');
    expect(panelId && document.getElementById(panelId)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'New epic' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'New plan' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'New sprint' }));
    expect(onCreateSprint).toHaveBeenCalledOnce();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: 'New task' }));
    expect(screen.getByRole('dialog')).toHaveFocus();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('closes disclosures on Escape, outside pointers and focus leaving the controls', async () => {
    const user = userEvent.setup();
    render(<WorkView projectId="p1" tasks={[task]} {...callbacks()} />);
    const trigger = screen.getByRole('button', { name: 'New' });
    await user.click(trigger);
    await user.tab();
    expect(screen.getByRole('button', { name: 'New task' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    await user.click(trigger);
    fireEvent.pointerDown(screen.getByRole('combobox', { name: 'Work view' }));
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    await user.click(trigger);
    await user.tab({ shift: true });
    expect(screen.getByRole('textbox', { name: 'Search work' })).toHaveFocus();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('keeps filters hidden until requested and preserves AND matching', async () => {
    const user = userEvent.setup();
    render(
      <WorkView
        projectId="p1"
        tasks={[
          { ...task, labels: ['frontend', 'release'] },
          { ...task, id: 'task-2', title: 'Another task', labels: ['frontend'] },
        ]}
        {...callbacks()}
      />,
    );
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Filters' }));
    await user.click(screen.getByRole('checkbox', { name: 'frontend' }));
    await user.click(screen.getByRole('checkbox', { name: 'release' }));
    expect(screen.getByText(task.title)).toBeVisible();
    expect(screen.queryByText('Another task')).not.toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.getByRole('button', { name: 'Filters 2' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    await user.click(screen.getByRole('button', { name: 'Filters 2' }));
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByText('Another task')).toBeVisible();
    expect(screen.getByRole('checkbox', { name: 'release' })).not.toBeChecked();
  });

  it('keeps a selected label clearable when it no longer exists on the loaded page', async () => {
    const user = userEvent.setup();
    const props = callbacks();
    const view = render(
      <WorkView projectId="p1" tasks={[{ ...task, labels: ['release'] }]} {...props} />,
    );
    await user.click(screen.getByRole('button', { name: 'Filters' }));
    await user.click(screen.getByRole('checkbox', { name: 'release' }));
    await user.keyboard('{Escape}');
    view.rerender(<WorkView projectId="p1" tasks={[task]} {...props} />);
    expect(screen.queryByText(task.title)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Filters 1' }));
    expect(screen.getByRole('checkbox', { name: 'release' })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByText(task.title)).toBeVisible();
    expect(screen.queryByRole('button', { name: /Filters/ })).not.toBeInTheDocument();
  });

  it('keeps project scope, view and search independent', () => {
    const onModeChange = vi.fn();
    const onSearch = vi.fn();
    render(
      <WorkView
        projectId="p1"
        tasks={[task]}
        {...callbacks()}
        scopeControl={
          <select aria-label="Work scope">
            <option>Backlog</option>
          </select>
        }
        onModeChange={onModeChange}
        onSearch={onSearch}
        scopeNotice={<p>No active sprint</p>}
      />,
    );
    expect(screen.getByRole('combobox', { name: 'Work scope' })).toHaveValue('Backlog');
    fireEvent.change(screen.getByRole('combobox', { name: 'Work view' }), {
      target: { value: 'plans' },
    });
    expect(onModeChange).toHaveBeenCalledWith('plans');
    expect(new URLSearchParams(window.location.search).get('view')).toBe('plans');
    fireEvent.change(screen.getByRole('textbox', { name: 'Search work' }), {
      target: { value: 'specific work' },
    });
    expect(onSearch).toHaveBeenCalledWith('specific work');
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    expect(onSearch).toHaveBeenLastCalledWith('');
    expect(screen.getByText('No active sprint')).toBeVisible();
  });
});
