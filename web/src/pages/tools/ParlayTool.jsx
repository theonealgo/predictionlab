import { useMemo, useState } from 'react';
import ToolLayout, { Field, ResetButton, ResultRow } from './ToolLayout.jsx';
import {
  americanToDecimal, combineDecimals, decimalToAmerican, fmtAmerican, fmtDecimal,
  fmtMoney, fmtPct, impliedFromDecimal, parseAmerican,
} from '../../tools/oddsMath.js';

function blankLeg(odds = '-110') {
  return { odds };
}

export default function ParlayTool() {
  const [stake, setStake] = useState('25');
  const [legs, setLegs] = useState([blankLeg('-110'), blankLeg('-110'), blankLeg('+150')]);

  const parsed = legs.map((leg) => {
    const a = parseAmerican(leg.odds);
    const d = a == null ? null : americanToDecimal(a);
    return { a, d, p: d == null ? null : impliedFromDecimal(d) };
  });
  const combinedDec = parsed.every((x) => x.d != null)
    ? combineDecimals(parsed.map((x) => x.d))
    : null;
  const combinedAm = combinedDec == null ? null : decimalToAmerican(combinedDec);
  const winP = combinedDec == null ? null : impliedFromDecimal(combinedDec);
  const stakeN = Number(stake);
  const payout = Number.isFinite(stakeN) && stakeN > 0 && combinedDec ? stakeN * combinedDec : null;
  const profit = payout != null ? payout - stakeN : null;
  const invalid = parsed.some((x) => x.a == null);

  const guide = useMemo(() => (
    <>
      <h2>What is a parlay calculator?</h2>
      <p>
        A parlay multiplies several independent bets into one ticket. Every leg must win for the parlay
        to pay. Bettors use a parlay calculator to convert each American price to decimal odds, multiply
        those decimals, and see the combined American odds, approximate win probability, payout, and profit
        before placing the wager.
      </p>
      <h2>Formulas</h2>
      <p>Convert each leg to decimal Dᵢ. Combined decimal D_total = product of Dᵢ. Implied win probability ≈ 1 / D_total. Payout = stake × D_total; profit = payout − stake.</p>
      <h2>Example</h2>
      <p>
        Three legs at −110, −110, and +150 become about 1.909 × 1.909 × 2.50 ≈ 9.12 decimal. A $25 stake
        returns roughly $228 with about $203 profit if all three cash — and roughly an 11% combined implied
        win chance before accounting for correlation.
      </p>
      <h2>FAQ</h2>
      <h3>Why multiply decimal odds?</h3>
      <p>Each decimal is a payout multiple. Independent legs compound by multiplication.</p>
      <h3>Is implied probability exact?</h3>
      <p>1/D_total assumes independence. Correlated legs (same game) need a different model.</p>
      <h3>Can I mix favorites and dogs?</h3>
      <p>Yes. Convert every American price to decimal first, then multiply.</p>
      <h3>What if a leg is invalid?</h3>
      <p>American odds must be ≤ −100 or ≥ +100. Fix that leg before trusting the combined price.</p>
    </>
  ), []);

  const faqJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: [
      { '@type': 'Question', name: 'Why multiply decimal odds?', acceptedAnswer: { '@type': 'Answer', text: 'Each decimal is a payout multiple; independent legs compound by multiplication.' } },
      { '@type': 'Question', name: 'Is parlay implied probability exact?', acceptedAnswer: { '@type': 'Answer', text: '1 divided by combined decimal assumes independent legs.' } },
      { '@type': 'Question', name: 'Can parlays mix favorites and underdogs?', acceptedAnswer: { '@type': 'Answer', text: 'Yes — convert each American price to decimal, then multiply.' } },
      { '@type': 'Question', name: 'What American odds are valid?', acceptedAnswer: { '@type': 'Answer', text: 'Odds must be less than or equal to −100 or greater than or equal to +100.' } },
    ],
  };

  return (
    <ToolLayout
      title="Parlay Calculator"
      subtitle="Add American-odds legs to see combined odds, implied win chance, payout, and profit."
      guide={guide}
      faqJsonLd={faqJsonLd}
    >
      <Field label="Bet amount ($)">
        <input value={stake} onChange={(e) => setStake(e.target.value)} inputMode="decimal" />
      </Field>
      <div className="pl-tools-legs">
        {legs.map((leg, i) => (
          <div className="pl-tools-leg" key={`leg-${i}`}>
            <Field label={`Leg ${i + 1} American odds`} hint={parsed[i].d ? `Decimal ${fmtDecimal(parsed[i].d)} · ${fmtPct(parsed[i].p)}` : 'Enter odds like −110'}>
              <input
                value={leg.odds}
                onChange={(e) => {
                  const next = [...legs];
                  next[i] = { odds: e.target.value };
                  setLegs(next);
                }}
                inputMode="numeric"
              />
            </Field>
            {legs.length > 2 && (
              <button
                type="button"
                className="pl-tools-remove"
                onClick={() => setLegs(legs.filter((_, j) => j !== i))}
              >
                Remove
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="pl-tools-actions">
        <button type="button" className="pl-tools-add" onClick={() => setLegs([...legs, blankLeg('-110')])}>
          + Add leg
        </button>
        <ResetButton onClick={() => { setStake('25'); setLegs([blankLeg('-110'), blankLeg('-110'), blankLeg('+150')]); }} />
      </div>
      {invalid && <p className="pl-tools-error" role="alert">Every leg needs valid American odds (≤ −100 or ≥ +100).</p>}
      <div className="pl-tools-results">
        <ResultRow label="Combined American odds" value={fmtAmerican(combinedAm)} />
        <ResultRow label="Combined decimal odds" value={fmtDecimal(combinedDec)} />
        <ResultRow label="Implied win probability" value={fmtPct(winP)} />
        <ResultRow label="Total payout" value={fmtMoney(payout)} />
        <ResultRow label="Net profit" value={fmtMoney(profit)} />
      </div>
    </ToolLayout>
  );
}
