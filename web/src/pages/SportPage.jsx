import { useEffect, useMemo, useRef, useState } from 'react';
import useCardPages from '../hooks/useCardPages.js';
import { CONFERENCES } from '../data/conferences.js';
import { DEFAULT_NAV, SPORT_NAV } from '../data/sportNav.js';
import { useDragScroll } from '../hooks/useDragScroll.js';
import collegeGroups from '../data/collegeGroups.json';
import PlayersPanel from '../components/PlayersPanel.jsx';
import GolfPanel from '../components/GolfPanel.jsx';
import ResultsPanel from '../components/ResultsPanel.jsx';
import PicksChart from '../components/PicksChart.jsx';
import { todayET, cardIsFinal } from '../slateDates.js';

// Football shows its whole week by default; the other sports show 4 days, with a button for the rest.
const FOOTBALL = new Set(['NFL', 'NCAAF', 'CFL']);
// Finished games: the last two days show under "Recent results", as on the classic page.
const RECENT_DAYS = 2;

function sportKey(slug) {
  return slug.replace(/-picks$/, '').toUpperCase();
}

function logosInCard(html) {
  return [...html.matchAll(/src="([^"]*\/teamlogos\/[^"]*)"/g)].map((m) => m[1]);
}

function localIsoDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function dayLabel(iso, todayIso) {
  if (!iso) return 'Upcoming';
  if (iso === todayIso) return 'Today';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function finalLabel(iso, todayIso) {
  return iso ? `Final · ${dayLabel(iso, todayIso)}` : 'Final';
}

// Teams on a card, by ESPN id: the logo files are named by id (.../teamlogos/ncaa/500/87.png -> 87).
function logoIds(card) {
  return logosInCard(card.html)
    .map((src) => /\/(\d+)\.png/.exec(src)?.[1])
    .filter(Boolean);
}

// Filters for college sports: each group's ESPN ids, matched against the ids on a card.
function collegeMatchers(groups) {
  return Object.fromEntries(
    Object.entries(groups)
      .filter(([, ids]) => ids.length > 0)
      .map(([label, ids]) => [label, (card) => logoIds(card).some((id) => ids.includes(id))]),
  );
}

// The league a card names in data-league (the soccer leagues).
function leagueOf(card) {
  const match = /data-league="([^"]*)"/.exec(card.html);
  return match ? match[1] : '';
}

// The tournament a tennis card names in its Event row.
function eventOf(card) {
  const match = /Event<\/span>\s*<span class="sf-val">([^<]*)/.exec(card.html);
  return match ? match[1].trim() : '';
}

// Filters for soccer: one per league. The 'All' chip is handled by the page.
function leagueMatchers(labels) {
  return Object.fromEntries(
    labels.filter((label) => label !== 'All').map((label) => [label, (card) => leagueOf(card) === label]),
  );
}

// Filters for pro sports: East and West (NFL: AFC and NFC) by team name (conferences.js).
const CONFERENCE_LABELS = { east: 'East', west: 'West', afc: 'AFC', nfc: 'NFC' };

function nameMatchers(conference) {
  if (!conference) return null;
  return Object.fromEntries(
    Object.entries(conference).map(([key, teams]) => [
      CONFERENCE_LABELS[key] || key,
      (card) => teams.includes(card.home) || teams.includes(card.away),
    ]),
  );
}

function nameKey(name) {
  return (name || '').toLowerCase().normalize('NFD').replace(/[^a-z0-9]/g, '');
}

function sameTeam(a, b) {
  const x = nameKey(a);
  const y = nameKey(b);
  return Boolean(x && y) && (x === y || x.includes(y) || y.includes(x));
}

// The card a live box points at (?game=Away|Home): both teams, else the home team, else the away team.
function targetCard(cards, game) {
  if (!game) return null;
  const [away, home] = game.split('|');
  const upcoming = cards.filter((card) => !cardIsFinal(card) && card.date >= todayET());
  for (const list of [upcoming, cards]) {
    const both = list.find((card) => sameTeam(card.away, away) && sameTeam(card.home, home));
    if (both) return both;
  }
  return upcoming.find((card) => sameTeam(card.home, home))
    || upcoming.find((card) => sameTeam(card.away, away))
    || null;
}

