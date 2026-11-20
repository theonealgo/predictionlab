import { useCallback, useEffect, useRef, useState } from 'react';
import { getJson, peekJson } from '../api.js';
import { applyCardStyles } from '../cardStyles.js';
import { todayET, cardIsFinal } from '../slateDates.js';

function plusDays(iso, count) {
  const [year, month, day] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}

function cardDay(card) {
  const stamped = String(card.date || '').slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(stamped)) return stamped;
  const html = String(card.html || '');
  const time = /class=["'][^"']*\bgame-time\b[^"']*["'][^>]*>([\s\S]*?)<\//i.exec(html);
  const text = time ? time[1].replace(/<[^>]+>/g, ' ') : html;
  const found = /(\d{4}-\d{2}-\d{2})/.exec(text);
  return found ? found[1] : '';
}

// Middle column for every sport: today through the requested span. Finished and older cards stay out.
function predictionCards(cards, span) {
  const today = todayET();
  const end = plusDays(today, span);
  return (cards || []).flatMap((card) => {
    if (cardIsFinal(card)) return [];
    const day = cardDay(card);
    if (!day || day < today || day > end) return [];
    return [String(card.date || '').slice(0, 10) === day ? card : { ...card, date: day }];
  });
}

const pause = (seconds = 1) => new Promise((resolve) => {
  const ms = Math.max(1, Math.min(30, Number(seconds) || 1)) * 1000;
  setTimeout(resolve, ms);
});
const identity = (card) => /data-game-id="([^"]+)"/.exec(card.html || '')?.[1]
  || `${card.date}|${card.away}|${card.home}`;

export default function useCardPages(slug, enabled = true, span = 0) {
  const path = `/api/web/sports/${slug}/cards?limit=50&span=${span}`;
  const [payload, setPayload] = useState(() => {
    const saved = enabled ? peekJson(path) : null;
    return saved ? { ...saved, cards: predictionCards(saved.cards, span) } : saved;
  });
  const [error, setError] = useState(null);
  // True after the first live cards fetch finishes (ok or fail). Cached peek alone
  // is not enough — results must wait so they do not fight the cards worker.
  const [ready, setReady] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const current = useRef(payload);
  const busy = useRef(false);
  const epoch = useRef(0);
  const commit = useCallback((value) => {
    current.current = value;
    applyCardStyles(value.css);
    setPayload(value);
  }, []);

  const readPage = useCallback(async (offset, generation) => {
    let failures = 0;
    // Cap wait at ~30s so a wedged worker never leaves the page spinning for minutes.
    const deadline = Date.now() + 30_000;
    for (let tries = 0; tries < 20 && epoch.current === generation; tries += 1) {
      if (Date.now() > deadline) break;
      try {
        const data = await getJson(offset ? `${path}&offset=${offset}` : path, { maxAge: 0 });
        if (!data.building) return { ...data, cards: predictionCards(data.cards, span) };
        const wait = Math.min(2, Number(data.retryAfter) || 2);
        await pause(wait);
        continue;
      } catch (failure) {
        if (++failures >= 4) throw failure;
      }
      await pause();
    }
    if (epoch.current === generation) throw new Error('Games are taking longer to load. Please retry.');
    return null;
  }, [path, span]);

  useEffect(() => {
    const generation = ++epoch.current;
    busy.current = false;
    setReady(false);
    if (!enabled) return undefined;
    const kept = peekJson(path);
    const slate = kept ? { ...kept, cards: predictionCards(kept.cards, span) } : kept;
    current.current = slate;
    setPayload(slate);
    if (kept) applyCardStyles(kept.css);
    async function refresh() {
      if (busy.current || document.visibilityState === 'hidden') return;
      busy.current = true;
      const previous = current.current;
      const targetCount = Math.max(5, previous?.cards?.length || 0);
      const cards = [];
      let offset = 0;
      let gotPage = false;
      try {
        do {
          const data = await readPage(offset, generation);
          if (!data || epoch.current !== generation) return;
          cards.push(...data.cards);
          const seen = new Set(cards.map(identity));
          const continuing = data.hasMore && data.nextOffset > offset && cards.length < targetCount;
          const remaining = continuing ? (previous?.cards || []).filter((card) => !seen.has(identity(card))) : [];
          commit({ ...data, cards: [...cards, ...remaining] });
          setError(null);
          gotPage = true;
          // Unlock results after the first page lands; extra pages keep loading.
          if (epoch.current === generation) setReady(true);
          if (!continuing) break;
          offset = data.nextOffset;
        } while (epoch.current === generation);
      } catch (failure) {
        if (epoch.current === generation) {
          setError(failure.message);
          setReady(true);
        }
      } finally {
        if (epoch.current === generation) {
          busy.current = false;
          if (!gotPage) setReady(true);
        }
      }
    }
    refresh();
    const interval = setInterval(refresh, 60000);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      ++epoch.current;
      clearInterval(interval);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [path, enabled, readPage, commit, span]);

  const loadMore = useCallback(async () => {
    const previous = current.current;
    if (!enabled || busy.current || !previous?.hasMore || previous.nextOffset == null) return;
    const generation = epoch.current;
    busy.current = true;
    setLoadingMore(true);
    try {
      const data = await readPage(previous.nextOffset, generation);
      if (!data || epoch.current !== generation) return;
      const seen = new Set(previous.cards.map(identity));
      commit({ ...data, cards: [...previous.cards, ...data.cards.filter((card) => !seen.has(identity(card)))] });
      setError(null);
    } catch (failure) {
      if (epoch.current === generation) setError(failure.message);
    } finally {
      if (epoch.current === generation) {
        busy.current = false;
        setLoadingMore(false);
      }
    }
  }, [enabled, readPage, commit]);
  return { payload, error, loadingMore, loadMore, ready };
}
