import { useEffect, useRef, useState } from 'react';
import { ApiError, api } from './api';
import { useI18n } from './i18n';
import type { Project } from './types';

export function ProjectName({
  project,
  canRename,
  refresh,
}: {
  project: Project;
  canRename: boolean;
  refresh: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(project.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const titleButton = useRef<HTMLButtonElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const wasEditing = useRef(false);

  useEffect(() => {
    if (editing && canRename) {
      nameInput.current?.focus();
      nameInput.current?.select();
    } else if (wasEditing.current) titleButton.current?.focus();
    wasEditing.current = editing && canRename;
  }, [editing, canRename]);

  if (!editing || !canRename)
    return (
      <div className="project-name">
        <h1 title={project.name}>
          {canRename ? (
            <button
              ref={titleButton}
              type="button"
              className="project-name-trigger"
              aria-label={`${project.name} — ${t('Rename project')}`}
              title={t('Rename project')}
              onClick={() => {
                setName(project.name);
                setError('');
                setEditing(true);
              }}
            >
              {project.name}
            </button>
          ) : (
            project.name
          )}
        </h1>
      </div>
    );

  return (
    <form
      className="project-name-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy || !name.trim()) return;
        setBusy(true);
        setError('');
        try {
          await api.renameProject(project.id, name.trim(), project.profile_version);
          await refresh();
          setEditing(false);
        } catch (failure) {
          if (failure instanceof ApiError && failure.status === 409) {
            await refresh();
            setError(t('The project changed. Review the name and save again.'));
          } else setError(t('Could not rename the project. Try again.'));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="project-name-fields">
        <input
          ref={nameInput}
          aria-label={t('Project name')}
          maxLength={200}
          value={name}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape' && !busy) setEditing(false);
          }}
        />
        <button className="primary" type="submit" disabled={busy || !name.trim()}>
          {t(busy ? 'Saving…' : 'Save name')}
        </button>
        <button
          className="secondary"
          type="button"
          disabled={busy}
          onClick={() => setEditing(false)}
        >
          {t('Cancel')}
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
