import { useMemo, useState } from 'react';
import ToolLayout, { Field, ResetButton, ResultRow } from './ToolLayout.jsx';
import { fmtPct, holdFromOverround, impliedFromAmerican, parseAmerican } from '../../tools/oddsMath.js';

export default function HoldTool() {
  const [threeWay, setThreeWay] = useState(false);
  const [side1, setSide1] = useState('-110');
  const [side2, setSide2] = useState('-110');
  const [draw, setDraw] = useState('+240');

  const a1 = parseAmerican(side1);
  const a2 = parseAmerican(side2);
  const aD = threeWay ? parseAmerican(draw) : null;
  const p1 = a1 == null ? null : impliedFromAmerican(a1);
  const p2 = a2 == null ? null : impliedFromAmerican(a2);
  const pD = aD == null ? null : impliedFromAmerican(aD);
  const probs = threeWay ? [p1, pD, p2] : [p1, p2];
  const valid = probs.every((p) => p != null);
  const overround = valid ? probs.reduce((s, p) => s + p, 0) : null;
  const hold = overround == null ? null : holdFromOverround(overround);
  const margin = overround == null ? null : overround - 1;

  const guide = useMemo(() => (
    <>
      <h2>What is sportsbook hold?</h2>
      <p>
        Hold (or vig) is the share of handle a book expects to keep from a balanced market. When both sides
        of a −110/−110 game are added up, implied probabilities total about 104.76%. That overround creates
        the book&apos;s margin. Sharp bettors watch hold to know how expensive a market is before hunting edges.
      </p>
      <h2>Formulas</h2>
      <p>Convert each outcome to implied probability Pᵢ. Overround = Σ Pᵢ. Hold % = (1 − 1/Overround) × 100. Bookmaker margin can also be shown as Overround − 1.</p>
      <h2>Example</h2>
      <p>
        Two −110 prices imply 52.38% + 52.38% = 104.76% overround. Hold ≈ 4.55%. A soccer 1X2 card with three
        prices that sum to 108% is a thicker market than a typical two-way NFL spread.
      </p>
      <h2>FAQ</h2>
      <h3>Is hold the same as vig?</h3>
      <p>They are related. Hold is the book&apos;s expected keep from overround; vig is the margin baked into prices.</p>
      <h3>Why use three-way mode?</h3>
      <p>Soccer and other 1X2 markets need home, draw, and away probabilities.</p>
      <h3>Does lower hold mean a better bet?</h3>
      <p>Lower hold means a cheaper market, not automatic edge — you still need a better probability than the price.</p>
      <h3>What if one side is missing?</h3>
      <p>Enter valid American odds for every outcome you include.</p>
    </>
  ), []);

  return (
    <ToolLayout
      title="Hold Calculator"
      subtitle="Enter both sides of a market (or 1X2) to see implied probabilities, overround, and sportsbook hold."
      guide={guide}
      faqJsonLd={{
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        mainEntity: [
          { '@type': 'Question', name: 'Is hold the same as vig?', acceptedAnswer: { '@type': 'Answer', text: 'They are related: hold comes from market overround; vig is the margin in the prices.' } },
          { '@type': 'Question', name: 'Why use three-way mode?', acceptedAnswer: { '@type': 'Answer', text: 'Soccer and other 1X2 markets need home, draw, and away probabilities.' } },
          { '@type': 'Question', name: 'Does lower hold mean a better bet?', acceptedAnswer: { '@type': 'Answer', text: 'Lower hold is a cheaper market, not automatic edge.' } },
          { '@type': 'Question', name: 'How is hold calculated?', acceptedAnswer: { '@type': 'Answer', text: 'Hold = (1 − 1/overround) × 100, where overround is the sum of implied probabilities.' } },
        ],
      }}
    >
      <label className="pl-tools-toggle">
        <input type="checkbox" checked={threeWay} onChange={(e) => setThreeWay(e.target.checked)} />
        <span>3-way market (Side 1 / Draw / Side 2)</span>
      </label>
      <div className="pl-tools-grid">
        <Field label="Side 1 American odds">
          <input value={side1} onChange={(e) => setSide1(e.target.value)} inputMode="numeric" />
        </Field>
        {threeWay && (
          <Field label="Draw American odds">
            <input value={draw} onChange={(e) => setDraw(e.target.value)} inputMode="numeric" />
          </Field>
        )}
        <Field label="Side 2 American odds">
          <input value={side2} onChange={(e) => setSide2(e.target.value)} inputMode="numeric" />
        </Field>
      </div>
      {!valid && <p className="pl-tools-error" role="alert">Enter valid American odds for every side.</p>}
      <div className="pl-tools-results">
        <ResultRow label="Side 1 implied probability" value={fmtPct(p1)} />
        {threeWay && <ResultRow label="Draw implied probability" value={fmtPct(pD)} />}
        <ResultRow label="Side 2 implied probability" value={fmtPct(p2)} />
        <ResultRow label="Total market overround" value={fmtPct(overround)} />
        <ResultRow label="Sportsbook hold %" value={fmtPct(hold)} />
        <ResultRow label="Bookmaker margin (overround − 1)" value={fmtPct(margin)} />
      </div>
      <ResetButton onClick={() => { setThreeWay(false); setSide1('-110'); setSide2('-110'); setDraw('+240'); }} />
    </ToolLayout>
  );
}
