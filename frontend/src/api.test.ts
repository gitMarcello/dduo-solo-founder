import { afterEach, describe, expect, it, vi } from 'vitest';
import { type ApiError, api } from './api';

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('api', () => {
  it('exchanges reusable links only in a POST body and preserves a valid cookie CSRF token after a rejected link', async () => {
    const token = `dduo_link_${'a'.repeat(43)}`;
    localStorage.setItem('dduo-solo-founder-csrf:p1', 'existing-csrf');
    const fetch = vi.fn().mockResolvedValue(response({ detail: 'expired' }, 401));
    vi.stubGlobal('fetch', fetch);
    await expect(api.exchangeBrowserSession('p1', token)).rejects.toMatchObject({ status: 401 });
    expect(fetch.mock.calls[0][0]).toBe('/api/projects/p1/auth/browser-session');
    expect(fetch.mock.calls[0][1].method).toBe('POST');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ ticket: token });
    expect(fetch.mock.calls[0][1].headers.Authorization).toBeUndefined();
    expect(localStorage.getItem('dduo-solo-founder-csrf:p1')).toBe('existing-csrf');
    expect(Object.values(localStorage)).not.toContain(token);
  });
  it('renames only the selected project with version checking', async () => {
    const fetch = vi.fn().mockResolvedValue(response({ id: 'p/1', name: 'New' }));
    vi.stubGlobal('fetch', fetch);
    await api.renameProject('p/1', 'New', 2);
    expect(fetch.mock.calls[0][0]).toBe('/api/projects/p%2F1/rename');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ name: 'New', expected_version: 2 });
  });
  it('keeps sprint lifecycle and compact paginated reads on the exact project endpoints', async () => {
    const fetch = vi.fn().mockImplementation(async () =>
      response({
        items: [],
        total: 123,
        limit: 25,
        offset: 100,
        task: { id: 't/1', title: 'Task' },
        plan: { id: 'plan/1', title: 'Plan' },
      }),
    );
    vi.stubGlobal('fetch', fetch);
    const sprint = { id: 's/1', version: 7 } as never;
    const task = await api.getTask('p/1', 't/1');
    const plan = await api.getPlan('p/1', 'plan/1');
    expect(task).toMatchObject({ id: 't/1', is_compact: false, attachments: [] });
    expect(plan).toMatchObject({ id: 'plan/1', is_compact: false, work_item_ids: [] });
    expect(
      (await api.listWork('p/1', { placement: 'all', offset: 100, limit: 25, q: 'release notes' }))
        .total,
    ).toBe(123);
    await api.listPlans('p/1', { offset: 100, q: 'release' });
    await api.listSprints('p/1', { status: 'archived', offset: 50 });
    await api.getSprint('p/1', 's/1');
    await api.createSprint('p/1', { title: 'Release', idempotency_key: 'create-key' });
    await api.startSprint('p/1', sprint, 'start-key');
    await api.closeSprintPreview('p/1', 's/1');
    await api.archiveSprint('p/1', sprint, {
      unfinished_destination: 'backlog',
      idempotency_key: 'archive-key',
    });
    await api.reopenSprint('p/1', sprint, 'reopen-key');
    await api.archivedSprintTasks('p/1', 's/1', { offset: 100, q: 'release' });
    await api.importSprintHistory('p/1', {
      title: 'Previous work',
      task_ids: ['t/1'],
      idempotency_key: 'history-key',
    });
    expect(fetch.mock.calls[0][0]).toBe('/api/projects/p%2F1/tasks/t%2F1?detail=full');
    expect(fetch.mock.calls[2][0]).toContain('offset=100');
    expect(fetch.mock.calls[2][0]).toContain('q=release+notes');
    expect(fetch.mock.calls[7][0]).toBe('/api/projects/p%2F1/sprints/s%2F1/start');
    expect(JSON.parse(fetch.mock.calls[7][1].body)).toEqual({
      expected_version: 7,
      idempotency_key: 'start-key',
    });
    expect(JSON.parse(fetch.mock.calls[9][1].body)).toEqual({
      expected_version: 7,
      unfinished_destination: 'backlog',
      idempotency_key: 'archive-key',
    });
  });
  it('loads project, memory, tasks, activity, and recovery state', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          current_member: null,
          capabilities: { manage_infrastructure: true, participate_in_project: true },
          members: [],
        }),
      )
      .mockResolvedValueOnce(response({ project: { id: 'p1', name: 'Demo' } }))
      .mockResolvedValueOnce(response({ items: [{ id: 't1' }] }))
      .mockResolvedValueOnce(response({ items: [{ id: 'plan-1' }] }))
      .mockResolvedValueOnce(response({ items: [{ id: 'e1' }] }))
      .mockResolvedValueOnce(response({ items: [{ id: 'm1' }] }))
      .mockResolvedValueOnce(response({ available: true, jobs: {}, memories: {} }))
      .mockResolvedValueOnce(response({ items: [{ id: 'j1' }] }))
      .mockResolvedValueOnce(response({ configured: true, items: [] }));
    vi.stubGlobal('fetch', fetch);
    const data = await api.loadProject('project/one');
    expect(data.project.id).toBe('p1');
    expect(data.tasks).toHaveLength(1);
    expect(data.plans).toHaveLength(1);
    expect(data.memories).toHaveLength(1);
    expect(data.sleepJobs).toHaveLength(1);
    expect(data.backup?.configured).toBe(true);
    expect(fetch).toHaveBeenNthCalledWith(1, '/api/projects/project%2Fone/team', expect.anything());
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      '/api/projects/project%2Fone/briefing',
      expect.anything(),
    );
  });

  it('does not request backup metadata for a project member', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          current_member: { id: 'member-1', capability: 'project_member' },
          capabilities: { manage_infrastructure: false, participate_in_project: true },
          members: [],
        }),
      )
      .mockResolvedValueOnce(response({ project: { id: 'p1', name: 'Demo' } }))
      .mockResolvedValueOnce(response({ items: [] }))
      .mockResolvedValueOnce(response({ items: [] }))
      .mockResolvedValueOnce(response({ items: [] }))
      .mockResolvedValueOnce(response({ items: [] }))
      .mockResolvedValueOnce(response({ available: true, jobs: {}, memories: {} }))
      .mockResolvedValueOnce(response({ items: [] }));
    vi.stubGlobal('fetch', fetch);

    const data = await api.loadProject('p1');

    expect(data.backup).toBeNull();
    expect(fetch.mock.calls.map(([path]) => String(path))).not.toContain(
      '/api/projects/p1/backups',
    );
  });

  it('sends optimistic task versions', async () => {
    const fetch = vi.fn().mockResolvedValue(response({ id: 't1', status: 'done' }));
    vi.stubGlobal('fetch', fetch);
    await api.updateTask('p1', { id: 't1', version: 4 } as never, { status: 'done' });
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({ status: 'done', expected_version: 4 });
  });

  it('creates tasks and checks health', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response({ status: 'ok' }))
      .mockResolvedValueOnce(response({ id: 't1', title: 'Task' }));
    vi.stubGlobal('fetch', fetch);
    await api.health();
    await api.createTask('p1', {
      kind: 'task',
      title: 'Task',
      priority: 'high',
      labels: ['release'],
    });
    const init = fetch.mock.calls[1][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      kind: 'task',
      title: 'Task',
      priority: 'high',
      labels: ['release'],
    });
  });

  it('uploads and removes task attachments', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response({ id: 'a1', filename: 'brief.txt' }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetch);
    await api.uploadTaskAttachment(
      'p1',
      't1',
      new File(['brief'], 'brief.txt', { type: 'text/plain' }),
    );
    const upload = fetch.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(upload.body))).toEqual(
      expect.objectContaining({
        kind: 'document',
        filename: 'brief.txt',
        mime_type: 'text/plain',
        content_base64: 'YnJpZWY=',
      }),
    );
    await api.removeTaskAttachment('p1', 't1', 'a1');
    expect((fetch.mock.calls[1][1] as RequestInit).method).toBe('DELETE');
    expect(api.taskAttachmentUrl('p/1', 't1', 'a1')).toContain('/projects/p%2F1/tasks/t1/');
  });

  it('creates versioned plans and keeps their attachments on the plan endpoint', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response({ id: 'plan-1', title: 'Release approach' }))
      .mockResolvedValueOnce(response({ id: 'plan-1', status: 'decided' }))
      .mockResolvedValueOnce(response({ id: 'a1', filename: 'brief.txt' }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetch);

    await api.createPlan('p1', {
      title: 'Release approach',
      objective: 'Choose the launch sequence',
      labels: ['release'],
      work_item_ids: ['epic-1'],
    });
    await api.updatePlan('p1', { id: 'plan-1', version: 4 } as never, {
      status: 'decided',
      work_item_ids: ['epic-1', 'task-1'],
    });
    await api.uploadPlanAttachment(
      'p1',
      'plan-1',
      new File(['brief'], 'brief.txt', { type: 'text/plain' }),
    );
    await api.removePlanAttachment('p1', 'plan-1', 'a1');

    expect(JSON.parse(String((fetch.mock.calls[0][1] as RequestInit).body))).toEqual({
      title: 'Release approach',
      objective: 'Choose the launch sequence',
      labels: ['release'],
      work_item_ids: ['epic-1'],
    });
    expect(JSON.parse(String((fetch.mock.calls[1][1] as RequestInit).body))).toEqual({
      status: 'decided',
      work_item_ids: ['epic-1', 'task-1'],
      expected_version: 4,
    });
    expect(fetch.mock.calls[2][0]).toContain('/projects/p1/plans/plan-1/attachments');
    expect((fetch.mock.calls[3][1] as RequestInit).method).toBe('DELETE');
    expect(api.planAttachmentUrl('p/1', 'plan-1', 'a1')).toContain('/projects/p%2F1/plans/');
  });

  it('creates a project backup', async () => {
    const fetch = vi.fn().mockResolvedValue(response({ id: 'b1', status: 'verified' }));
    vi.stubGlobal('fetch', fetch);
    await api.createBackup('project/one');
    expect(fetch).toHaveBeenCalledWith('/api/projects/project%2Fone/backups', {
      method: 'POST',
      headers: undefined,
    });
    expect(api.backupDownloadUrl('project/one', 'backup/one')).toBe(
      '/api/projects/project%2Fone/backups/backup%2Fone/download',
    );
  });

  it('loads lazy observability summary and filtered event pages', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          period: {},
          coverage: {},
          context: {},
          sleep: {},
          embeddings: {},
          reliability: {},
        }),
      )
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }));
    vi.stubGlobal('fetch', fetch);
    await api.observabilitySummary('p/1', '7d', 'Europe/Rome', 'member-1');
    await api.observabilityEvents('p/1', {
      range: '7d',
      category: 'embedding',
      status: 'success',
      actor: 'system',
      cursor: 'cursor-value',
    });
    expect(fetch.mock.calls[0][0]).toContain(
      '/projects/p%2F1/observability/summary?range=7d&timezone=Europe%2FRome',
    );
    expect(fetch.mock.calls[0][0]).toContain('actor=member-1');
    expect(fetch.mock.calls[1][0]).toContain('category=embedding');
    expect(fetch.mock.calls[1][0]).toContain('status=success');
    expect(fetch.mock.calls[1][0]).toContain('actor=system');
    expect(fetch.mock.calls[1][0]).toContain('cursor=cursor-value');
  });

  it('uses the exact browser, team, invitation, and versioned manual contracts', async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(response({ capabilities: {}, current_member: null })),
      );
    vi.stubGlobal('fetch', fetch);

    await api.exchangeBrowserSession('project/one', 'dduo_web_ticket');
    await api.team('project/one');
    await api.operationalManual('project/one');
    await api.createTeamInvitation('project/one', {
      display_name: 'Sam',
      expires_in_hours: 72,
      language: 'it',
    });
    await api.updateOperationalManual('project/one', {
      content: 'Deploy after QA.',
      expected_version: 4,
      idempotency_key: 'manual-request-1',
    });
    await api.createOperationalManualDraft('project/one', 5);
    await api.revokeTeamMember('project/one', 'member/one');
    await api.logoutBrowserSession('project/one');

    expect(fetch.mock.calls[0][0]).toBe('/api/projects/project%2Fone/auth/browser-session');
    expect(JSON.parse(String((fetch.mock.calls[0][1] as RequestInit).body))).toEqual({
      ticket: 'dduo_web_ticket',
    });
    expect(fetch.mock.calls[1][0]).toBe('/api/projects/project%2Fone/team');
    expect(fetch.mock.calls[2][0]).toBe('/api/projects/project%2Fone/team/manual');
    expect(JSON.parse(String((fetch.mock.calls[3][1] as RequestInit).body))).toEqual({
      display_name: 'Sam',
      expires_in_hours: 72,
      language: 'it',
      api_url: `${window.location.origin}/api`,
      dashboard_url: window.location.origin,
    });
    expect(JSON.parse(String((fetch.mock.calls[4][1] as RequestInit).body))).toEqual({
      content: 'Deploy after QA.',
      expected_version: 4,
      idempotency_key: 'manual-request-1',
    });
    expect(JSON.parse(String((fetch.mock.calls[5][1] as RequestInit).body))).toEqual({
      expected_version: 5,
    });
    expect(fetch.mock.calls[6][0]).toBe('/api/projects/project%2Fone/team/members/member%2Fone');
    expect((fetch.mock.calls[6][1] as RequestInit).method).toBe('DELETE');
    expect(fetch.mock.calls[7][0]).toBe('/api/projects/project%2Fone/auth/logout');
    expect((fetch.mock.calls[7][1] as RequestInit).method).toBe('POST');
  });

  it('keeps the browser CSRF token origin-local and sends it on stateful requests', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          authenticated: true,
          csrf_token: 'dduo_csrf_origin_secret',
          capabilities: {},
          current_member: null,
        }),
      )
      .mockImplementation(() =>
        Promise.resolve(response({ capabilities: {}, current_member: null })),
      );
    vi.stubGlobal('fetch', fetch);

    await api.exchangeBrowserSession('project-one', 'dduo_web_ticket');
    await api.createTeamInvitation('project-one', {
      display_name: 'Sam',
      expires_in_hours: 24,
    });
    await api.team('project-one');
    await api.logoutBrowserSession('project-one');

    expect((fetch.mock.calls[0][1] as RequestInit).headers).not.toHaveProperty('X-DDUO-CSRF');
    expect((fetch.mock.calls[1][1] as RequestInit).headers).toMatchObject({
      'X-DDUO-CSRF': 'dduo_csrf_origin_secret',
    });
    expect((fetch.mock.calls[2][1] as RequestInit).headers).toBeUndefined();
    expect((fetch.mock.calls[3][1] as RequestInit).headers).toMatchObject({
      'X-DDUO-CSRF': 'dduo_csrf_origin_secret',
    });
    expect(localStorage.getItem('dduo-solo-founder-csrf:project-one')).toBeNull();
  });

  it('loads an exact context event only when requested', async () => {
    const fetch = vi.fn().mockResolvedValue(
      response({
        event: { id: 'event-1' },
        content: 'Private turn context follows',
        components: [],
      }),
    );
    vi.stubGlobal('fetch', fetch);
    await api.contextEventDetail('p/1', 'event/1');
    expect(fetch.mock.calls[0][0]).toContain(
      '/projects/p%2F1/observability/context-events/event%2F1',
    );
  });

  it('controls sleep and explains memory provenance', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(response({ items: [] })));
    vi.stubGlobal('fetch', fetch);
    await api.requestSleep('p1');
    await api.retrySleep('p1', 'j1');
    await api.explainMemory('p1', 'm1');
    await api.forgetMemory('p1', 'm1', 'obsolete');
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it('surfaces backend details as typed errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() => Promise.resolve(response({ detail: 'project not found' }, 404))),
    );
    await expect(api.loadProject('missing')).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: 'ApiError',
        message: 'project not found',
        status: 404,
      }),
    );
  });

  it('falls back to the HTTP status when an error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('offline', { status: 503 })));
    await expect(api.health()).rejects.toThrow('Request failed with status 503');
  });
});
