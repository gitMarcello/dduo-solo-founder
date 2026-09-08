import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from './api';
import type {
  Plan,
  PlanCreateInput,
  PlanUpdateInput,
  ProjectData,
  Task,
  TaskAttachment,
  TaskCreateInput,
  TaskStatus,
  TaskUpdateInput,
} from './types';

const STORAGE_KEY = 'dduo-solo-founder-project';

function countsAsOpen(task: Task) {
  return Number(task.kind === 'task' && !['done', 'cancelled'].includes(task.status));
}

function projectFromUrl() {
  return new URLSearchParams(window.location.search).get('project')?.trim() ?? '';
}

function replaceProjectInUrl(projectId: string) {
  const url = new URL(window.location.href);
  if (projectId) {
    url.searchParams.set('project', projectId);
  } else {
    url.searchParams.delete('project');
    url.searchParams.delete('tab');
  }
  window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
}

export function useProjectData(enabled = true) {
  const [projectId, setProjectId] = useState(
    () => projectFromUrl() || localStorage.getItem(STORAGE_KEY) || '',
  );
  const [data, setData] = useState<ProjectData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [backingUp, setBackingUp] = useState(false);
  const [schedulingSleep, setSchedulingSleep] = useState(false);
  const requestSequence = useRef(0);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;

  const refresh = useCallback(
    async ({ background = false }: { background?: boolean } = {}) => {
      if (!enabled || !projectId) return;
      const sequence = ++requestSequence.current;
      if (!background) {
        setLoading(true);
        setError('');
      }
      try {
        const value = await api.loadProject(projectId);
        if (sequence === requestSequence.current && currentProject.current === projectId) {
          setData(value);
          localStorage.setItem(STORAGE_KEY, projectId);
        }
      } catch (reason) {
        if (
          !background &&
          sequence === requestSequence.current &&
          currentProject.current === projectId
        ) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (sequence === requestSequence.current && currentProject.current === projectId) {
          setLoading(false);
        }
      }
    },
    [enabled, projectId],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const hasBackgroundWork = Boolean(
    data &&
      ((data.memoryStatus.jobs.pending ?? 0) + (data.memoryStatus.jobs.running ?? 0) > 0 ||
        ['scheduled', 'running'].includes(data.backup?.latest?.status ?? '')),
  );

  useEffect(() => {
    if (!hasBackgroundWork) return;
    const timer = window.setInterval(() => void refresh({ background: true }), 2_500);
    return () => window.clearInterval(timer);
  }, [hasBackgroundWork, refresh]);

  function connect(nextProjectId: string) {
    const normalized = nextProjectId.trim();
    if (!normalized) return;
    requestSequence.current += 1;
    currentProject.current = normalized;
    setData(null);
    setError('');
    setBackingUp(false);
    setSchedulingSleep(false);
    replaceProjectInUrl(normalized);
    setProjectId(normalized);
  }

  async function disconnect() {
    const departingProject = currentProject.current;
    requestSequence.current += 1;
    if (departingProject) {
      try {
        await api.logoutBrowserSession(departingProject);
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 401) {
          // The server-side session is already unusable; clearing the local
          // selection is safe and a future ticket will replace the stale cookie.
        } else {
          if (currentProject.current === departingProject) {
            setError(
              reason instanceof Error
                ? `Browser session could not be closed: ${reason.message}`
                : `Browser session could not be closed: ${String(reason)}`,
            );
          }
          return;
        }
      }
    }
    if (currentProject.current !== departingProject) return;
    currentProject.current = '';
    localStorage.removeItem(STORAGE_KEY);
    replaceProjectInUrl('');
    setProjectId('');
    setData(null);
    setError('');
    setBackingUp(false);
    setSchedulingSleep(false);
  }

  async function createTask(input: TaskCreateInput) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      const task = await api.createTask(activeProjectId, input);
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? {
                ...current,
                tasks: [task, ...current.tasks],
                openTaskCount:
                  current.openTaskCount === undefined
                    ? undefined
                    : current.openTaskCount + countsAsOpen(task),
              }
            : current,
        );
      }
      return task;
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function updateTask(task: Task, input: TaskUpdateInput) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      const updated = await api.updateTask(activeProjectId, task, input);
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? {
                ...current,
                tasks: current.tasks.map((item) => (item.id === updated.id ? updated : item)),
                openTaskCount:
                  current.openTaskCount === undefined
                    ? undefined
                    : Math.max(
                        0,
                        current.openTaskCount + countsAsOpen(updated) - countsAsOpen(task),
                      ),
              }
            : current,
        );
      }
      return updated;
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function createPlan(input: PlanCreateInput) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      const plan = await api.createPlan(activeProjectId, input);
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? { ...current, plans: [plan, ...current.plans] }
            : current,
        );
      }
      return plan;
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function updatePlan(plan: Plan, input: PlanUpdateInput) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      const updated = await api.updatePlan(activeProjectId, plan, input);
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? {
                ...current,
                plans: current.plans.map((item) => (item.id === updated.id ? updated : item)),
              }
            : current,
        );
      }
      return updated;
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function setTaskStatus(task: Task, status: TaskStatus) {
    if (!data) return;
    const activeProjectId = data.project.id;
    try {
      await updateTask(task, {
        status,
        completion_evidence: status === 'done' ? 'Completed from dDuo Solo Founder UI' : null,
      });
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function uploadTaskAttachments(task: Task, files: File[]) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      const attachments: TaskAttachment[] = [];
      for (const file of files) {
        attachments.push(await api.uploadTaskAttachment(activeProjectId, task.id, file));
      }
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? {
                ...current,
                tasks: current.tasks.map((item) =>
                  item.id === task.id
                    ? {
                        ...item,
                        attachments: [
                          ...item.attachments,
                          ...attachments.filter(
                            (attachment) =>
                              !item.attachments.some((current) => current.id === attachment.id),
                          ),
                        ],
                      }
                    : item,
                ),
              }
            : current,
        );
      }
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function removeTaskAttachment(task: Task, artifactId: string) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      await api.removeTaskAttachment(activeProjectId, task.id, artifactId);
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? {
                ...current,
                tasks: current.tasks.map((item) =>
                  item.id === task.id
                    ? {
                        ...item,
                        attachments: item.attachments.filter((item) => item.id !== artifactId),
                      }
                    : item,
                ),
              }
            : current,
        );
      }
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function uploadPlanAttachments(plan: Plan, files: File[]) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      const attachments: TaskAttachment[] = [];
      for (const file of files) {
        attachments.push(await api.uploadPlanAttachment(activeProjectId, plan.id, file));
      }
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? {
                ...current,
                plans: current.plans.map((item) =>
                  item.id === plan.id
                    ? {
                        ...item,
                        attachments: [
                          ...item.attachments,
                          ...attachments.filter(
                            (attachment) =>
                              !item.attachments.some((current) => current.id === attachment.id),
                          ),
                        ],
                      }
                    : item,
                ),
              }
            : current,
        );
      }
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function removePlanAttachment(plan: Plan, artifactId: string) {
    if (!data) throw new Error('Project is not connected');
    const activeProjectId = data.project.id;
    try {
      await api.removePlanAttachment(activeProjectId, plan.id, artifactId);
      if (currentProject.current === activeProjectId) {
        setData((current) =>
          current?.project.id === activeProjectId
            ? {
                ...current,
                plans: current.plans.map((item) =>
                  item.id === plan.id
                    ? {
                        ...item,
                        attachments: item.attachments.filter((item) => item.id !== artifactId),
                      }
                    : item,
                ),
              }
            : current,
        );
      }
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  async function backupNow() {
    if (!data?.backup || !data.team.capabilities.manage_infrastructure) return;
    const activeProjectId = data.project.id;
    setBackingUp(true);
    setError('');
    try {
      await api.createBackup(activeProjectId);
      const value = await api.loadProject(activeProjectId);
      if (currentProject.current === activeProjectId) setData(value);
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (currentProject.current === activeProjectId) setBackingUp(false);
    }
  }

  async function sleepNow() {
    if (!data) return;
    const activeProjectId = data.project.id;
    const activeJobs =
      (data.memoryStatus.jobs.pending ?? 0) + (data.memoryStatus.jobs.running ?? 0);
    if (activeJobs > 0 || schedulingSleep) {
      await refresh({ background: true });
      return;
    }
    setSchedulingSleep(true);
    setError('');
    try {
      await api.requestSleep(activeProjectId);
      await refresh({ background: true });
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (currentProject.current === activeProjectId) setSchedulingSleep(false);
    }
  }

  async function retrySleep(jobId: string) {
    if (!data) return;
    const activeProjectId = data.project.id;
    setError('');
    try {
      await api.retrySleep(activeProjectId, jobId);
      const value = await api.loadProject(activeProjectId);
      if (currentProject.current === activeProjectId) setData(value);
    } catch (reason) {
      if (currentProject.current === activeProjectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    }
  }

  return {
    projectId,
    data,
    loading,
    error,
    backingUp,
    schedulingSleep,
    connect,
    disconnect,
    refresh,
    createTask,
    updateTask,
    createPlan,
    updatePlan,
    setTaskStatus,
    uploadTaskAttachments,
    removeTaskAttachment,
    uploadPlanAttachments,
    removePlanAttachment,
    backupNow,
    sleepNow,
    retrySleep,
  };
}
