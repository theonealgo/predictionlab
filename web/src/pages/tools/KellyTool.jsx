import { useMemo, useState } from 'react';
import ToolLayout, { Field, ResetButton, ResultRow } from './ToolLayout.jsx';
import {
  americanToDecimal, fmtMoney, fmtPct, kellyFraction, parseAmerican, parseProbPct,
} from '../../tools/oddsMath.js';

export default function KellyTool() {
  const [bankroll, setBankroll] = useState('1000');
  const [odds, setOdds] = useState('+120');
  const [winPct, setWinPct] = useState('50');
  const [mult, setMult] = useState(0.5);

  const a = parseAmerican(odds);
  const d = a == null ? null : americanToDecimal(a);
  const p = parseProbPct(winPct);
  const bank = Number(bankroll);
  const ev = d != null && p != null ? p * d - 1 : null;
  const full = d != null && p != null ? kellyFraction(d, p) : null;
  const adj = full == null ? null : Math.max(0, full * mult);
  const negEv = ev != null && ev <= 0;
  const stakePct = negEv ? 0 : adj;
  const stake$ = Number.isFinite(bank) && bank > 0 && stakePct != null ? bank * stakePct : null;
  const expectedProfit = stake$ != null && ev != null ? stake$ * ev : null;

  const guide = useMemo(() => (
    <>
      <h2>What is the Kelly criterion?</h2>
      <p>
        Kelly sizing chooses a stake fraction that maximizes long-run bankroll growth when you know your edge.
        In practice most bettors use half or quarter Kelly to reduce variance. Pair Kelly with a real win
        probability — PredictionLab model probabilities are a natural input for the estimated true win chance.
      </p>
      <h2>Formulas</h2>
      <p>Convert American odds to decimal D; b = D − 1. With win probability p and q = 1 − p: EV% = ((p × D) − 1) × 100. Full Kelly f* = (b·p − q) / b. Recommended stake % = max(0, f* × multiplier). Wager $ = bankroll × stake %.</p>
      <h2>Example</h2>
      <p>
        Bankroll $1,000, odds +120 (D = 2.20), estimated win probability 50%. EV = 10%. Full Kelly is about
        8.3% ($83). Half Kelly recommends roughly 4.2% ($42). If EV is zero or negative, stake is $0 — no bet.
      </p>
      <h2>FAQ</h2>
      <h3>Why not always use full Kelly?</h3>
      <p>Full Kelly is volatile and assumes your probability is exact. Half Kelly is the usual default.</p>
      <h3>Where do I get true win probability?</h3>
      <p>Use a calibrated model — PredictionLab win probabilities on the predictions pages are built for this.</p>
      <h3>What if Kelly is negative?</h3>
      <p>That means negative EV at the posted price. Do not bet.</p>
      <h3>Does Kelly guarantee profit?</h3>
      <p>No. It sizes for growth under an edge; variance and model error remain.</p>
    </>
  ), []);

  return (
    <ToolLayout
      title="Kelly Criterion Calculator"
      subtitle="Size a wager from bankroll, sportsbook odds, and your estimated true win probability."
      guide={guide}
      faqJsonLd={{
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        mainEntity: [
          { '@type': 'Question', name: 'Why not always use full Kelly?', acceptedAnswer: { '@type': 'Answer', text: 'Full Kelly is volatile and assumes perfect probabilities; half Kelly is commonly recommended.' } },
          { '@type': 'Question', name: 'Where do I get true win probability?', acceptedAnswer: { '@type': 'Answer', text: 'Use a calibrated model such as PredictionLab win probabilities on the predictions pages.' } },
          { '@type': 'Question', name: 'What if Kelly is negative?', acceptedAnswer: { '@type': 'Answer', text: 'Negative Kelly means negative expected value — the recommended stake is $0.' } },
          { '@type': 'Question', name: 'Does Kelly guarantee profit?', acceptedAnswer: { '@type': 'Answer', text: 'No. Kelly sizes growth under an edge; results still vary.' } },
        ],
      }}
    >
      <div className="pl-tools-grid">
        <Field label="Total bankroll ($)">
          <input value={bankroll} onChange={(e) => setBankroll(e.target.value)} inputMode="decimal" />
        </Field>
        <Field label="Sportsbook odds (American)">
          <input value={odds} onChange={(e) => setOdds(e.target.value)} inputMode="numeric" />
        </Field>
        <Field label="Estimated true win probability %" hint="You can use PredictionLab model win % here">
          <input value={winPct} onChange={(e) => setWinPct(e.target.value)} inputMode="decimal" />
        </Field>
      </div>
      <div className="pl-tools-kelly-mult" role="group" aria-label="Kelly multiplier">
        {[
          { v: 1, label: 'Full 1.0×' },
          { v: 0.5, label: 'Half 0.5×' },
          { v: 0.25, label: 'Quarter 0.25×' },
        ].map((opt) => (
          <button
            key={opt.v}
            type="button"
            className={`pl-tools-chip${mult === opt.v ? ' is-on' : ''}`}
            onClick={() => setMult(opt.v)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      {(a == null || p == null || !Number.isFinite(bank) || bank <= 0) && (
        <p className="pl-tools-error" role="alert">Enter a valid bankroll, American odds, and win probability.</p>
      )}
      <div className="pl-tools-results">
        <ResultRow label="Expected value (EV %)" value={ev == null ? '—' : `${(ev * 100).toFixed(2)}%`} tone={ev != null && ev > 0 ? 'good' : ev != null && ev <= 0 ? 'bad' : null} />
        <ResultRow label="Full Kelly stake %" value={fmtPct(full)} />
        <ResultRow label="Full Kelly dollars" value={fmtMoney(full != null && Number.isFinite(bank) ? bank * Math.max(0, full) : null)} />
        <ResultRow
          label="Recommended stake %"
          value={negEv ? '0% (No bet — negative EV)' : fmtPct(stakePct)}
        />
        <ResultRow
          label="Recommended wager $"
          value={negEv ? '$0 (No bet — negative EV)' : fmtMoney(stake$)}
          tone={negEv ? 'bad' : 'good'}
        />
        <ResultRow label="Expected profit on recommended stake" value={negEv ? '$0' : fmtMoney(expectedProfit)} />
      </div>
      <ResetButton onClick={() => { setBankroll('1000'); setOdds('+120'); setWinPct('50'); setMult(0.5); }} />
    </ToolLayout>
  );
}
