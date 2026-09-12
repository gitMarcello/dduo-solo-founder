import {
  Activity,
  AlertTriangle,
  BookOpen,
  Brain,
  CheckCircle2,
  Circle,
  Copy,
  DatabaseBackup,
  Download,
  FolderKanban,
  Gauge,
  ListTodo,
  LogOut,
  Moon,
  RotateCcw,
  Settings,
  ShieldCheck,
  UserPlus,
  Users,
  X,
} from 'lucide-react';
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from './api';
import { type Language, type Translate, useI18n } from './i18n';
import { MarkdownContent, MarkdownExcerpt } from './Markdown';
import type {
  ActivityEvent,
  BackupStatus,
  ContextEventDetail,
  MeasurementCoverage,
  Memory,
  MemoryProvenance,
  MemoryStatus,
  ObservabilityEvent,
  ObservabilityRange,
  ObservabilitySummary,
  OperationalManual,
  OperationalManualDraft,
  PricingCoverage,
  Project,
  SleepJob,
  TeamInvitation,
  TeamMember,
  TeamOverview,
} from './types';

export type Tab =
  | 'project'
  | 'memory'
  | 'tasks'
  | 'team'
  | 'observability'
  | 'activity'
  | 'backup'
  | 'setup';

export function Sidebar({
  tab,
  openTasks,
  memoryState,
  canManageInfrastructure,
  canOpenLocalSetup,
  onTab,
}: {
  tab: Tab;
  openTasks: number | null;
  memoryState: 'online' | 'attention' | 'offline' | 'unknown';
  canManageInfrastructure: boolean;
  canOpenLocalSetup: boolean;
  onTab: (tab: Tab) => void;
}) {
  const { t } = useI18n();
  const allItems: Array<{ id: Tab; label: string; icon: ReactNode }> = [
    { id: 'tasks', label: t('Work'), icon: <ListTodo /> },
    { id: 'project', label: t('Overview'), icon: <FolderKanban /> },
    { id: 'memory', label: t('Memory'), icon: <Brain /> },
    { id: 'team', label: t('Team'), icon: <Users /> },
    { id: 'observability', label: t('Observability'), icon: <Gauge /> },
    { id: 'activity', label: t('Activity'), icon: <Activity /> },
    { id: 'backup', label: t('Backup'), icon: <DatabaseBackup /> },
    { id: 'setup', label: t('Setup'), icon: <Settings /> },
  ];
  const items = allItems.filter((item) => {
    if (item.id === 'backup') return canManageInfrastructure;
    if (item.id === 'setup') return canOpenLocalSetup;
    return true;
  });
  return (
    <aside>
      <div className="brand">
        <span>dD</span>
        <strong>dDuo Solo Founder</strong>
      </div>
      <nav aria-label={t('Primary navigation')}>
        {items.map((item) => (
          <button
            type="button"
            aria-label={item.label}
            aria-current={tab === item.id ? 'page' : undefined}
            className={tab === item.id ? 'active' : ''}
            key={item.id}
            onClick={() => onTab(item.id)}
          >
            {item.icon}
            <span className="nav-label">{item.label}</span>
            {item.id === 'tasks' && openTasks !== null && <small>{openTasks}</small>}
          </button>
        ))}
      </nav>
      <div className={`status ${memoryState}`}>
        <i />
        {memoryState === 'online'
          ? t('Memory online')
          : memoryState === 'attention'
            ? t('Memory needs attention')
            : memoryState === 'unknown'
              ? t('Memory status not verified')
              : t('Memory unavailable')}
      </div>
    </aside>
  );
}

export function ConnectionForm({ onConnect }: { onConnect: (projectId: string) => void }) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    onConnect(value);
  }
  return (
    <section className="empty" aria-labelledby="connect-title">
      <FolderKanban />
      <h2 id="connect-title">{t('Connect a project')}</h2>
      <form onSubmit={submit}>
        <label htmlFor="project-id">{t('Project UUID')}</label>
        <div className="connect-row">
          <input
            id="project-id"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
          />
          <button disabled={!value.trim()} type="submit">
            {t('Connect')}
          </button>
        </div>
      </form>
    </section>
  );
}

export function ProjectView({ project }: { project: Project }) {
  const { t } = useI18n();
  return (
    <div className="content">
      <section>
        <h2>{t('Why it exists')}</h2>
        {project.cause ? (
          <MarkdownContent className="cause">{project.cause}</MarkdownContent>
        ) : (
          <p className="cause">{t('Waiting for onboarding')}</p>
        )}
      </section>
      <div className="cols">
        <section>
          <h2>{t('Objectives')}</h2>
          {project.objectives.length ? (
            project.objectives.map((item) => (
              <div className="line" key={item}>
                <Circle />
                <MarkdownContent className="line-content">{item}</MarkdownContent>
              </div>
            ))
          ) : (
            <p className="muted">{t('No objectives yet')}</p>
          )}
        </section>
        <section>
          <h2>{t('Principles')}</h2>
          {project.principles.length ? (
            project.principles.map((item) => (
              <div className="line" key={item}>
                <CheckCircle2 />
                <MarkdownContent className="line-content">{item}</MarkdownContent>
              </div>
            ))
          ) : (
            <p className="muted">{t('No principles yet')}</p>
          )}
        </section>
      </div>
    </div>
  );
}

function teamRole(
  member: Pick<TeamMember, 'capability' | 'capability_label'> | null | undefined,
  t: Translate,
) {
  if (!member) return t('Project access');
  return member.capability === 'infrastructure_manager'
    ? t('Infrastructure manager')
    : t('Project member');
}

function teamMemberStatus(status: TeamMember['status'], t: Translate) {
  return status === 'active' ? t('Active') : t('Revoked');
}

