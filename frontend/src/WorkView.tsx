import {
  AlertCircle,
  Calendar,
  Check,
  CheckCircle2,
  Circle,
  File as FileIcon,
  FileText,
  Image,
  Layers3,
  Link2,
  Paperclip,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import {
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ApiError, api } from './api';
import { type Translate, useI18n } from './i18n';
import { MarkdownContent, MarkdownExcerpt } from './Markdown';
import type {
  Plan,
  PlanCreateInput,
  PlanStatus,
  PlanUpdateInput,
  Sprint,
  Task,
  TaskAttachment,
  TaskCreateInput,
  TaskKind,
  TaskPriority,
  TaskStatus,
  TaskUpdateInput,
} from './types';
import { WorkDropdown } from './WorkDropdown';
import { WorkPicker } from './WorkPicker';

export type WorkMode = 'board' | 'list' | 'epics' | 'plans';

export type WorkViewProps = {
  projectId: string;
  tasks: Task[];
  plans?: Plan[];
  sprints?: Sprint[];
  epicCatalog?: Task[];
  defaultSprintId?: string | null;
  loadTask?: (taskId: string) => Promise<Task>;
  loadPlan?: (planId: string) => Promise<Plan>;
  onModeChange?: (mode: WorkMode) => void;
  onSearch?: (query: string) => void;
  historical?: boolean;
  pageCaption?: ReactNode;
  scopeControl?: ReactNode;
  sprintControls?: ReactNode;
  onCreateSprint?: () => void;
  scopeNotice?: ReactNode;
  onCreate: (input: TaskCreateInput) => Promise<Task>;
  onUpdate: (task: Task, input: TaskUpdateInput) => Promise<Task>;
  onCreatePlan?: (input: PlanCreateInput) => Promise<Plan>;
  onUpdatePlan?: (plan: Plan, input: PlanUpdateInput) => Promise<Plan>;
  onStatus: (task: Task, status: TaskStatus) => Promise<void>;
  onUpload: (task: Task, files: File[]) => Promise<void>;
  onRemoveAttachment: (task: Task, artifactId: string) => Promise<void>;
  onUploadPlan?: (plan: Plan, files: File[]) => Promise<void>;
  onRemovePlanAttachment?: (plan: Plan, artifactId: string) => Promise<void>;
};

const unsupportedPlanCreate = async (_input: PlanCreateInput): Promise<Plan> => {
  throw new Error('Plan actions are unavailable');
};
const unsupportedPlanUpdate = async (_plan: Plan, _input: PlanUpdateInput): Promise<Plan> => {
  throw new Error('Plan actions are unavailable');
};
const unsupportedPlanUpload = async (_plan: Plan, _files: File[]): Promise<void> => {
  throw new Error('Plan actions are unavailable');
};
const unsupportedPlanAttachmentRemoval = async (
  _plan: Plan,
  _artifactId: string,
): Promise<void> => {
  throw new Error('Plan actions are unavailable');
};

const views: Array<{ value: WorkMode; label: string }> = [
  { value: 'board', label: 'Board' },
  { value: 'list', label: 'List' },
  { value: 'epics', label: 'Epics' },
  { value: 'plans', label: 'Plans' },
];

const boardColumns: Array<{ status: TaskStatus; label: string }> = [
  { status: 'todo', label: 'To do' },
  { status: 'in_progress', label: 'In progress' },
  { status: 'blocked', label: 'Blocked' },
  { status: 'done', label: 'Done' },
  { status: 'cancelled', label: 'Cancelled' },
];

const priorityRank: Record<TaskPriority, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

const labelPalette = [
  { background: '#e7f4ee', color: '#176648', dot: '#24966c' },
  { background: '#e9f0f8', color: '#315f88', dot: '#4d82b0' },
  { background: '#f8efdc', color: '#795817', dot: '#b98a2f' },
  { background: '#f7e9e8', color: '#8a4039', dot: '#bc6259' },
  { background: '#ececef', color: '#555665', dot: '#7c7e91' },
];

const previewImageTypes = new Set([
  'image/avif',
  'image/gif',
  'image/jpeg',
  'image/png',
  'image/webp',
]);

function labelStyle(label: string): CSSProperties {
  const hash = [...label].reduce((value, character) => value + character.charCodeAt(0), 0);
  const palette = labelPalette[hash % labelPalette.length];
  return {
    '--label-bg': palette.background,
    '--label-color': palette.color,
    '--label-dot': palette.dot,
  } as CSSProperties;
}

function statusLabel(status: TaskStatus, t: Translate) {
  const label = boardColumns.find((column) => column.status === status)?.label ?? 'Cancelled';
  return t(label as 'To do' | 'In progress' | 'Blocked' | 'Done' | 'Cancelled');
}

function planStatusLabel(status: PlanStatus, t: Translate) {
  const label = (
    {
      draft: 'Draft',
      decided: 'Decided',
      executing: 'In execution',
      completed: 'Completed',
      superseded: 'Superseded',
    } as const
  )[status];
  return t(label);
}

function priorityLabel(priority: TaskPriority, t: Translate) {
  return t(
    (
      {
        low: 'Low',
        medium: 'Medium',
        high: 'High',
        critical: 'Critical',
      } as const
    )[priority],
  );
}

function normalizedLabels(labels: string[]) {
  return [...new Set(labels.map((label) => label.trim()).filter(Boolean))];
}

function changedFields<T extends object>(base: T, draft: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(draft).filter(
      ([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key as keyof T]),
    ),
  ) as Partial<T>;
}

function taskForm(task: Task): TaskCreateInput {
  return {
    kind: task.kind,
    epic_id: task.epic_id ?? null,
    sprint_id: task.sprint_id ?? null,
    title: task.title.trim(),
    description: task.description,
    next_action: task.next_action?.trim() || null,
    status: task.status,
    priority: task.priority,
    labels: normalizedLabels(task.labels),
    due_at: task.due_at ?? null,
  };
}

function planForm(plan: Plan): PlanCreateInput {
  return {
    title: plan.title.trim(),
    objective: plan.objective,
    content: plan.content,
    status: plan.status,
    labels: normalizedLabels(plan.labels),
    work_item_ids: plan.work_item_ids,
  };
}

function sortWork(left: Task, right: Task) {
  const priority = priorityRank[left.priority] - priorityRank[right.priority];
  if (priority) return priority;
  if (left.due_at && right.due_at) return left.due_at.localeCompare(right.due_at);
  if (left.due_at) return -1;
  if (right.due_at) return 1;
  return right.updated_at.localeCompare(left.updated_at);
}

function shortDate(value: string | null | undefined, locale: string) {
  if (!value) return '';
  return new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric' }).format(
    new Date(value),
  );
}

