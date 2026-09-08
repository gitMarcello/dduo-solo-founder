import { useEffect, useState } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import type { Task, WorkPage } from './types';

/** A paginated project-wide chooser finds work outside the current board. */
export function WorkPicker({
  projectId,
  kind,
  selected,
  onChoose,
}: {
  projectId: string;
  kind?: 'task' | 'epic';
  selected: string[];
  onChoose: (task: Task) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<WorkPage<Task>>({ items: [], total: 0, limit: 25, offset: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError('');
      void api
        .listWork(projectId, { kind, scope: 'all', placement: 'all', q: query, offset, limit: 25 })
        .then((value) => {
          if (current) setPage(value);
        })
        .catch((reason) => {
          if (current) setError(reason instanceof Error ? reason.message : String(reason));
        })
        .finally(() => {
          if (current) setLoading(false);
        });
    }, 200);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [projectId, kind, query, offset]);
  return (
    <section className="work-picker" aria-label={t('Choose project work')}>
      <label>
        {t('Search the whole project')}
        <input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOffset(0);
          }}
        />
      </label>
      {loading && <p role="status">{t('Loading work…')}</p>}
      {error && <p role="alert">{error}</p>}
      {page.items.map((task) => (
        <button
          type="button"
          key={task.id}
          aria-pressed={selected.includes(task.id)}
          className="plan-work-option"
          onClick={() => onChoose(task)}
        >
          {selected.includes(task.id) ? '✓ ' : ''}
          {task.title}
        </button>
      ))}
      <div className="work-pagination">
        <button
          type="button"
          className="secondary"
          disabled={loading || !offset}
          onClick={() => setOffset((value) => Math.max(0, value - 25))}
        >
          {t('Previous page')}
        </button>
        <span>
          {page.total} {t('items')}
        </span>
        <button
          type="button"
          className="secondary"
          disabled={loading || offset + page.items.length >= page.total}
          onClick={() => setOffset((value) => value + 25)}
        >
          {t('Next page')}
        </button>
      </div>
    </section>
  );
}
