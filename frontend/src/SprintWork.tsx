import { Archive, ArrowLeft, ArrowRight, X } from 'lucide-react';
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import { MarkdownContent } from './Markdown';
import type { Plan, Sprint, SprintClosePreview, Task, WorkPage, WorkPlacement } from './types';
import { WorkDropdown } from './WorkDropdown';
import { useModalDialog, type WorkMode, WorkView, type WorkViewProps } from './WorkView';

const PAGE_SIZE = 50;
const placements: WorkPlacement[] = ['current', 'backlog', 'archive', 'all'];
const placementLabels = {
  current: 'Current sprint',
  backlog: 'Backlog',
  archive: 'Archive',
  all: 'All work',
} as const;
const emptyPage = <T,>(): WorkPage<T> => ({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });

function SprintDialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const ref = useModalDialog(onClose);
  return (
    <div className="drawer-backdrop">
      <section
        className="sprint-dialog"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className="drawer-header">
          <h2>{title}</h2>
          <button type="button" className="icon" aria-label={t('Close')} onClick={onClose}>
            <X />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

export function SprintWork(props: WorkViewProps) {
  const { projectId } = props;
  const { t, locale } = useI18n();
  const [placement, setPlacement] = useState<WorkPlacement>(() => {
    const requested = new URLSearchParams(window.location.search).get('placement');
    return placements.find((value) => value === requested) ?? 'current';
  });
  const [selectedSprintId, setSelectedSprintId] = useState(
    () => new URLSearchParams(window.location.search).get('sprint') ?? '',
  );
  const [mode, setMode] = useState<WorkMode>(() => {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get('plan') ? 'plans' : params.get('view');
    return ['board', 'list', 'epics', 'plans'].includes(requested ?? '')
      ? (requested as WorkMode)
      : 'board';
  });
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [sprints, setSprints] = useState<Sprint[]>([]);
  const [sprintOffset, setSprintOffset] = useState(0);
  const [sprintTotal, setSprintTotal] = useState(0);
  const [sprintsReady, setSprintsReady] = useState(false);
  const [taskPage, setTaskPage] = useState<WorkPage<Task>>(() => ({
    ...emptyPage<Task>(),
    items: props.tasks,
  }));
  const [planPage, setPlanPage] = useState<WorkPage<Plan>>(() => ({
    ...emptyPage<Plan>(),
    items: props.plans ?? [],
  }));
  const [epics, setEpics] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [objective, setObjective] = useState('');
  const [preview, setPreview] = useState<SprintClosePreview | null>(null);
  const [destination, setDestination] = useState('');
  const [importing, setImporting] = useState(false);
  const [selectedHistory, setSelectedHistory] = useState<string[]>([]);
  const keys = useRef(new Map<string, string>());
  const activeSprint = sprints.find((sprint) => sprint.status === 'active');
  const selectedSprint = sprints.find((sprint) => sprint.id === selectedSprintId);
  const displayedSprint = selectedSprint ?? (placement === 'current' ? activeSprint : undefined);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const loadTask = useCallback((id: string) => api.getTask(projectId, id), [projectId]);
  const loadPlan = useCallback((id: string) => api.getPlan(projectId, id), [projectId]);

  function mutationKey(input: unknown) {
    const fingerprint = JSON.stringify(input);
    let key = keys.current.get(fingerprint);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(fingerprint, key);
    }
    return key;
  }

  useEffect(() => {
    if (query.trim() === debouncedQuery) return;
    const timer = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, debouncedQuery]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: Mutations and parent refreshes invalidate the remote inventory even when project identity is unchanged.
  useEffect(() => {
    let current = true;
    void Promise.all([
      api.listSprints(projectId),
      api.listSprints(projectId, { status: 'active' }),
      api.listSprints(projectId, { status: 'planned' }),
      api.listWork(projectId, { kind: 'epic', placement: 'all', scope: 'all' }),
      selectedSprintId ? api.getSprint(projectId, selectedSprintId) : Promise.resolve(null),
    ])
      .then(([page, active, planned, epicPage, exactSprint]) => {
        if (!current) return;
        setSprints([
          ...new Map(
            [
              ...page.items,
              ...active.items,
              ...planned.items,
              ...(exactSprint ? [exactSprint] : []),
            ].map((sprint) => [sprint.id, sprint]),
          ).values(),
        ]);
        setSprintOffset(page.items.length);
        setSprintTotal(page.total);
        setEpics(epicPage.items);
        setSprintsReady(true);
      })
      .catch((reason) => {
        if (current) {
          setError(String(reason.message ?? reason));
          setLoading(false);
        }
      });
    return () => {
      current = false;
    };
  }, [projectId, revision, props.tasks, props.plans, selectedSprintId]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: Refetch for explicit invalidation and sprint identity; unrelated sprint metadata does not change this page.
  useEffect(() => {
    if (!sprintsReady) return;
    let current = true;
    setLoading(true);
    setError('');
    const options = { limit: PAGE_SIZE, offset, q: debouncedQuery };
    const workRequest =
      mode === 'plans'
        ? api.listPlans(projectId, options).then((page) => {
            if (current) setPlanPage(page);
          })
        : mode === 'epics'
          ? api
              .listWork(projectId, { ...options, kind: 'epic', placement: 'all', scope: 'all' })
              .then((page) => {
                if (current) setTaskPage(page);
              })
          : placement === 'archive' && displayedSprint
            ? api.archivedSprintTasks(projectId, displayedSprint.id, options).then((page) => {
                if (current) setTaskPage(page);
              })
            : api
                .listWork(projectId, {
                  ...options,
                  kind: 'task',
                  scope: 'all',
                  placement:
                    placement === 'current' && !activeSprint && !displayedSprint
                      ? 'backlog'
                      : displayedSprint
                        ? 'all'
                        : placement,
                  sprint_id: displayedSprint?.id,
                })
                .then((page) => {
                  if (current) setTaskPage(page);
                });
    void workRequest
      .catch((reason) => {
        if (current) setError(String(reason.message ?? reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [
    projectId,
    placement,
    displayedSprint?.id,
    activeSprint?.id,
    mode,
    offset,
    debouncedQuery,
    revision,
    sprintsReady,
    props.tasks,
    props.plans,
  ]);

  function changePlacement(value: WorkPlacement) {
    setPlacement(value);
    setSelectedSprintId('');
    setOffset(0);
    setImporting(false);
    const url = new URL(window.location.href);
    url.searchParams.set('placement', value);
    url.searchParams.delete('sprint');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
  }

  function chooseSprint(id: string) {
    setSelectedSprintId(id);
    setOffset(0);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('sprint', id);
    else url.searchParams.delete('sprint');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
  }

  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await action();
      keys.current.clear();
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function createSprint(event: FormEvent) {
    event.preventDefault();
    const input = { title: title.trim(), objective: objective.trim() };
    if (!input.title) return;
    await mutate(async () => {
      const sprint = await api.createSprint(projectId, {
        ...input,
        idempotency_key: mutationKey(['create', input]),
      });
      setSprints((current) => [...current, sprint]);
      changePlacement('current');
      chooseSprint(sprint.id);
      setCreating(false);
      setTitle('');
      setObjective('');
    });
  }

  async function requestPreview() {
    if (!displayedSprint) return;
    await mutate(async () => {
      setPreview(await api.closeSprintPreview(projectId, displayedSprint.id));
      setDestination('');
    });
  }

  async function closeSprint(event: FormEvent) {
    event.preventDefault();
    if (!preview || (preview.unfinished_count > 0 && !destination)) return;
    const input = {
      unfinished_destination:
        destination && destination !== 'backlog' ? ('sprint' as const) : ('backlog' as const),
      ...(destination && destination !== 'backlog' ? { destination_sprint_id: destination } : {}),
    };
    await mutate(async () => {
      const archived = await api.archiveSprint(projectId, preview.sprint, {
        ...input,
        idempotency_key: mutationKey(['archive', preview.sprint, input]),
      });
      setPreview(null);
      changePlacement('archive');
      chooseSprint(archived.id);
    });
  }

  async function moreSprints() {
    setBusy(true);
    try {
      const page = await api.listSprints(projectId, { offset: sprintOffset });
      setSprints((current) => [
        ...new Map([...current, ...page.items].map((sprint) => [sprint.id, sprint])).values(),
      ]);
      setSprintOffset(page.offset + page.items.length);
      setSprintTotal(page.total);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const page = mode === 'plans' ? planPage : taskPage;
  const historicalCandidates = taskPage.items.filter(
    (task) =>
      task.kind === 'task' && !task.sprint_id && ['done', 'cancelled'].includes(task.status),
  );
  const scopeValue = selectedSprintId ? `${placement}:${selectedSprintId}` : placement;
  const liveSprints = sprints.filter((sprint) => sprint.status !== 'archived');
  const archivedSprints = sprints.filter(
    (sprint) => sprint.status === 'archived' || Boolean(sprint.archive_version),
  );
  const knownScope =
    !selectedSprintId ||
    liveSprints.some((sprint) => scopeValue === `current:${sprint.id}`) ||
    archivedSprints.some((sprint) => scopeValue === `archive:${sprint.id}`);
  const scopeControl = (
    <label className="work-scope-select">
      <span className="sr-only">{t('Work scope')}</span>
      <select
        value={scopeValue}
        disabled={mode === 'epics' || mode === 'plans'}
        onChange={(event) => {
          const [nextPlacement, sprintId] = event.target.value.split(':');
          if (!placements.includes(nextPlacement as WorkPlacement)) return;
          changePlacement(nextPlacement as WorkPlacement);
          if (sprintId) chooseSprint(sprintId);
        }}
      >
        <optgroup label={t('Work scope')}>
          {placements.map((value) => (
            <option key={value} value={value}>
              {t(placementLabels[value])}
            </option>
          ))}
        </optgroup>
        {liveSprints.length > 0 && (
          <optgroup label={t('Planned and active sprints')}>
            {liveSprints.map((sprint) => (
              <option key={sprint.id} value={`current:${sprint.id}`}>
                {sprint.title}
              </option>
            ))}
          </optgroup>
        )}
        {archivedSprints.length > 0 && (
          <optgroup label={t('Archived sprints')}>
            {archivedSprints.map((sprint) => (
              <option key={sprint.id} value={`archive:${sprint.id}`}>
                {sprint.title}
              </option>
            ))}
          </optgroup>
        )}
        {!knownScope && (
          <option value={scopeValue}>{displayedSprint?.title ?? t('Loading work…')}</option>
        )}
      </select>
    </label>
  );
  const sprintControls = (displayedSprint || sprintOffset < sprintTotal) && (
    <WorkDropdown
      label={t('Sprint details')}
      key={`${placement}:${displayedSprint?.id ?? ''}`}
      closeOnAction
    >
      {displayedSprint && (
        <div className="sprint-menu-details">
          <h2>{displayedSprint.title}</h2>
          {displayedSprint.objective && (
            <MarkdownContent>{displayedSprint.objective}</MarkdownContent>
          )}
        </div>
      )}
      {placement !== 'archive' && displayedSprint?.status === 'planned' && (
        <button
          type="button"
          disabled={busy || Boolean(activeSprint)}
          onClick={() =>
            void mutate(() =>
              api.startSprint(projectId, displayedSprint, mutationKey(['start', displayedSprint])),
            )
          }
        >
          {t('Start sprint')}
        </button>
      )}
      {placement !== 'archive' && displayedSprint?.status === 'active' && (
        <button type="button" disabled={busy} onClick={() => void requestPreview()}>
          <Archive />
          {t('Close and archive')}
        </button>
      )}
      {displayedSprint?.status === 'archived' && (
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void mutate(async () => {
              const sprint = await api.reopenSprint(
                projectId,
                displayedSprint,
                mutationKey(['reopen', displayedSprint]),
              );
              changePlacement('current');
              chooseSprint(sprint.id);
            })
          }
        >
          {t('Reopen as planned')}
        </button>
      )}
      {sprintOffset < sprintTotal && (
        <button type="button" disabled={busy} onClick={() => void moreSprints()}>
          {t('Load more sprints')}
        </button>
      )}
    </WorkDropdown>
  );
  return (
    <section className="sprint-work" aria-label={t('Development cycles')}>
      {error && (
        <div role="alert" className="drawer-error">
          {error}
          <button className="secondary" type="button" onClick={refresh}>
            {t('Retry')}
          </button>
        </div>
      )}
      {loading && <p role="status">{t('Loading work…')}</p>}
      <WorkView
        {...props}
        scopeControl={scopeControl}
        sprintControls={sprintControls}
        onCreateSprint={() => {
          setCreating(true);
          setTitle('');
          setObjective('');
        }}
        scopeNotice={
          mode === 'epics' || mode === 'plans' ? (
            <p className="work-scope-note">
              {t('Epics and plans belong to the whole project and can span several sprints.')}
            </p>
          ) : placement === 'current' && !displayedSprint ? (
            <p className="work-scope-note">
              {t('No active sprint. Your unfinished work remains in the backlog.')}
            </p>
          ) : null
        }
        pageCaption={
          <>
            <span>
              {t('{start}–{end} of {total} items', {
                start: page.total ? offset + 1 : 0,
                end: Math.min(offset + page.items.length, page.total),
                total: page.total,
              })}
            </span>
            {(mode === 'board' || mode === 'list') && <> · {t('Task counts on this page.')}</>}
          </>
        }
        tasks={taskPage.items}
        plans={planPage.items}
        epicCatalog={mode === 'epics' ? [] : epics}
        sprints={sprints}
        defaultSprintId={
          placement === 'current' && displayedSprint?.status !== 'archived'
            ? displayedSprint?.id
            : null
        }
        loadTask={loadTask}
        loadPlan={loadPlan}
        historical={placement === 'archive'}
        onModeChange={(next) => {
          setMode(next);
          setOffset(0);
        }}
        onSearch={setQuery}
        onCreate={async (input) => {
          const item = await props.onCreate(input);
          refresh();
          return item;
        }}
        onUpdate={async (task, input) => {
          const item = await props.onUpdate(task, input);
          refresh();
          return item;
        }}
        onCreatePlan={async (input) => {
          const item = await (props.onCreatePlan
            ? props.onCreatePlan(input)
            : api.createPlan(projectId, input));
          refresh();
          return item;
        }}
        onUpdatePlan={async (plan, input) => {
          const item = await (props.onUpdatePlan
            ? props.onUpdatePlan(plan, input)
            : api.updatePlan(projectId, plan, input));
          refresh();
          return item;
        }}
        onStatus={async (task, status) => {
          await props.onStatus(task, status);
          refresh();
        }}
      />
      <nav className="work-pagination" aria-label={t('Work pages')}>
        <button
          type="button"
          className="secondary"
          disabled={loading || offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
        >
          <ArrowLeft />
          {t('Previous page')}
        </button>
        <span>
          {new Intl.NumberFormat(locale).format(page.total)} {t('items')}
        </span>
        <button
          type="button"
          className="secondary"
          disabled={loading || offset + page.items.length >= page.total}
          onClick={() => setOffset((value) => value + PAGE_SIZE)}
        >
          {t('Next page')}
          <ArrowRight />
        </button>
      </nav>
      {placement === 'archive' && !selectedSprintId && (mode === 'board' || mode === 'list') && (
        <section className="history-import">
          <button
            type="button"
            className="secondary"
            onClick={() => setImporting((value) => !value)}
          >
            {t('Group selected past work')}
          </button>
          {importing && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!title.trim() || !selectedHistory.length) return;
                void mutate(async () => {
                  const input = { title: title.trim(), task_ids: selectedHistory };
                  const sprint = await api.importSprintHistory(projectId, {
                    ...input,
                    idempotency_key: mutationKey(['history', input]),
                  });
                  setImporting(false);
                  setSelectedHistory([]);
                  setTitle('');
                  chooseSprint(sprint.id);
                });
              }}
            >
              <p>
                {t(
                  'Choose completed tasks to group. This records a historical collection; it does not invent past sprint dates.',
                )}
              </p>
              {historicalCandidates.map((task) => (
                <label key={task.id}>
                  <input
                    type="checkbox"
                    checked={selectedHistory.includes(task.id)}
                    onChange={() =>
                      setSelectedHistory((current) =>
                        current.includes(task.id)
                          ? current.filter((id) => id !== task.id)
                          : [...current, task.id],
                      )
                    }
                  />
                  {task.title}
                </label>
              ))}
              <label>
                {t('Historical collection name')}
                <input
                  required
                  maxLength={300}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
              <button
                type="submit"
                className="primary"
                disabled={busy || !title.trim() || !selectedHistory.length}
              >
                {t('Archive {count} selected tasks', { count: selectedHistory.length })}
              </button>
            </form>
          )}
        </section>
      )}
      {creating && (
        <SprintDialog title={t('New sprint')} onClose={() => setCreating(false)}>
          <form onSubmit={(event) => void createSprint(event)}>
            <label>
              {t('Sprint name')}
              <input
                required
                maxLength={300}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            <label>
              {t('Objective (optional)')}
              <textarea
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
                rows={3}
              />
            </label>
            <p>{t('Create a planned sprint, select its tasks, then start it when ready.')}</p>
            <button className="primary" type="submit" disabled={busy || !title.trim()}>
              {busy ? t('Saving') : t('Create sprint')}
            </button>
          </form>
        </SprintDialog>
      )}
      {preview && (
        <SprintDialog title={t('Close and archive')} onClose={() => setPreview(null)}>
          <form onSubmit={(event) => void closeSprint(event)}>
            <h3>{preview.sprint.title}</h3>
            <p>
              {t('{completed} completed · {unfinished} unfinished · {total} total', {
                completed: preview.completed_count,
                unfinished: preview.unfinished_count,
                total: preview.total,
              })}
            </p>
            <p>
              {t(
                'The closing snapshot keeps completed work and its history. Unfinished tasks keep their status and move to your chosen destination.',
              )}
            </p>
            {preview.unfinished_count > 0 && (
              <label>
                {t('Move unfinished tasks to')}
                <select
                  required
                  value={destination}
                  onChange={(event) => setDestination(event.target.value)}
                >
                  <option value="">{t('Choose a destination')}</option>
                  <option value="backlog">{t('Backlog')}</option>
                  {sprints
                    .filter(
                      (sprint) => sprint.status === 'planned' && sprint.id !== preview.sprint.id,
                    )
                    .map((sprint) => (
                      <option key={sprint.id} value={sprint.id}>
                        {sprint.title}
                      </option>
                    ))}
                </select>
              </label>
            )}
            {error && (
              <div role="alert">
                <p>{error}</p>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => void requestPreview()}
                >
                  {t('Reload closing preview')}
                </button>
              </div>
            )}
            <button
              className="primary"
              type="submit"
              disabled={busy || (preview.unfinished_count > 0 && !destination)}
            >
              {t('Archive sprint')}
            </button>
          </form>
        </SprintDialog>
      )}
    </section>
  );
}
