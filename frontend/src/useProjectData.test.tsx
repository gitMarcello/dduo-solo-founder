import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';
import type { Plan, ProjectData, Task, TaskAttachment } from './types';
import { useProjectData } from './useProjectData';

const plan: Plan = {
  id: 'plan-1',
  title: 'Release approach',
  objective: 'Choose the launch sequence',
  content: 'Compare the beta gate and the final release path.',
  status: 'decided',
  labels: ['release'],
  work_item_ids: [],
  attachments: [],
  version: 1,
  created_at: '2026-07-21T12:00:00Z',
  updated_at: '2026-07-21T12:00:00Z',
};

const task: Task = {
  id: 'task-1',
  kind: 'task',
  title: 'Release Android',
  description: 'Run the release checks.',
  status: 'todo',
  priority: 'high',
  labels: ['release'],
  dependencies: [],
  attachments: [],
  version: 1,
  created_at: '2026-07-21T12:00:00Z',
  updated_at: '2026-07-21T12:00:00Z',
};

function projectData(attachments: TaskAttachment[] = []): ProjectData {
  return {
    project: {
      id: 'project-1',
      name: 'Release project',
      cause: 'Ship safely',
      principles: [],
      objectives: [],
      profile_version: 1,
    },
    team: {
      current_member: null,
      capabilities: { manage_infrastructure: true, participate_in_project: true },
      members: [],
    },
    tasks: [task],
    plans: [{ ...plan, attachments }],
    events: [],
    memories: [],
    memoryStatus: {
      available: true,
      state: 'updated',
      summary: 'Memory is current.',
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
      include_qdrant: true,
      qdrant_collection: 'project-1',
      retention: { daily: 7, weekly: 4, monthly: 12 },
      latest: null,
      latest_verified: null,
      items: [],
    },
  };
}

