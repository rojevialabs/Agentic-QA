import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import ReasoningTrace from './ReasoningTrace';

const line = (id, text, action = {}) => ({ id, agent: 'triage_healer', text, action, timestamp: '2026-09-27T10:00:00Z' });

test('streams thoughts and renders the heal decision against the threshold', () => {
  render(<ReasoningTrace thoughts={[
    line('1', 'THOUGHT: The Add to Cart button is now Add to Bag.'),
    line('2', 'ACTION: applied', { selector_key: 'catalog.addToCartButton', broken_selector: '#add-to-cart-btn',
      new_locator: '#add-to-bag-btn', confidence: 0.9, llm_confidence: 0.9, threshold: 0.8, applied: true,
      verification: '1 match in scope' }),
  ]} />);
  expect(screen.getByText('THOUGHT: The Add to Cart button is now Add to Bag.')).toBeTruthy();
  expect(screen.getByText('#add-to-cart-btn → #add-to-bag-btn')).toBeTruthy();
  expect(screen.getByRole('img', { name: 'confidence 0.90, threshold 0.80' })).toBeTruthy();
  expect(screen.getByText('Applied')).toBeTruthy();
});

test('shows an empty state before the run streams anything', () => {
  render(<ReasoningTrace thoughts={[]} />);
  expect(screen.getByText(/Agent reasoning streams here/)).toBeTruthy();
});
