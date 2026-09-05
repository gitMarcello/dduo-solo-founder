import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Language } from './i18n';
import type {
  OperationalManual,
  OperationalManualDraft,
  TeamInvitation,
  TeamOverview,
} from './types';

type PendingManualWrite = {
  projectId: string;
  content: string;
  expectedVersion: number;
  idempotencyKey: string;
};

function idempotencyKey() {
  return `manual-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

export function useTeam(
  projectId: string,
  enabled: boolean,
  includeManual: boolean,
  initialTeam: TeamOverview | null = null,
  language?: Language,
) {
  const [team, setTeam] = useState<TeamOverview | null>(null);
  const [manual, setManual] = useState<OperationalManual | null>(null);
  const [draft, setDraft] = useState<OperationalManualDraft | null>(null);
  const [invitation, setInvitation] = useState<TeamInvitation | null>(null);
  const [loading, setLoading] = useState(false);
  const [manualLoading, setManualLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [revokingMemberId, setRevokingMemberId] = useState('');
  const [error, setError] = useState('');
  const [manualError, setManualError] = useState('');
  const teamRequestSequence = useRef(0);
  const manualRequestSequence = useRef(0);
  const loadedProject = useRef('');
  const visibility = useRef({ projectId: '', enabled: false, includeManual: false });
  const pendingManualWrite = useRef<PendingManualWrite | null>(null);
  const initialTeamRef = useRef(initialTeam);
  initialTeamRef.current = initialTeam;

  useEffect(() => {
    if (loadedProject.current === projectId) return;
    loadedProject.current = projectId;
    teamRequestSequence.current += 1;
    manualRequestSequence.current += 1;
    setTeam(initialTeamRef.current);
    setManual(null);
    setDraft(null);
    setInvitation(null);
    setError('');
    setManualError('');
    setLoading(false);
    setManualLoading(false);
    setSaving(false);
    setDrafting(false);
    setInviting(false);
    setRevokingMemberId('');
    pendingManualWrite.current = null;
  }, [projectId]);

  useEffect(() => {
    if (loadedProject.current === projectId && initialTeam) setTeam(initialTeam);
  }, [initialTeam, projectId]);

  const loadTeam = useCallback(async () => {
    if (!enabled || !projectId) return;
    const sequence = ++teamRequestSequence.current;
    setLoading(true);
    setError('');
    try {
      const value = await api.team(projectId);
      if (sequence === teamRequestSequence.current && loadedProject.current === projectId) {
        setTeam(value);
      }
    } catch (reason) {
      if (sequence === teamRequestSequence.current && loadedProject.current === projectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (sequence === teamRequestSequence.current && loadedProject.current === projectId) {
        setLoading(false);
      }
    }
  }, [enabled, projectId]);

  const loadManual = useCallback(async () => {
    if (!enabled || !includeManual || !projectId) return;
    const sequence = ++manualRequestSequence.current;
    setManualLoading(true);
    setManualError('');
    try {
      const value = await api.operationalManual(projectId);
      if (sequence === manualRequestSequence.current && loadedProject.current === projectId) {
        setManual(value.manual);
        setTeam((current) =>
          current
            ? {
                ...current,
                current_member: value.current_member,
                capabilities: value.capabilities,
              }
            : current,
        );
      }
    } catch (reason) {
      if (sequence === manualRequestSequence.current && loadedProject.current === projectId) {
        setManualError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (sequence === manualRequestSequence.current && loadedProject.current === projectId) {
        setManualLoading(false);
      }
    }
  }, [enabled, includeManual, projectId]);

  useEffect(() => {
    const previous = visibility.current;
    const projectChanged = previous.projectId !== projectId;
    const teamActivated =
      enabled &&
      (projectChanged || !previous.enabled || (includeManual && !previous.includeManual));
    const manualActivated = enabled && includeManual && (projectChanged || !previous.includeManual);
    visibility.current = { projectId, enabled, includeManual };
    if (teamActivated) void loadTeam();
    if (manualActivated) void loadManual();
  }, [enabled, includeManual, loadManual, loadTeam, projectId]);

  const createInvitation = useCallback(
    async (displayName: string, expiresInHours: number) => {
      if (!projectId) return;
      setInviting(true);
      setError('');
      try {
        const value = await api.createTeamInvitation(projectId, {
          display_name: displayName,
          expires_in_hours: expiresInHours,
          ...(language ? { language } : {}),
        });
        if (loadedProject.current === projectId) setInvitation(value.invitation);
      } catch (reason) {
        if (loadedProject.current === projectId) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
        throw reason;
      } finally {
        if (loadedProject.current === projectId) setInviting(false);
      }
    },
    [language, projectId],
  );

  const revokeMember = useCallback(
    async (memberId: string) => {
      if (!projectId || !memberId) return;
      setRevokingMemberId(memberId);
      setError('');
      try {
        const value = await api.revokeTeamMember(projectId, memberId);
        if (loadedProject.current === projectId) setTeam(value);
      } catch (reason) {
        if (loadedProject.current === projectId) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
        throw reason;
      } finally {
        if (loadedProject.current === projectId) setRevokingMemberId('');
      }
    },
    [projectId],
  );

  const saveManual = useCallback(
    async (content: string, expectedVersion?: number) => {
      if (!projectId || !manual) return;
      const targetVersion = expectedVersion ?? manual.version;
      const previous = pendingManualWrite.current;
      const pending =
        previous?.projectId === projectId &&
        previous.content === content &&
        previous.expectedVersion === targetVersion
          ? previous
          : {
              projectId,
              content,
              expectedVersion: targetVersion,
              idempotencyKey: idempotencyKey(),
            };
      pendingManualWrite.current = pending;
      setSaving(true);
      setError('');
      try {
        const value = await api.updateOperationalManual(projectId, {
          content,
          expected_version: targetVersion,
          idempotency_key: pending.idempotencyKey,
        });
        if (loadedProject.current === projectId) {
          setManual(value.manual);
          setDraft(null);
          if (pendingManualWrite.current?.idempotencyKey === pending.idempotencyKey) {
            pendingManualWrite.current = null;
          }
        }
      } catch (reason) {
        if (loadedProject.current === projectId) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
        throw reason;
      } finally {
        if (loadedProject.current === projectId) setSaving(false);
      }
    },
    [manual, projectId],
  );

  const createDraft = useCallback(async () => {
    if (!projectId || !manual) return;
    setDrafting(true);
    setError('');
    try {
      const value = await api.createOperationalManualDraft(projectId, manual.version);
      if (loadedProject.current === projectId) setDraft(value.draft);
    } catch (reason) {
      if (loadedProject.current === projectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (loadedProject.current === projectId) setDrafting(false);
    }
  }, [manual, projectId]);

  const belongsToCurrentProject = loadedProject.current === projectId;
  const currentManual = belongsToCurrentProject ? manual : null;
  const currentManualError = belongsToCurrentProject ? manualError : '';

  return {
    team: belongsToCurrentProject ? team : null,
    manual: currentManual,
    draft: belongsToCurrentProject ? draft : null,
    invitation: belongsToCurrentProject ? invitation : null,
    loading: belongsToCurrentProject && loading,
    manualLoading: Boolean(
      includeManual && (manualLoading || (!currentManual && !currentManualError)),
    ),
    saving: belongsToCurrentProject && saving,
    drafting: belongsToCurrentProject && drafting,
    inviting: belongsToCurrentProject && inviting,
    revokingMemberId: belongsToCurrentProject ? revokingMemberId : '',
    error: belongsToCurrentProject ? error : '',
    manualError: currentManualError,
    refresh: async () => {
      setDraft(null);
      setInvitation(null);
      await Promise.all([loadTeam(), includeManual ? loadManual() : Promise.resolve()]);
    },
    refreshManual: loadManual,
    createInvitation,
    revokeMember,
    saveManual,
    createDraft,
    setDraft,
  };
}
