import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';
import { I18nProvider } from './i18n';
import { ProjectName } from './ProjectName';
import type { Project } from './types';

const project: Project = {
  id: 'p1',
  name: 'Demo',
  cause: '',
  principles: [],
  objectives: [],
  profile_version: 1,
};
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});
function mount(canRename = true, refresh = vi.fn().mockResolvedValue(undefined)) {
  localStorage.setItem('dduo.dashboard.language', 'en');
  return {
    ...render(
      <I18nProvider>
        <ProjectName project={project} canRename={canRename} refresh={refresh} />
      </I18nProvider>,
    ),
    refresh,
  };
}
it('keeps the name read-only for members', () => {
  mount(false);
  expect(screen.getByRole('heading')).toHaveTextContent('Demo');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
it('saves only a valid, explicit change, then refreshes', async () => {
  const rename = vi.spyOn(api, 'renameProject').mockResolvedValue({ ...project, name: 'Orchard' });
  const { refresh } = mount();
  const title = screen.getByRole('button', { name: 'Demo — Rename project' });
  expect(title).toHaveTextContent('Demo');
  expect(title.closest('h1')).toBe(screen.getByRole('heading', { name: 'Demo' }));
  expect(title.querySelector('svg')).toBeNull();
  fireEvent.click(title);
  const input = screen.getByRole('textbox');
  expect(input).toHaveFocus();
  expect(input).toHaveAttribute('maxlength', '200');
  fireEvent.change(input, { target: { value: '  ' } });
  expect(screen.getByRole('button', { name: 'Save name' })).toBeDisabled();
  const form = input.closest('form');
  if (!form) throw new Error('Missing rename form');
  fireEvent.submit(form);
  expect(rename).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: ' Orchard ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save name' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(rename).toHaveBeenCalledWith('p1', 'Orchard', 1);
  await waitFor(() => expect(screen.queryByRole('textbox')).not.toBeInTheDocument());
  expect(screen.getByRole('button', { name: 'Demo — Rename project' })).toHaveFocus();
});
it('cancels without mutation with Escape or Cancel', () => {
  const rename = vi.spyOn(api, 'renameProject');
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Demo — Rename project' }));
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
  expect(screen.getByRole('button', { name: 'Demo — Rename project' })).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Demo — Rename project' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('button', { name: 'Demo — Rename project' })).toHaveFocus();
  expect(rename).not.toHaveBeenCalled();
});
it('preserves the draft and refreshes after a conflict; reports other errors', async () => {
  vi.spyOn(api, 'renameProject')
    .mockRejectedValueOnce(new ApiError('Conflict', 409))
    .mockRejectedValueOnce(new Error('Offline'));
  const { refresh } = mount();
  fireEvent.click(screen.getByRole('button', { name: 'Demo — Rename project' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Orchard' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save name' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('The project changed');
  expect(refresh).toHaveBeenCalledOnce();
  expect(screen.getByRole('textbox')).toHaveValue('Orchard');
  fireEvent.click(screen.getByRole('button', { name: 'Save name' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not rename'));
});
