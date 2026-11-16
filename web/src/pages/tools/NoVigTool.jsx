import { useMemo, useState } from 'react';
import ToolLayout, { Field, ResetButton, ResultRow } from './ToolLayout.jsx';
import {
  decimalFromImplied, decimalToAmerican, fairProbs, fmtAmerican, fmtDecimal, fmtPct,
  holdFromOverround, impliedFromAmerican, parseAmerican,
} from '../../tools/oddsMath.js';

export default function NoVigTool() {
  const [threeWay, setThreeWay] = useState(false);
  const [side1, setSide1] = useState('-150');
  const [side2, setSide2] = useState('+130');
  const [draw, setDraw] = useState('+250');

  const raw = threeWay
    ? [parseAmerican(side1), parseAmerican(draw), parseAmerican(side2)]
    : [parseAmerican(side1), parseAmerican(side2)];
  const implied = raw.map((a) => (a == null ? null : impliedFromAmerican(a)));
  const valid = implied.every((p) => p != null);
  const overround = valid ? implied.reduce((s, p) => s + p, 0) : null;
  const fair = valid ? fairProbs(implied) : null;
  const fairDec = fair ? fair.map(decimalFromImplied) : null;
  const fairAm = fairDec ? fairDec.map(decimalToAmerican) : null;
  const vig = overround == null ? null : holdFromOverround(overround);

  const guide = useMemo(() => (
    <>
      <h2>What are no-vig (fair) odds?</h2>
      <p>
        Sportsbook prices include a margin. No-vig, or fair odds, remove that margin by renormalizing each
        side&apos;s implied probability so the market sums to 100%. Sharps use fair odds as a cleaner estimate of
        the market&apos;s true probability before comparing to a model like PredictionLab.
      </p>
      <h2>Formulas</h2>
      <p>Compute raw implied probabilities Pᵢ. P_total = Σ Pᵢ. Fair probability for side i is Pᵢ / P_total. Fair decimal odds = 1 / P_fair; convert that decimal back to American odds.</p>
      <h2>Example</h2>
      <p>
        −150 / +130 implies about 60.0% and 43.5% (103.5% overround). Fair probabilities are roughly 58.0% and
        42.0%, or about −138 / +138 after the vig is removed.
      </p>
      <h2>FAQ</h2>
      <h3>Is no-vig the same as true probability?</h3>
      <p>It is the market price with vig removed — still a market view, not a model forecast.</p>
      <h3>When should I use three-way mode?</h3>
      <p>For soccer 1X2 and other three-outcome markets.</p>
      <h3>How do I use fair odds with a model?</h3>
      <p>Compare PredictionLab win probability to the fair probability; positive difference is model edge.</p>
      <h3>What if the market is badly skewed?</h3>
      <p>Fair odds still renormalize; very lopsided books can distort the estimate.</p>
    </>
  ), []);

  return (
    <ToolLayout
      title="No-Vig Calculator"
      subtitle="Strip the sportsbook margin to estimate fair win probabilities and fair American/decimal odds."
      guide={guide}
      faqJsonLd={{
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        mainEntity: [
          { '@type': 'Question', name: 'Is no-vig the same as true probability?', acceptedAnswer: { '@type': 'Answer', text: 'No-vig removes the book margin from market prices; it is still a market view, not a model forecast.' } },
          { '@type': 'Question', name: 'When should I use three-way mode?', acceptedAnswer: { '@type': 'Answer', text: 'For soccer 1X2 and other three-outcome markets.' } },
          { '@type': 'Question', name: 'How do I use fair odds with a model?', acceptedAnswer: { '@type': 'Answer', text: 'Compare a model win probability to the fair probability; the difference is model edge vs the market.' } },
          { '@type': 'Question', name: 'How is vig removed?', acceptedAnswer: { '@type': 'Answer', text: 'Divide each implied probability by the sum of all implied probabilities.' } },
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
        <ResultRow label="Side 1 fair win probability" value={fmtPct(fair?.[0])} />
        <ResultRow label="Side 1 fair odds" value={`${fmtAmerican(fairAm?.[0])} · ${fmtDecimal(fairDec?.[0])}`} />
        {threeWay && (
          <>
            <ResultRow label="Draw fair win probability" value={fmtPct(fair?.[1])} />
            <ResultRow label="Draw fair odds" value={`${fmtAmerican(fairAm?.[1])} · ${fmtDecimal(fairDec?.[1])}`} />
            <ResultRow label="Side 2 fair win probability" value={fmtPct(fair?.[2])} />
            <ResultRow label="Side 2 fair odds" value={`${fmtAmerican(fairAm?.[2])} · ${fmtDecimal(fairDec?.[2])}`} />
          </>
        )}
        {!threeWay && (
          <>
            <ResultRow label="Side 2 fair win probability" value={fmtPct(fair?.[1])} />
            <ResultRow label="Side 2 fair odds" value={`${fmtAmerican(fairAm?.[1])} · ${fmtDecimal(fairDec?.[1])}`} />
          </>
        )}
        <ResultRow label="Vig / hold removed" value={fmtPct(vig)} />
      </div>
      <ResetButton onClick={() => { setThreeWay(false); setSide1('-150'); setSide2('+130'); setDraw('+250'); }} />
    </ToolLayout>
  );
}
