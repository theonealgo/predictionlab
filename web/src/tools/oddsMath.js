/** Shared sportsbook odds math for the Tools calculators. */

export function parseAmerican(raw) {
  if (raw == null || raw === '') return null;
  const text = String(raw).trim().replace(/\u2212/g, '-').replace(/,/g, '');
  if (!text) return null;
  if (/^(even|ev)$/i.test(text)) return 100;
  const val = Number(text);
  if (!Number.isFinite(val) || !Number.isInteger(val)) return null;
  if (Math.abs(val) < 100 || Math.abs(val) > 100000) return null;
  return val;
}

export function parseDecimal(raw) {
  if (raw == null || raw === '') return null;
  const val = Number(String(raw).trim().replace(/,/g, ''));
  if (!Number.isFinite(val) || val <= 1) return null;
  return val;
}

export function parseFraction(raw) {
  if (raw == null || raw === '') return null;
  const text = String(raw).trim().replace(/\s+/g, '');
  const m = text.match(/^(-?\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const num = Number(m[1]);
  const den = Number(m[2]);
  if (!Number.isFinite(num) || !Number.isFinite(den) || den === 0) return null;
  const dec = 1 + num / den;
  return dec > 1 ? dec : null;
}

export function parseProbPct(raw) {
  if (raw == null || raw === '') return null;
  const text = String(raw).trim().replace(/%/g, '');
  const val = Number(text);
  if (!Number.isFinite(val) || val <= 0 || val >= 100) return null;
  return val / 100;
}

export function americanToDecimal(american) {
  const a = Number(american);
  if (!Number.isFinite(a) || Math.abs(a) < 100) return null;
  return a > 0 ? 1 + a / 100 : 1 + 100 / Math.abs(a);
}

export function decimalToAmerican(decimal) {
  const d = Number(decimal);
  if (!Number.isFinite(d) || d <= 1) return null;
  if (d >= 2) return Math.round((d - 1) * 100);
  return Math.round(-100 / (d - 1));
}

export function impliedFromAmerican(american) {
  const a = Number(american);
  if (!Number.isFinite(a) || Math.abs(a) < 100) return null;
  return a < 0 ? Math.abs(a) / (Math.abs(a) + 100) : 100 / (a + 100);
}

export function impliedFromDecimal(decimal) {
  const d = Number(decimal);
  if (!Number.isFinite(d) || d <= 1) return null;
  return 1 / d;
}

export function decimalFromImplied(p) {
  if (!Number.isFinite(p) || p <= 0 || p >= 1) return null;
  return 1 / p;
}

export function fractionFromDecimal(decimal) {
  const d = Number(decimal);
  if (!Number.isFinite(d) || d <= 1) return null;
  const profit = d - 1;
  // Reduce a simple fraction approximation for display.
  const scale = 100;
  let num = Math.round(profit * scale);
  let den = scale;
  const g = gcd(Math.abs(num), den) || 1;
  num /= g;
  den /= g;
  return `${num}/${den}`;
}

function gcd(a, b) {
  let x = a;
  let y = b;
  while (y) {
    const t = y;
    y = x % y;
    x = t;
  }
  return x;
}

export function fmtAmerican(american) {
  if (american == null || !Number.isFinite(american)) return '—';
  const n = Math.round(american);
  return n > 0 ? `+${n}` : String(n);
}

export function fmtDecimal(decimal, digits = 3) {
  if (decimal == null || !Number.isFinite(decimal)) return '—';
  return Number(decimal).toFixed(digits);
}

export function fmtPct(p, digits = 2) {
  if (p == null || !Number.isFinite(p)) return '—';
  return `${(p * 100).toFixed(digits)}%`;
}

export function fmtMoney(n, digits = 2) {
  if (n == null || !Number.isFinite(n)) return '—';
  const sign = n < 0 ? '-' : '';
  return `${sign}$${Math.abs(n).toFixed(digits)}`;
}

export function combineDecimals(decimals) {
  if (!decimals.length || decimals.some((d) => d == null || d <= 1)) return null;
  return decimals.reduce((acc, d) => acc * d, 1);
}

export function fairProbs(impliedList) {
  const total = impliedList.reduce((s, p) => s + (p || 0), 0);
  if (total <= 0) return null;
  return impliedList.map((p) => p / total);
}

export function holdFromOverround(overround) {
  if (!Number.isFinite(overround) || overround <= 0) return null;
  return 1 - 1 / overround;
}

export function kellyFraction(decimal, winProb) {
  const d = Number(decimal);
  const p = Number(winProb);
  if (!Number.isFinite(d) || d <= 1 || !Number.isFinite(p) || p <= 0 || p >= 1) return null;
  const b = d - 1;
  const q = 1 - p;
  return (b * p - q) / b;
}

export function fromAmericanField(raw) {
  const american = parseAmerican(raw);
  if (american == null) return null;
  const decimal = americanToDecimal(american);
  const implied = impliedFromAmerican(american);
  return {
    american,
    decimal,
    implied,
    fraction: fractionFromDecimal(decimal),
  };
}
