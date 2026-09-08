import { RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
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

function DashboardApp() {
  const { language, setLanguage, t } = useI18n();
  const [tab, setTab] = useState<Tab>(initialTab);
  const [browserTicket] = useState(() => {
    const query = new URLSearchParams(window.location.search);
    const ticket = query.get('ticket')?.trim() ?? '';
    const projectId = query.get('project')?.trim() ?? '';
    return ticket ? { ticket, projectId } : null;
  });
  const [browserAuthReady, setBrowserAuthReady] = useState(!browserTicket);
  const [browserAuthLoading, setBrowserAuthLoading] = useState(Boolean(browserTicket));
  const [browserAuthError, setBrowserAuthError] = useState('');
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
    if (!browserTicket || browserExchangeInFlight.current) return;
    browserExchangeInFlight.current = true;
    const attempt = ++browserExchangeAttempt.current;
    setBrowserAuthLoading(true);
    setBrowserAuthError('');
    if (!browserTicket.projectId) {
      setBrowserAuthError(t('The browser access link is missing its project identifier.'));
      setBrowserAuthLoading(false);
      browserExchangeInFlight.current = false;
      return;
    }
    try {
      await api.exchangeBrowserSession(browserTicket.projectId, browserTicket.ticket);
      if (attempt === browserExchangeAttempt.current) setBrowserAuthReady(true);
    } catch (reason) {
      if (attempt === browserExchangeAttempt.current) {
        setBrowserAuthError(reason instanceof Error ? reason.message : String(reason));
        setBrowserAuthReady(false);
      }
    } finally {
      browserExchangeInFlight.current = false;
      if (attempt === browserExchangeAttempt.current) setBrowserAuthLoading(false);
    }
  }, [browserTicket, t]);

  useEffect(() => {
    if (!browserTicket || browserExchangeStarted.current) return;
    browserExchangeStarted.current = true;
    const url = new URL(window.location.href);
    url.searchParams.delete('ticket');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    void exchangeBrowserTicket();
  }, [browserTicket, exchangeBrowserTicket]);

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

  const openTasks =
    project.data?.openTaskCount ??
    project.data?.tasks.filter(
      (task) => task.kind === 'task' && !['done', 'cancelled'].includes(task.status),
    ).length ??
    0;
  const memoryState = !online
    ? 'offline'
    : project.data && !project.data.memoryStatus.available
      ? 'attention'
      : 'online';
  const canManageInfrastructure = Boolean(project.data?.team.capabilities.manage_infrastructure);
  const canOpenLocalSetup = Boolean(
    canManageInfrastructure && project.data?.team.current_member?.trusted_local,
  );
  const backupStatus = project.data?.backup ?? null;
  const refreshing =
    tab === 'observability'
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
              disabled={!project.projectId || refreshing}
              aria-label={t('Refresh current view')}
              title={t('Refresh')}
              onClick={() =>
                void (tab === 'observability'
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
        {browserAuthError && (
          <div className="error browser-auth-error" role="alert">
            <span>
              {t('Browser access could not be established: {error}', { error: browserAuthError })}
            </span>
            {browserTicket?.projectId && (
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
        {project.error && (
          <div className="error" role="alert">
            {project.error}
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
