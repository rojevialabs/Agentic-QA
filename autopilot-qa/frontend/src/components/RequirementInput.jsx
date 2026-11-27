import { useState } from 'react';

const EXAMPLES = [
  'User can search for a product, add it to cart, and complete checkout.',
  'Payment is declined when the card is expired.',
  'User can log in with valid credentials and sees an error with a wrong password.',
];

export default function RequirementInput({ onSubmit, busy }) {
  const [text, setText] = useState(EXAMPLES[0]);
  const [cases, setCases] = useState('2'); // quick demo by default: fewer cases means a faster run
  const [error, setError] = useState(null);

  async function submit(event) {
    event.preventDefault();
    setError(null);
    try {
      await onSubmit(text.trim(), cases === 'full' ? null : Number(cases));
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-head"><h2>Requirement</h2></div>
      <label htmlFor="requirement" className="caption">Describe the behaviour to test in plain English.</label>
      <textarea id="requirement" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} />
      <div className="row">
        <button className="primary" type="submit" disabled={busy || !text.trim()}>{busy ? 'Running…' : 'Run AutoPilot-QA'}</button>
        <label htmlFor="max-cases" className="caption" style={{ margin: 0 }}>Test cases</label>
        <select id="max-cases" value={cases} onChange={(e) => setCases(e.target.value)} disabled={busy}>
          <option value="2">2 · quick demo (one positive, one negative)</option>
          <option value="3">3</option>
          <option value="full">Up to 6 · full analysis</option>
        </select>
        {EXAMPLES.map((example) => (
          <button key={example} type="button" className="chip" onClick={() => setText(example)} disabled={busy}>
            {example.split(',')[0].replace(/\.$/, '')}
          </button>
        ))}
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
    </form>
  );
}
