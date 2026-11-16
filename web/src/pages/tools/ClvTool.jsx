import { useMemo, useState } from 'react';
import ToolLayout, { Field, ResetButton, ResultRow } from './ToolLayout.jsx';
import {
  fairProbs, fmtPct, impliedFromAmerican, parseAmerican,
} from '../../tools/oddsMath.js';

export default function ClvTool() {
  const [taken, setTaken] = useState('-110');
  const [close, setClose] = useState('-130');
  const [mode, setMode] = useState('simple'); // simple | novig
  const [oppClose, setOppClose] = useState('+110');

  const aTaken = parseAmerican(taken);
  const aClose = parseAmerican(close);
  const aOpp = parseAmerican(oppClose);
  const pTaken = aTaken == null ? null : impliedFromAmerican(aTaken);
  let pClose = aClose == null ? null : impliedFromAmerican(aClose);
  if (mode === 'novig' && aClose != null && aOpp != null) {
    const fair = fairProbs([impliedFromAmerican(aClose), impliedFromAmerican(aOpp)]);
    pClose = fair ? fair[0] : null;
  }
  const clvRel = pTaken && pClose ? (pClose - pTaken) / pTaken : null;
  const clvRaw = pTaken != null && pClose != null ? pClose - pTaken : null;
  const tone = clvRel == null ? null : (clvRel > 0 ? 'good' : clvRel < 0 ? 'bad' : null);

  const guide = useMemo(() => (
    <>
      <h2>What is closing line value (CLV)?</h2>
      <p>
        Closing line value measures whether you beat the market&apos;s final price. If you bet −110 and the
        market closes −130 on your side, you captured a better number than the close — positive CLV. Long-run
        positive CLV is one of the strongest signs a process has edge, even before results variance settles.
      </p>
      <h2>Formulas</h2>
      <p>P_taken = implied probability of odds taken. P_close = implied probability of closing odds (or no-vig fair close if the opposing close is supplied). CLV % = (P_close − P_taken) / P_taken × 100. Also show the raw gap P_close − P_taken.</p>
      <h2>Example</h2>
      <p>
        Odds taken −110 ≈ 52.38%. Closing −130 ≈ 56.52%. Relative CLV ≈ +7.9%. With an opposing close of +110,
        no-vig closing probability on your side is a bit lower than the raw −130 imply — toggle modes to compare.
      </p>
      <h2>FAQ</h2>
      <h3>Is positive CLV a win?</h3>
      <p>CLV is about price quality, not the game result. One bet can lose and still show positive CLV.</p>
      <h3>When should I use no-vig close?</h3>
      <p>When you have both closing sides and want the vig-free close probability.</p>
      <h3>What if the close moved against me?</h3>
      <p>Negative CLV means you got a worse number than the final market.</p>
      <h3>How does this relate to PredictionLab?</h3>
      <p>Model probabilities help decide when a price is good enough to bet before the close moves.</p>
    </>
  ), []);

  return (
    <ToolLayout
      title="CLV Calculator"
      subtitle="Compare the odds you took to the closing line and see closing line value as a percent."
      guide={guide}
      faqJsonLd={{
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        mainEntity: [
          { '@type': 'Question', name: 'Is positive CLV a win?', acceptedAnswer: { '@type': 'Answer', text: 'CLV measures price quality versus the close, not whether the bet won.' } },
          { '@type': 'Question', name: 'When should I use no-vig close?', acceptedAnswer: { '@type': 'Answer', text: 'When you have both closing sides and want a vig-free closing probability.' } },
          { '@type': 'Question', name: 'What does negative CLV mean?', acceptedAnswer: { '@type': 'Answer', text: 'You got a worse number than the market’s final price on that side.' } },
          { '@type': 'Question', name: 'How is CLV % calculated?', acceptedAnswer: { '@type': 'Answer', text: 'CLV % = (closing implied probability − taken implied probability) ÷ taken implied probability × 100.' } },
        ],
      }}
    >
      <div className="pl-tools-grid">
        <Field label="Odds taken (American)">
          <input value={taken} onChange={(e) => setTaken(e.target.value)} inputMode="numeric" />
        </Field>
        <Field label="Closing line odds (American)">
          <input value={close} onChange={(e) => setClose(e.target.value)} inputMode="numeric" />
        </Field>
      </div>
      <label className="pl-tools-toggle">
        <input
          type="checkbox"
          checked={mode === 'novig'}
          onChange={(e) => setMode(e.target.checked ? 'novig' : 'simple')}
        />
        <span>No-vig closing line (use opposing close)</span>
      </label>
      {mode === 'novig' && (
        <Field label="Opposing closing side odds (American)">
          <input value={oppClose} onChange={(e) => setOppClose(e.target.value)} inputMode="numeric" />
        </Field>
      )}
      {(pTaken == null || pClose == null) && (
        <p className="pl-tools-error" role="alert">Enter valid American odds for taken and closing prices.</p>
      )}
      <div className="pl-tools-results">
        <ResultRow label="Implied probability at bet" value={fmtPct(pTaken)} />
        <ResultRow
          label={mode === 'novig' ? 'True no-vig closing probability' : 'Closing implied probability'}
          value={fmtPct(pClose)}
        />
        <ResultRow label="Raw probability difference" value={fmtPct(clvRaw)} />
        <ResultRow
          label="CLV %"
          value={clvRel == null ? '—' : `${clvRel >= 0 ? '+' : ''}${(clvRel * 100).toFixed(2)}%`}
          tone={tone}
        />
      </div>
      {tone && (
        <p className={`pl-tools-badge is-${tone}`}>
          {tone === 'good' ? 'Positive CLV — you beat the close' : 'Negative CLV — close beat your price'}
        </p>
      )}
      <ResetButton onClick={() => { setTaken('-110'); setClose('-130'); setOppClose('+110'); setMode('simple'); }} />
    </ToolLayout>
  );
}