export function TeamView({
  team,
  manual,
  draft,
  invitation,
  loading,
  manualLoading = false,
  saving,
  drafting,
  inviting,
  revokingMemberId,
  error,
  manualError = '',
  onInvite,
  onRevokeMember,
  onSaveManual,
  onCreateDraft,
  onDraft,
  onRetryManual,
}: {
  project: Project;
  team: TeamOverview | null;
  manual: OperationalManual | null;
  draft: OperationalManualDraft | null;
  invitation: TeamInvitation | null;
  loading: boolean;
  manualLoading?: boolean;
  saving: boolean;
  drafting: boolean;
  inviting: boolean;
  revokingMemberId: string;
  error: string;
  manualError?: string;
  onInvite: (displayName: string, expiresInHours: number) => Promise<void>;
  onRevokeMember: (memberId: string) => Promise<void>;
  onSaveManual: (content: string, expectedVersion?: number) => Promise<void>;
  onCreateDraft: () => Promise<void>;
  onDraft: (draft: OperationalManualDraft | null) => void;
  onRetryManual?: () => Promise<void>;
}) {
  const { locale, t } = useI18n();
  const [inviteName, setInviteName] = useState('');
  const [inviteHours, setInviteHours] = useState(24);
  const [editingManual, setEditingManual] = useState(false);
  const [manualContent, setManualContent] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const [invitationClock, setInvitationClock] = useState(() => Date.now());
  const canManage = Boolean(team?.capabilities.manage_infrastructure);
  const trustedLocal = Boolean(team?.current_member?.trusted_local);
  const manualAuthor = team?.members.find(
    (member) => member.id === manual?.updated_by_member_id,
  )?.display_name;
  const invitationConsumed = Boolean(invitation?.consumed_at);
  const invitationExpiry = invitation ? Date.parse(invitation.expires_at) : Number.NaN;
  const invitationExpired = Boolean(
    invitation && (!Number.isFinite(invitationExpiry) || invitationExpiry <= invitationClock),
  );
  const invitationUsable = Boolean(invitation && !invitationConsumed && !invitationExpired);

  useEffect(() => {
    if (!editingManual) setManualContent(manual?.content ?? '');
  }, [editingManual, manual]);

  useEffect(() => {
    setDraftContent(draft?.draft ?? '');
  }, [draft]);

  useEffect(() => {
    setCopyStatus('');
    setInvitationClock(Date.now());
    if (!invitation) return undefined;
    const expiresAt = Date.parse(invitation.expires_at);
    if (!Number.isFinite(expiresAt)) return undefined;
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) return undefined;
    const timer = window.setTimeout(
      () => setInvitationClock(Date.now()),
      Math.min(remaining + 25, 2_147_483_647),
    );
    return () => window.clearTimeout(timer);
  }, [invitation]);

  async function submitInvite(event: FormEvent) {
    event.preventDefault();
    const displayName = inviteName.trim();
    if (!displayName) return;
    try {
      await onInvite(displayName, inviteHours);
      setInviteName('');
      setCopyStatus('');
    } catch {
      // The hook keeps the server error visible without clearing the form.
    }
  }

  async function saveManual(event: FormEvent) {
    event.preventDefault();
    try {
      await onSaveManual(manualContent);
      setEditingManual(false);
    } catch {
      // Keep the editor open so a version conflict can be reviewed safely.
    }
  }

  async function acceptDraft() {
    if (!draft) return;
    try {
      await onSaveManual(draftContent, draft.based_on_version);
      setEditingManual(false);
    } catch {
      // The draft remains unpersisted until the manager resolves the error.
    }
  }

  async function copyInvitation() {
    if (!invitation || !invitationUsable) return;
    try {
      const prompt = invitation.setup_prompt;
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(prompt);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = prompt;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.append(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('copy command was rejected');
      }
      setCopyStatus(t('Prompt copied'));
    } catch {
      setCopyStatus(t('Copy failed'));
    }
  }

  async function revokeMember(member: TeamMember) {
    if (!window.confirm(t('Revoke project access for {name}?', { name: member.display_name })))
      return;
    try {
      await onRevokeMember(member.id);
    } catch {
      // The hook keeps the server error visible and the member unchanged.
    }
  }

  return (
    <div className="content team-content">
      {error && (
        <div className="error team-error" role="alert">
          {error}
        </div>
      )}
      <section>
        <div className="section-heading team-heading">
          <div>
            <h2>{t('Project team')}</h2>
            <p className="muted">
              {t(
                'Everyone shares the same project memory. Access remains isolated from every other project.',
              )}
            </p>
          </div>
          {team?.current_member && (
            <div className="current-member">
              <strong>{team.current_member.display_name}</strong>
              <span>{teamRole(team.current_member, t)}</span>
            </div>
          )}
        </div>
        {loading && !team ? (
          <p className="muted" role="status">
            {t('Loading team…')}
          </p>
        ) : (
          <div className="team-member-list">
            {team?.members.map((member) => (
              <article key={member.id} className="team-member">
                <span className="team-avatar" aria-hidden="true">
                  {member.display_name.slice(0, 2).toUpperCase()}
                </span>
                <div>
                  <strong>{member.display_name}</strong>
                  <span>{teamRole(member, t)}</span>
                  <small>
                    {t('{count} active device(s) · joined {date}', {
                      count: member.devices?.filter((device) => !device.revoked_at).length ?? 0,
                      date: formatDate(member.created_at, locale),
                    })}
                  </small>
                </div>
                <span className="member-controls">
                  <span className={`member-state ${member.status}`}>
                    {teamMemberStatus(member.status, t)}
                  </span>
                  {canManage &&
                    member.status === 'active' &&
                    member.capability === 'project_member' && (
                      <button
                        type="button"
                        className="member-revoke"
                        disabled={revokingMemberId === member.id}
                        aria-label={t('Revoke access for {name}', { name: member.display_name })}
                        onClick={() => void revokeMember(member)}
                      >
                        {revokingMemberId === member.id ? t('Revoking…') : t('Revoke access')}
                      </button>
                    )}
                </span>
              </article>
            ))}
            {team && !team.members.length && (
              <p className="muted">
                {t(
                  'Team access has not been initialized yet. The trusted local owner can still manage this project.',
                )}
              </p>
            )}
          </div>
        )}
      </section>

      <section className="manual-panel">
        <div className="section-heading team-heading">
          <div>
            <h2>{t('Operational manual')}</h2>
            <p className="muted">
              {t(
                'Project operating rules delivered to every assistant session, whether you work alone or with a team.',
              )}
            </p>
          </div>
          {manual && (
            <div className="manual-version">
              <BookOpen />
              <span>{t('Version {version}', { version: manual.version })}</span>
            </div>
          )}
        </div>
        {!manual && manualLoading ? (
          <p className="muted" role="status">
            {t('Loading operational manual…')}
          </p>
        ) : !manual && manualError ? (
          <div className="manual-load-error" role="alert">
            <p>{t('Operational manual could not be loaded: {error}', { error: manualError })}</p>
            <button type="button" className="secondary" onClick={() => void onRetryManual?.()}>
              {t('Retry manual')}
            </button>
          </div>
        ) : !manual ? (
          <p className="muted">{t('Operational manual is unavailable.')}</p>
        ) : editingManual && canManage ? (
          <form className="manual-editor" onSubmit={(event) => void saveManual(event)}>
            <label htmlFor="operational-manual">{t('Manual content')}</label>
            <textarea
              id="operational-manual"
              rows={16}
              maxLength={manual.hard_limit_characters}
              value={manualContent}
              onChange={(event) => setManualContent(event.target.value)}
            />
            <div className="manual-editor-footer">
              <span>
                {t('{current} / {limit} characters', {
                  current: manualContent.length.toLocaleString(locale),
                  limit: manual.hard_limit_characters.toLocaleString(locale),
                })}
              </span>
              <div>
                <button
                  type="button"
                  className="secondary"
                  disabled={saving}
                  onClick={() => {
                    setManualContent(manual.content);
                    setEditingManual(false);
                  }}
                >
                  {t('Cancel')}
                </button>
                <button
                  type="submit"
                  className="primary"
                  disabled={saving || manualContent.trim() === manual.content.trim()}
                >
                  {saving ? t('Saving…') : t('Save new version')}
                </button>
              </div>
            </div>
          </form>
        ) : (
          <>
            <div className="manual-meta">
              <span>
                {t('{count} characters', { count: manual.characters.toLocaleString(locale) })}
              </span>
              {manualAuthor && <span>{t('Updated by {name}', { name: manualAuthor })}</span>}
              {manual.updated_at && (
                <span>{t('Updated {date}', { date: formatDate(manual.updated_at, locale) })}</span>
              )}
              {manual.warnings.includes('manual_above_soft_limit') && (
                <span className="manual-warning">
                  {t('Long manual — a compact draft is recommended for review')}
                </span>
              )}
              {manual.warnings.includes('manual_empty') && (
                <span className="manual-warning">{t('The manual is empty')}</span>
              )}
            </div>
            <div className={`manual-document ${manual.content ? '' : 'empty-manual'}`}>
              {manual.content ? (
                <MarkdownContent>{manual.content}</MarkdownContent>
              ) : (
                t('No operational rules have been recorded yet.')
              )}
            </div>
            {canManage && (
              <div className="manual-actions">
                <button type="button" className="secondary" onClick={() => setEditingManual(true)}>
                  {t('Edit manual')}
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={drafting}
                  onClick={() => void onCreateDraft().catch(() => undefined)}
                >
                  {drafting ? t('Creating draft…') : t('Create compact draft')}
                </button>
              </div>
            )}
          </>
        )}
        {draft && canManage && (
          <div className="compact-draft" aria-live="polite">
            <div>
              <strong>{t('Compact draft — not yet active')}</strong>
              <span>
                {t('Based on version {version} · {source}', {
                  version: draft.based_on_version,
                  source:
                    draft.source === 'provider' ? t('Model-assisted') : t('Deterministic fallback'),
                })}
              </span>
            </div>
            <textarea
              aria-label={t('Compact manual draft')}
              rows={14}
              maxLength={manual?.hard_limit_characters}
              value={draftContent}
              onChange={(event) => setDraftContent(event.target.value)}
            />
            <div className="manual-editor-footer">
              <span>
                {t('{count} characters · review before accepting', {
                  count: draftContent.length.toLocaleString(locale),
                })}
              </span>
              <div>
                <button
                  type="button"
                  className="secondary"
                  disabled={saving}
                  onClick={() => onDraft(null)}
                >
                  {t('Discard draft')}
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={saving || !draftContent.trim()}
                  onClick={() => void acceptDraft()}
                >
                  {saving ? t('Saving…') : t('Accept as new version')}
                </button>
              </div>
            </div>
          </div>
        )}
        {!canManage && manual && (
          <p className="manual-readonly">
            {t(
              'Visible across the project. Only the Infrastructure manager can publish a new version.',
            )}
          </p>
        )}
      </section>

      <section>
        <div className="section-heading team-heading">
          <div>
            <h2>{t('Invite a project member')}</h2>
            <p className="muted">
              {t(
                'The generated prompt connects one person to this project only. It never contains VPS credentials.',
              )}
            </p>
          </div>
          <UserPlus />
        </div>
        {canManage && !trustedLocal ? (
          <>
            <form className="invite-form" onSubmit={(event) => void submitInvite(event)}>
              <label>
                {t('Member name')}
                <input
                  value={inviteName}
                  maxLength={200}
                  onChange={(event) => setInviteName(event.target.value)}
                  placeholder={t('Member name')}
                />
              </label>
              <label>
                {t('Valid for')}
                <select
                  value={inviteHours}
                  onChange={(event) => setInviteHours(Number(event.target.value))}
                >
                  <option value={24}>{t('24 hours')}</option>
                  <option value={72}>{t('3 days')}</option>
                  <option value={168}>{t('7 days')}</option>
                </select>
              </label>
              <button className="primary" type="submit" disabled={inviting || !inviteName.trim()}>
                {inviting ? t('Creating…') : t('Create invitation')}
              </button>
            </form>
            {invitation && (
              <div className="invitation-output">
                <div>
                  <strong>{t('One-time setup prompt')}</strong>
                  <span>
                    {invitationConsumed
                      ? t('Invitation already consumed')
                      : invitationExpired
                        ? t('Invitation expired')
                        : t('Expires {date}', { date: formatDate(invitation.expires_at, locale) })}
                  </span>
                </div>
                {invitationUsable ? (
                  <textarea
                    aria-label={t('Invitation setup prompt')}
                    readOnly
                    rows={13}
                    value={invitation.setup_prompt}
                  />
                ) : (
                  <p className="invitation-unavailable" role="status">
                    {t(
                      'This one-time invitation can no longer be used. Create a new invitation if access is still needed.',
                    )}
                  </p>
                )}
                <div className="invitation-copy-row">
                  <span role="status">{copyStatus}</span>
                  <button
                    type="button"
                    className="secondary"
                    disabled={!invitationUsable}
                    onClick={() => void copyInvitation()}
                  >
                    <Copy /> {invitationUsable ? t('Copy prompt') : t('Prompt unavailable')}
                  </button>
                </div>
              </div>
            )}
          </>
        ) : trustedLocal ? (
          <p className="manual-readonly">
            {t(
              'To share this project, use a Linux VPS from any provider with at least 1 GiB RAM, 1 vCPU, 2 GiB persistent disk-backed swap, and 5 GiB free in Docker storage after swap. Setup checks the live resources before promotion; invitations become available when shared memory is online.',
            )}
          </p>
        ) : (
          <p className="manual-readonly">
            {t('Invitations are created by the Infrastructure manager.')}
          </p>
        )}
      </section>
    </div>
  );
}

export function SetupView({
  error = '',
  onOpen,
  opening,
}: {
  error?: string;
  onOpen: () => Promise<void>;
  opening: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="content">
      <section>
        <div className="section-heading">
          <div>
            <h2>{t('Setup & health')}</h2>
            <p className="muted">
              {t(
                'Runtime, embeddings, client sign-in and lifecycle permissions are managed by the infrastructure manager.',
              )}
            </p>
          </div>
          <button
            className="primary"
            disabled={opening}
            type="button"
            onClick={() => void onOpen()}
          >
            <Settings />
            {opening ? t('Opening…') : t('Open setup')}
          </button>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </section>
    </div>
  );
}

export function ActivityView({ events }: { events: ActivityEvent[] }) {
  const { locale, t } = useI18n();
  return (
    <div className="content">
      <section>
        <h2>{t('Recent activity')}</h2>
        {events.map((event) => (
          <article className="event" key={event.id}>
            <i />
            <div>
              <h3>
                <MarkdownExcerpt>{event.summary}</MarkdownExcerpt>
              </h3>
              <p>
                {event.kind} · {new Date(event.created_at).toLocaleString(locale)}
              </p>
            </div>
          </article>
        ))}
        {!events.length && <p className="muted">{t('No activity yet')}</p>}
      </section>
    </div>
  );
}