function formatBytes(value: number, locale: string) {
  if (value < 1024) return `${new Intl.NumberFormat(locale).format(value)} B`;
  if (value < 1024 * 1024)
    return `${new Intl.NumberFormat(locale).format(Math.round(value / 1024))} KB`;
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / (1024 * 1024))} MB`;
}

function Label({
  value,
  removable,
  onRemove,
}: {
  value: string;
  removable?: boolean;
  onRemove?: () => void;
}) {
  const { t } = useI18n();
  return (
    <span className="work-label" style={labelStyle(value)}>
      <i />
      {value}
      {removable && (
        <button type="button" aria-label={t('Remove {name}', { name: value })} onClick={onRemove}>
          <X />
        </button>
      )}
    </span>
  );
}

function WorkRow({
  task,
  epic,
  onOpen,
  onStatus,
}: {
  task: Task;
  epic?: Task;
  onOpen: () => void;
  onStatus: (status: TaskStatus) => void;
}) {
  const { locale, t } = useI18n();
  return (
    <article className="work-row">
      <button
        type="button"
        className="row-check"
        title={
          task.status === 'done'
            ? t('Reopen {name}', { name: task.title })
            : t('Complete {name}', { name: task.title })
        }
        onClick={() => onStatus(task.status === 'done' ? 'todo' : 'done')}
      >
        {task.status === 'done' ? <CheckCircle2 /> : <Circle />}
      </button>
      <button type="button" className="work-row-main" onClick={onOpen}>
        <span className="row-title">
          {task.kind === 'epic' && <Layers3 />}
          {task.title}
        </span>
        <span className="row-meta">
          {epic?.title && <span>{epic.title} · </span>}
          <MarkdownExcerpt>{task.next_action || task.description}</MarkdownExcerpt>
        </span>
      </button>
      <div className="row-labels">
        {task.labels.slice(0, 2).map((label) => (
          <Label key={label} value={label} />
        ))}
      </div>
      {task.attachments.length > 0 && (
        <span
          className="attachment-count"
          title={t('{count} attachments', { count: task.attachments.length })}
        >
          <Paperclip /> {task.attachments.length}
        </span>
      )}
      {task.due_at && (
        <span className="due-date">
          <Calendar /> {shortDate(task.due_at, locale)}
        </span>
      )}
      <span
        className="priority-mark"
        data-priority={task.priority}
        title={t('{priority} priority', { priority: priorityLabel(task.priority, t) })}
      />
    </article>
  );
}

function TaskCard({
  task,
  epic,
  onOpen,
  onDragStart,
}: {
  task: Task;
  epic?: Task;
  onOpen: () => void;
  onDragStart: () => void;
}) {
  const { t } = useI18n();
  return (
    <article className="work-card" draggable onDragStart={onDragStart}>
      <button type="button" onClick={onOpen}>
        <span className="card-kicker">
          {task.kind === 'epic' ? t('Epic') : epic?.title || t('Task')}
          <i data-priority={task.priority} />
        </span>
        <strong>{task.title}</strong>
        {(task.next_action || task.description) && (
          <MarkdownExcerpt className="markdown-summary">
            {task.next_action || task.description}
          </MarkdownExcerpt>
        )}
        <span className="card-footer">
          <span className="card-labels">
            {task.labels.slice(0, 2).map((label) => (
              <Label key={label} value={label} />
            ))}
          </span>
          {task.attachments.length > 0 && (
            <span className="attachment-count">
              <Paperclip /> {task.attachments.length}
            </span>
          )}
        </span>
      </button>
    </article>
  );
}

function BoardView({
  tasks,
  epics,
  onOpen,
  onStatus,
}: {
  tasks: Task[];
  epics: Map<string, Task>;
  onOpen: (task: Task) => void;
  onStatus: (task: Task, status: TaskStatus) => void;
}) {
  const { t } = useI18n();
  const [draggedId, setDraggedId] = useState('');
  return (
    <section
      className={`board${tasks.some((task) => task.status === 'cancelled') ? ' board-with-cancelled' : ''}`}
      aria-label={t('Work view')}
    >
      {boardColumns
        .filter(
          (column) =>
            column.status !== 'cancelled' || tasks.some((task) => task.status === 'cancelled'),
        )
        .map((column) => {
          const items = tasks.filter((task) => task.status === column.status).sort(sortWork);
          return (
            <fieldset
              className="board-column"
              key={column.status}
              onDragOver={(event) => event.preventDefault()}
              onDrop={() => {
                const task = tasks.find((item) => item.id === draggedId);
                if (task && task.status !== column.status) onStatus(task, column.status);
                setDraggedId('');
              }}
            >
              <legend className="sr-only">
                {t(column.label as 'To do' | 'In progress' | 'Blocked' | 'Done')}
              </legend>
              <div className="column-heading">
                <h3>{t(column.label as 'To do' | 'In progress' | 'Blocked' | 'Done')}</h3>
                <span>{items.length}</span>
              </div>
              <div className="board-stack">
                {items.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    epic={task.epic_id ? epics.get(task.epic_id) : undefined}
                    onOpen={() => onOpen(task)}
                    onDragStart={() => setDraggedId(task.id)}
                  />
                ))}
              </div>
            </fieldset>
          );
        })}
    </section>
  );
}

function ListView({
  tasks,
  epics,
  onOpen,
}: {
  tasks: Task[];
  epics: Map<string, Task>;
  onOpen: (task: Task) => void;
}) {
  const { locale, t } = useI18n();
  return (
    <div className="work-table-wrap">
      <table className="work-table">
        <thead>
          <tr>
            <th>{t('Work')}</th>
            <th>{t('Labels')}</th>
            <th>{t('Epic')}</th>
            <th>{t('Status')}</th>
            <th>{t('Priority')}</th>
            <th>{t('Due')}</th>
          </tr>
        </thead>
        <tbody>
          {[...tasks].sort(sortWork).map((task) => (
            <tr key={task.id}>
              <td>
                <button type="button" className="table-title" onClick={() => onOpen(task)}>
                  {task.kind === 'epic' ? <Layers3 /> : <Check />}
                  <span>
                    <strong>{task.title}</strong>
                    <small>{task.kind === 'epic' ? t('Epic') : t('Task')}</small>
                  </span>
                </button>
              </td>
              <td>
                <div className="table-labels">
                  {task.labels.slice(0, 2).map((label) => (
                    <Label key={label} value={label} />
                  ))}
                </div>
              </td>
              <td>{task.epic_id ? epics.get(task.epic_id)?.title : ''}</td>
              <td>
                <span className="status-text" data-status={task.status}>
                  {statusLabel(task.status, t)}
                </span>
              </td>
              <td className="priority-text" data-priority={task.priority}>
                {priorityLabel(task.priority, t)}
              </td>
              <td>{shortDate(task.due_at, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!tasks.length && <p className="work-empty">{t('No matching work')}</p>}
    </div>
  );
}

function EpicsView({
  epics,
  tasks,
  matches,
  onOpen,
  onStatus,
}: {
  epics: Task[];
  tasks: Task[];
  matches: (task: Task) => boolean;
  onOpen: (task: Task) => void;
  onStatus: (task: Task, status: TaskStatus) => void;
}) {
  const { t } = useI18n();
  const visible = epics.filter(
    (epic) => matches(epic) || tasks.some((task) => task.epic_id === epic.id && matches(task)),
  );
  return (
    <div className="epics-view">
      {visible.map((epic) => {
        const children = tasks.filter((task) => task.epic_id === epic.id && matches(task));
        const completed =
          epic.task_counts?.completed ?? children.filter((task) => task.status === 'done').length;
        const total = epic.task_counts?.total ?? children.length;
        const progress = total ? Math.round((completed / total) * 100) : 0;
        return (
          <article className="epic-group" key={epic.id}>
            <button type="button" className="epic-heading" onClick={() => onOpen(epic)}>
              <span className="epic-icon">
                <Layers3 />
              </span>
              <span>
                <small>{t('Epic')}</small>
                <strong>{epic.title}</strong>
              </span>
              <span className="epic-progress">
                <span>
                  <i style={{ width: `${progress}%` }} />
                </span>
                {completed}/{total}
              </span>
            </button>
            <div className="epic-children">
              {children.sort(sortWork).map((task) => (
                <WorkRow
                  key={task.id}
                  task={task}
                  onOpen={() => onOpen(task)}
                  onStatus={(status) => onStatus(task, status)}
                />
              ))}
              {!children.length && (
                <p className="work-empty">
                  {total ? t('Open the epic to inspect its linked work.') : t('No matching tasks')}
                </p>
              )}
            </div>
          </article>
        );
      })}
      {!visible.length && (
        <div className="epics-empty">
          <Layers3 />
          <h3>{t('No epics yet')}</h3>
          <p>{t('Group related tasks under one outcome.')}</p>
        </div>
      )}
    </div>
  );
}

function PlansView({
  plans,
  taskMap,
  onOpen,
}: {
  plans: Plan[];
  taskMap: Map<string, Task>;
  onOpen: (plan: Plan) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="plans-view">
      {plans.map((plan) => {
        const linked = plan.work_item_ids
          .map((workItemId) => taskMap.get(workItemId))
          .filter((item): item is Task => Boolean(item));
        return (
          <article className="plan-card" key={plan.id}>
            <button type="button" className="plan-card-main" onClick={() => onOpen(plan)}>
              <span className="plan-card-kicker">
                <span>
                  <FileText /> {t('Plan')}
                </span>
                <span className="plan-status" data-status={plan.status}>
                  {planStatusLabel(plan.status, t)}
                </span>
              </span>
              <strong>{plan.title}</strong>
              {(plan.objective || plan.content) && (
                <MarkdownExcerpt className="markdown-summary">
                  {plan.objective || plan.content}
                </MarkdownExcerpt>
              )}
              <span className="plan-card-footer">
                <span className="card-labels">
                  {plan.labels.slice(0, 3).map((label) => (
                    <Label key={label} value={label} />
                  ))}
                </span>
                <span className="plan-card-metrics">
                  {linked.length > 0 && (
                    <span title={t('{count} linked work items', { count: linked.length })}>
                      <Link2 /> {linked.length}
                    </span>
                  )}
                  {plan.attachments.length > 0 && (
                    <span title={t('{count} attachments', { count: plan.attachments.length })}>
                      <Paperclip /> {plan.attachments.length}
                    </span>
                  )}
                </span>
              </span>
            </button>
            {linked.length > 0 && (
              <div className="plan-linked-work">
                {linked.slice(0, 3).map((item) => (
                  <span key={item.id}>
                    {item.kind === 'epic' ? <Layers3 /> : <Check />} {item.title}
                  </span>
                ))}
                {linked.length > 3 && <span>+{linked.length - 3}</span>}
              </div>
            )}
          </article>
        );
      })}
      {!plans.length && (
        <div className="epics-empty plans-empty">
          <FileText />
          <h3>{t('No plans yet')}</h3>
          <p>{t('Shape a broad approach here before committing the work.')}</p>
        </div>
      )}
    </div>
  );
}

function AttachmentRow({
  projectId,
  task,
  attachment,
  onRemove,
  onError,
}: {
  projectId: string;
  task: Task;
  attachment: TaskAttachment;
  onRemove?: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const { locale, t } = useI18n();
  const [removing, setRemoving] = useState(false);
  const url = api.taskAttachmentUrl(projectId, task.id, attachment.id);
  const previewable =
    attachment.kind === 'image' && previewImageTypes.has(attachment.mime_type.toLowerCase());
  return (
    <div className="attachment-row">
      {previewable ? (
        <img src={url} alt={attachment.filename} />
      ) : (
        <span className="attachment-icon">
          <FileIcon />
        </span>
      )}
      <a
        href={url}
        target={previewable ? '_blank' : undefined}
        rel={previewable ? 'noreferrer' : undefined}
        download={previewable ? undefined : attachment.filename || true}
      >
        <strong>{attachment.filename || t('Attachment')}</strong>
        <small>{formatBytes(attachment.size_bytes, locale)}</small>
      </a>
      {onRemove && (
        <button
          type="button"
          className="icon small-icon"
          title={t('Remove {name}', { name: attachment.filename })}
          disabled={removing}
          onClick={async () => {
            setRemoving(true);
            try {
              await onRemove();
            } catch (reason) {
              onError(reason instanceof Error ? reason.message : String(reason));
            } finally {
              setRemoving(false);
            }
          }}
        >
          <Trash2 />
        </button>
      )}
    </div>
  );
}

const dialogFocusable = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function useModalDialog(onClose: () => void) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog) return undefined;
    dialog.focus({ preventScroll: true });

    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(dialog?.querySelectorAll<HTMLElement>(dialogFocusable) ?? []);
      if (!focusable.length) {
        event.preventDefault();
        dialog?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === dialog)
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, []);

  return dialogRef;
}

function TaskDrawer({
  projectId,
  task,
  defaultKind,
  epics,
  sprints = [],
  defaultSprintId = null,
  historical = false,
  onOpenTask,
  onClose,
  onCreate,
  onUpdate,
  onUpload,
  onRemoveAttachment,
}: {
  projectId: string;
  task: Task | null;
  defaultKind: TaskKind;
  epics: Task[];
  sprints?: Sprint[];
  defaultSprintId?: string | null;
  historical?: boolean;
  onOpenTask?: (taskId: string) => void;
  onClose: () => void;
  onCreate: (input: TaskCreateInput) => Promise<Task>;
  onUpdate: (task: Task, input: TaskUpdateInput) => Promise<Task>;
  onUpload: (task: Task, files: File[]) => Promise<void>;
  onRemoveAttachment: (task: Task, artifactId: string) => Promise<void>;
}) {
  const { locale, t } = useI18n();
  const [kind, setKind] = useState<TaskKind>(task?.kind ?? defaultKind);
  const [title, setTitle] = useState(task?.title ?? '');
  const [description, setDescription] = useState(task?.description ?? '');
  const [nextAction, setNextAction] = useState(task?.next_action ?? '');
  const [status, setStatus] = useState<TaskStatus>(task?.status ?? 'todo');
  const [priority, setPriority] = useState<TaskPriority>(task?.priority ?? 'medium');
  const [epicId, setEpicId] = useState(task?.epic_id ?? '');
  const [sprintId, setSprintId] = useState(task?.sprint_id ?? defaultSprintId ?? '');
  const [dueDate, setDueDate] = useState(task?.due_at?.slice(0, 10) ?? '');
  const [labels, setLabels] = useState(task?.labels ?? []);
  const [labelInput, setLabelInput] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(!task);
  const [editBase, setEditBase] = useState(task);
  const [conflict, setConflict] = useState(false);
  const [childPage, setChildPage] = useState<{ items: Task[]; total: number }>({
    items: [],
    total: 0,
  });
  const [childOffset, setChildOffset] = useState(0);
  const [findEpic, setFindEpic] = useState(false);
  const [extraEpics, setExtraEpics] = useState<Task[]>([]);
  const dialogRef = useModalDialog(onClose);

  useEffect(() => {
    if (!onOpenTask || task?.kind !== 'epic') return;
    let current = true;
    void api
      .listWork(projectId, {
        epic_id: task.id,
        scope: 'all',
        placement: 'all',
        limit: 50,
        offset: childOffset,
      })
      .then((page) => {
        if (current) setChildPage(page);
      })
      .catch((reason) => {
        if (current) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      current = false;
    };
  }, [projectId, task?.id, task?.kind, childOffset, onOpenTask]);

  function loadDraft(value: TaskCreateInput) {
    setKind(value.kind);
    setTitle(value.title);
    setDescription(value.description ?? '');
    setNextAction(value.next_action ?? '');
    setStatus(value.status ?? 'todo');
    setPriority(value.priority);
    setEpicId(value.epic_id ?? '');
    setSprintId(value.sprint_id ?? '');
    setDueDate(value.due_at?.slice(0, 10) ?? '');
    setLabels(value.labels);
  }

  function draft(): TaskCreateInput {
    return {
      kind,
      epic_id: kind === 'task' ? epicId || null : null,
      title: title.trim(),
      sprint_id: kind === 'task' ? sprintId || null : null,
      description,
      next_action: nextAction.trim() || null,
      status,
      priority,
      labels: normalizedLabels(labels),
      due_at:
        dueDate === (editBase?.due_at?.slice(0, 10) ?? '')
          ? (editBase?.due_at ?? null)
          : dueDate
            ? new Date(`${dueDate}T12:00:00`).toISOString()
            : null,
    };
  }

  async function resolveConflict(reapply: boolean) {
    if (!editBase) return;
    setSaving(true);
    try {
      const changes = changedFields(taskForm(editBase), draft());
      const latest = await api.getTask(projectId, editBase.id);
      loadDraft({ ...taskForm(latest), ...(reapply ? changes : {}) });
      setEditBase(latest);
      setConflict(false);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  function addLabels() {
    const additions = labelInput.split(',');
    setLabels((current) => normalizedLabels([...current, ...additions]));
    setLabelInput('');
  }

  function labelKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      addLabels();
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError('');
    const input = draft();
    try {
      const saved = editBase
        ? await onUpdate(editBase, changedFields(taskForm(editBase), input))
        : await onCreate(input);
      if (files.length) {
        try {
          await onUpload(saved, files);
        } catch (reason) {
          if (!task) {
            onClose();
            return;
          }
          throw reason;
        }
      }
      onClose();
    } catch (reason) {
      setConflict(reason instanceof ApiError && reason.status === 409);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="drawer-backdrop">
      <div
        ref={dialogRef}
        className={`task-drawer ${editing ? 'drawer-editing' : 'drawer-reading'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-drawer-title"
        tabIndex={-1}
      >
        <form onSubmit={(event) => void save(event)}>
          <div className="drawer-header">
            <div>
              <p>
                {task
                  ? editing
                    ? t('Edit {kind}', { kind: task.kind === 'epic' ? t('Epic') : t('Task') })
                    : task.kind === 'epic'
                      ? t('Epic')
                      : t('Task')
                  : t('New {kind}', { kind: kind === 'epic' ? t('Epic') : t('Task') })}
              </p>
              <h2 id="task-drawer-title">
                {task?.title || (kind === 'epic' ? t('Plan an outcome') : t('Define the work'))}
              </h2>
            </div>
            <button
              type="button"
              className="icon"
              title={t('Close')}
              aria-label={t('Close drawer')}
              onClick={onClose}
            >
              <X />
            </button>
          </div>

          <div className="drawer-body">
            {historical && task && (
              <p className="work-history-note">
                {t(
                  'The drawer shows the current task. The archived sprint keeps its closing snapshot.',
                )}
              </p>
            )}
            {!editing && task ? (
              <div className="drawer-details">
                <dl className="drawer-detail-meta">
                  <div>
                    <dt>{t('Status')}</dt>
                    <dd className="status-text" data-status={task.status}>
                      {statusLabel(task.status, t)}
                    </dd>
                  </div>
                  <div>
                    <dt>{t('Priority')}</dt>
                    <dd className="priority-text" data-priority={task.priority}>
                      {priorityLabel(task.priority, t)}
                    </dd>
                  </div>
                  {task.due_at && (
                    <div>
                      <dt>{t('Due')}</dt>
                      <dd>{shortDate(task.due_at, locale)}</dd>
                    </div>
                  )}
                  {task.epic_id && (
                    <div>
                      <dt>{t('Epic')}</dt>
                      <dd>{epics.find((epic) => epic.id === task.epic_id)?.title || '—'}</dd>
                    </div>
                  )}
                </dl>
                {task.description && (
                  <section className="drawer-document drawer-document-primary">
                    <h3>{t('Description')}</h3>
                    <MarkdownContent>{task.description}</MarkdownContent>
                  </section>
                )}
                {task.kind === 'epic' && onOpenTask && (
                  <section className="drawer-linked-work">
                    <h3>
                      {t('Linked work')} · {childPage.total}
                    </h3>
                    {childPage.items.map((child) => (
                      <button
                        key={child.id}
                        type="button"
                        className="plan-work-option"
                        onClick={() => onOpenTask(child.id)}
                      >
                        {child.title}
                      </button>
                    ))}
                    <div className="work-pagination">
                      <button
                        className="secondary"
                        type="button"
                        disabled={!childOffset}
                        onClick={() => setChildOffset((value) => Math.max(0, value - 50))}
                      >
                        {t('Previous page')}
                      </button>
                      <button
                        className="secondary"
                        type="button"
                        disabled={childOffset + childPage.items.length >= childPage.total}
                        onClick={() => setChildOffset((value) => value + 50)}
                      >
                        {t('Next page')}
                      </button>
                    </div>
                  </section>
                )}
                {task.objective && (
                  <section className="drawer-document">
                    <h3>{t('Outcome')}</h3>
                    <MarkdownContent>{task.objective}</MarkdownContent>
                  </section>
                )}
                {task.next_action && (
                  <section className="drawer-document">
                    <h3>{t('Next action')}</h3>
                    <MarkdownContent>{task.next_action}</MarkdownContent>
                  </section>
                )}
                {task.rationale && (
                  <section className="drawer-document">
                    <h3>{t('Rationale')}</h3>
                    <MarkdownContent>{task.rationale}</MarkdownContent>
                  </section>
                )}
                {task.completion_evidence && (
                  <section className="drawer-document">
                    <h3>{t('Completion evidence')}</h3>
                    <MarkdownContent>{task.completion_evidence}</MarkdownContent>
                  </section>
                )}
                {task.labels.length > 0 && (
                  <fieldset className="drawer-detail-labels">
                    <legend className="sr-only">{t('Labels')}</legend>
                    {task.labels.map((label) => (
                      <Label key={label} value={label} />
                    ))}
                  </fieldset>
                )}
                {task.attachments.length > 0 && (
                  <section className="drawer-detail-attachments">
                    <h3>{t('Images and files')}</h3>
                    {task.attachments.map((attachment) => (
                      <AttachmentRow
                        key={attachment.id}
                        projectId={projectId}
                        task={task}
                        attachment={attachment}
                        onError={setError}
                      />
                    ))}
                  </section>
                )}
              </div>
            ) : (
              <>
                <fieldset className="kind-control">
                  <legend>{t('Type')}</legend>
                  <button
                    type="button"
                    className={kind === 'task' ? 'selected' : ''}
                    aria-pressed={kind === 'task'}
                    onClick={() => setKind('task')}
                  >
                    <Check /> {t('Task')}
                  </button>
                  <button
                    type="button"
                    className={kind === 'epic' ? 'selected' : ''}
                    aria-pressed={kind === 'epic'}
                    onClick={() => {
                      setKind('epic');
                      setEpicId('');
                    }}
                  >
                    <Layers3 /> {t('Epic')}
                  </button>
                </fieldset>

                <label className="field full-field" htmlFor="work-title">
                  {t('Title')}
                  <input
                    id="work-title"
                    maxLength={300}
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder={
                      kind === 'epic'
                        ? t('Outcome to coordinate')
                        : t('Concrete next piece of work')
                    }
                  />
                </label>

                <div className="field-grid">
                  <label className="field" htmlFor="work-status">
                    {t('Status')}
                    <select
                      id="work-status"
                      value={status}
                      onChange={(event) => setStatus(event.target.value as TaskStatus)}
                    >
                      <option value="todo">{t('To do')}</option>
                      <option value="in_progress">{t('In progress')}</option>
                      <option value="blocked">{t('Blocked')}</option>
                      <option value="done">{t('Done')}</option>
                      <option value="cancelled">{t('Cancelled')}</option>
                    </select>
                  </label>
                  <label className="field" htmlFor="work-priority">
                    {t('Priority')}
                    <select
                      id="work-priority"
                      value={priority}
                      onChange={(event) => setPriority(event.target.value as TaskPriority)}
                    >
                      <option value="low">{t('Low')}</option>
                      <option value="medium">{t('Medium')}</option>
                      <option value="high">{t('High')}</option>
                      <option value="critical">{t('Critical')}</option>
                    </select>
                  </label>
                  <label className="field" htmlFor="work-due">
                    {t('Due')}
                    <input
                      id="work-due"
                      type="date"
                      value={dueDate}
                      onChange={(event) => setDueDate(event.target.value)}
                    />
                  </label>
                </div>

                {kind === 'task' && (
                  <label className="field full-field" htmlFor="work-sprint">
                    {t('Sprint')}
                    <select
                      id="work-sprint"
                      value={sprintId}
                      onChange={(event) => setSprintId(event.target.value)}
                    >
                      <option value="">{t('Backlog / no sprint')}</option>
                      {sprints
                        .filter((sprint) => sprint.status !== 'archived' || sprint.id === sprintId)
                        .map((sprint) => (
                          <option
                            key={sprint.id}
                            value={sprint.id}
                            disabled={sprint.status === 'archived'}
                          >
                            {sprint.title}
                          </option>
                        ))}
                    </select>
                    {sprints.some(
                      (sprint) => sprint.id === task?.sprint_id && sprint.status === 'archived',
                    ) && (
                      <small>
                        {t(
                          'To reopen archived work, choose the backlog or another sprint. The archive keeps its original snapshot.',
                        )}
                      </small>
                    )}
                  </label>
                )}

                {kind === 'task' && (
                  <label className="field full-field" htmlFor="work-epic">
                    {t('Epic')}
                    <select
                      id="work-epic"
                      value={epicId}
                      onChange={(event) => setEpicId(event.target.value)}
                    >
                      <option value="">{t('No epic')}</option>
                      {[
                        ...new Map(
                          [...epics, ...extraEpics].map((item) => [item.id, item]),
                        ).values(),
                      ]
                        .filter((epic) => epic.id !== task?.id)
                        .map((epic) => (
                          <option key={epic.id} value={epic.id}>
                            {epic.title}
                          </option>
                        ))}
                    </select>
                  </label>
                )}

                {kind === 'task' && onOpenTask && (
                  <div className="field full-field">
                    <button
                      className="secondary"
                      type="button"
                      onClick={() => setFindEpic((value) => !value)}
                    >
                      {t('Find another epic')}
                    </button>
                    {findEpic && (
                      <WorkPicker
                        projectId={projectId}
                        kind="epic"
                        selected={[epicId]}
                        onChoose={(item) => {
                          setExtraEpics((current) => [...current, item]);
                          setEpicId(item.id);
                          setFindEpic(false);
                        }}
                      />
                    )}
                  </div>
                )}

                <label className="field full-field" htmlFor="work-description">
                  {t('Description')}
                  <textarea
                    id="work-description"
                    rows={10}
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder={t('Useful context, constraints, or expected outcome')}
                  />
                </label>

                <label className="field full-field" htmlFor="work-next-action">
                  {t('Next action')}
                  <input
                    id="work-next-action"
                    value={nextAction}
                    onChange={(event) => setNextAction(event.target.value)}
                    placeholder={t('The next concrete move')}
                  />
                </label>

                <div className="field full-field">
                  <span className="field-label">{t('Labels')}</span>
                  <div className="label-editor">
                    <div className="label-values">
                      {labels.map((label) => (
                        <Label
                          key={label}
                          value={label}
                          removable
                          onRemove={() =>
                            setLabels((current) => current.filter((item) => item !== label))
                          }
                        />
                      ))}
                    </div>
                    <div className="label-input-row">
                      <input
                        aria-label={t('Add label')}
                        value={labelInput}
                        onKeyDown={labelKeyDown}
                        onChange={(event) => setLabelInput(event.target.value)}
                        onBlur={() => {
                          if (labelInput.trim()) addLabels();
                        }}
                        placeholder={t('Type a label and press Enter')}
                      />
                      <button
                        type="button"
                        className="icon"
                        title={t('Add label')}
                        onClick={addLabels}
                      >
                        <Plus />
                      </button>
                    </div>
                  </div>
                </div>

                <div className="field full-field attachments-field">
                  <span className="field-label">{t('Images and files')}</span>
                  {task?.attachments.map((attachment) => (
                    <AttachmentRow
                      key={attachment.id}
                      projectId={projectId}
                      task={task}
                      attachment={attachment}
                      onRemove={() => onRemoveAttachment(task, attachment.id)}
                      onError={setError}
                    />
                  ))}
                  {files.map((file) => (
                    <div className="attachment-row pending" key={`${file.name}-${file.size}`}>
                      <span className="attachment-icon">
                        {file.type.startsWith('image/') ? <Image /> : <FileIcon />}
                      </span>
                      <span>
                        <strong>{file.name}</strong>
                        <small>
                          {formatBytes(file.size, locale)} · {t('ready to upload')}
                        </small>
                      </span>
                      <button
                        type="button"
                        className="icon small-icon"
                        title={t('Remove {name}', { name: file.name })}
                        onClick={() =>
                          setFiles((current) =>
                            current.filter(
                              (item) => item.name !== file.name || item.size !== file.size,
                            ),
                          )
                        }
                      >
                        <X />
                      </button>
                    </div>
                  ))}
                  <label className="file-picker">
                    <Paperclip />
                    {t('Add images or files')}
                    <input
                      type="file"
                      multiple
                      onChange={(event) => {
                        const selected = event.target.files ? Array.from(event.target.files) : [];
                        const oversized = selected.find((file) => file.size > 10 * 1024 * 1024);
                        setError(
                          oversized
                            ? t('{name} exceeds the 10 MiB limit', { name: oversized.name })
                            : '',
                        );
                        setFiles((current) => [
                          ...current,
                          ...selected.filter((file) => file.size <= 10 * 1024 * 1024),
                        ]);
                        event.target.value = '';
                      }}
                    />
                  </label>
                </div>

                {error && (
                  <div className="drawer-error" role="alert">
                    <AlertCircle /> {error}
                  </div>
                )}
                {conflict && (
                  <div className="conflict-actions">
                    <p>
                      {t(
                        'This item changed elsewhere. Reload it, or reapply your edited fields and review before saving.',
                      )}
                    </p>
                    <button
                      type="button"
                      className="secondary"
                      disabled={saving}
                      onClick={() => void resolveConflict(false)}
                    >
                      {t('Reload latest')}
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={saving}
                      onClick={() => void resolveConflict(true)}
                    >
                      {t('Reapply my changes')}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="drawer-footer">
            <button type="button" className="secondary" onClick={onClose}>
              {editing ? t('Cancel') : t('Close')}
            </button>
            {editing ? (
              <button
                key="save"
                type="submit"
                className="primary"
                disabled={!title.trim() || saving || conflict}
              >
                {saving ? t('Saving') : task ? t('Save changes') : t('Create {kind}', { kind })}
              </button>
            ) : (
              <button
                key="edit"
                type="button"
                className="primary"
                onClick={() => {
                  if (task) {
                    setEditBase(task);
                    loadDraft(taskForm(task));
                  }
                  setEditing(true);
                }}
              >
                {t('Edit {kind}', { kind: task?.kind === 'epic' ? t('Epic') : t('Task') })}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

function PlanAttachmentRow({
  projectId,
  plan,
  attachment,
  onRemove,
  onError,
}: {
  projectId: string;
  plan: Plan;
  attachment: TaskAttachment;
  onRemove?: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const { locale, t } = useI18n();
  const [removing, setRemoving] = useState(false);
  const url = api.planAttachmentUrl(projectId, plan.id, attachment.id);
  const previewable =
    attachment.kind === 'image' && previewImageTypes.has(attachment.mime_type.toLowerCase());
  return (
    <div className="attachment-row">
      {previewable ? (
        <img src={url} alt={attachment.filename} />
      ) : (
        <span className="attachment-icon">
          <FileIcon />
        </span>
      )}
      <a
        href={url}
        target={previewable ? '_blank' : undefined}
        rel={previewable ? 'noreferrer' : undefined}
        download={previewable ? undefined : attachment.filename || true}
      >
        <strong>{attachment.filename || t('Attachment')}</strong>
        <small>{formatBytes(attachment.size_bytes, locale)}</small>
      </a>
      {onRemove && (
        <button
          type="button"
          className="icon small-icon"
          title={t('Remove {name}', { name: attachment.filename })}
          disabled={removing}
          onClick={async () => {
            setRemoving(true);
            try {
              await onRemove();
            } catch (reason) {
              onError(reason instanceof Error ? reason.message : String(reason));
            } finally {
              setRemoving(false);
            }
          }}
        >
          <Trash2 />
        </button>
      )}
    </div>
  );
}

function PlanDrawer({
  projectId,
  plan,
  tasks,
  onClose,
  onCreate,
  onUpdate,
  onUpload,
  onRemoveAttachment,
  onOpenTask,
}: {
  projectId: string;
  plan: Plan | null;
  tasks: Task[];
  onClose: () => void;
  onCreate: (input: PlanCreateInput) => Promise<Plan>;
  onUpdate: (plan: Plan, input: PlanUpdateInput) => Promise<Plan>;
  onUpload: (plan: Plan, files: File[]) => Promise<void>;
  onRemoveAttachment: (plan: Plan, artifactId: string) => Promise<void>;
  onOpenTask?: (taskId: string) => void;
}) {
  const { locale, t } = useI18n();
  const [title, setTitle] = useState(plan?.title ?? '');
  const [objective, setObjective] = useState(plan?.objective ?? '');
  const [content, setContent] = useState(plan?.content ?? '');
  const [status, setStatus] = useState<PlanStatus>(plan?.status ?? 'draft');
  const [labels, setLabels] = useState(plan?.labels ?? []);
  const [workItemIds, setWorkItemIds] = useState(plan?.work_item_ids ?? []);
  const [labelInput, setLabelInput] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(!plan);
  const [editBase, setEditBase] = useState(plan);
  const [conflict, setConflict] = useState(false);
  const [findWork, setFindWork] = useState(false);
  const [extraWork, setExtraWork] = useState<Task[]>([]);
  const dialogRef = useModalDialog(onClose);

  function loadDraft(value: PlanCreateInput) {
    setTitle(value.title);
    setObjective(value.objective ?? '');
    setContent(value.content ?? '');
    setStatus(value.status ?? 'draft');
    setLabels(value.labels ?? []);
    setWorkItemIds(value.work_item_ids ?? []);
  }

  function draft(): PlanCreateInput {
    return {
      title: title.trim(),
      objective,
      content,
      status,
      labels: normalizedLabels(labels),
      work_item_ids: workItemIds,
    };
  }

  async function resolveConflict(reapply: boolean) {
    if (!editBase) return;
    setSaving(true);
    try {
      const changes = changedFields(planForm(editBase), draft());
      const latest = await api.getPlan(projectId, editBase.id);
      loadDraft({ ...planForm(latest), ...(reapply ? changes : {}) });
      setEditBase(latest);
      setConflict(false);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  function addLabels() {
    setLabels((current) => normalizedLabels([...current, ...labelInput.split(',')]));
    setLabelInput('');
  }

  function labelKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      addLabels();
    }
  }

  function toggleWorkItem(workItemId: string) {
    setWorkItemIds((current) =>
      current.includes(workItemId)
        ? current.filter((item) => item !== workItemId)
        : [...current, workItemId],
    );
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError('');
    const input = draft();
    try {
      const saved = editBase
        ? await onUpdate(editBase, changedFields(planForm(editBase), input))
        : await onCreate(input);
      if (files.length) {
        try {
          await onUpload(saved, files);
        } catch (reason) {
          if (!plan) {
            onClose();
            return;
          }
          throw reason;
        }
      }
      onClose();
    } catch (reason) {
      setConflict(reason instanceof ApiError && reason.status === 409);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="drawer-backdrop">
      <div
        ref={dialogRef}
        className={`task-drawer plan-drawer ${editing ? 'drawer-editing' : 'drawer-reading'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="plan-drawer-title"
        tabIndex={-1}
      >
        <form onSubmit={(event) => void save(event)}>
          <div className="drawer-header">
            <div>
              <p>{plan ? (editing ? t('Edit plan') : t('Plan')) : t('New plan')}</p>
              <h2 id="plan-drawer-title">{plan?.title || t('Shape the approach')}</h2>
            </div>
            <button
              type="button"
              className="icon"
              title={t('Close')}
              aria-label={t('Close drawer')}
              onClick={onClose}
            >
              <X />
            </button>
          </div>

          <div className="drawer-body">
            {!editing && plan ? (
              <div className="drawer-details">
                <dl className="drawer-detail-meta">
                  <div>
                    <dt>{t('Status')}</dt>
                    <dd>
                      <span className="plan-status" data-status={plan.status}>
                        {planStatusLabel(plan.status, t)}
                      </span>
                    </dd>
                  </div>
                </dl>
                {plan.objective && (
                  <section className="drawer-document drawer-document-primary">
                    <h3>{t('Outcome')}</h3>
                    <MarkdownContent>{plan.objective}</MarkdownContent>
                  </section>
                )}
                {plan.content && (
                  <section className="drawer-document">
                    <h3>{t('Plan')}</h3>
                    <MarkdownContent>{plan.content}</MarkdownContent>
                  </section>
                )}
                {plan.labels.length > 0 && (
                  <fieldset className="drawer-detail-labels">
                    <legend className="sr-only">{t('Labels')}</legend>
                    {plan.labels.map((label) => (
                      <Label key={label} value={label} />
                    ))}
                  </fieldset>
                )}
                {plan.work_item_ids.length > 0 && (
                  <section className="drawer-linked-work">
                    <h3>{t('Linked work')}</h3>
                    <div className="plan-work-list">
                      {plan.work_item_ids.map((workItemId) => {
                        const item = tasks.find((task) => task.id === workItemId);
                        return (
                          <button
                            type="button"
                            className="plan-work-option plan-work-option-readonly"
                            key={workItemId}
                            disabled={!onOpenTask}
                            onClick={() => onOpenTask?.(workItemId)}
                          >
                            <span>
                              <small>{item?.kind === 'epic' ? t('Epic') : t('Task')}</small>
                              <strong>{item?.title ?? workItemId}</strong>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </section>
                )}
                {plan.attachments.length > 0 && (
                  <section className="drawer-detail-attachments">
                    <h3>{t('Images and files')}</h3>
                    {plan.attachments.map((attachment) => (
                      <PlanAttachmentRow
                        key={attachment.id}
                        projectId={projectId}
                        plan={plan}
                        attachment={attachment}
                        onError={setError}
                      />
                    ))}
                  </section>
                )}
              </div>
            ) : (
              <>
                <label className="field full-field" htmlFor="plan-title">
                  {t('Title')}
                  <input
                    id="plan-title"
                    aria-label={t('Plan title')}
                    maxLength={300}
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder={t('The decision or approach to shape')}
                  />
                </label>

                <label className="field full-field" htmlFor="plan-objective">
                  {t('Outcome')}
                  <textarea
                    id="plan-objective"
                    aria-label={t('Plan outcome')}
                    rows={2}
                    value={objective}
                    onChange={(event) => setObjective(event.target.value)}
                    placeholder={t('What this plan should make clear or achieve')}
                  />
                </label>

                <label className="field full-field" htmlFor="plan-content">
                  {t('Plan')}
                  <textarea
                    id="plan-content"
                    aria-label={t('Plan content')}
                    rows={10}
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    placeholder={t(
                      'Context, options, decisions, risks, constraints, and the proposed path',
                    )}
                  />
                </label>

                <label className="field full-field" htmlFor="plan-status">
                  {t('Status')}
                  <select
                    id="plan-status"
                    aria-label={t('Plan status')}
                    value={status}
                    onChange={(event) => setStatus(event.target.value as PlanStatus)}
                  >
                    <option value="draft">{t('Draft')}</option>
                    <option value="decided">{t('Decided')}</option>
                    <option value="executing">{t('In execution')}</option>
                    <option value="completed">{t('Completed')}</option>
                    <option value="superseded">{t('Superseded')}</option>
                  </select>
                </label>

                <div className="field full-field">
                  <span className="field-label">{t('Labels')}</span>
                  <div className="label-editor">
                    <div className="label-values">
                      {labels.map((label) => (
                        <Label
                          key={label}
                          value={label}
                          removable
                          onRemove={() =>
                            setLabels((current) => current.filter((item) => item !== label))
                          }
                        />
                      ))}
                    </div>
                    <div className="label-input-row">
                      <input
                        aria-label={t('Add plan label')}
                        value={labelInput}
                        onKeyDown={labelKeyDown}
                        onChange={(event) => setLabelInput(event.target.value)}
                        onBlur={() => {
                          if (labelInput.trim()) addLabels();
                        }}
                        placeholder={t('Type a label and press Enter')}
                      />
                      <button
                        type="button"
                        className="icon"
                        title={t('Add plan label')}
                        onClick={addLabels}
                      >
                        <Plus />
                      </button>
                    </div>
                  </div>
                </div>

                <fieldset className="field full-field plan-work-links">
                  <legend>{t('Linked work')}</legend>
                  <p>
                    {t(
                      'Connect this plan to the epics and tasks it informs. It can stay unlinked while the approach is still open.',
                    )}
                  </p>
                  <div className="plan-work-list">
                    {[
                      ...new Map([...tasks, ...extraWork].map((item) => [item.id, item])).values(),
                    ].map((task) => (
                      <label className="plan-work-option" key={task.id}>
                        <input
                          type="checkbox"
                          checked={workItemIds.includes(task.id)}
                          onChange={() => toggleWorkItem(task.id)}
                        />
                        <span>
                          <small>{task.kind === 'epic' ? t('Epic') : t('Task')}</small>
                          <strong>{task.title}</strong>
                        </span>
                      </label>
                    ))}
                    {!tasks.length && !extraWork.length && (
                      <p className="muted">
                        {t('No Work items on this page. Search the project to link more.')}
                      </p>
                    )}
                  </div>
                  {onOpenTask && (
                    <>
                      <button
                        className="secondary"
                        type="button"
                        onClick={() => setFindWork((value) => !value)}
                      >
                        {t('Find more work')}
                      </button>
                      {findWork && (
                        <WorkPicker
                          projectId={projectId}
                          selected={workItemIds}
                          onChoose={(item) => {
                            setExtraWork((current) => [...current, item]);
                            toggleWorkItem(item.id);
                          }}
                        />
                      )}
                    </>
                  )}
                </fieldset>

                <div className="field full-field attachments-field">
                  <span className="field-label">{t('Images and files')}</span>
                  {plan?.attachments.map((attachment) => (
                    <PlanAttachmentRow
                      key={attachment.id}
                      projectId={projectId}
                      plan={plan}
                      attachment={attachment}
                      onRemove={() => onRemoveAttachment(plan, attachment.id)}
                      onError={setError}
                    />
                  ))}
                  {files.map((file) => (
                    <div className="attachment-row pending" key={`${file.name}-${file.size}`}>
                      <span className="attachment-icon">
                        {file.type.startsWith('image/') ? <Image /> : <FileIcon />}
                      </span>
                      <span>
                        <strong>{file.name}</strong>
                        <small>
                          {formatBytes(file.size, locale)} · {t('ready to upload')}
                        </small>
                      </span>
                      <button
                        type="button"
                        className="icon small-icon"
                        title={t('Remove {name}', { name: file.name })}
                        onClick={() =>
                          setFiles((current) =>
                            current.filter(
                              (item) => item.name !== file.name || item.size !== file.size,
                            ),
                          )
                        }
                      >
                        <X />
                      </button>
                    </div>
                  ))}
                  <label className="file-picker">
                    <Paperclip />
                    {t('Add images or files')}
                    <input
                      aria-label={t('Add plan images or files')}
                      type="file"
                      multiple
                      onChange={(event) => {
                        const selected = event.target.files ? Array.from(event.target.files) : [];
                        const oversized = selected.find((file) => file.size > 10 * 1024 * 1024);
                        setError(
                          oversized
                            ? t('{name} exceeds the 10 MiB limit', { name: oversized.name })
                            : '',
                        );
                        setFiles((current) => [
                          ...current,
                          ...selected.filter((file) => file.size <= 10 * 1024 * 1024),
                        ]);
                        event.target.value = '';
                      }}
                    />
                  </label>
                </div>

                {conflict && (
                  <div className="conflict-actions">
                    <p>
                      {t(
                        'This item changed elsewhere. Reload it, or reapply your edited fields and review before saving.',
                      )}
                    </p>
                    <button
                      type="button"
                      className="secondary"
                      disabled={saving}
                      onClick={() => void resolveConflict(false)}
                    >
                      {t('Reload latest')}
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={saving}
                      onClick={() => void resolveConflict(true)}
                    >
                      {t('Reapply my changes')}
                    </button>
                  </div>
                )}
                {error && (
                  <div className="drawer-error" role="alert">
                    <AlertCircle /> {error}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="drawer-footer">
            <button type="button" className="secondary" onClick={onClose}>
              {editing ? t('Cancel') : t('Close')}
            </button>
            {editing ? (
              <button
                key="save"
                type="submit"
                className="primary"
                disabled={!title.trim() || saving || conflict}
              >
                {saving ? t('Saving') : plan ? t('Save changes') : t('Create plan')}
              </button>
            ) : (
              <button
                key="edit"
                type="button"
                className="primary"
                onClick={() => {
                  if (plan) {
                    setEditBase(plan);
                    loadDraft(planForm(plan));
                  }
                  setEditing(true);
                }}
              >
                {t('Edit plan')}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

export function WorkView({
  projectId,
  tasks,
  plans = [],
  sprints = [],
  epicCatalog = [],
  defaultSprintId = null,
  loadTask,
  loadPlan,
  onModeChange,
  onSearch,
  historical = false,
  pageCaption,
  scopeControl,
  sprintControls,
  onCreateSprint,
  scopeNotice,
  onCreate,
  onUpdate,
  onCreatePlan = unsupportedPlanCreate,
  onUpdatePlan = unsupportedPlanUpdate,
  onStatus,
  onUpload,
  onRemoveAttachment,
  onUploadPlan = unsupportedPlanUpload,
  onRemovePlanAttachment = unsupportedPlanAttachmentRemoval,
}: WorkViewProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<WorkMode>(() => {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get('plan') ? 'plans' : params.get('view');
    return views.find((view) => view.value === requested)?.value ?? 'board';
  });
  const [query, setQuery] = useState('');
  const [selectedLabels, setSelectedLabels] = useState<string[]>([]);
  const [taskEditor, setTaskEditor] = useState<{ id?: string; kind: TaskKind } | null>(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('plan')) return null;
    const requested = params.get('work');
    const task = requested ? tasks.find((item) => item.id === requested) : null;
    return task
      ? { id: task.id, kind: task.kind }
      : requested && loadTask
        ? { id: requested, kind: 'task' }
        : null;
  });
  const [planEditor, setPlanEditor] = useState<{ id?: string } | null>(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('work')) return null;
    const requested = params.get('plan');
    return requested && (loadPlan || plans.some((plan) => plan.id === requested))
      ? { id: requested }
      : null;
  });
  const [resolvedTask, setResolvedTask] = useState<Task | null>(null);
  const [resolvedPlan, setResolvedPlan] = useState<Plan | null>(null);
  const [detailError, setDetailError] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  const taskVersion = tasks.find((task) => task.id === taskEditor?.id)?.version;
  const planVersion = plans.find((plan) => plan.id === planEditor?.id)?.version;

  // biome-ignore lint/correctness/useExhaustiveDependencies: A changed compact version invalidates the full detail without replacing the open edit draft.
  useEffect(() => {
    if (!taskEditor?.id || !loadTask) return;
    let current = true;
    setDetailLoading(true);
    setDetailError('');
    void loadTask(taskEditor.id)
      .then((task) => {
        if (current) setResolvedTask(task);
      })
      .catch((error) => {
        if (current) setDetailError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (current) setDetailLoading(false);
      });
    return () => {
      current = false;
    };
  }, [taskEditor?.id, taskVersion, loadTask]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: A changed compact version invalidates the full detail without replacing the open edit draft.
  useEffect(() => {
    if (!planEditor?.id || !loadPlan) return;
    let current = true;
    setDetailLoading(true);
    setDetailError('');
    void loadPlan(planEditor.id)
      .then((plan) => {
        if (current) setResolvedPlan(plan);
      })
      .catch((error) => {
        if (current) setDetailError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (current) setDetailLoading(false);
      });
    return () => {
      current = false;
    };
  }, [planEditor?.id, planVersion, loadPlan]);

  useEffect(() => {
    const url = new URL(window.location.href);
    const workId = url.searchParams.get('work');
    const planId = url.searchParams.get('plan');
    const invalid =
      Boolean(workId && planId) ||
      Boolean(workId && !loadTask && !tasks.some((task) => task.id === workId)) ||
      Boolean(planId && !loadPlan && !plans.some((plan) => plan.id === planId));
    if (!invalid) return;
    url.searchParams.delete('work');
    url.searchParams.delete('plan');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setTaskEditor(null);
    setPlanEditor(null);
  }, [plans, tasks, loadTask, loadPlan]);

  const epics = useMemo(
    () => [
      ...new Map(
        [...epicCatalog, ...tasks.filter((task) => task.kind === 'epic')].map((task) => [
          task.id,
          task,
        ]),
      ).values(),
    ],
    [tasks, epicCatalog],
  );
  const epicMap = useMemo(() => new Map(epics.map((epic) => [epic.id, epic])), [epics]);
  const taskMap = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);
  const labels = useMemo(
    () =>
      [
        ...new Set([
          ...tasks.flatMap((task) => task.labels),
          ...plans.flatMap((plan) => plan.labels),
        ]),
      ].sort((a, b) => a.localeCompare(b)),
    [tasks, plans],
  );
  const filterLabels = [...new Set([...selectedLabels, ...labels])].sort((a, b) =>
    a.localeCompare(b),
  );
  const matches = (task: Task) => {
    const text = `${task.title} ${task.description} ${task.next_action || ''} ${task.labels.join(' ')}`;
    return (
      (Boolean(onSearch) ||
        !query.trim() ||
        text.toLowerCase().includes(query.trim().toLowerCase())) &&
      selectedLabels.every((label) => task.labels.includes(label))
    );
  };
  const matchesPlan = (plan: Plan) => {
    const text = `${plan.title} ${plan.objective} ${plan.content} ${plan.labels.join(' ')}`;
    return (
      (Boolean(onSearch) ||
        !query.trim() ||
        text.toLowerCase().includes(query.trim().toLowerCase())) &&
      selectedLabels.every((label) => plan.labels.includes(label))
    );
  };
  const filtered = tasks.filter(matches);
  const filteredTasks = filtered.filter((task) => task.kind === 'task');
  const filteredPlans = plans.filter(matchesPlan);
  const openTasks = tasks.filter(
    (task) => task.kind === 'task' && !['done', 'cancelled'].includes(task.status),
  );
  const activePlans = plans.filter((plan) => !['superseded', 'completed'].includes(plan.status));
  const editingTask = taskEditor?.id
    ? loadTask
      ? resolvedTask?.id === taskEditor.id
        ? resolvedTask
        : null
      : (tasks.find((task) => task.id === taskEditor.id) ?? null)
    : null;
  const editingPlan = planEditor?.id
    ? loadPlan
      ? resolvedPlan?.id === planEditor.id
        ? resolvedPlan
        : null
      : (plans.find((plan) => plan.id === planEditor.id) ?? null)
    : null;

  function changeStatus(task: Task, status: TaskStatus) {
    if (historical) {
      openTask(task);
      return;
    }
    void onStatus(task, status).catch(() => undefined);
  }

  function openTask(task: Task) {
    const url = new URL(window.location.href);
    url.searchParams.delete('plan');
    url.searchParams.set('work', task.id);
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setPlanEditor(null);
    setTaskEditor({ id: task.id, kind: task.kind });
  }

  const openTaskById = useCallback((id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.delete('plan');
    url.searchParams.set('work', id);
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setPlanEditor(null);
    setTaskEditor({ id, kind: 'task' });
  }, []);

  function closeTask() {
    const url = new URL(window.location.href);
    url.searchParams.delete('work');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setTaskEditor(null);
  }

  function openPlan(plan: Plan) {
    const url = new URL(window.location.href);
    url.searchParams.delete('work');
    url.searchParams.set('view', 'plans');
    url.searchParams.set('plan', plan.id);
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setMode('plans');
    setTaskEditor(null);
    setPlanEditor({ id: plan.id });
  }

  function closePlan() {
    const url = new URL(window.location.href);
    url.searchParams.delete('plan');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setPlanEditor(null);
  }

  function selectMode(nextMode: WorkMode) {
    onModeChange?.(nextMode);
    const url = new URL(window.location.href);
    url.searchParams.set('view', nextMode);
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setMode(nextMode);
  }

  return (
    <div className="content work-content">
      <div className="work-toolbar">
        <div className="work-selectors">
          {scopeControl}
          <select
            aria-label={t('Work view')}
            value={mode}
            onChange={(event) => selectMode(event.target.value as WorkMode)}
          >
            {views.map((view) => (
              <option key={view.value} value={view.value}>
                {t(view.label as 'Board' | 'List' | 'Epics' | 'Plans')}
              </option>
            ))}
          </select>
        </div>
        <label className="work-search">
          <Search />
          <span className="sr-only">{t('Search work')}</span>
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              onSearch?.(event.target.value);
            }}
            placeholder={t('Search work')}
          />
          {query && (
            <button
              type="button"
              title={t('Clear search')}
              onClick={() => {
                setQuery('');
                onSearch?.('');
              }}
            >
              <X />
            </button>
          )}
        </label>
        <div className="work-tools">
          {filterLabels.length > 0 && (
            <WorkDropdown
              label={
                <>
                  {t('Filters')}
                  {selectedLabels.length > 0 && (
                    <>
                      {' '}
                      <span className="work-filter-count">{selectedLabels.length}</span>
                    </>
                  )}
                </>
              }
            >
              <fieldset className="work-filter-options">
                <legend>{t('Labels on this page')}</legend>
                {filterLabels.map((label) => (
                  <label key={label} style={labelStyle(label)}>
                    <input
                      type="checkbox"
                      checked={selectedLabels.includes(label)}
                      onChange={() =>
                        setSelectedLabels((current) =>
                          current.includes(label)
                            ? current.filter((item) => item !== label)
                            : [...current, label],
                        )
                      }
                    />
                    <i aria-hidden="true" /> {label}
                  </label>
                ))}
              </fieldset>
              <button type="button" onClick={() => setSelectedLabels([])}>
                <X aria-hidden="true" /> {t('Clear filters')}
              </button>
            </WorkDropdown>
          )}
          {sprintControls}
          <WorkDropdown label={t('New')} primary closeOnAction>
            <button type="button" onClick={() => setTaskEditor({ kind: 'task' })}>
              <Plus aria-hidden="true" /> {t('New task')}
            </button>
            <button type="button" onClick={() => setTaskEditor({ kind: 'epic' })}>
              <Layers3 aria-hidden="true" /> {t('New epic')}
            </button>
            <button type="button" onClick={() => setPlanEditor({})}>
              <FileText aria-hidden="true" /> {t('New plan')}
            </button>
            {onCreateSprint && (
              <button type="button" onClick={onCreateSprint}>
                <Calendar aria-hidden="true" /> {t('New sprint')}
              </button>
            )}
          </WorkDropdown>
        </div>
      </div>

      <div className="work-summary-group">
        <div className="work-summary">
          <span>
            <strong>{openTasks.length}</strong> {t('open tasks')}
          </span>
          <span>
            <strong>
              {epics.filter((epic) => !['done', 'cancelled'].includes(epic.status)).length}
            </strong>{' '}
            {t('active epics')}
          </span>
          <span
            className={openTasks.some((task) => task.status === 'blocked') ? 'has-blocked' : ''}
          >
            <strong>{openTasks.filter((task) => task.status === 'blocked').length}</strong>{' '}
            {t('blocked')}
          </span>
          <span>
            <strong>{activePlans.length}</strong> {t('active plans')}
          </span>
        </div>
        {pageCaption && <p className="work-page-caption">{pageCaption}</p>}
        {scopeNotice}
      </div>

      <div className="work-surface">
        {mode === 'board' && (
          <BoardView
            tasks={filteredTasks}
            epics={epicMap}
            onOpen={openTask}
            onStatus={changeStatus}
          />
        )}
        {mode === 'list' && <ListView tasks={filtered} epics={epicMap} onOpen={openTask} />}
        {mode === 'epics' && (
          <EpicsView
            epics={epics}
            tasks={tasks.filter((task) => task.kind === 'task')}
            matches={matches}
            onOpen={openTask}
            onStatus={changeStatus}
          />
        )}
        {mode === 'plans' && (
          <PlansView plans={filteredPlans} taskMap={taskMap} onOpen={openPlan} />
        )}
      </div>

      {detailLoading && <p role="status">{t('Loading work details…')}</p>}
      {detailError && (
        <div role="alert">
          {detailError}
          <button
            type="button"
            className="secondary"
            onClick={() => {
              closeTask();
              closePlan();
              setDetailError('');
            }}
          >
            {t('Close')}
          </button>
        </div>
      )}
      {taskEditor && (!taskEditor.id || editingTask) && (
        <TaskDrawer
          key={taskEditor.id ?? `new-${taskEditor.kind}`}
          projectId={projectId}
          task={editingTask}
          defaultKind={taskEditor.kind}
          epics={epics}
          sprints={sprints}
          defaultSprintId={defaultSprintId}
          historical={historical}
          onOpenTask={loadTask ? openTaskById : undefined}
          onClose={closeTask}
          onCreate={onCreate}
          onUpdate={onUpdate}
          onUpload={onUpload}
          onRemoveAttachment={onRemoveAttachment}
        />
      )}
      {planEditor && (!planEditor.id || editingPlan) && (
        <PlanDrawer
          key={planEditor.id ?? 'new-plan'}
          projectId={projectId}
          plan={editingPlan}
          tasks={tasks}
          onClose={closePlan}
          onCreate={onCreatePlan}
          onUpdate={onUpdatePlan}
          onUpload={onUploadPlan}
          onRemoveAttachment={onRemovePlanAttachment}
          onOpenTask={loadTask ? openTaskById : undefined}
        />
      )}
    </div>
  );
}
