import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import RequirementInput from './RequirementInput';

afterEach(cleanup);

test('quick demo (2 cases) is the default and is sent with the requirement', () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<RequirementInput onSubmit={onSubmit} busy={false} />);
  expect(screen.getByLabelText('Test cases').value).toBe('2');
  fireEvent.change(screen.getByLabelText(/Describe the behaviour/), { target: { value: 'A shopper checks out.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run AutoPilot-QA' }));
  expect(onSubmit).toHaveBeenCalledWith('A shopper checks out.', 2);
});

test('the full analysis sends no limit', () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<RequirementInput onSubmit={onSubmit} busy={false} />);
  fireEvent.change(screen.getByLabelText('Test cases'), { target: { value: 'full' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run AutoPilot-QA' }));
  expect(onSubmit).toHaveBeenCalledWith(expect.any(String), null);
});

test('offers the viva prompts as one-click examples', () => {
  render(<RequirementInput onSubmit={vi.fn()} busy={false} />);
  expect(screen.getAllByRole('button').filter((b) => b.className.includes('chip')).length).toBeGreaterThanOrEqual(3);
});