export function MemoryView({
  projectId,
  memories,
  status,
  jobs,
  onSleep,
  onRetry,
  onOpenSetup,
  schedulingSleep,
}: {
  projectId: string;
  memories: Memory[];
  status: MemoryStatus;
  jobs: SleepJob[];
  onSleep: () => Promise<void>;
  onRetry: (jobId: string) => Promise<void>;
  onOpenSetup: () => Promise<void>;
  schedulingSleep: boolean;
}) {
  const { locale, t } = useI18n();
  const [provenance, setProvenance] = useState<Record<string, MemoryProvenance>>({});
  const [busy, setBusy] = useState('');
  const activeJobs = (status.jobs.pending ?? 0) + (status.jobs.running ?? 0);
  const isConsolidating = activeJobs > 0 || schedulingSleep;
  const providerLabels: Record<MemoryStatus['state'], string> = {
    updated: t('Up to date'),
    updating: t('Updating'),
    waiting: t('Waiting'),
    limited: t('Limited'),
    connection_required: t('Connection required'),
  };
  const localizedSummary =
    status.state === 'connection_required'
      ? t('Connect {provider} in Setup to resume memory.', {
          provider: status.provider
            ? status.provider.charAt(0).toUpperCase() + status.provider.slice(1)
            : t('the selected client'),
        })
      : status.state === 'limited'
        ? t('Memory has a temporary usage limit and will retry automatically.')
        : status.state === 'waiting'
          ? t('Local memory will retry automatically.')
          : status.state === 'updating'
            ? t('Recent turns are waiting to be consolidated.')
            : t('Memory is up to date.');

  async function explain(memoryId: string) {
    if (provenance[memoryId]) {
      setProvenance((current) => {
        const next = { ...current };
        delete next[memoryId];
        return next;
      });
      return;
    }
    setBusy(memoryId);
    try {
      const value = await api.explainMemory(projectId, memoryId);
      setProvenance((current) => ({ ...current, [memoryId]: value }));
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="content memory-content">
      <section className="memory-health">
        <div className="section-heading">
          <div>
            <h2>{t('Memory engine')}</h2>
            <p className="muted">
              {isConsolidating
                ? t('Consolidating {turns} automatically.', {
                    turns:
                      activeJobs === 1
                        ? t('one turn')
                        : activeJobs
                          ? t('{count} turns', { count: activeJobs })
                          : t('recent turns'),
                  })
                : localizedSummary}
            </p>
          </div>
          {status.state === 'connection_required' ? (
            <button className="primary" type="button" onClick={() => void onOpenSetup()}>
              <Settings />
              {t('Open Setup')}
            </button>
          ) : (
            <button
              className="secondary"
              disabled={isConsolidating}
              type="button"
              onClick={() => void onSleep()}
            >
              <Moon className={isConsolidating ? 'spin' : ''} />
              {isConsolidating ? t('Consolidating…') : t('Consolidate now')}
            </button>
          )}
        </div>
        <dl className="memory-metrics">
          <div>
            <dt>{t('Active memories')}</dt>
            <dd>{status.memories.active ?? 0}</dd>
          </div>
          <div>
            <dt>{t('To consolidate')}</dt>
            <dd>{activeJobs}</dd>
          </div>
          <div>
            <dt>{t('Waiting')}</dt>
            <dd>{status.jobs.waiting ?? 0}</dd>
          </div>
        </dl>
        {status.providers && (
          <ul className="memory-providers" aria-label={t('Memory providers')}>
            {(['codex', 'claude'] as const).map((provider) => {
              const providerStatus = status.providers?.[provider];
              if (!providerStatus) return null;
              const queued =
                (providerStatus.jobs.pending ?? 0) + (providerStatus.jobs.running ?? 0);
              const waiting = providerStatus.jobs.waiting ?? 0;
              return (
                <li key={provider}>
                  <strong>{provider === 'codex' ? 'Codex' : 'Claude'}</strong>
                  <span data-state={providerStatus.state}>
                    {providerLabels[providerStatus.state]}
                  </span>
                  <small>
                    {waiting
                      ? t('{count} waiting', { count: waiting })
                      : queued
                        ? t('{count} queued', { count: queued })
                        : t('No pending work')}
                  </small>
                </li>
              );
            })}
          </ul>
        )}
        {jobs.find((job) => job.status === 'waiting') && (
          <div className="backup-alert error-state" role="alert">
            <AlertTriangle />
            <div>
              <h3>
                {status.state === 'connection_required'
                  ? t('Connection required')
                  : status.state === 'limited'
                    ? t('Temporary usage limit')
                    : t('Memory will retry')}
              </h3>
              <p>
                {localizedSummary}
                {status.retry_at
                  ? t('Next attempt {date}.', { date: formatDate(status.retry_at, locale) })
                  : ''}
              </p>
            </div>
            {status.state === 'connection_required' ? (
              <button
                className="icon"
                type="button"
                aria-label={t('Open memory setup')}
                title={t('Open memory setup')}
                onClick={() => void onOpenSetup()}
              >
                <Settings />
              </button>
            ) : (
              <button
                className="icon"
                type="button"
                title={t('Retry sleep')}
                onClick={() => {
                  const job = jobs.find((item) => item.status === 'waiting');
                  if (job) void onRetry(job.id);
                }}
              >
                <RotateCcw />
              </button>
            )}
          </div>
        )}
      </section>
      <section>
        <h2>{t('Consolidated memory')}</h2>
        <div className="memory-list">
          {memories.map((memory) => (
            <article className="memory-row" key={memory.id}>
              <button
                type="button"
                className="memory-main"
                aria-expanded={Boolean(provenance[memory.id])}
                disabled={busy === memory.id}
                onClick={() => void explain(memory.id)}
              >
                <span data-memory-type={memory.node_type}>
                  {memory.node_type.replace('_', ' ')}
                </span>
                <strong>{memory.node_key}</strong>
                <MarkdownExcerpt className="memory-excerpt">{memory.text}</MarkdownExcerpt>
                <small>{t('Revision {revision}', { revision: memory.revision })}</small>
              </button>
              {provenance[memory.id] && (
                <div className="provenance">
                  <strong>
                    {t('{count} revisions', { count: provenance[memory.id].revisions.length })}
                  </strong>
                  {provenance[memory.id].revisions.map((revision) => (
                    <div className="provenance-entry" key={revision.id}>
                      <strong>v{revision.revision}</strong>
                      <MarkdownContent>{revision.text}</MarkdownContent>
                    </div>
                  ))}
                  {provenance[memory.id].sources.map((source) => (
                    <MarkdownContent className="provenance-entry" key={source.turn_id}>
                      {source.user_prompt}
                    </MarkdownContent>
                  ))}
                  {(provenance[memory.id].source_artifacts ?? []).map((artifact) => (
                    <p key={artifact.artifact_id}>
                      {t('Source: {source}', { source: artifact.filename || artifact.source_uri })}
                    </p>
                  ))}
                  {!provenance[memory.id].sources.length && <p>{t('No retained source turns')}</p>}
                </div>
              )}
            </article>
          ))}
          {!memories.length && <p className="muted">{t('No consolidated memories yet')}</p>}
        </div>
      </section>
    </div>
  );
}

function currentLocale() {
  return document.documentElement.lang.toLowerCase().startsWith('it') ? 'it-IT' : 'en-US';
}

function formatDate(value?: string, locale = currentLocale()) {
  return value
    ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(
        new Date(value),
      )
    : locale === 'it-IT'
      ? 'Mai'
      : 'Never';
}

function formatBytes(value: number | undefined, locale: string) {
  if (!value) return '—';
  return new Intl.NumberFormat(locale, {
    style: 'unit',
    unit: value >= 1_000_000 ? 'megabyte' : 'kilobyte',
    unitDisplay: 'short',
    maximumFractionDigits: 1,
  }).format(value / (value >= 1_000_000 ? 1_000_000 : 1_000));
}

function remoteBackupSetupRequest(projectId: string, language: Language) {
  if (language === 'en') {
    return [
      `Configure the encrypted automatic backup for dDuo project ${projectId} on its authoritative VPS.`,
      'Verify that this chat is open in the authorized checkout and that I am the Infrastructure manager.',
      'Perform the VPS steps yourself, use persistent storage outside the containers, and verify the first Full Recovery Bundle.',
      'Let me save the recovery key once and never put credentials, recovery keys, or tokens in Git, Work, the operational manual, invitations, memory, or logs.',
      'If VPS access is needed, enable off-record mode first and ask only for what is missing; do not ask me to run terminal commands.',
    ].join(' ');
  }
  return [
    `Configura il backup automatico cifrato del progetto dDuo ${projectId} sulla sua VPS autorevole.`,
    'Verifica che questa chat sia aperta nel checkout autorizzato e che io sia il Gestore dell’infrastruttura.',
    'Esegui tu i passaggi sulla VPS, usa una destinazione persistente fuori dai container e verifica il primo Full Recovery Bundle.',
    'Fammi salvare la recovery key una sola volta e non inserire credenziali, recovery key o token in Git, Work, manuale operativo, inviti, memoria o log.',
    'Se serve accesso alla VPS, attiva prima la modalità off-record e chiedimi solo ciò che manca; non chiedermi comandi da eseguire nel terminale.',
  ].join(' ');
}

export function BackupView({
  projectId,
  status,
  backingUp,
  canOpenLocalSetup,
  onBackup,
  onOpenSetup,
}: {
  projectId: string;
  status: BackupStatus;
  backingUp: boolean;
  canOpenLocalSetup: boolean;
  onBackup: () => Promise<void>;
  onOpenSetup: () => Promise<void>;
}) {
  const { language, locale, t } = useI18n();
  const [handoffStatus, setHandoffStatus] = useState<'idle' | 'copied' | 'failed'>('idle');

  async function backup() {
    try {
      await onBackup();
    } catch {
      // The project state exposes the error in the page-level alert.
    }
  }

  async function copyRemoteSetupRequest() {
    const request = remoteBackupSetupRequest(projectId, language);
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(request);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = request;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.append(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('copy command was rejected');
      }
      setHandoffStatus('copied');
    } catch {
      setHandoffStatus('failed');
    }
  }

  return (
    <div className="content">
      <section className="backup-panel">
        <div className="section-heading">
          <div>
            <h2>{t('Backup')}</h2>
            <p className="muted">
              {status.configured
                ? t('Encrypted and created automatically after changes.')
                : t('Set up once, then protected automatically.')}
            </p>
          </div>
          {status.configured ? (
            <button
              className="secondary"
              type="button"
              disabled={backingUp}
              onClick={() => void backup()}
            >
              <DatabaseBackup className={backingUp ? 'spin' : ''} />
              {backingUp ? t('Creating…') : t('Create now')}
            </button>
          ) : canOpenLocalSetup ? (
            <button className="primary" type="button" onClick={() => void onOpenSetup()}>
              <Settings />
              {t('Enable backup')}
            </button>
          ) : (
            <button className="primary" type="button" onClick={() => void copyRemoteSetupRequest()}>
              <Copy />
              {handoffStatus === 'copied' ? t('Request copied') : t('Copy setup request')}
            </button>
          )}
        </div>
        {!status.configured ? (
          <div className="backup-alert" role="status">
            <AlertTriangle />
            <div>
              <h3>
                {canOpenLocalSetup
                  ? t('Automatic backup is not enabled')
                  : t('Backup must be enabled on the VPS')}
              </h3>
              {canOpenLocalSetup ? (
                <p>
                  {status.configuration_error ||
                    t(
                      'Choose Enable backup once. dDuo will create encrypted archives automatically.',
                    )}
                </p>
              ) : (
                <>
                  <p>
                    {t(
                      'Copy the safe request above into an authorized chat for this project. dDuo can complete the host work with the infrastructure manager; this dashboard does not open local Setup, expose a secret, or ask a team member to use the terminal.',
                    )}
                  </p>
                  <p className="backup-handoff-request">
                    {remoteBackupSetupRequest(projectId, language)}
                  </p>
                  {handoffStatus === 'failed' && (
                    <p className="backup-copy-error" role="alert">
                      {t(
                        'The request could not be copied. Select the text above and copy it manually.',
                      )}
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        ) : (
          <>
            <div className="backup-state">
              <ShieldCheck />
              <div>
                <h3>{status.dirty ? t('New changes pending') : t('Memory protected')}</h3>
                <p>
                  {t('Last verified {date}', { date: formatDate(status.last_backup_at, locale) })}
                </p>
              </div>
            </div>
            <dl className="backup-details">
              <div>
                <dt>{t('Latest archive')}</dt>
                <dd>
                  {status.latest_verified?.archive_name ? (
                    <a
                      className="backup-download"
                      download
                      href={api.backupDownloadUrl(projectId, status.latest_verified.id)}
                    >
                      <Download />
                      {t('Download archive')}
                    </a>
                  ) : (
                    '—'
                  )}
                </dd>
              </div>
              <div>
                <dt>{t('Archive size')}</dt>
                <dd>{formatBytes(status.latest_verified?.size_bytes, locale)}</dd>
              </div>
              <div>
                <dt>{t('Vector index')}</dt>
                <dd>
                  {status.latest_verified?.includes_qdrant
                    ? t('Included')
                    : t('Rebuild on restore')}
                </dd>
              </div>
              <div>
                <dt>{t('Retention')}</dt>
                <dd>
                  {status.retention.daily} {t('daily')} · {status.retention.weekly} {t('weekly')} ·{' '}
                  {status.retention.monthly} {t('monthly')}
                </dd>
              </div>
            </dl>
          </>
        )}
        {status.latest?.status === 'failed' && (
          <div className="backup-alert error-state" role="alert">
            <AlertTriangle />
            <div>
              <h3>{t('Latest backup failed')}</h3>
              <p>{status.latest.error}</p>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export function DisconnectButton({ onDisconnect }: { onDisconnect: () => void | Promise<void> }) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      className="icon"
      aria-label={t('Close browser session and switch project')}
      title={t('Close browser session and switch project')}
      onClick={() => void onDisconnect()}
    >
      <LogOut />
    </button>
  );
}

function number(value: number | null | undefined) {
  return value === null || value === undefined
    ? '—'
    : new Intl.NumberFormat(currentLocale()).format(value);
}

function bytes(value: number | null | undefined) {
  if (value === null || value === undefined) return '—';
  if (value < 1_000) return `${number(value)} B`;
  if (value < 1_000_000)
    return `${new Intl.NumberFormat(currentLocale(), { maximumFractionDigits: 1 }).format(value / 1_000)} KB`;
  return `${new Intl.NumberFormat(currentLocale(), { maximumFractionDigits: 1 }).format(value / 1_000_000)} MB`;
}

function duration(value: number | null | undefined) {
  if (value === null || value === undefined) return '—';
  return value < 1_000
    ? `${number(value)} ms`
    : `${new Intl.NumberFormat(currentLocale(), { maximumFractionDigits: 1 }).format(value / 1_000)} s`;
}

function percent(value: number | null | undefined) {
  return value === null || value === undefined
    ? '—'
    : `${new Intl.NumberFormat(currentLocale(), { maximumFractionDigits: 1 }).format(value)}%`;
}

function detailNumber(details: Record<string, string | number | boolean>, key: string) {
  const value = details[key];
  return typeof value === 'number' ? value : null;
}

function detailText(details: Record<string, string | number | boolean>, key: string) {
  const value = details[key];
  return typeof value === 'string' ? value : null;
}

function validMoney(value: string | null | undefined) {
  return (
    value !== null && value !== undefined && value.trim() !== '' && Number.isFinite(Number(value))
  );
}

function money(value: string | null | undefined) {
  if (!validMoney(value)) return '—';
  const numericValue = Number(value);
  if (numericValue > 0 && numericValue < 0.000001) return '<$0.000001';
  return new Intl.NumberFormat(currentLocale(), {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(numericValue);
}

function interactiveProviderLabel(provider: string | null, t: Translate) {
  if (provider?.toLowerCase() === 'claude') return 'Claude';
  if (provider?.toLowerCase() === 'codex') return 'Codex';
  return provider || t('Unknown provider');
}

function interactiveModelLabel(provider: string | null, model: string | null, t: Translate) {
  if (model) return model;
  return provider?.toLowerCase() === 'claude' ? t('Mixed/unknown') : t('Unknown model');
}

function ApiEquivalentCost({
  value,
  coverage,
  note,
}: {
  value: string | null | undefined;
  coverage?: PricingCoverage;
  note: string;
}) {
  const { t } = useI18n();
  const available = validMoney(value);
  return (
    <>
      <dd>{money(value)}</dd>
      <small className="metric-coverage">
        <span className={`measurement-badge ${available ? 'estimated' : 'unavailable'}`}>
          {available ? t('Estimated') : t('Unavailable')}
        </span>
        {coverage && (
          <span>
            {t('{priced} priced · {unavailable} unavailable', {
              priced: number(coverage.priced),
              unavailable: number(coverage.unavailable),
            })}
          </span>
        )}
      </small>
      <small>{note}</small>
    </>
  );
}

function labelForSource(source: string, t: Translate) {
  if (source === 'provider_reported') return t('Reported');
  if (source === 'local_estimate') return t('Estimated');
  return t('Unavailable');
}

function eventStatusLabel(status: string, t: Translate) {
  const labels: Record<string, ReturnType<Translate>> = {
    success: t('Success'),
    failed: t('Failed'),
    partial_failure: t('Partial failure'),
    degraded: t('Degraded'),
    blocked: t('Blocked'),
  };
  return labels[status] ?? status.replaceAll('_', ' ');
}

function CoverageBadges({ coverage }: { coverage: MeasurementCoverage }) {
  const { t } = useI18n();
  const sources = [
    { count: coverage.reported, label: t('Reported'), className: 'reported' },
    { count: coverage.estimated, label: t('Estimated'), className: 'estimated' },
    { count: coverage.unavailable, label: t('Unavailable'), className: 'unavailable' },
  ].filter((source) => source.count > 0);
  if (!sources.length) {
    return <span className="measurement-coverage-empty">{t('No data')}</span>;
  }
  return (
    <span className="measurement-badges">
      {sources.map((source) => (
        <span className={`measurement-badge ${source.className}`} key={source.label}>
          {source.label}
        </span>
      ))}
    </span>
  );
}

function SourcedTokens({
  reported,
  estimated,
}: {
  reported: number | null;
  estimated: number | null;
}) {
  const { t } = useI18n();
  if (reported === null && estimated === null) return '—';
  return (
    <span className="sourced-values">
      {reported !== null && (
        <span>
          {number(reported)}
          <span className="sourced-label">{t('Reported')}</span>
        </span>
      )}
      {estimated !== null && (
        <span>
          {number(estimated)}
          <span className="sourced-label">{t('Estimated')}</span>
        </span>
      )}
    </span>
  );
}

function sleepOperationLabel(operation: string, t: Translate) {
  if (operation === 'sleep.topic_segmentation') return t('Topic segmentation');
  if (operation === 'sleep.memory_action_planning') return t('Memory action planning');
  return operation;
}

function UsageTimeline({
  title,
  points,
  series,
}: {
  title: string;
  points: ObservabilitySummary['context']['timeline'];
  series: Array<{
    key: keyof ObservabilitySummary['context']['timeline'][number];
    label: string;
    color: string;
  }>;
}) {
  const { locale, t } = useI18n();
  const valueFor = (
    point: ObservabilitySummary['context']['timeline'][number],
    key: (typeof series)[number]['key'],
  ) => {
    const value = point[key];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const values = points.flatMap((point) =>
    series.map((item) => valueFor(point, item.key)).filter((item) => item !== null),
  );
  const maximum = Math.max(1, ...values);
  const path = (key: (typeof series)[number]['key']) => {
    let drawing = false;
    return points
      .map((point, index) => {
        const value = valueFor(point, key);
        if (value === null) {
          drawing = false;
          return '';
        }
        const x = points.length < 2 ? 50 : (index / (points.length - 1)) * 100;
        const y = 54 - (value / maximum) * 50;
        const command = drawing ? 'L' : 'M';
        drawing = true;
        return `${command}${x},${y}`;
      })
      .filter(Boolean)
      .join(' ');
  };
  return (
    <div className="usage-chart">
      <div className="chart-heading">
        <h3>{title}</h3>
        <div className="chart-legend">
          {series.map((item) => (
            <span key={item.key}>
              <i style={{ background: item.color }} />
              {item.label}
            </span>
          ))}
        </div>
      </div>
      {points.length && values.length ? (
        <>
          <svg viewBox="0 0 100 60" role="img" aria-label={t('{title} over time', { title })}>
            <line x1="0" x2="100" y1="54" y2="54" />
            {series.map((item) => (
              <path
                key={item.key}
                d={path(item.key)}
                fill="none"
                stroke={item.color}
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
          <details>
            <summary>{t('Show chart data')}</summary>
            <table>
              <thead>
                <tr>
                  <th>{t('Period')}</th>
                  {series.map((item) => (
                    <th key={item.key}>{item.label}</th>
                  ))}
                  <th>{t('Coverage')}</th>
                </tr>
              </thead>
              <tbody>
                {points.map((point) => (
                  <tr key={point.start}>
                    <td>{new Date(point.start).toLocaleString(locale)}</td>
                    {series.map((item) => (
                      <td key={item.key}>{number(valueFor(point, item.key))}</td>
                    ))}
                    <td>
                      <CoverageBadges coverage={point.coverage} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      ) : (
        <p className="muted">{t('No measurements in this period')}</p>
      )}
    </div>
  );
}

export function ObservabilityView({
  projectId,
  summary,
  events,
  range,
  onRange,
  filters,
  onFilters,
  loading,
  eventsLoading,
  error,
  nextCursor,
  onLoadMore,
  members = [],
}: {
  projectId: string;
  summary: ObservabilitySummary | null;
  events: ObservabilityEvent[];
  range: ObservabilityRange;
  onRange: (range: ObservabilityRange) => void;
  filters: { category: string; status: string; actor?: string };
  onFilters: (filters: { category: string; status: string; actor?: string }) => void;
  loading: boolean;
  eventsLoading: boolean;
  error: string;
  nextCursor: string | null;
  onLoadMore: () => Promise<void>;
  members?: TeamMember[];
}) {
  const { locale, t } = useI18n();
  const [inspectorEventId, setInspectorEventId] = useState<string | null>(null);
  const [inspectorDetail, setInspectorDetail] = useState<ContextEventDetail | null>(null);
  const [inspectorLoading, setInspectorLoading] = useState(false);
  const [inspectorError, setInspectorError] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const inspectorRequest = useRef<AbortController | null>(null);
  const inspectorRequestSequence = useRef(0);
  const inspectorTrigger = useRef<HTMLButtonElement | null>(null);
  const inspectorPanel = useRef<HTMLElement | null>(null);
  const inspectorCloseButton = useRef<HTMLButtonElement | null>(null);
  const previousProjectId = useRef(projectId);
  const ranges: Array<{ id: ObservabilityRange; label: string }> = [
    { id: '24h', label: '24h' },
    { id: '7d', label: '7d' },
    { id: '30d', label: '30d' },
    { id: 'all', label: t('All') },
  ];
  async function inspectContext(eventId: string, trigger: HTMLButtonElement) {
    inspectorRequest.current?.abort();
    const controller = new AbortController();
    const sequence = ++inspectorRequestSequence.current;
    inspectorRequest.current = controller;
    inspectorTrigger.current = trigger;
    setInspectorEventId(eventId);
    setInspectorDetail(null);
    setInspectorError('');
    setCopyStatus('');
    setInspectorLoading(true);
    try {
      const detail = await api.contextEventDetail(projectId, eventId, controller.signal);
      if (sequence === inspectorRequestSequence.current && !controller.signal.aborted) {
        setInspectorDetail(detail);
      }
    } catch (reason) {
      if (
        sequence === inspectorRequestSequence.current &&
        !controller.signal.aborted &&
        (!(reason instanceof Error) || reason.name !== 'AbortError')
      ) {
        setInspectorError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (sequence === inspectorRequestSequence.current) {
        inspectorRequest.current = null;
        setInspectorLoading(false);
      }
    }
  }

  const closeInspector = useCallback(() => {
    inspectorRequest.current?.abort();
    inspectorRequest.current = null;
    inspectorRequestSequence.current += 1;
    setInspectorEventId(null);
    setInspectorDetail(null);
    setInspectorError('');
    setInspectorLoading(false);
    setCopyStatus('');
    const trigger = inspectorTrigger.current;
    queueMicrotask(() => trigger?.focus());
  }, []);

  useEffect(() => {
    if (previousProjectId.current === projectId) return;
    previousProjectId.current = projectId;
    inspectorRequest.current?.abort();
    inspectorRequest.current = null;
    inspectorRequestSequence.current += 1;
    setInspectorEventId(null);
    setInspectorDetail(null);
    setInspectorError('');
    setInspectorLoading(false);
    setCopyStatus('');
    inspectorTrigger.current = null;
  }, [projectId]);

  useEffect(
    () => () => {
      inspectorRequest.current?.abort();
      inspectorRequestSequence.current += 1;
    },
    [],
  );

  useEffect(() => {
    if (!inspectorEventId) return undefined;
    inspectorCloseButton.current?.focus();
    const handleInspectorKeys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeInspector();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(
        inspectorPanel.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', handleInspectorKeys);
    return () => window.removeEventListener('keydown', handleInspectorKeys);
  }, [closeInspector, inspectorEventId]);

  useEffect(() => {
    if (!inspectorEventId) return undefined;
    const shell = document.querySelector<HTMLElement>('.shell');
    if (!shell) return undefined;
    const ariaHidden = shell.getAttribute('aria-hidden');
    const wasInert = shell.hasAttribute('inert');
    shell.setAttribute('aria-hidden', 'true');
    shell.setAttribute('inert', '');
    return () => {
      if (ariaHidden === null) shell.removeAttribute('aria-hidden');
      else shell.setAttribute('aria-hidden', ariaHidden);
      if (!wasInert) shell.removeAttribute('inert');
    };
  }, [inspectorEventId]);

  async function copyInspectorContent() {
    if (!inspectorDetail) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(inspectorDetail.content);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = inspectorDetail.content;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.append(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('copy command was rejected');
      }
      setCopyStatus(t('Copied'));
    } catch {
      setCopyStatus(t('Copy failed'));
    }
  }

  const componentEntries = Object.entries(summary?.context.automatic.component_bytes ?? {}).sort(
    ([left], [right]) => left.localeCompare(right),
  );
  const componentTotal = componentEntries.reduce((total, [, value]) => total + value, 0);
  const contextDelivery = summary?.context.automatic.delivery ?? {
    measured_injections: 0,
    snapshot_injections: 0,
    delta_injections: 0,
    fallback_injections: 0,
    unknown_injections: 0,
    reused_characters: null,
    reused_utf8_bytes: null,
    reused_estimated_tokens: null,
  };
  const selectedActor = filters.actor
    ? filters.actor === 'system'
      ? t('System / legacy')
      : members.find((member) => member.id === filters.actor)?.display_name || t('Selected user')
    : t('All users');
  const agentUsage = summary?.agent_usage;
  const agentProviderModels = agentUsage?.by_provider_model ?? [];
  const agentCostBreakdown = agentUsage?.equivalent_api_cost_breakdown ?? {
    uncached_input_usd: null,
    cached_input_usd: null,
    cache_write_input_usd: null,
    output_usd: null,
    priced: agentUsage?.pricing_coverage?.priced ?? 0,
    unavailable: agentUsage?.pricing_coverage?.unavailable ?? 0,
  };
  return (
    <div className="content observability-content">
      <section className="observability-intro">
        <div className="section-heading">
          <div>
            <h2>{t('Observability')}</h2>
            <p className="muted">
              {t('Usage is separated by source. Values are never combined into a false total.')}
            </p>
          </div>
          <fieldset className="segments">
            <legend className="sr-only">{t('Observability range')}</legend>
            {ranges.map((item) => (
              <button
                key={item.id}
                type="button"
                className={range === item.id ? 'selected' : ''}
                aria-pressed={range === item.id}
                onClick={() => onRange(item.id)}
              >
                {item.label}
              </button>
            ))}
          </fieldset>
        </div>
        {summary?.coverage.collection_started_at ? (
          <p className="muted coverage-note">
            {t(
              'Metrics available since {date} · {reported} reported · {estimated} estimated · {unavailable} unavailable',
              {
                date: formatDate(summary.coverage.collection_started_at, locale),
                reported: summary.coverage.reported,
                estimated: summary.coverage.estimated,
                unavailable: summary.coverage.unavailable,
              },
            )}
          </p>
        ) : (
          <p className="muted coverage-note">
            {t('Metrics start when this version is installed. No historical usage is inferred.')}
          </p>
        )}
      </section>
      {loading && !summary && (
        <section>
          <p className="muted">{t('Loading observability…')}</p>
        </section>
      )}
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {summary && (
        <>
          <section>
            <div className="section-heading">
              <div>
                <h2>{t('dDuo context emitted')}</h2>
                <p className="muted">
                  {t(
                    'Exact plugin output, separated between automatic founder context and requested MCP results. Token equivalents are deterministic estimates, not model-reported consumption.',
                  )}
                </p>
              </div>
              <CoverageBadges coverage={summary.context.coverage} />
            </div>
            <dl className="observability-metrics">
              <div>
                <dt>{t('Automatic injections')}</dt>
                <dd>{number(summary.context.automatic.injections)}</dd>
                <small className="metric-coverage">
                  <span>
                    {summary.context.automatic.estimated_tokens === null
                      ? '—'
                      : t('{count} token estimate', {
                          count: number(summary.context.automatic.estimated_tokens),
                        })}
                  </span>
                  <CoverageBadges coverage={summary.context.automatic.coverage} />
                </small>
                {(summary.context.automatic.estimated_tokens_p50 != null ||
                  summary.context.automatic.estimated_tokens_p95 != null) && (
                  <small>
                    {t('Per injection: p50 {p50} · p95 {p95} estimated tokens', {
                      p50: number(summary.context.automatic.estimated_tokens_p50),
                      p95: number(summary.context.automatic.estimated_tokens_p95),
                    })}
                  </small>
                )}
              </div>
              <div>
                <dt>{t('Requested MCP results')}</dt>
                <dd>{number(summary.context.requested.results)}</dd>
                <small className="metric-coverage">
                  <span>
                    {summary.context.requested.estimated_tokens === null
                      ? '—'
                      : t('{count} token estimate', {
                          count: number(summary.context.requested.estimated_tokens),
                        })}
                  </span>
                  <CoverageBadges coverage={summary.context.requested.coverage} />
                </small>
              </div>
              <div>
                <dt>{t('Automatic size')}</dt>
                <dd>{bytes(summary.context.automatic.utf8_bytes)}</dd>
                <small>
                  {t('{count} characters', { count: number(summary.context.automatic.characters) })}
                </small>
              </div>
              <div>
                <dt>{t('MCP result size')}</dt>
                <dd>{bytes(summary.context.requested.utf8_bytes)}</dd>
                <small>
                  {t('{count} characters', { count: number(summary.context.requested.characters) })}
                </small>
              </div>
            </dl>
            <dl className="observability-metrics context-budget-metrics">
              <div>
                <dt>{t('Founder brief budget')}</dt>
                <dd>{number(summary.context.automatic.budget.budget_limit_characters)}</dd>
                <small>
                  {t(
                    'characters per automatic hook · {measured} measured · {inline} inline expected',
                    {
                      measured: number(summary.context.automatic.budget.measured_injections),
                      inline: number(summary.context.automatic.budget.inline_expected_injections),
                    },
                  )}
                </small>
              </div>
              <div>
                <dt>{t('Budget utilization')}</dt>
                <dd>{percent(summary.context.automatic.budget.budget_utilization_p50_percent)}</dd>
                <small>
                  p50 · p95{' '}
                  {percent(summary.context.automatic.budget.budget_utilization_p95_percent)}
                </small>
              </div>
              <div>
                <dt>{t('Selected knowledge')}</dt>
                <dd>{number(summary.context.automatic.budget.included_items)}</dd>
                <small>
                  {t('{partial} partial · {omitted} omitted', {
                    partial: number(summary.context.automatic.budget.partial_items),
                    omitted: number(summary.context.automatic.budget.omitted_items),
                  })}
                </small>
              </div>
              <div>
                <dt>{t('Context avoided')}</dt>
                <dd>
                  {summary.context.automatic.budget.avoided_estimated_tokens === null
                    ? '—'
                    : t('{count} tokens', {
                        count: number(summary.context.automatic.budget.avoided_estimated_tokens),
                      })}
                </dd>
                <small>
                  {t('{budgeted} budgeted · {fallback} fallback', {
                    budgeted: number(summary.context.automatic.budget.budgeted_injections),
                    fallback: number(summary.context.automatic.budget.fallback_injections),
                  })}
                </small>
              </div>
              <div>
                <dt>{t('Stable context reused')}</dt>
                <dd>
                  {contextDelivery.reused_estimated_tokens === null
                    ? '—'
                    : t('{count} tokens', {
                        count: number(contextDelivery.reused_estimated_tokens),
                      })}
                </dd>
                <small>
                  {t('Delta {delta} · Snapshot {snapshot} · Fallback {fallback}', {
                    delta: number(contextDelivery.delta_injections),
                    snapshot: number(contextDelivery.snapshot_injections),
                    fallback: number(contextDelivery.fallback_injections),
                  })}
                </small>
                {contextDelivery.unknown_injections > 0 && (
                  <small>
                    {t('Legacy or unknown {count}', {
                      count: number(contextDelivery.unknown_injections),
                    })}
                  </small>
                )}
                <small>{t('Already present in the live session · not provider cache')}</small>
              </div>
            </dl>
            {componentEntries.length > 0 && (
              <div className="context-component-breakdown">
                <h3>{t('Automatic context by component')}</h3>
                <p className="muted">
                  {t('Exact emitted bytes; token equivalents remain deterministic estimates.')}
                </p>
                <div>
                  {componentEntries.map(([name, value]) => (
                    <div key={name}>
                      <span>{name.replaceAll('_', ' ')}</span>
                      <span className="component-bar" aria-hidden="true">
                        <i
                          style={{
                            width: `${componentTotal ? Math.max(2, (value / componentTotal) * 100) : 0}%`,
                          }}
                        />
                      </span>
                      <strong>{bytes(value)}</strong>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <UsageTimeline
              title={t('Emitted context')}
              points={summary.context.timeline}
              series={[
                { key: 'automatic_estimated_tokens', label: t('Automatic'), color: '#0071e3' },
                { key: 'requested_estimated_tokens', label: t('MCP results'), color: '#af52de' },
              ]}
            />
          </section>
          <section>
            <div className="section-heading">
              <div>
                <h2>{t('Interactive agent — API equivalent')}</h2>
                <p className="muted">
                  {t(
                    'Codex and Claude samples appear when their local adapters report structured per-turn usage. These are API-equivalent estimates, not subscription charges.',
                  )}
                </p>
                <p className="muted">
                  {t(
                    'Token counters keep each provider and model separate. Provider input, cache read and cache write use provider-specific semantics and are never added into a cross-provider total.',
                  )}
                </p>
                <p className="muted">
                  {t(
                    'Cache counters tell us how many tokens were reused, but providers do not identify the exact text spans served from cache.',
                  )}
                </p>
                <p className="muted">
                  {t(
                    'Claude cost is an estimate computed by Claude Code. It can reflect configured model pricing and may differ from both the current public list price and an actual bill.',
                  )}
                </p>
                <p className="metric-scope">{t('User: {user}', { user: selectedActor })}</p>
              </div>
              {agentUsage ? (
                <CoverageBadges coverage={agentUsage.coverage} />
              ) : (
                <span className="measurement-badge unavailable">{t('Unavailable')}</span>
              )}
            </div>
            <dl className="observability-metrics">
              <div>
                <dt>{t('Observed samples')}</dt>
                <dd>{agentUsage ? number(agentUsage.requests) : '—'}</dd>
                <small>
                  {agentUsage
                    ? t('{success} successful · {failed} failed', {
                        success: number(agentUsage.successes),
                        failed: number(agentUsage.failures),
                      })
                    : t('Interactive usage is unavailable')}
                </small>
              </div>
              <div>
                <dt>{t('Provider/model rows')}</dt>
                <dd>{agentUsage ? number(agentProviderModels.length) : '—'}</dd>
                <small>{t('Token counters are shown below without a combined total')}</small>
              </div>
              <div>
                <dt>{t('Duration')}</dt>
                <dd>{agentUsage ? duration(agentUsage.duration_p50_ms) : '—'}</dd>
                <small>
                  {t('p50 · p95 {p95}', {
                    p95: agentUsage ? duration(agentUsage.duration_p95_ms) : '—',
                  })}
                </small>
              </div>
              <div>
                <dt>{t('API equivalent estimate')}</dt>
                <ApiEquivalentCost
                  value={agentUsage?.equivalent_api_cost_usd}
                  coverage={agentUsage?.pricing_coverage}
                  note={t('Catalog-derived or client-reported estimate · not actual spend')}
                />
              </div>
            </dl>
            {agentUsage && (
              <>
                <dl className="equivalent-cost-strip provider-cost-summary">
                  <div>
                    <dt>{t('Uncached input cost')}</dt>
                    <dd>{money(agentCostBreakdown.uncached_input_usd)}</dd>
                  </div>
                  <div>
                    <dt>{t('Cached input cost')}</dt>
                    <dd>{money(agentCostBreakdown.cached_input_usd)}</dd>
                  </div>
                  <div>
                    <dt>{t('Cache write cost')}</dt>
                    <dd>{money(agentCostBreakdown.cache_write_input_usd)}</dd>
                  </div>
                  <div>
                    <dt>{t('Output cost')}</dt>
                    <dd>{money(agentCostBreakdown.output_usd)}</dd>
                  </div>
                </dl>
                <p className="metric-scope">
                  {t('Cost breakdown coverage: {priced} priced · {unavailable} unavailable', {
                    priced: number(agentCostBreakdown.priced),
                    unavailable: number(agentCostBreakdown.unavailable),
                  })}
                </p>
              </>
            )}
            <fieldset className="provider-cost-breakdown">
              <legend className="sr-only">{t('Interactive usage by provider and model')}</legend>
              {agentProviderModels.length ? (
                agentProviderModels.map((item) => (
                  <div key={`${item.provider || 'unknown'}:${item.model || 'unknown'}`}>
                    <strong>{interactiveProviderLabel(item.provider, t)}</strong>
                    <span>{interactiveModelLabel(item.provider, item.model, t)}</span>
                    <small>{t('{count} samples observed', { count: number(item.requests) })}</small>
                    <dl className="provider-token-breakdown">
                      <div>
                        <dt>{t('Provider input')}</dt>
                        <dd>
                          <SourcedTokens
                            reported={item.input_tokens_reported}
                            estimated={item.input_tokens_estimated}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>{t('Uncached input')}</dt>
                        <dd>
                          <SourcedTokens
                            reported={item.uncached_input_tokens_reported}
                            estimated={null}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>{t('Cache read')}</dt>
                        <dd>
                          <SourcedTokens
                            reported={item.cached_input_tokens_reported}
                            estimated={null}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>{t('Cache hit')}</dt>
                        <dd>{percent(item.cache_hit_percent)}</dd>
                      </div>
                      <div>
                        <dt>{t('Cache write')}</dt>
                        <dd>
                          <SourcedTokens
                            reported={item.cache_write_input_tokens_reported}
                            estimated={null}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>{t('Output')}</dt>
                        <dd>
                          <SourcedTokens
                            reported={item.output_tokens_reported}
                            estimated={item.output_tokens_estimated}
                          />
                        </dd>
                      </div>
                    </dl>
                    {item.provider?.toLowerCase() === 'claude' &&
                      !item.model &&
                      validMoney(item.equivalent_api_cost_usd) && (
                        <small>
                          {t(
                            'Claude Code computed this estimate; it may reflect configured pricing and span models.',
                          )}
                        </small>
                      )}
                    <span className="provider-equivalent-cost">
                      {t('{cost} API equivalent', { cost: money(item.equivalent_api_cost_usd) })}
                    </span>
                    <span
                      className={`measurement-badge ${validMoney(item.equivalent_api_cost_usd) ? 'estimated' : 'unavailable'}`}
                    >
                      {validMoney(item.equivalent_api_cost_usd) ? t('Estimated') : t('Unavailable')}
                    </span>
                    <small>
                      {t('{priced} priced · {unavailable} unavailable', {
                        priced: number(item.pricing_coverage.priced),
                        unavailable: number(item.pricing_coverage.unavailable),
                      })}
                    </small>
                  </div>
                ))
              ) : (
                <p className="muted">
                  <span className="measurement-badge unavailable">{t('Unavailable')}</span>{' '}
                  {t('No interactive provider/model usage in this period.')}
                </p>
              )}
            </fieldset>
          </section>
          <section>
            <div className="section-heading">
              <div>
                <h2>{t('Sleep')}</h2>
                <p className="muted">
                  {t(
                    'Topic segmentation and memory planning run through the authenticated CLI bridge.',
                  )}
                </p>
              </div>
              <CoverageBadges coverage={summary.sleep.coverage} />
            </div>
            <dl className="observability-metrics">
              <div>
                <dt>{t('Passes')}</dt>
                <dd>{number(summary.sleep.requests)}</dd>
                <small>
                  {t('{success} successful · {failed} failed', {
                    success: number(summary.sleep.successes),
                    failed: number(summary.sleep.failures),
                  })}
                </small>
              </div>
              <div>
                <dt>{t('Input tokens')}</dt>
                <dd>
                  <SourcedTokens
                    reported={summary.sleep.input_tokens_reported}
                    estimated={summary.sleep.input_tokens_estimated}
                  />
                </dd>
              </div>
              <div>
                <dt>{t('Output tokens')}</dt>
                <dd>
                  <SourcedTokens
                    reported={summary.sleep.output_tokens_reported}
                    estimated={summary.sleep.output_tokens_estimated}
                  />
                </dd>
              </div>
              <div>
                <dt>{t('Duration')}</dt>
                <dd>{duration(summary.sleep.duration_p50_ms)}</dd>
                <small>p95 {duration(summary.sleep.duration_p95_ms)}</small>
              </div>
            </dl>
            <dl className="equivalent-cost-strip">
              <div>
                <dt>{t('API equivalent estimate')}</dt>
                <ApiEquivalentCost
                  value={summary.sleep.equivalent_api_cost_usd}
                  coverage={summary.sleep.pricing_coverage}
                  note={t(
                    'Catalog-derived or Claude Code client estimate · subscription access is not billed here',
                  )}
                />
              </div>
            </dl>
            <fieldset className="sleep-pass-breakdown">
              <legend className="sr-only">{t('Sleep pass breakdown')}</legend>
              {summary.sleep.by_operation.length ? (
                summary.sleep.by_operation.map((operation) => (
                  <div key={operation.operation}>
                    <strong>{sleepOperationLabel(operation.operation, t)}</strong>
                    <span>
                      {t('{passes} passes · {success} successful · {failed} failed', {
                        passes: number(operation.requests),
                        success: number(operation.successes),
                        failed: number(operation.failures),
                      })}
                    </span>
                    <small>
                      <SourcedTokens
                        reported={operation.input_tokens_reported}
                        estimated={operation.input_tokens_estimated}
                      />{' '}
                      {t('Input tokens').toLowerCase()}
                    </small>
                    <CoverageBadges coverage={operation.coverage} />
                  </div>
                ))
              ) : (
                <p className="muted">{t('No sleep passes in this period')}</p>
              )}
            </fieldset>
            <UsageTimeline
              title={t('Sleep input tokens')}
              points={summary.sleep.timeline}
              series={[
                { key: 'input_tokens_reported', label: t('Reported'), color: '#34c759' },
                { key: 'input_tokens_estimated', label: t('Estimated'), color: '#ff9f0a' },
              ]}
            />
          </section>
          <section>
            <div className="section-heading">
              <div>
                <h2>{t('Embedding API')}</h2>
                <p className="muted">
                  {t('Provider usage and saved list-price costs are shown when available.')}
                </p>
              </div>
              <CoverageBadges coverage={summary.embeddings.coverage} />
            </div>
            <dl className="observability-metrics">
              <div>
                <dt>{t('Requests')}</dt>
                <dd>{number(summary.embeddings.requests)}</dd>
                <small>
                  {t('{success} successful · {failed} failed', {
                    success: number(summary.embeddings.successes),
                    failed: number(summary.embeddings.failures),
                  })}
                </small>
              </div>
              <div>
                <dt>{t('Input tokens')}</dt>
                <dd>
                  <SourcedTokens
                    reported={summary.embeddings.input_tokens_reported}
                    estimated={summary.embeddings.input_tokens_estimated}
                  />
                </dd>
              </div>
              <div>
                <dt>{t('Estimated attributable cost')}</dt>
                <dd>{money(summary.embeddings.cost_usd)}</dd>
                <small>{t('Embedding API only')}</small>
              </div>
              <div>
                <dt>{t('Duration')}</dt>
                <dd>{duration(summary.embeddings.duration_p50_ms)}</dd>
                <small>p95 {duration(summary.embeddings.duration_p95_ms)}</small>
              </div>
            </dl>
            <UsageTimeline
              title={t('Embedding input tokens')}
              points={summary.embeddings.timeline}
              series={[{ key: 'input_tokens_reported', label: t('Reported'), color: '#0071e3' }]}
            />
          </section>
          <section>
            <div className="section-heading">
              <div>
                <h2>{t('Reliability')}</h2>
                <p className="muted">
                  {t('Operational outcomes and end-to-end retrieval latency.')}
                </p>
              </div>
              <CoverageBadges coverage={summary.reliability.coverage} />
            </div>
            <dl className="observability-metrics">
              <div>
                <dt>{t('Observed operations')}</dt>
                <dd>{number(summary.reliability.requests)}</dd>
                <small>
                  {t('{count} failed or degraded', { count: number(summary.reliability.failures) })}
                </small>
              </div>
              <div>
                <dt>{t('Retrieval runs')}</dt>
                <dd>{number(summary.reliability.retrieval.requests)}</dd>
                <small className="metric-coverage">
                  <span>
                    {t('{count} degraded', {
                      count: number(summary.reliability.retrieval.failures),
                    })}
                  </span>
                  <CoverageBadges coverage={summary.reliability.retrieval.coverage} />
                </small>
              </div>
              <div>
                <dt>{t('Retrieval p50')}</dt>
                <dd>{duration(summary.reliability.retrieval.duration_p50_ms)}</dd>
                <small>p95 {duration(summary.reliability.retrieval.duration_p95_ms)}</small>
              </div>
              <div>
                <dt>{t('Measurement coverage')}</dt>
                <dd>{number(summary.coverage.events)}</dd>
                <small>{t('Events in selected range')}</small>
              </div>
            </dl>
          </section>
        </>
      )}
      <section>
        <div className="section-heading observability-events-heading">
          <div>
            <h2>{t('Recent operations')}</h2>
            <p className="muted">
              {t(
                'Metrics stay compact; exact dDuo context is loaded only when you inspect an event.',
              )}
            </p>
          </div>
          <div className="event-filters">
            <label>
              {t('User')}
              <select
                aria-label={t('Operation user')}
                value={filters.actor ?? ''}
                onChange={(event) => onFilters({ ...filters, actor: event.target.value })}
              >
                <option value="">{t('All users')}</option>
                {members.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.display_name}
                  </option>
                ))}
                <option value="system">{t('System / legacy')}</option>
              </select>
            </label>
            <label>
              {t('Category')}
              <select
                aria-label={t('Operation category')}
                value={filters.category}
                onChange={(event) => onFilters({ ...filters, category: event.target.value })}
              >
                <option value="">{t('All categories')}</option>
                <option value="context">{t('Context')}</option>
                <option value="agent_usage">{t('Interactive agent')}</option>
                <option value="embedding">{t('Embedding')}</option>
                <option value="sleep_model">{t('Sleep')}</option>
                <option value="retrieval">{t('Retrieval')}</option>
              </select>
            </label>
            <label>
              {t('Status')}
              <select
                aria-label={t('Operation status')}
                value={filters.status}
                onChange={(event) => onFilters({ ...filters, status: event.target.value })}
              >
                <option value="">{t('All statuses')}</option>
                <option value="success">{t('Success')}</option>
                <option value="failed">{t('Failed')}</option>
                <option value="partial_failure">{t('Partial failure')}</option>
                <option value="degraded">{t('Degraded')}</option>
                <option value="blocked">{t('Blocked')}</option>
              </select>
            </label>
          </div>
        </div>
        <div className="observability-event-table">
          <table>
            <thead>
              <tr>
                <th>{t('When')}</th>
                <th>{t('User')}</th>
                <th>{t('Operation')}</th>
                <th>{t('Status')}</th>
                <th>{t('Measure')}</th>
                <th>{t('Duration')}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td>{formatDate(event.occurred_at, locale)}</td>
                  <td>
                    <strong>{event.actor_member?.display_name || t('System / legacy')}</strong>
                    {event.actor_member && <small>{teamRole(event.actor_member, t)}</small>}
                  </td>
                  <td>
                    <strong>{event.operation}</strong>
                    <small>
                      {event.provider || 'dDuo'}
                      {event.model ? ` · ${event.model}` : ''}
                    </small>
                    {event.category === 'context' && event.has_content && (
                      <button
                        type="button"
                        className="context-inspect-button"
                        onClick={(clickEvent) =>
                          void inspectContext(event.id, clickEvent.currentTarget)
                        }
                      >
                        {t('Inspect emitted context')}
                      </button>
                    )}
                    {event.category === 'context' && event.has_content !== true && (
                      <small>{t('Content unavailable for this historical event')}</small>
                    )}
                  </td>
                  <td>
                    <span className={`event-status ${event.status}`}>
                      {eventStatusLabel(event.status, t)}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`measurement-badge ${event.measurement_source === 'provider_reported' ? 'reported' : event.measurement_source === 'local_estimate' ? 'estimated' : 'unavailable'}`}
                    >
                      {labelForSource(event.measurement_source, t)}
                    </span>
                    <small>
                      {event.input_tokens !== null && event.input_tokens !== undefined
                        ? t('{count} input tokens', { count: number(event.input_tokens) })
                        : event.utf8_bytes !== null && event.utf8_bytes !== undefined
                          ? bytes(event.utf8_bytes)
                          : '—'}
                    </small>
                    {event.cost_usd !== null && event.cost_usd !== undefined && (
                      <small>
                        {money(event.cost_usd)}{' '}
                        {event.category === 'embedding'
                          ? t('attributable cost')
                          : event.category === 'agent_usage' || event.category === 'sleep_model'
                            ? t('API equivalent')
                            : t('cost')}
                      </small>
                    )}
                  </td>
                  <td>{duration(event.duration_ms)}</td>
                </tr>
              ))}
              {!events.length && !eventsLoading && !error && (
                <tr>
                  <td colSpan={6} className="muted">
                    {t('No operations in this period')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {eventsLoading && <p className="muted">{t('Loading operations…')}</p>}
        {nextCursor && (
          <button
            className="secondary"
            type="button"
            disabled={eventsLoading}
            onClick={() => void onLoadMore()}
          >
            {t('Load more')}
          </button>
        )}
      </section>
      {inspectorEventId &&
        createPortal(
          <div className="context-inspector-backdrop" role="presentation">
            <aside
              ref={inspectorPanel}
              className="context-inspector"
              role="dialog"
              aria-modal="true"
              aria-labelledby="context-inspector-title"
              aria-describedby="context-inspector-description"
            >
              <header>
                <div>
                  <h2 id="context-inspector-title">{t('Context emitted by dDuo')}</h2>
                  <p id="context-inspector-description">
                    {t(
                      'Exact plugin delivery with a deterministic token estimate — not provider-reported consumption and not the complete model prompt.',
                    )}
                  </p>
                </div>
                <button
                  ref={inspectorCloseButton}
                  type="button"
                  className="icon"
                  aria-label={t('Close context inspector')}
                  onClick={() => closeInspector()}
                >
                  <X />
                </button>
              </header>
              <div className="context-inspector-body">
                {inspectorLoading && (
                  <p className="muted" role="status" aria-live="polite">
                    {t('Loading emitted context…')}
                  </p>
                )}
                {inspectorError && (
                  <div className="error" role="alert">
                    {inspectorError}
                  </div>
                )}
                {inspectorDetail && (
                  <>
                    <dl className="context-inspector-metrics">
                      <div>
                        <dt>{t('Captured')}</dt>
                        <dd>{formatDate(inspectorDetail.captured_at, locale)}</dd>
                      </div>
                      <div>
                        <dt>{t('Estimated tokens')}</dt>
                        <dd>
                          {t('{count} tokens', {
                            count: number(inspectorDetail.event.input_tokens),
                          })}
                        </dd>
                      </div>
                      <div>
                        <dt>{t('Size')}</dt>
                        <dd>{bytes(inspectorDetail.event.utf8_bytes)}</dd>
                      </div>
                      <div>
                        <dt>{t('Client')}</dt>
                        <dd>{inspectorDetail.event.provider || 'dDuo'}</dd>
                      </div>
                    </dl>
                    {detailNumber(inspectorDetail.event.details, 'budget_limit_characters') !==
                      null && (
                      <section className="context-inspector-selection">
                        <h3>{t('Founder brief selection')}</h3>
                        <p className="muted">
                          {t(
                            'dDuo selected complete, relevant units before emitting this hook payload. “Inline expected” describes the configured delivery path; it is not proof of provider-side consumption.',
                          )}
                        </p>
                        <dl>
                          <div>
                            <dt>{t('Outcome')}</dt>
                            <dd>
                              {detailText(
                                inspectorDetail.event.details,
                                'budget_outcome',
                              )?.replaceAll('_', ' ') || '—'}
                            </dd>
                          </div>
                          <div>
                            <dt>{t('Client-safe units')}</dt>
                            <dd>
                              {number(
                                detailNumber(
                                  inspectorDetail.event.details,
                                  'client_character_units',
                                ),
                              )}{' '}
                              /{' '}
                              {number(
                                detailNumber(
                                  inspectorDetail.event.details,
                                  'budget_limit_characters',
                                ),
                              )}
                            </dd>
                          </div>
                          <div>
                            <dt>{t('Candidate context')}</dt>
                            <dd>
                              {number(
                                detailNumber(
                                  inspectorDetail.event.details,
                                  'candidate_estimated_tokens',
                                ),
                              )}{' '}
                              {t('Estimated tokens').toLowerCase()}
                            </dd>
                          </div>
                          <div>
                            <dt>{t('Selection')}</dt>
                            <dd>
                              {t('{included} included · {partial} partial · {omitted} omitted', {
                                included: number(
                                  detailNumber(inspectorDetail.event.details, 'included_items'),
                                ),
                                partial: number(
                                  detailNumber(inspectorDetail.event.details, 'partial_items'),
                                ),
                                omitted: number(
                                  detailNumber(inspectorDetail.event.details, 'omitted_items'),
                                ),
                              })}
                            </dd>
                          </div>
                        </dl>
                      </section>
                    )}
                    <section className="context-inspector-correlation">
                      <h3>{t('Delivery identity')}</h3>
                      <dl>
                        <div>
                          <dt>{t('Operation')}</dt>
                          <dd>{inspectorDetail.event.operation}</dd>
                        </div>
                        <div>
                          <dt>{t('Scope')}</dt>
                          <dd>{inspectorDetail.event.scope || '—'}</dd>
                        </div>
                        <div>
                          <dt>{t('Event')}</dt>
                          <dd>{inspectorDetail.event.id}</dd>
                        </div>
                        <div>
                          <dt>{t('Session')}</dt>
                          <dd>{inspectorDetail.event.session_id || '—'}</dd>
                        </div>
                        <div>
                          <dt>{t('Turn')}</dt>
                          <dd>{inspectorDetail.event.turn_id || '—'}</dd>
                        </div>
                        <div>
                          <dt>{t('Retrieval')}</dt>
                          <dd>{inspectorDetail.event.retrieval_run_id || '—'}</dd>
                        </div>
                        <div>
                          <dt>{t('MCP tool')}</dt>
                          <dd>{inspectorDetail.tool_name || '—'}</dd>
                        </div>
                        <div>
                          <dt>{t('Estimator')}</dt>
                          <dd>{inspectorDetail.estimator_version}</dd>
                        </div>
                      </dl>
                    </section>
                    {inspectorDetail.turn && (
                      <section className="context-inspector-orientation">
                        <h3>{t('Turn orientation — excluded from dDuo usage')}</h3>
                        <div>
                          <strong>{t('User request')}</strong>
                          <MarkdownContent>{inspectorDetail.turn.user_prompt}</MarkdownContent>
                        </div>
                        <div>
                          <strong>{t('Assistant response')}</strong>
                          {inspectorDetail.turn.assistant_response ? (
                            <MarkdownContent>
                              {inspectorDetail.turn.assistant_response}
                            </MarkdownContent>
                          ) : (
                            <p>—</p>
                          )}
                        </div>
                      </section>
                    )}
                    {inspectorDetail.components.length > 0 && (
                      <section>
                        <h3>{t('Emitted components')}</h3>
                        <div className="context-inspector-components">
                          {inspectorDetail.components.map((component) => (
                            <div key={component.name}>
                              <strong>{component.name.replaceAll('_', ' ')}</strong>
                              <span>
                                {t('{bytes} · {tokens} estimated tokens', {
                                  bytes: bytes(component.utf8_bytes),
                                  tokens: number(component.estimated_tokens),
                                })}
                              </span>
                              {component.item_count !== null &&
                                component.item_count !== undefined && (
                                  <small>
                                    {t('{count} emitted', { count: number(component.item_count) })}
                                    {component.candidate_item_count !== null &&
                                    component.candidate_item_count !== undefined
                                      ? ` ${t('of {count} candidates', { count: number(component.candidate_item_count) })}`
                                      : ''}
                                    {component.partial_item_count
                                      ? ` · ${t('{count} partial', { count: number(component.partial_item_count) })}`
                                      : ''}
                                    {component.omitted_item_count
                                      ? ` · ${t('{count} omitted', { count: number(component.omitted_item_count) })}`
                                      : ''}
                                  </small>
                                )}
                              {component.references?.length ? (
                                <small>
                                  {t('References: {references}', {
                                    references: component.references.join(', '),
                                  })}
                                </small>
                              ) : null}
                              {component.omitted_references?.length ? (
                                <small>
                                  {t('Omitted references: {references}', {
                                    references: component.omitted_references.join(', '),
                                  })}
                                </small>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      </section>
                    )}
                    {inspectorDetail.retrieval?.memories.length ? (
                      <section>
                        <h3>{t('Memories emitted in this turn')}</h3>
                        <div className="context-inspector-memories">
                          {inspectorDetail.retrieval.memories.map((memory) => (
                            <article key={memory.id}>
                              <div>
                                <span>{memory.node_type.replace('_', ' ')}</span>
                                <small>
                                  {t('Revision {revision}{score}', {
                                    revision: memory.revision,
                                    score:
                                      memory.score !== null && memory.score !== undefined
                                        ? t(' · score {score}', { score: memory.score.toFixed(3) })
                                        : '',
                                  })}
                                </small>
                              </div>
                              <MarkdownContent>{memory.text}</MarkdownContent>
                            </article>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    <section>
                      <div className="context-raw-heading">
                        <div>
                          <h3>{t('Exact emitted context')}</h3>
                          <small>
                            SHA-256 {inspectorDetail.content_sha256} · renderer{' '}
                            {inspectorDetail.render_version} · producer{' '}
                            {inspectorDetail.producer_version}
                          </small>
                        </div>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => void copyInspectorContent()}
                        >
                          <Copy /> {copyStatus === t('Copied') ? t('Copied') : t('Copy')}
                        </button>
                      </div>
                      <span className="sr-only" role="status" aria-live="polite">
                        {copyStatus}
                      </span>
                      <pre>{inspectorDetail.content}</pre>
                    </section>
                  </>
                )}
              </div>
            </aside>
          </div>,
          document.body,
        )}
    </div>
  );
}
