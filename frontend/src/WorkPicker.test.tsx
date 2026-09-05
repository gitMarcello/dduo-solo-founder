import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from './api';
import type { Task } from './types';
import { WorkPicker } from './WorkPicker';

afterEach(() => vi.restoreAllMocks());

it('finds and selects work beyond the first project page without changing existing selection', async () => {
  const choose = vi.fn();
  vi.spyOn(api, 'listWork').mockImplementation(async (_project, options = {}) => ({
    items: [
      {
        id: `t${options.offset ?? 0}`,
        title: options.q ? 'Matching epic' : `Item ${options.offset ?? 0}`,
      } as Task,
    ],
    total: options.q ? 1 : 51,
    limit: 25,
    offset: options.offset ?? 0,
  }));
  render(<WorkPicker projectId="p1" kind="epic" selected={['t0']} onChoose={choose} />);
  expect(await screen.findByRole('button', { name: '✓ Item 0' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await waitFor(() => expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Item 25' }));
  expect(choose).toHaveBeenCalledWith(expect.objectContaining({ id: 't25' }));
  fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
  await screen.findByRole('button', { name: '✓ Item 0' });
  fireEvent.change(screen.getByLabelText('Search the whole project'), {
    target: { value: 'release' },
  });
  await screen.findByRole('button', { name: '✓ Matching epic' });
  expect(api.listWork).toHaveBeenLastCalledWith(
    'p1',
    expect.objectContaining({ q: 'release', kind: 'epic', offset: 0, placement: 'all' }),
  );
});

it('reports search failure and recovers when the query changes', async () => {
  vi.spyOn(api, 'listWork')
    .mockRejectedValueOnce(new Error('Offline'))
    .mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  render(<WorkPicker projectId="p1" selected={[]} onChoose={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Offline');
  fireEvent.change(screen.getByLabelText('Search the whole project'), {
    target: { value: 'retry' },
  });
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
});
