import { useCallback, useEffect, useRef, useState } from 'react';
import { getJson, peekJson } from '../api.js';
import { applyCardStyles } from '../cardStyles.js';

const pause = () => new Promise((resolve) => setTimeout(resolve, 1000));
const identity = (card) => /data-game-id="([^"]+)"/.exec(card.html || '')?.[1]
  || `${card.date}|${card.away}|${card.home}`;

export default function useCardPages(slug, enabled = true) {
  const path = `/api/web/sports/${slug}/cards?limit=5`;
  const [payload, setPayload] = useState(() => enabled ? peekJson(path) : null);
  const [error, setError] = useState(null);
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
    for (let tries = 0; tries < 90 && epoch.current === generation; tries += 1) {
      try {
        const data = await getJson(offset ? `${path}&offset=${offset}` : path, { maxAge: 0 });
        if (!data.building) return data;
      } catch (failure) {
        if (++failures >= 4) throw failure;
      }
      await pause();
    }
    if (epoch.current === generation) throw new Error('Games are taking longer to load. Please retry.');
    return null;
  }, [path]);

  useEffect(() => {
    const generation = ++epoch.current;
    busy.current = false;
    if (!enabled) return undefined;
    const kept = peekJson(path);
    current.current = kept;
    setPayload(kept);
    if (kept) applyCardStyles(kept.css);
    async function refresh() {
      if (busy.current || document.visibilityState === 'hidden') return;
      busy.current = true;
      const previous = current.current;
      const targetCount = Math.max(5, previous?.cards?.length || 0);
      const cards = [];
      let offset = 0;
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
          if (!continuing) break;
          offset = data.nextOffset;
        } while (epoch.current === generation);
      } catch (failure) {
        if (epoch.current === generation) setError(failure.message);
      } finally {
        if (epoch.current === generation) busy.current = false;
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
  }, [path, enabled, readPage, commit]);

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
  return { payload, error, loadingMore, loadMore };
}
