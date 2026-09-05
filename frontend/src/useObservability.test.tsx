import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import type { ObservabilitySummary } from './types';
import { useObservability } from './useObservability';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function summary(project: string) {
  return { project } as unknown as ObservabilitySummary;
}

describe('useObservability', () => {
  it('never exposes a late observability response under a different project', async () => {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    let resolveFirstSummary: ((value: ObservabilitySummary) => void) | undefined;
    let resolveFirstEvents: ((value: { items: []; next_cursor: null }) => void) | undefined;
    let resolveSecondSummary: ((value: ObservabilitySummary) => void) | undefined;
    let resolveSecondEvents: ((value: { items: []; next_cursor: null }) => void) | undefined;
    vi.spyOn(api, 'observabilitySummary').mockImplementation((projectId) => {
      return new Promise((resolve) => {
        if (projectId === 'p1') resolveFirstSummary = resolve;
        else resolveSecondSummary = resolve;
      });
    });
    vi.spyOn(api, 'observabilityEvents').mockImplementation((projectId) => {
      return new Promise((resolve) => {
        if (projectId === 'p1') resolveFirstEvents = resolve;
        else resolveSecondEvents = resolve;
      });
    });
    const { result, rerender } = renderHook(({ projectId }) => useObservability(projectId, true), {
      initialProps: { projectId: 'p1' },
    });
    await waitFor(() =>
      expect(api.observabilitySummary).toHaveBeenCalledWith('p1', '7d', timezone, undefined),
    );
    rerender({ projectId: 'p2' });
    await waitFor(() =>
      expect(api.observabilitySummary).toHaveBeenCalledWith('p2', '7d', timezone, undefined),
    );

    await act(async () => {
      resolveFirstSummary?.(summary('p1'));
      resolveFirstEvents?.({ items: [], next_cursor: null });
    });
    expect(result.current.summary).toBeNull();
    expect(result.current.events).toEqual([]);

    await act(async () => {
      resolveSecondSummary?.(summary('p2'));
      resolveSecondEvents?.({ items: [], next_cursor: null });
    });
    expect(result.current.summary).toEqual(summary('p2'));
  });

  it('applies the same user filter to summary and paginated events', async () => {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    const loadSummary = vi.spyOn(api, 'observabilitySummary').mockResolvedValue(summary('p1'));
    const loadEvents = vi
      .spyOn(api, 'observabilityEvents')
      .mockResolvedValue({ items: [], next_cursor: null });
    const { result } = renderHook(() => useObservability('p1', true));
    await waitFor(() => expect(loadSummary).toHaveBeenCalled());
    act(() => result.current.setFilters({ category: '', status: '', actor: 'system' }));
    await waitFor(() =>
      expect(loadSummary).toHaveBeenLastCalledWith('p1', '7d', timezone, 'system'),
    );
    expect(loadEvents).toHaveBeenLastCalledWith('p1', expect.objectContaining({ actor: 'system' }));
  });

  it('hides stale measurements while a new filter loads and after it fails', async () => {
    let rejectSummary: ((reason: Error) => void) | undefined;
    let rejectEvents: ((reason: Error) => void) | undefined;
    vi.spyOn(api, 'observabilitySummary').mockImplementation(
      (_projectId, _range, _timezone, actor) =>
        actor === 'system'
          ? new Promise((_resolve, reject) => {
              rejectSummary = reject;
            })
          : Promise.resolve(summary('all-users')),
    );
    vi.spyOn(api, 'observabilityEvents').mockImplementation((_projectId, options) =>
      options.actor === 'system'
        ? new Promise((_resolve, reject) => {
            rejectEvents = reject;
          })
        : Promise.resolve({
            items: [{ id: 'all-users-event' } as never],
            next_cursor: 'all-users-cursor',
          }),
    );
    const { result } = renderHook(() => useObservability('p1', true));
    await waitFor(() => expect(result.current.summary).toEqual(summary('all-users')));
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    act(() => result.current.setFilters({ category: '', status: '', actor: 'system' }));
    expect(result.current.summary).toBeNull();
    expect(result.current.events).toEqual([]);
    expect(result.current.nextCursor).toBeNull();

    await act(async () => {
      rejectSummary?.(new Error('Summary unavailable'));
      rejectEvents?.(new Error('Events unavailable'));
    });
    expect(result.current.summary).toBeNull();
    expect(result.current.events).toEqual([]);
    expect(result.current.loading).toBe(false);
    expect(result.current.eventsLoading).toBe(false);
    expect(result.current.error).toContain('Summary unavailable');
    expect(result.current.error).toContain('Events unavailable');
  });

  it('resets project-specific filters immediately when the project changes', async () => {
    vi.spyOn(api, 'observabilitySummary').mockImplementation((projectId) =>
      Promise.resolve(summary(projectId)),
    );
    const events = vi
      .spyOn(api, 'observabilityEvents')
      .mockResolvedValue({ items: [], next_cursor: null });
    const { result, rerender } = renderHook(({ projectId }) => useObservability(projectId, true), {
      initialProps: { projectId: 'p1' },
    });
    await waitFor(() => expect(result.current.summary).toEqual(summary('p1')));
    act(() => result.current.setFilters({ category: 'embedding', status: 'failed', actor: 'm1' }));
    rerender({ projectId: 'p2' });

    expect(result.current.filters).toEqual({ category: '', status: '', actor: '' });
    expect(result.current.summary).toBeNull();
    expect(result.current.events).toEqual([]);
    await waitFor(() => expect(result.current.summary).toEqual(summary('p2')));
    expect(events).toHaveBeenLastCalledWith(
      'p2',
      expect.objectContaining({ category: undefined, status: undefined, actor: undefined }),
    );
  });
});
