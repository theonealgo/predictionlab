import { useMemo, useState } from 'react';
import ToolLayout, { Field, ResetButton, ResultRow } from './ToolLayout.jsx';
import {
  americanToDecimal, decimalFromImplied, decimalToAmerican, fmtAmerican, fmtDecimal,
  fmtMoney, fmtPct, fractionFromDecimal, impliedFromAmerican, impliedFromDecimal,
  parseAmerican, parseDecimal, parseFraction, parseProbPct,
} from '../../tools/oddsMath.js';

const DEFAULTS = { american: '-110', decimal: '1.909', fraction: '10/11', prob: '52.38', stake: '100' };

function faqLd() {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: [
      {
        '@type': 'Question',
        name: 'How do you convert American odds to implied probability?',
        acceptedAnswer: {
          '@type': 'Answer',
          text: 'For negative odds O, implied probability is |O| / (|O| + 100). For positive odds O, it is 100 / (O + 100).',
        },
      },
      {
        '@type': 'Question',
        name: 'What are decimal odds?',
        acceptedAnswer: {
          '@type': 'Answer',
          text: 'Decimal odds are the total payout multiple of your stake, including stake. Implied probability is 1 divided by the decimal price.',
        },
      },
      {
        '@type': 'Question',
        name: 'Why do sportsbooks show different formats?',
        acceptedAnswer: {
          '@type': 'Answer',
          text: 'US books often use American odds; many international markets use decimal or fractional. Converting keeps the same price comparable across books.',
        },
      },
      {
        '@type': 'Question',
        name: 'Does implied probability include the vig?',
        acceptedAnswer: {
          '@type': 'Answer',
          text: 'Yes. Raw implied probabilities from both sides of a market usually sum to more than 100%. Use the No-Vig Calculator to remove that margin.',
        },
      },
    ],
  };
}

export default function OddsConversionTool() {
  const [american, setAmerican] = useState(DEFAULTS.american);
  const [decimal, setDecimal] = useState(DEFAULTS.decimal);
  const [fraction, setFraction] = useState(DEFAULTS.fraction);
  const [prob, setProb] = useState(DEFAULTS.prob);
  const [stake, setStake] = useState(DEFAULTS.stake);
  const [error, setError] = useState('');

  const apply = (src, value) => {
    setError('');
    let a = null;
    let d = null;
    let p = null;
    if (src === 'american') {
      setAmerican(value);
      a = parseAmerican(value);
      if (a == null) {
        setError('American odds must be ≤ −100 or ≥ +100.');
        return;
      }
      d = americanToDecimal(a);
      p = impliedFromAmerican(a);
    } else if (src === 'decimal') {
      setDecimal(value);
      d = parseDecimal(value);
      if (d == null) {
        setError('Decimal odds must be greater than 1.00.');
        return;
      }
      a = decimalToAmerican(d);
      p = impliedFromDecimal(d);
    } else if (src === 'fraction') {
      setFraction(value);
      d = parseFraction(value);
      if (d == null) {
        setError('Enter fractional odds like 10/11 or 3/2.');
        return;
      }
      a = decimalToAmerican(d);
      p = impliedFromDecimal(d);
    } else {
      setProb(value);
      p = parseProbPct(value);
      if (p == null) {
        setError('Implied probability must be between 0 and 100 (not including ends).');
        return;
      }
      d = decimalFromImplied(p);
      a = decimalToAmerican(d);
    }
    setAmerican(fmtAmerican(a).replace('—', ''));
    setDecimal(fmtDecimal(d, 3));
    setFraction(fractionFromDecimal(d));
    setProb((p * 100).toFixed(2));
  };

  const stakeN = Number(stake);
  const d = parseDecimal(decimal);
  const payout = Number.isFinite(stakeN) && stakeN > 0 && d ? stakeN * d : null;
  const profit = payout != null ? payout - stakeN : null;

  const guide = useMemo(() => (
    <>
      <h2>What is an odds conversion calculator?</h2>
      <p>
        Sportsbooks quote the same price in different languages. American odds (for example −110 or +150),
        decimal odds (1.91 or 2.50), and fractional odds (10/11 or 3/2) are interchangeable views of one
        payout schedule. Sharp bettors convert every price into implied probability so they can compare
        books, estimate edge against a model, and size stakes consistently.
      </p>
      <h2>Formulas</h2>
      <h3>American → implied probability</h3>
      <p>If O is negative: |O| / (|O| + 100). If O is positive: 100 / (O + 100).</p>
      <h3>Decimal → implied probability</h3>
      <p>1 / Decimal. American → decimal: if O &lt; 0 then 1 + 100/|O|; if O &gt; 0 then 1 + O/100.</p>
      <h2>Example</h2>
      <p>
        A −110 favorite implies 52.38% and pays about 1.909× your stake. On a $100 bet the total return is
        roughly $190.91 and net profit is $90.91 if the bet wins. Switching the same price to +150 flips the
        market to an underdog at 40.00% implied probability and 2.50 decimal odds.
      </p>
      <h2>FAQ</h2>
      <h3>How do you convert American odds to implied probability?</h3>
      <p>Negative: |O|/(|O|+100). Positive: 100/(O+100).</p>
      <h3>What are decimal odds?</h3>
      <p>The total payout multiple including stake. Implied probability is 1 ÷ decimal.</p>
      <h3>Why convert formats?</h3>
      <p>So you can compare US and international books on the same probability scale.</p>
      <h3>Does implied probability include vig?</h3>
      <p>Yes. Both sides of a market usually sum above 100%. Remove that with the No-Vig Calculator.</p>
    </>
  ), []);

  return (
    <ToolLayout
      title="Odds Conversion Calculator"
      subtitle="Edit any field — American, decimal, fractional, or implied probability — and the others update instantly."
      guide={guide}
      faqJsonLd={faqLd()}
    >
      <div className="pl-tools-grid">
        <Field label="American odds" hint="Examples: −110, +150">
          <input value={american} onChange={(e) => apply('american', e.target.value)} inputMode="numeric" />
        </Field>
        <Field label="Decimal odds" hint="Examples: 1.91, 2.50">
          <input value={decimal} onChange={(e) => apply('decimal', e.target.value)} inputMode="decimal" />
        </Field>
        <Field label="Fractional odds" hint="Examples: 10/11, 3/2">
          <input value={fraction} onChange={(e) => apply('fraction', e.target.value)} />
        </Field>
        <Field label="Implied probability %" hint="Between 0 and 100">
          <input value={prob} onChange={(e) => apply('prob', e.target.value)} inputMode="decimal" />
        </Field>
        <Field label="Bet amount ($)" hint="Optional — shows payout and profit">
          <input value={stake} onChange={(e) => setStake(e.target.value)} inputMode="decimal" />
        </Field>
      </div>
      {error && <p className="pl-tools-error" role="alert">{error}</p>}
      <div className="pl-tools-results">
        <ResultRow label="Implied probability" value={fmtPct(parseProbPct(prob))} />
        <ResultRow label="Total payout" value={fmtMoney(payout)} />
        <ResultRow label="Net profit" value={fmtMoney(profit)} />
      </div>
      <ResetButton onClick={() => {
        setAmerican(DEFAULTS.american);
        setDecimal(DEFAULTS.decimal);
        setFraction(DEFAULTS.fraction);
        setProb(DEFAULTS.prob);
        setStake(DEFAULTS.stake);
        setError('');
      }}
      />
    </ToolLayout>
  );
}
