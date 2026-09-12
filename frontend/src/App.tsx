import { RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from './api';
import {
  ActivityView,
  BackupView,
  ConnectionForm,
  DisconnectButton,
  MemoryView,
  ObservabilityView,
  ProjectView,
  SetupView,
  Sidebar,
  type Tab,
  TeamView,
} from './components';
import { I18nProvider, useI18n } from './i18n';
import { ProjectName } from './ProjectName';
import { SprintWork } from './SprintWork';
import { useObservability } from './useObservability';
import { useProjectData } from './useProjectData';
import { useTeam } from './useTeam';

const TABS: Tab[] = [
  'project',
  'memory',
  'tasks',
  'team',
  'observability',
  'activity',
  'backup',
  'setup',
];

function initialTab(): Tab {
  const requested = new URLSearchParams(window.location.search).get('tab');
  return TABS.includes(requested as Tab) ? (requested as Tab) : 'tasks';
}

function initialBrowserAccess() {
  const query = new URLSearchParams(window.location.search);
  const projects = query.getAll('project');
  const links = query.getAll('access_token');
  const tickets = query.getAll('ticket');
  const values = [...links, ...tickets];
  const token = values[0] ?? '';
  const projectId = projects[0]?.trim() ?? '';
  const invalid =
    projects.length > 1 ||
    (projects.length > 0 &&
      (!projectId ||
        projects[0] !== projectId ||
        projectId.length > 160 ||
        /\s/.test(projectId))) ||
    (values.length > 0 &&
      (values.length !== 1 ||
        projects.length !== 1 ||
        !projectId ||
        !token ||
        token !== token.trim() ||
        [...token].some(
          (character) => character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127,
        ) ||
        (links.length > 0 && !/^dduo_link_[A-Za-z0-9_-]{43}$/.test(token))));
  return { token, projectId, legacy: tickets.length > 0, invalid };
}

type BrowserAccessError = '' | 'invalid' | 'expired' | 'legacy_expired' | 'unreachable';

function DashboardApp() {
  const { language, setLanguage, t } = useI18n();
  const [tab, setTab] = useState<Tab>(initialTab);
  const [browserAccess] = useState(initialBrowserAccess);
  const [browserAuthReady, setBrowserAuthReady] = useState(
    !browserAccess.token && !browserAccess.invalid,
  );
  const [browserAuthLoading, setBrowserAuthLoading] = useState(
    Boolean(browserAccess.token) && !browserAccess.invalid,
  );
  const [browserAuthError, setBrowserAuthError] = useState<BrowserAccessError>(
    browserAccess.invalid ? 'invalid' : '',
  );
  const browserExchangeStarted = useRef(false);
  const browserExchangeAttempt = useRef(0);
  const browserExchangeInFlight = useRef(false);
  const [online, setOnline] = useState(false);
  const [openingSetup, setOpeningSetup] = useState(false);
  const [setupError, setSetupError] = useState('');
  const project = useProjectData(browserAuthReady);
  const observability = useObservability(project.data?.project.id ?? '', tab === 'observability');
  const team = useTeam(
    project.data?.project.id ?? '',
    tab === 'team' || tab === 'observability',
    tab === 'team',
    project.data?.team ?? null,
    language,
  );

  const exchangeBrowserTicket = useCallback(async () => {
    if (!browserAccess.token || browserAccess.invalid || browserExchangeInFlight.current) return;
    browserExchangeInFlight.current = true;
    const attempt = ++browserExchangeAttempt.current;
    setBrowserAuthLoading(true);
    setBrowserAuthError('');
    try {
      await api.exchangeBrowserSession(browserAccess.projectId, browserAccess.token);
      if (attempt === browserExchangeAttempt.current) setBrowserAuthReady(true);
    } catch (reason) {
      const rejected = reason instanceof ApiError && reason.status >= 400 && reason.status < 500;
      // An old link must not invalidate an already-authorized cookie for this
      // exact project. This is one read-only check, never a repeated exchange.
      if (rejected) {
        try {
          await api.team(browserAccess.projectId);
          if (attempt === browserExchangeAttempt.current) setBrowserAuthReady(true);
          return;
        } catch (fallbackReason) {
          if (!(fallbackReason instanceof ApiError) || fallbackReason.status >= 500) {
            if (attempt === browserExchangeAttempt.current) {
              setBrowserAuthError('unreachable');
              setBrowserAuthReady(false);
            }
            return;
          }
        }
      }
      if (attempt === browserExchangeAttempt.current) {
        setBrowserAuthError(
          rejected ? (browserAccess.legacy ? 'legacy_expired' : 'expired') : 'unreachable',
        );
        setBrowserAuthReady(false);
      }
    } finally {
      browserExchangeInFlight.current = false;
      if (attempt === browserExchangeAttempt.current) setBrowserAuthLoading(false);
    }
  }, [browserAccess]);

  useEffect(() => {
    if (browserExchangeStarted.current) return;
    browserExchangeStarted.current = true;
    const url = new URL(window.location.href);
    url.searchParams.delete('ticket');
    url.searchParams.delete('access_token');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    void exchangeBrowserTicket();
  }, [exchangeBrowserTicket]);

  useEffect(() => {
    let active = true;
    if (!browserAuthReady) return undefined;
    api
      .health()
      .then(() => active && setOnline(true))
      .catch(() => active && setOnline(false));
    return () => {
      active = false;
    };
  }, [browserAuthReady]);

  const openTasks = !project.data
    ? null
    : (project.data?.openTaskCount ??
      project.data?.tasks.filter(
        (task) => task.kind === 'task' && !['done', 'cancelled'].includes(task.status),
      ).length ??
      0);
  const memoryState = !project.data
    ? 'unknown'
    : !online
      ? 'offline'
      : project.data && !project.data.memoryStatus.available
        ? 'attention'
        : 'online';
  const authMessage =
    browserAuthError === 'invalid'
      ? t(
          'This access link is incomplete or ambiguous. Ask your assistant for a new link to this page.',
        )
      : browserAuthError === 'legacy_expired'
        ? t(
            'This older access link has expired or was already used. Ask your assistant for a new link to this page.',
          )
        : browserAuthError === 'expired' || project.accessDenied
          ? t('Access has expired or is missing. Ask your assistant for a new link to this page.')
          : t(
              'The memory server could not be reached. Try again shortly; your data has not been removed.',
            );
  const canManageInfrastructure = Boolean(project.data?.team.capabilities.manage_infrastructure);
  const canOpenLocalSetup = Boolean(
    canManageInfrastructure && project.data?.team.current_member?.trusted_local,
  );
  const backupStatus = project.data?.backup ?? null;
  const refreshing = !project.data
    ? project.loading
    : tab === 'observability'
      ? observability.loading || observability.eventsLoading
      : tab === 'team'
        ? team.loading || team.manualLoading
        : project.loading;

  useEffect(() => {
    if (
      !project.data ||
      (tab !== 'backup' && tab !== 'setup') ||
      (tab === 'backup' && canManageInfrastructure) ||
      (tab === 'setup' && canOpenLocalSetup)
    ) {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set('tab', 'tasks');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setTab('tasks');
  }, [canManageInfrastructure, canOpenLocalSetup, project.data, tab]);

  function selectTab(nextTab: Tab) {
    const url = new URL(window.location.href);
    url.searchParams.set('tab', nextTab);
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    setTab(nextTab);
  }

  async function openSetup() {
    if (!project.data) return;
    setOpeningSetup(true);
    setSetupError('');
    try {
      await api.openSetup(project.data.project.id);
    } catch (error) {
      setSetupError(error instanceof Error ? error.message : t('Setup could not open.'));
    } finally {
      setOpeningSetup(false);
    }
  }

  return (
    <div className="shell">
      <Sidebar
        tab={tab}
        openTasks={openTasks}
        memoryState={memoryState}
        canManageInfrastructure={canManageInfrastructure}
        canOpenLocalSetup={canOpenLocalSetup}
        onTab={selectTab}
      />
      <main>
        <header>
          <div className="header-title">
            <p>
              {t(
                tab === 'tasks'
                  ? 'WORK'
                  : (tab.toUpperCase() as
                      | 'PROJECT'
                      | 'MEMORY'
                      | 'TEAM'
                      | 'OBSERVABILITY'
                      | 'ACTIVITY'
                      | 'BACKUP'
                      | 'SETUP'),
              )}
            </p>
            {project.data?.project ? (
              <ProjectName
                key={project.data.project.id}
                project={project.data.project}
                canRename={canManageInfrastructure}
                refresh={project.refresh}
              />
            ) : (
              <h1>dDuo Solo Founder</h1>
            )}
          </div>
          <div className="header-actions">
            <fieldset className="language-switch" aria-label={t('Language')}>
              <legend className="sr-only">{t('Language')}</legend>
              <button
                type="button"
                aria-label={t('Italian')}
                title={t('Italian')}
                aria-pressed={language === 'it'}
                className={language === 'it' ? 'active' : ''}
                onClick={() => setLanguage('it')}
              >
                IT
              </button>
              <button
                type="button"
                aria-label={t('English')}
                title={t('English')}
                aria-pressed={language === 'en'}
                className={language === 'en' ? 'active' : ''}
                onClick={() => setLanguage('en')}
              >
                EN
              </button>
            </fieldset>
            {project.data && <DisconnectButton onDisconnect={project.disconnect} />}
            <button
              type="button"
              className="icon"
              disabled={
                !project.projectId ||
                refreshing ||
                browserAuthLoading ||
                (!browserAuthReady && browserAuthError !== 'unreachable')
              }
              aria-label={t('Refresh current view')}
              title={t('Refresh')}
              onClick={() =>
                void (!browserAuthReady
                  ? exchangeBrowserTicket()
                  : !project.data
                    ? project.refresh()
                    : tab === 'observability'
                      ? Promise.all([observability.refresh(), team.refresh()])
                      : tab === 'team'
                        ? team.refresh()
                        : project.refresh())
              }
            >
              <RefreshCw className={refreshing ? 'spin' : ''} />
            </button>
          </div>
        </header>
        {(browserAuthError || project.accessDenied) && (
          <div className="error browser-auth-error" role="alert">
            <span>{authMessage}</span>
            {browserAuthError === 'unreachable' && (
              <button
                type="button"
                className="secondary browser-auth-retry"
                disabled={browserAuthLoading}
                onClick={() => void exchangeBrowserTicket()}
              >
                {browserAuthLoading ? t('Retrying…') : t('Retry browser access')}
              </button>
            )}
          </div>
        )}
        {browserAuthLoading && !browserAuthError && !browserAuthReady && (
          <div className="loading" role="status">
            {t('Establishing browser access…')}
          </div>
        )}
        {browserAuthReady && !project.projectId && <ConnectionForm onConnect={project.connect} />}
        {project.error && !project.accessDenied && (
          <div className="error" role="alert">
            {project.loadUnavailable
              ? t(
                  'The memory server could not be reached. Try again shortly; your data has not been removed.',
                )
              : project.error}
          </div>
        )}
        {project.loading && !project.data && (
          <div className="loading" role="status">
            {t('Loading project…')}
          </div>
        )}
        {project.data && tab === 'project' && <ProjectView project={project.data.project} />}
        {project.data && tab === 'memory' && (
          <MemoryView
            projectId={project.data.project.id}
            memories={project.data.memories}
            status={project.data.memoryStatus}
            jobs={project.data.sleepJobs}
            onSleep={project.sleepNow}
            onRetry={project.retrySleep}
            onOpenSetup={openSetup}
            schedulingSleep={project.schedulingSleep}
          />
        )}
        {project.data && tab === 'tasks' && (
          <SprintWork
            key={project.data.project.id}
            projectId={project.data.project.id}
            tasks={project.data.tasks}
            plans={project.data.plans}
            onCreate={project.createTask}
            onUpdate={project.updateTask}
            onCreatePlan={project.createPlan}
            onUpdatePlan={project.updatePlan}
            onStatus={project.setTaskStatus}
            onUpload={project.uploadTaskAttachments}
            onRemoveAttachment={project.removeTaskAttachment}
            onUploadPlan={project.uploadPlanAttachments}
            onRemovePlanAttachment={project.removePlanAttachment}
          />
        )}
        {project.data && tab === 'team' && (
          <TeamView
            project={project.data.project}
            team={team.team}
            manual={team.manual}
            draft={team.draft}
            invitation={team.invitation}
            loading={team.loading}
            manualLoading={team.manualLoading}
            saving={team.saving}
            drafting={team.drafting}
            inviting={team.inviting}
            revokingMemberId={team.revokingMemberId}
            error={team.error}
            manualError={team.manualError}
            onInvite={team.createInvitation}
            onRevokeMember={team.revokeMember}
            onSaveManual={team.saveManual}
            onCreateDraft={team.createDraft}
            onDraft={team.setDraft}
            onRetryManual={team.refreshManual}
          />
        )}
        {project.data && tab === 'observability' && (
          <ObservabilityView
            projectId={project.data.project.id}
            summary={observability.summary}
            events={observability.events}
            range={observability.range}
            onRange={observability.setRange}
            filters={observability.filters}
            onFilters={observability.setFilters}
            loading={observability.loading}
            eventsLoading={observability.eventsLoading}
            error={observability.error}
            nextCursor={observability.nextCursor}
            onLoadMore={observability.loadMore}
            members={team.team?.members ?? project.data.team.members}
          />
        )}
        {project.data && tab === 'activity' && <ActivityView events={project.data.events} />}
        {project.data && backupStatus && canManageInfrastructure && tab === 'backup' && (
          <BackupView
            key={project.data.project.id}
            projectId={project.data.project.id}
            status={backupStatus}
            backingUp={project.backingUp}
            canOpenLocalSetup={canOpenLocalSetup}
            onBackup={project.backupNow}
            onOpenSetup={openSetup}
          />
        )}
        {project.data && canOpenLocalSetup && tab === 'setup' && (
          <SetupView error={setupError} onOpen={openSetup} opening={openingSetup} />
        )}
      </main>
    </div>
  );
}

export function App() {
  return (
    <I18nProvider>
      <DashboardApp />
    </I18nProvider>
  );
}