describe('useProjectData', () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, '', '/');
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('keeps Plan attachment failures visible and recovers without losing local state', async () => {
    const existing: TaskAttachment = {
      id: 'existing-file',
      content_hash: 'existing-hash',
      kind: 'document' as const,
      filename: 'existing.txt',
      mime_type: 'text/plain',
      size_bytes: 8,
      source_uri: '',
      summary: '',
      created_at: '2026-07-21T12:00:00Z',
    };
    const uploaded = {
      id: 'new-file',
      content_hash: 'new-hash',
      kind: 'document' as const,
      filename: 'approach.txt',
      mime_type: 'text/plain',
      size_bytes: 8,
      source_uri: '',
      summary: '',
      created_at: '2026-07-21T12:01:00Z',
      created: true,
      linked: true,
    };
    const load = vi.spyOn(api, 'loadProject').mockResolvedValue(projectData([existing]));
    const upload = vi.spyOn(api, 'uploadPlanAttachment');
    const remove = vi.spyOn(api, 'removePlanAttachment');
    localStorage.setItem('dduo-solo-founder-project', 'project-1');

    const { result } = renderHook(() => useProjectData());
    await waitFor(() => expect(load).toHaveBeenCalledWith('project-1'));
    await waitFor(() => expect(result.current.data?.plans[0].attachments).toEqual([existing]));

    const file = new File(['approach'], 'approach.txt', { type: 'text/plain' });
    upload.mockRejectedValueOnce(new Error('Plan upload unavailable'));
    await act(async () => {
      await expect(result.current.uploadPlanAttachments(plan, [file])).rejects.toThrow(
        'Plan upload unavailable',
      );
    });
    expect(result.current.error).toBe('Plan upload unavailable');
    expect(result.current.data?.plans[0].attachments).toEqual([existing]);

    upload.mockResolvedValueOnce(uploaded);
    await act(async () => {
      await result.current.uploadPlanAttachments(plan, [file]);
    });
    expect(result.current.data?.plans[0].attachments).toEqual([existing, uploaded]);

    remove.mockRejectedValueOnce('Plan attachment cannot be removed');
    await act(async () => {
      await expect(result.current.removePlanAttachment(plan, existing.id)).rejects.toBe(
        'Plan attachment cannot be removed',
      );
    });
    expect(result.current.error).toBe('Plan attachment cannot be removed');
    expect(result.current.data?.plans[0].attachments).toEqual([existing, uploaded]);

    remove.mockResolvedValueOnce(undefined);
    await act(async () => {
      await result.current.removePlanAttachment(plan, existing.id);
    });
    expect(result.current.data?.plans[0].attachments).toEqual([uploaded]);
  });

  it('keeps failed Work and lifecycle operations recoverable in the dashboard', async () => {
    const load = vi.spyOn(api, 'loadProject').mockResolvedValue(projectData());
    vi.spyOn(api, 'createTask').mockRejectedValueOnce('Task creation unavailable');
    const updateTask = vi
      .spyOn(api, 'updateTask')
      .mockResolvedValueOnce({ ...task, status: 'done', version: 2 })
      .mockResolvedValueOnce({ ...task, status: 'in_progress', version: 3 })
      .mockRejectedValueOnce(new Error('Task update unavailable'));
    vi.spyOn(api, 'uploadTaskAttachment').mockRejectedValueOnce('Task upload unavailable');
    vi.spyOn(api, 'removeTaskAttachment').mockRejectedValueOnce(
      new Error('Task removal unavailable'),
    );
    const backup = vi.spyOn(api, 'createBackup').mockRejectedValueOnce('Backup unavailable');
    const sleep = vi
      .spyOn(api, 'requestSleep')
      .mockResolvedValueOnce({ scheduled: 0, items: [] })
      .mockRejectedValueOnce(new Error('Sleep unavailable'));
    const retry = vi.spyOn(api, 'retrySleep').mockRejectedValueOnce('Retry unavailable');
    localStorage.setItem('dduo-solo-founder-project', 'project-1');

    const { result } = renderHook(() => useProjectData());
    await waitFor(() => expect(load).toHaveBeenCalledWith('project-1'));
    await waitFor(() => expect(result.current.data?.project.id).toBe('project-1'));

    await act(async () => {
      await expect(
        result.current.createTask({
          kind: 'task',
          title: 'Follow up',
          priority: 'medium',
          labels: [],
        }),
      ).rejects.toBe('Task creation unavailable');
    });
    expect(result.current.error).toBe('Task creation unavailable');

    await act(async () => {
      await result.current.setTaskStatus(task, 'done');
      await result.current.setTaskStatus(task, 'in_progress');
    });
    expect(updateTask).toHaveBeenNthCalledWith(1, 'project-1', task, {
      status: 'done',
      completion_evidence: 'Completed from dDuo Solo Founder UI',
    });
    expect(updateTask).toHaveBeenNthCalledWith(2, 'project-1', task, {
      status: 'in_progress',
      completion_evidence: null,
    });

    await act(async () => {
      await expect(result.current.updateTask(task, { title: 'Updated release' })).rejects.toThrow(
        'Task update unavailable',
      );
    });
    expect(result.current.error).toBe('Task update unavailable');

    const file = new File(['brief'], 'brief.txt', { type: 'text/plain' });
    await act(async () => {
      await expect(result.current.uploadTaskAttachments(task, [file])).rejects.toBe(
        'Task upload unavailable',
      );
    });
    expect(result.current.error).toBe('Task upload unavailable');

    await act(async () => {
      await expect(result.current.removeTaskAttachment(task, 'missing')).rejects.toThrow(
        'Task removal unavailable',
      );
    });
    expect(result.current.error).toBe('Task removal unavailable');

    await act(async () => {
      await expect(result.current.backupNow()).rejects.toBe('Backup unavailable');
    });
    expect(backup).toHaveBeenCalledWith('project-1');
    expect(result.current.error).toBe('Backup unavailable');

    await act(async () => {
      await result.current.sleepNow();
    });
    expect(sleep).toHaveBeenCalledWith('project-1');

    await act(async () => {
      await expect(result.current.sleepNow()).rejects.toThrow('Sleep unavailable');
    });
    expect(result.current.error).toBe('Sleep unavailable');

    await act(async () => {
      await expect(result.current.retrySleep('job-1')).rejects.toBe('Retry unavailable');
    });
    expect(retry).toHaveBeenCalledWith('project-1', 'job-1');
    expect(result.current.error).toBe('Retry unavailable');
  });

  it('never publishes a late project response after switching projects', async () => {
    let resolveFirst: ((value: ProjectData) => void) | undefined;
    const second = {
      ...projectData(),
      project: { ...projectData().project, id: 'project-2', name: 'Second project' },
    };
    const load = vi.spyOn(api, 'loadProject').mockImplementation((projectId) =>
      projectId === 'project-1'
        ? new Promise((resolve) => {
            resolveFirst = resolve;
          })
        : Promise.resolve(second),
    );
    localStorage.setItem('dduo-solo-founder-project', 'project-1');

    const { result } = renderHook(() => useProjectData());
    await waitFor(() => expect(load).toHaveBeenCalledWith('project-1'));
    act(() => result.current.connect('project-2'));
    await waitFor(() => expect(result.current.data?.project.id).toBe('project-2'));

    await act(async () => resolveFirst?.(projectData()));
    expect(result.current.data?.project.id).toBe('project-2');
    expect(localStorage.getItem('dduo-solo-founder-project')).toBe('project-2');
  });

  it('does not leave foreground loading stuck when a newer background refresh wins', async () => {
    let foreground: ((value: ProjectData) => void) | undefined;
    let background: ((value: ProjectData) => void) | undefined;
    const load = vi
      .spyOn(api, 'loadProject')
      .mockResolvedValueOnce(projectData())
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            foreground = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            background = resolve;
          }),
      );
    localStorage.setItem('dduo-solo-founder-project', 'project-1');
    const { result } = renderHook(() => useProjectData());
    await waitFor(() => expect(result.current.data?.project.id).toBe('project-1'));

    let foregroundRefresh: Promise<void> | undefined;
    let backgroundRefresh: Promise<void> | undefined;
    act(() => {
      foregroundRefresh = result.current.refresh();
      backgroundRefresh = result.current.refresh({ background: true });
    });
    expect(result.current.loading).toBe(true);

    await act(async () => {
      foreground?.(projectData());
      await foregroundRefresh;
      background?.(projectData());
      await backgroundRefresh;
    });

    expect(load).toHaveBeenCalledTimes(3);
    expect(result.current.loading).toBe(false);
  });

  it('closes the browser session before forgetting the selected project', async () => {
    vi.spyOn(api, 'loadProject').mockResolvedValue(projectData());
    const logout = vi.spyOn(api, 'logoutBrowserSession').mockResolvedValue({
      logged_out: true,
      current_member: null,
      capabilities: { manage_infrastructure: true, participate_in_project: true },
    });
    localStorage.setItem('dduo-solo-founder-project', 'project-1');
    const { result } = renderHook(() => useProjectData());
    await waitFor(() => expect(result.current.data?.project.id).toBe('project-1'));

    await act(async () => result.current.disconnect());

    expect(logout).toHaveBeenCalledWith('project-1');
    expect(result.current.projectId).toBe('');
    expect(result.current.data).toBeNull();
    expect(localStorage.getItem('dduo-solo-founder-project')).toBeNull();
  });

  it('clears an already invalid session but retains the project when logout cannot be confirmed', async () => {
    vi.spyOn(api, 'loadProject').mockResolvedValue(projectData());
    const logout = vi
      .spyOn(api, 'logoutBrowserSession')
      .mockRejectedValueOnce(new ApiError('Session expired', 401))
      .mockRejectedValueOnce(new Error('Gateway unavailable'));
    localStorage.setItem('dduo-solo-founder-project', 'project-1');
    const first = renderHook(() => useProjectData());
    await waitFor(() => expect(first.result.current.data?.project.id).toBe('project-1'));

    await act(async () => first.result.current.disconnect());
    expect(first.result.current.projectId).toBe('');
    first.unmount();

    localStorage.setItem('dduo-solo-founder-project', 'project-1');
    const second = renderHook(() => useProjectData());
    await waitFor(() => expect(second.result.current.data?.project.id).toBe('project-1'));
    await act(async () => second.result.current.disconnect());

    expect(logout).toHaveBeenCalledTimes(2);
    expect(second.result.current.projectId).toBe('project-1');
    expect(second.result.current.error).toBe(
      'Browser session could not be closed: Gateway unavailable',
    );
    expect(localStorage.getItem('dduo-solo-founder-project')).toBe('project-1');
  });
});