export default function SportPage({ slug }) {
  const sport = sportKey(slug);
  const isGolf = sport === 'GOLF';
  const nav = SPORT_NAV[sport] || DEFAULT_NAV;
  const conference = CONFERENCES[sport] || null;
  const college = collegeGroups[sport] || null;
  const [todayIso, setTodayIso] = useState(() => todayET());
  const daysShownFirst = FOOTBALL.has(sport) ? 7 : 4;
  const [tab, setTab] = useState(nav.tabs[0]);
  const { payload, error, loadingMore, loadMore } = useCardPages(slug, !isGolf);
  const moreGames = useRef(null);
  // Phones show one column at a time; this switch flips between the games and the results.
  const [mobilePane, setMobilePane] = useState('predictions');
  const [filter, setFilter] = useState(() => {
    const league = new URLSearchParams(window.location.search).get('league');
    if (league && sport === 'TENNIS') return league;
    return league && nav.filters && nav.filters.includes(league) ? league : 'All';
  });
  const [teamFilter, setTeamFilter] = useState(
    () => new URLSearchParams(window.location.search).get('team') || null,
  );
  const [showAllDays, setShowAllDays] = useState(false);
  const [picksView, setPicksView] = useState('cards');
  const teamStrip = useDragScroll();
  const leagueStrip = useDragScroll();

  const cards = payload?.cards || [];
  const [gameParam] = useState(() => new URLSearchParams(window.location.search).get('game'));
  const target = useMemo(() => targetCard(cards, gameParam), [cards, gameParam]);
  useEffect(() => { setTodayIso(todayET()); }, [payload]);
  useEffect(() => {
    if (!payload?.hasMore || loadingMore || !moreGames.current) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && document.visibilityState !== 'hidden') loadMore();
    }, { rootMargin: '250px' });
    observer.observe(moreGames.current);
    return () => observer.disconnect();
  }, [payload, loadingMore, loadMore]);
  useEffect(() => {
    if (!payload?.hasMore || loadingMore) return;
    const missingTarget = gameParam && !target;
    const missingTeam = teamFilter && !cards.some((card) => card.home === teamFilter || card.away === teamFilter);
    if (missingTarget || missingTeam) loadMore();
  }, [payload, loadingMore, gameParam, target, teamFilter, cards, loadMore]);

  // Opened from a live box: show every day so the game is on the page, then scroll to it.
  useEffect(() => {
    if (!target) return;
    setShowAllDays(true);
    const timer = setTimeout(() => {
      document.querySelector('.pl-card-row.is-target')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
    return () => clearTimeout(timer);
  }, [target]);

  // Cards grouped by game date. Finished games are grouped by their date too (FINAL:<date>).
  const days = useMemo(() => {
    const map = new Map();
    cards.forEach((card) => {
      const key = cardIsFinal(card) ? `FINAL:${card.date || ''}` : card.date || '';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(card);
    });
    return [...map.entries()];
  }, [cards]);

  // Upcoming days, earliest first.
  const upcomingDays = useMemo(
    () => days
      .filter(([key]) => !key.startsWith('FINAL') && key >= todayIso)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
    [days, todayIso],
  );

  // The last two days of finished games, most recent first.
  const recentDays = useMemo(
    () => days
      .filter(([key]) => key.startsWith('FINAL'))
      .map(([key, list]) => [key.slice('FINAL:'.length), list])
      .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
      .slice(0, RECENT_DAYS),
    [days],
  );

  // Team bubbles come from today's games (or the first upcoming day with games).
  const teams = useMemo(() => {
    const todays = upcomingDays.find(([day]) => day === todayIso);
    const base = todays ? todays[1] : (upcomingDays[0] ? upcomingDays[0][1] : []);
    const seen = new Map();
    base.forEach((card) => {
      const logos = logosInCard(card.html);
      [[card.away, logos[0]], [card.home, logos[1]]].forEach(([name, src]) => {
        if (name && !seen.has(name)) {
          seen.set(name, { name, src: src || '' });
        }
      });
    });
    // A team picked from search keeps its bubble even when it does not play that day.
    if (teamFilter && !seen.has(teamFilter)) {
      const card = cards.find((c) => c.home === teamFilter || c.away === teamFilter);
      const logos = card ? logosInCard(card.html) : [];
      seen.set(teamFilter, { name: teamFilter, src: (card && (card.away === teamFilter ? logos[0] : logos[1])) || '' });
    }
    return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [upcomingDays, todayIso, teamFilter, cards]);

  // Two rows that slide sideways: the first half on top, the rest below.
  const teamRows = useMemo(() => {
    if (teams.length === 0) return [];
    const half = Math.ceil(teams.length / 2);
    return [teams.slice(0, half), teams.slice(half)];
  }, [teams]);

  // Tennis: one chip per tournament on the page, most matches first.
  const tennisEvents = useMemo(() => {
    if (sport !== 'TENNIS') return [];
    const counts = new Map();
    cards.forEach((card) => {
      const name = eventOf(card);
      if (name) counts.set(name, (counts.get(name) || 0) + 1);
    });
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([name]) => name);
  }, [sport, cards]);
  const shownNav = tennisEvents.length > 1 ? { ...nav, filters: ['All', ...tennisEvents], bubbles: true, text: true } : nav;

  // Filters: All, plus the groups each sport has. A chip with no teams behind it stays greyed out.
  const groupMatchers = college
    ? collegeMatchers(college.groups)
    : sport === 'SOCCER' ? leagueMatchers(nav.filters)
    : sport === 'TENNIS' ? Object.fromEntries(tennisEvents.map((name) => [name, (card) => eventOf(card) === name]))
    : nameMatchers(conference);
  const filterWorks = (label) => label === 'All' || Boolean(groupMatchers && groupMatchers[label]);

  const matches = (card) => {
    let inFilter = true;
    if (groupMatchers && filter !== 'All' && groupMatchers[filter]) {
      inFilter = groupMatchers[filter](card);
    }
    const onTeam = !teamFilter || card.home === teamFilter || card.away === teamFilter;
    return inFilter && onTeam;
  };

  const shownUpcoming = upcomingDays
    .map(([day, list]) => [day, list.filter(matches)])
    .filter(([, list]) => list.length > 0);
  const visibleDays = showAllDays ? shownUpcoming : shownUpcoming.slice(0, daysShownFirst);
  const shownRecent = recentDays
    .map(([day, list]) => [day, list.filter(matches)])
    .filter(([, list]) => list.length > 0);

  const chooseTab = (name) => {
    setTab(name);
    setFilter('All');
    setTeamFilter(null);
    setPicksView('cards');
  };

  const showGames = tab === 'Games' && picksView === 'cards';
  const leagueSplit = shownNav.filters ? Math.ceil(shownNav.filters.length / 2) : 0;

  return (
    <>
      <div className="pl-mobile-switch" role="tablist" aria-label="Show predictions or results">
        {[['predictions', 'Predictions'], ['results', 'Results']].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={mobilePane === key}
            className={mobilePane === key ? 'active' : ''}
            onClick={() => { setMobilePane(key); window.scrollTo({ top: 0 }); }}
          >
            {label}
          </button>
        ))}
      </div>
      <main className={mobilePane === 'predictions' ? 'pl-center' : 'pl-center pl-mobile-hidden'}>
        <h1 className="pl-title">{sport}</h1>
        <div className="pl-tabs" role="tablist">
          {nav.tabs.map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={tab === name}
              className={tab === name ? 'is-active' : ''}
              onClick={() => chooseTab(name)}
            >
              {name}
            </button>
          ))}
        </div>

        {tab === 'Games' && (
          <div className="pl-results-views pl-picks-views" role="tablist">
            {['cards', 'chart'].map((view) => (
              <button
                key={view}
                type="button"
                role="tab"
                aria-selected={picksView === view}
                className={picksView === view ? 'is-active' : ''}
                onClick={() => setPicksView(view)}
              >
                {view === 'cards' ? 'Cards' : 'Chart'}
              </button>
            ))}
          </div>
        )}

        {tab === 'Games' && picksView === 'chart' && (
          <PicksChart key={slug} slug={slug} onPredictions={() => setPicksView('cards')} />
        )}

        {showGames && shownNav.filters && shownNav.bubbles && (
          <div className={`pl-team-strip pl-league-strip${shownNav.text ? ' is-text' : ''}`} ref={leagueStrip.ref} {...leagueStrip.handlers}>
            <div className="pl-team-rows">
              {[shownNav.filters.slice(0, leagueSplit), shownNav.filters.slice(leagueSplit)].map((row, rowIndex) => (
                <div className="pl-team-row" key={rowIndex}>
                  {row.map((label) => (
                    <button
                      key={label}
                      type="button"
                      disabled={!filterWorks(label)}
                      className={`pl-league-pill${filter === label ? ' is-active' : ''}`}
                      onClick={() => setFilter(label)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}

        {showGames && shownNav.filters && !shownNav.bubbles && (
          <div className={`pl-conf-row${shownNav.filters.length > 4 ? ' is-long' : ''}`}>
            {shownNav.filters.map((label) => (
              <button
                key={label}
                type="button"
                disabled={!filterWorks(label)}
                className={filter === label ? 'is-active' : ''}
                onClick={() => setFilter(label)}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {showGames && (
          <>
            <div className="pl-team-strip" ref={teamStrip.ref} {...teamStrip.handlers}>
              <div className="pl-team-rows">
                {teamRows.map((row, rowIndex) => (
                  <div className="pl-team-row" key={rowIndex}>
                    {row.map((team) => (
                      <button
                        key={team.name}
                        type="button"
                        title={team.name}
                        className={`pl-team-pill${teamFilter === team.name ? ' is-active' : ''}`}
                        onClick={() => setTeamFilter(teamFilter === team.name ? null : team.name)}
                      >
                        {team.src && <img src={team.src} alt="" draggable={false} loading="lazy" />}
                        <span>{team.name}</span>
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </div>
            {error && <p className="pl-empty">Could not load games: {error}</p>}
            {!error && !payload && <p className="pl-empty">Loading games…</p>}
            {!error && payload && !payload.hasMore && shownUpcoming.length === 0 && shownRecent.length === 0 && (
              <p className="pl-empty">No games scheduled right now.</p>
            )}
            {visibleDays.map(([day, list]) => (
              <section key={day || 'upcoming'} className="pl-day">
                <h2 className="pl-day-title">{dayLabel(day, todayIso)}</h2>
                {list.map((card, index) => (
                  <div
                    className={card === target ? 'pl-card-row is-target' : 'pl-card-row'}
                    key={`${day}-${card.away}-${card.home}-${index}`}
                    dangerouslySetInnerHTML={{ __html: card.html }}
                  />
                ))}
              </section>
            ))}
            {!showAllDays && shownUpcoming.length > daysShownFirst && (
              <button type="button" className="pl-show-more" onClick={() => setShowAllDays(true)}>
                Show all {shownUpcoming.length} days
              </button>
            )}
            {shownRecent.length > 0 && (
              <div className="pl-recent">
                <h2 className="pl-recent-title">Recent results</h2>
                {shownRecent.map(([day, list]) => (
                  <section key={`final-${day || 'undated'}`} className="pl-day">
                    <h2 className="pl-day-title">{finalLabel(day, todayIso)}</h2>
                    {list.map((card, index) => (
                      <div
                        className={card === target ? 'pl-card-row is-target' : 'pl-card-row'}
                        key={`final-${day}-${card.away}-${card.home}-${index}`}
                        dangerouslySetInnerHTML={{ __html: card.html }}
                      />
                    ))}
                  </section>
                ))}
              </div>
            )}
            {payload?.hasMore && (
              <div ref={moreGames}>
                <button type="button" className="pl-show-more" disabled={loadingMore} onClick={loadMore}>
                  {loadingMore ? 'Loading more games…' : 'Load more games'}
                </button>
              </div>
            )}
            {payload && payload.shareImage && (
              <div className="pl-share-image" dangerouslySetInnerHTML={{ __html: payload.shareImage }} />
            )}
          </>
        )}

        {tab === 'Tournaments' && <GolfPanel />}
        {tab === 'Players' && <PlayersPanel sport={sport} />}
      </main>

      <aside className={mobilePane === 'results' ? 'pl-right' : 'pl-right pl-mobile-hidden'}>
        <div className="pl-right-title">Results</div>
        <ResultsPanel
          enabled={isGolf || Boolean(payload) || mobilePane === 'results'}
          src={`/${slug.replace('-picks', '-results')}`}
          picksPath={`/${slug.replace('-projections', '-picks')}`}
          onPredictions={() => { chooseTab(nav.tabs[0]); setMobilePane('predictions'); }}
        />
      </aside>
    </>
  );
}
