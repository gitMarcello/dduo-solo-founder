import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import type { ObservabilityEvent, ObservabilityRange, ObservabilitySummary } from './types';

type EventFilters = { category: string; status: string; actor?: string };
type ProjectFilters = { projectId: string; value: EventFilters };
type QueryError = { key: string; message: string };

const EMPTY_FILTERS: EventFilters = { category: '', status: '', actor: '' };

function localTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}

export function useObservability(projectId: string, enabled: boolean) {
  const [range, setRange] = useState<ObservabilityRange>('7d');
  const [summary, setSummary] = useState<ObservabilitySummary | null>(null);
  const [events, setEvents] = useState<ObservabilityEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [projectFilters, setProjectFilters] = useState<ProjectFilters>({
    projectId,
    value: EMPTY_FILTERS,
  });
  const [loading, setLoading] = useState(false);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<QueryError | null>(null);
  const [eventsError, setEventsError] = useState<QueryError | null>(null);
  const summaryRequest = useRef(0);
  const eventsRequest = useRef(0);
  const currentProject = useRef(projectId);
  const summaryDataKey = useRef('');
  const eventsDataKey = useRef('');
  const currentSummaryKey = useRef('');
  const currentEventsKey = useRef('');
  currentProject.current = projectId;

  const timezone = useMemo(localTimezone, []);
  const filters = projectFilters.projectId === projectId ? projectFilters.value : EMPTY_FILTERS;
  const summaryKey = JSON.stringify([projectId, range, timezone, filters.actor || '']);
  const eventsKey = JSON.stringify([
    projectId,
    range,
    filters.category,
    filters.status,
    filters.actor || '',
  ]);
  currentSummaryKey.current = summaryKey;
  currentEventsKey.current = eventsKey;

  const loadSummary = useCallback(async () => {
    if (!enabled || !projectId) return;
    const request = ++summaryRequest.current;
    setLoading(true);
    setSummaryError(null);
    try {
      const value = await api.observabilitySummary(
        projectId,
        range,
        timezone,
        filters.actor || undefined,
      );
      if (
        request === summaryRequest.current &&
        currentProject.current === projectId &&
        currentSummaryKey.current === summaryKey
      ) {
        summaryDataKey.current = summaryKey;
        setSummary(value);
      }
    } catch (reason) {
      if (
        request === summaryRequest.current &&
        currentProject.current === projectId &&
        currentSummaryKey.current === summaryKey
      ) {
        summaryDataKey.current = '';
        setSummary(null);
        setSummaryError({ key: summaryKey, message: errorMessage(reason) });
      }
    } finally {
      if (
        request === summaryRequest.current &&
        currentProject.current === projectId &&
        currentSummaryKey.current === summaryKey
      ) {
        setLoading(false);
      }
    }
  }, [enabled, filters.actor, projectId, range, summaryKey, timezone]);

  const loadEvents = useCallback(
    async (cursor?: string, append = false) => {
      if (!enabled || !projectId) return;
      const request = ++eventsRequest.current;
      const canAppend = append && eventsDataKey.current === eventsKey;
      setEventsLoading(true);
      setEventsError(null);
      try {
        const page = await api.observabilityEvents(projectId, {
          range,
          category: filters.category || undefined,
          status: filters.status || undefined,
          actor: filters.actor || undefined,
          cursor,
        });
        if (
          request === eventsRequest.current &&
          currentProject.current === projectId &&
          currentEventsKey.current === eventsKey
        ) {
          eventsDataKey.current = eventsKey;
          setEvents((current) => (canAppend ? [...current, ...page.items] : page.items));
          setNextCursor(page.next_cursor);
        }
      } catch (reason) {
        if (
          request === eventsRequest.current &&
          currentProject.current === projectId &&
          currentEventsKey.current === eventsKey
        ) {
          if (!canAppend) {
            eventsDataKey.current = '';
            setEvents([]);
            setNextCursor(null);
          }
          setEventsError({ key: eventsKey, message: errorMessage(reason) });
        }
      } finally {
        if (
          request === eventsRequest.current &&
          currentProject.current === projectId &&
          currentEventsKey.current === eventsKey
        ) {
          setEventsLoading(false);
        }
      }
    },
    [enabled, eventsKey, filters.actor, filters.category, filters.status, projectId, range],
  );

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  useEffect(() => {
    if (enabled) return;
    summaryRequest.current += 1;
    eventsRequest.current += 1;
    summaryDataKey.current = '';
    eventsDataKey.current = '';
    setSummary(null);
    setEvents([]);
    setNextCursor(null);
    setSummaryError(null);
    setEventsError(null);
    setLoading(false);
    setEventsLoading(false);
  }, [enabled]);

  const refresh = useCallback(async () => {
    await Promise.all([loadSummary(), loadEvents()]);
  }, [loadEvents, loadSummary]);

  const currentSummaryError = summaryError?.key === summaryKey ? summaryError.message : '';
  const currentEventsError = eventsError?.key === eventsKey ? eventsError.message : '';
  const summaryReady = summaryDataKey.current === summaryKey;
  const eventsReady = eventsDataKey.current === eventsKey;

  return {
    range,
    setRange,
    summary: summaryReady ? summary : null,
    events: eventsReady ? events : [],
    nextCursor: eventsReady ? nextCursor : null,
    filters,
    setFilters: (value: EventFilters) => setProjectFilters({ projectId, value }),
    loading: Boolean(enabled && projectId && (loading || (!summaryReady && !currentSummaryError))),
    eventsLoading: Boolean(
      enabled && projectId && (eventsLoading || (!eventsReady && !currentEventsError)),
    ),
    error: [currentSummaryError, currentEventsError].filter(Boolean).join(' · '),
    refresh,
    loadMore: () => (eventsReady && nextCursor ? loadEvents(nextCursor, true) : Promise.resolve()),
  };
}
