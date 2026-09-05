import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import type { OperationalManualEnvelope, TeamOverview } from './types';
import { useTeam } from './useTeam';

const team: TeamOverview = {
  current_member: {
    id: 'manager-1',
    project_id: 'p1',
    display_name: 'Alex',
    capability: 'infrastructure_manager',
    capability_label: 'Gestore dell’infrastruttura',
    trusted_local: false,
  },
  capabilities: { manage_infrastructure: true, participate_in_project: true },
  members: [],
};

const manual: OperationalManualEnvelope = {
  current_member: team.current_member,
  capabilities: team.capabilities,
  manual: {
    content: 'Deploy after QA.',
    version: 2,
    characters: 16,
    soft_limit_characters: 4_000,
    hard_limit_characters: 100_000,
    warnings: [],
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('useTeam', () => {
  it('loads team metadata lazily and fetches the manual only for the Team view', async () => {
    const loadTeam = vi.spyOn(api, 'team').mockResolvedValue(team);
    const loadManual = vi.spyOn(api, 'operationalManual').mockResolvedValue(manual);
    const { result, rerender } = renderHook(
      ({ enabled, includeManual }) => useTeam('p1', enabled, includeManual),
      { initialProps: { enabled: false, includeManual: false } },
    );
    expect(loadTeam).not.toHaveBeenCalled();
    rerender({ enabled: true, includeManual: false });
    await waitFor(() => expect(result.current.team).toEqual(team));
    expect(loadManual).not.toHaveBeenCalled();
    rerender({ enabled: true, includeManual: true });
    await waitFor(() => expect(result.current.manual).toEqual(manual.manual));
    expect(loadTeam).toHaveBeenCalledTimes(2);
  });

  it('refreshes team and manual on tab re-entry without clearing the visible snapshot', async () => {
    let resolveTeam: ((value: TeamOverview) => void) | undefined;
    let resolveManual: ((value: OperationalManualEnvelope) => void) | undefined;
    const updatedTeam: TeamOverview = {
      ...team,
      members: [
        {
          id: 'member-1',
          project_id: 'p1',
          display_name: 'Sam',
          capability: 'project_member',
          capability_label: 'Membro del progetto',
          status: 'active',
          created_at: '2026-08-28T08:00:00Z',
          updated_at: '2026-08-28T08:00:00Z',
        },
      ],
    };
    const updatedManual: OperationalManualEnvelope = {
      ...manual,
      manual: { ...manual.manual, content: 'Updated shared rules.', version: 3 },
    };
    vi.spyOn(api, 'team')
      .mockResolvedValueOnce(team)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveTeam = resolve;
          }),
      );
    vi.spyOn(api, 'operationalManual')
      .mockResolvedValueOnce(manual)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveManual = resolve;
          }),
      );
    const { result, rerender } = renderHook(
      ({ enabled, includeManual }) => useTeam('p1', enabled, includeManual),
      { initialProps: { enabled: true, includeManual: true } },
    );
    await waitFor(() => expect(result.current.manual?.version).toBe(2));

    rerender({ enabled: false, includeManual: false });
    rerender({ enabled: true, includeManual: true });
    await waitFor(() => expect(result.current.manualLoading).toBe(true));
    expect(result.current.team).toEqual(team);
    expect(result.current.manual).toEqual(manual.manual);

    await act(async () => {
      resolveTeam?.(updatedTeam);
      resolveManual?.(updatedManual);
    });
    await waitFor(() => expect(result.current.manual?.version).toBe(3));
    expect(result.current.team).toEqual(updatedTeam);
    expect(result.current.manualLoading).toBe(false);
  });

  it('keeps compact output as a draft and persists only an explicit versioned save', async () => {
    vi.spyOn(api, 'team').mockResolvedValue(team);
    vi.spyOn(api, 'operationalManual').mockResolvedValue(manual);
    const compact = vi.spyOn(api, 'createOperationalManualDraft').mockResolvedValue({
      current_member: team.current_member,
      capabilities: team.capabilities,
      draft: {
        draft: 'Compact rules.',
        based_on_version: 2,
        source: 'provider',
        characters: 14,
        warnings: [],
        persisted: false,
      },
    });
    const save = vi.spyOn(api, 'updateOperationalManual').mockResolvedValue({
      ...manual,
      manual: { ...manual.manual, content: 'Compact rules.', version: 3 },
    });
    const invite = vi.spyOn(api, 'createTeamInvitation').mockResolvedValue({
      current_member: team.current_member,
      capabilities: team.capabilities,
      invitation: {
        id: 'invite-1',
        display_name: 'Sam',
        invite_payload: 'opaque_invite_payload',
        setup_prompt:
          'dduo-solo-founder remote-join --invite-payload opaque_invite_payload --project-root .',
        expires_at: '2026-08-29T10:00:00Z',
      },
    });
    const revokedTeam: TeamOverview = {
      ...team,
      members: [
        {
          id: 'member-1',
          project_id: 'p1',
          display_name: 'Sam',
          capability: 'project_member',
          capability_label: 'Membro del progetto',
          status: 'revoked',
          created_at: '2026-08-28T08:00:00Z',
          updated_at: '2026-08-28T09:00:00Z',
        },
      ],
    };
    const revoke = vi.spyOn(api, 'revokeTeamMember').mockResolvedValue(revokedTeam);
    const { result } = renderHook(() => useTeam('p1', true, true, null, 'it'));
    await waitFor(() => expect(result.current.manual?.version).toBe(2));

    await act(async () => result.current.createDraft());
    expect(compact).toHaveBeenCalledWith('p1', 2);
    expect(result.current.draft?.persisted).toBe(false);
    expect(save).not.toHaveBeenCalled();

    await act(async () => result.current.saveManual('Compact rules.', 2));
    expect(save).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ content: 'Compact rules.', expected_version: 2 }),
    );
    expect(result.current.manual?.version).toBe(3);
    expect(result.current.draft).toBeNull();

    await act(async () => result.current.createInvitation('Sam', 24));
    expect(invite).toHaveBeenCalledWith('p1', {
      display_name: 'Sam',
      expires_in_hours: 24,
      language: 'it',
    });
    expect(result.current.invitation?.display_name).toBe('Sam');

    await act(async () => result.current.revokeMember('member-1'));
    expect(revoke).toHaveBeenCalledWith('p1', 'member-1');
    expect(result.current.team).toEqual(revokedTeam);
    expect(result.current.revokingMemberId).toBe('');
  });

  it('reuses the manual idempotency key only while retrying the same failed payload', async () => {
    vi.spyOn(api, 'team').mockResolvedValue(team);
    vi.spyOn(api, 'operationalManual').mockResolvedValue(manual);
    const save = vi
      .spyOn(api, 'updateOperationalManual')
      .mockRejectedValueOnce(new Error('Response lost'))
      .mockResolvedValueOnce({
        ...manual,
        manual: { ...manual.manual, content: 'Changed rules.', version: 3 },
      })
      .mockResolvedValueOnce({
        ...manual,
        manual: { ...manual.manual, content: 'Different rules.', version: 4 },
      });
    const { result } = renderHook(() => useTeam('p1', true, true));
    await waitFor(() => expect(result.current.manual?.version).toBe(2));

    await act(async () => {
      await expect(result.current.saveManual('Changed rules.', 2)).rejects.toThrow('Response lost');
    });
    await act(async () => result.current.saveManual('Changed rules.', 2));
    const firstKey = save.mock.calls[0][1].idempotency_key;
    expect(save.mock.calls[1][1].idempotency_key).toBe(firstKey);

    await act(async () => result.current.saveManual('Different rules.', 3));
    expect(save.mock.calls[2][1].idempotency_key).not.toBe(firstKey);
  });

  it('keeps team and manual failures readable without turning drafts into writes', async () => {
    vi.spyOn(api, 'team').mockResolvedValue(team);
    vi.spyOn(api, 'operationalManual').mockResolvedValue(manual);
    vi.spyOn(api, 'createTeamInvitation').mockRejectedValue('Invitation unavailable');
    vi.spyOn(api, 'updateOperationalManual').mockRejectedValue(
      new Error('Manual version conflict'),
    );
    vi.spyOn(api, 'createOperationalManualDraft').mockRejectedValue('Draft unavailable');
    vi.spyOn(api, 'revokeTeamMember').mockRejectedValue(new Error('Revocation unavailable'));
    const { result } = renderHook(() => useTeam('p1', true, true));
    await waitFor(() => expect(result.current.manual?.version).toBe(2));

    await act(async () => {
      await expect(result.current.createInvitation('Sam', 24)).rejects.toBe(
        'Invitation unavailable',
      );
    });
    expect(result.current.error).toBe('Invitation unavailable');
    expect(result.current.inviting).toBe(false);

    await act(async () => {
      await expect(result.current.saveManual('Changed.', 2)).rejects.toThrow(
        'Manual version conflict',
      );
    });
    expect(result.current.error).toBe('Manual version conflict');
    expect(result.current.saving).toBe(false);

    await act(async () => {
      await expect(result.current.createDraft()).rejects.toBe('Draft unavailable');
    });
    expect(result.current.error).toBe('Draft unavailable');
    expect(result.current.drafting).toBe(false);

    await act(async () => {
      await expect(result.current.revokeMember('member-1')).rejects.toThrow(
        'Revocation unavailable',
      );
    });
    expect(result.current.error).toBe('Revocation unavailable');
    expect(result.current.revokingMemberId).toBe('');
  });

  it('drops late team responses when the selected project changes', async () => {
    let resolveFirst: ((value: TeamOverview) => void) | undefined;
    const teamRequest = vi.spyOn(api, 'team').mockImplementation((projectId) =>
      projectId === 'p1'
        ? new Promise((resolve) => {
            resolveFirst = resolve;
          })
        : Promise.resolve({ ...team, current_member: null }),
    );
    const { result, rerender } = renderHook(({ projectId }) => useTeam(projectId, true, false), {
      initialProps: { projectId: 'p1' },
    });
    await waitFor(() => expect(teamRequest).toHaveBeenCalledWith('p1'));
    rerender({ projectId: 'p2' });
    await waitFor(() => expect(teamRequest).toHaveBeenCalledWith('p2'));
    await waitFor(() => expect(result.current.team?.current_member).toBeNull());
    resolveFirst?.(team);
    await act(async () => Promise.resolve());
    expect(result.current.team?.current_member).toBeNull();
  });

  it('reports initial loading failures and remains inert without a project', async () => {
    const loadTeam = vi.spyOn(api, 'team').mockRejectedValue(new Error('Team unavailable'));
    vi.spyOn(api, 'operationalManual').mockRejectedValue('Manual unavailable');
    const { result, rerender } = renderHook(
      ({ projectId, includeManual }) => useTeam(projectId, true, includeManual),
      { initialProps: { projectId: 'p1', includeManual: false } },
    );
    await waitFor(() => expect(result.current.error).toBe('Team unavailable'));
    rerender({ projectId: '', includeManual: true });
    await act(async () => {
      await result.current.createInvitation('Nobody', 24);
      await result.current.saveManual('Nothing');
      await result.current.createDraft();
    });
    expect(loadTeam).toHaveBeenCalledTimes(1);
    expect(result.current.team).toBeNull();
  });

  it('reports a manual read failure without hiding the loaded team', async () => {
    vi.spyOn(api, 'team').mockResolvedValue(team);
    vi.spyOn(api, 'operationalManual').mockRejectedValue('Manual unavailable');
    const { result } = renderHook(() => useTeam('p1', true, true));
    await waitFor(() => expect(result.current.team).toEqual(team));
    await waitFor(() => expect(result.current.manualError).toBe('Manual unavailable'));
    expect(result.current.error).toBe('');
    expect(result.current.manual).toBeNull();
    expect(result.current.manualLoading).toBe(false);
  });
});
