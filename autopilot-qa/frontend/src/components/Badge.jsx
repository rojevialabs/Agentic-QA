// Status is never shown by colour alone: every badge pairs a tone dot with an icon and a text label.
const TONES = {
  pass: ['good', '✓', 'Pass'],
  fail: ['critical', '✕', 'Fail'],
  flaky: ['warning', '!', 'Flaky'],
  completed: ['good', '✓', 'Completed'],
  escalated: ['critical', '✕', 'Escalated'],
  failed: ['critical', '✕', 'Failed'],
  running: ['accent', '●', 'Running'],
  pending: ['', '○', 'Pending'],
  idle: ['', '○', 'Idle'],
  working: ['accent', '●', 'Working'],
  retrying: ['warning', '↻', 'Retrying'],
  done: ['good', '✓', 'Done'],
  high: ['critical', '▲', 'High'],
  medium: ['serious', '■', 'Medium'],
  low: ['', '▼', 'Low'],
  healed: ['accent', '⚕', 'Healed'],
};

export default function Badge({ status, label }) {
  const [tone, icon, text] = TONES[status] ?? ['', '•', status];
  const pulse = status === 'working' || status === 'running';
  return (
    <span className="badge" title={label ?? text}>
      <span className={`dot ${tone} ${pulse ? 'pulse' : ''}`} aria-hidden="true" />
      <span aria-hidden="true">{icon}</span>
      {label ?? text}
    </span>
  );
}
