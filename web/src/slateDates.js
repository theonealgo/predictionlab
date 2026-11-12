export function todayET(now = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}

export function cardIsFinal(card) {
  if (typeof card.isFinal === 'boolean') return card.isFinal;
  const html = String(card.html || '');
  const time = /class=["'][^"']*\bgame-time\b[^"']*["'][^>]*>([\s\S]*?)<\//i.exec(html);
  return Boolean(time && /\b(?:FINAL|COMPLETED)\b/i.test(time[1].replace(/<[^>]+>/g, ' ')));
}
