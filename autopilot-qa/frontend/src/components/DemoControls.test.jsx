import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import DemoControls from './DemoControls';

const STATE = {
  seeded_changes: [],
  threshold: 0.8,
  changes: [
    { id: 'catalog.add_button_renamed', label: 'benign_ui_change', description: 'Add to Cart renamed to Add to Bag' },
    { id: 'catalog.add_button_wishlist', label: 'real_defect', description: 'Replaced by Add to Wishlist' },
  ],
  selector_maps: [
    { version: 1, status: 'active', created_by: 'seed', healed: [] },
    { version: 2, status: 'active', created_by: 'healer', healed: ['catalog.addToCartButton: #add-to-cart-btn -> #add-to-bag-btn'] },
  ],
};

function mockFetch() {
  const calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    calls.push({ url, method: options.method ?? 'GET', body: options.body ? JSON.parse(options.body) : undefined });
    return { ok: true, json: async () => (url.endsWith('/demo/state') ? STATE : { archived: 1 }) };
  }));
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test('lists the seeded changes and the selector-map versions', async () => {
  mockFetch();
  render(<DemoControls runId="run-1" busy={false} onReexecute={() => {}} />);
  expect(await screen.findByRole('option', { name: /Add to Cart renamed to Add to Bag/ })).toBeTruthy();
  expect(screen.getByRole('group', { name: 'Real defects (must be reported)' })).toBeTruthy();
  expect(screen.getByText(/v2 active · catalog\.addToCartButton: #add-to-cart-btn -> #add-to-bag-btn/)).toBeTruthy();
});

test('applies the chosen change, resets healing and re-executes with a one-off threshold', async () => {
  const calls = mockFetch();
  const onReexecute = vi.fn();
  render(<DemoControls runId="run-1" busy={false} onReexecute={onReexecute} />);
  fireEvent.change(await screen.findByLabelText('UI change on ShopLite'), { target: { value: 'catalog.add_button_renamed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply change' }));
  await waitFor(() => expect(calls.some((c) => c.url.endsWith('/demo/seeded-changes'))).toBe(true));
  expect(calls.find((c) => c.url.endsWith('/demo/seeded-changes')).body).toEqual({ seeded_changes: ['catalog.add_button_renamed'] });

  fireEvent.click(screen.getByRole('button', { name: 'Reset healing' }));
  await waitFor(() => expect(calls.some((c) => c.url.endsWith('/demo/reset-healing'))).toBe(true));

  fireEvent.change(screen.getByLabelText('Healing threshold'), { target: { value: '0.99' } });
  fireEvent.click(screen.getByRole('button', { name: 'Re-execute with this threshold' }));
  expect(onReexecute).toHaveBeenCalledWith(0.99);
});
