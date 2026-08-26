"""Soccer competition catalog: ESPN slugs, numeric IDs, and continent groups.

Display names for leagues that were already curated stay unchanged so existing
DB rows and tests keep matching. New competitions use official ESPN names.
Slugs and numeric IDs come from sports.core.api.espn.com/v2/sports/soccer/leagues.
"""
from __future__ import annotations

# (key, customer-facing label) — ESPN browse buckets, no vendor wording.
SOCCER_REGION_DEFS = (
    ('top', 'Top Competitions'),
    ('concacaf', 'USA, Mexico & CONCACAF'),
    ('europe', 'Europe'),
    ('internationals', 'Internationals'),
    ('south-america', 'South America'),
    ('asia', 'Asia'),
    ('africa', 'Africa'),
    ('live', 'Live'),
)

# Existing curated names stay first. Then remaining competitions by region.
# name, espn_slug, numeric_id, regions, extra aliases (official ESPN names + common)
_LEAGUES: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    # --- already curated ---
    ('English Premier League', 'eng.1', '700', ('top', 'europe'),
     ('premier league', 'epl', 'eng.1')),
    ('UEFA Champions League', 'uefa.champions', '775', ('top', 'europe'),
     ('champions league', 'uefa champions league qualifiers')),
    ('UEFA Europa League', 'uefa.europa', '776', ('top', 'europe'),
     ('europa league', 'uefa europa league qualifiers')),
    ('UEFA Europa Conference League', 'uefa.europa.conf', '20296', ('top', 'europe'),
     ('uefa conference league', 'europa conference league', 'conference league',
      'uefa europa conference league qualifiers')),
    ('Spanish LaLiga', 'esp.1', '740', ('top', 'europe'),
     ('spanish laliga', 'laliga', 'la liga', 'esp.1')),
    ('German Bundesliga', 'ger.1', '720', ('top', 'europe'),
     ('bundesliga', 'ger.1')),
    ('Italian Serie A', 'ita.1', '730', ('top', 'europe'),
     ('serie a', 'ita.1')),
    ('French Ligue 1', 'fra.1', '710', ('top', 'europe'),
     ('ligue 1', 'fra.1')),
    ('Dutch Eredivisie', 'ned.1', '725', ('europe',),
     ('eredivisie', 'ned.1')),
    ('Portuguese Primeira Liga', 'por.1', '715', ('europe',),
     ('primeira liga', 'por.1')),
    ('EFL Championship', 'eng.2', '3914', ('top', 'europe'),
     ('english league championship', 'league championship', 'english championship',
      'eng.2')),
    ('FA Cup', 'eng.fa', '3918', ('top', 'europe'),
     ('english fa cup', 'eng.fa')),
    ('EFL Cup', 'eng.league_cup', '3920', ('top', 'europe'),
     ('english carabao cup', 'carabao cup', 'english league cup', 'league cup',
      'eng.league_cup')),
    ('Major League Soccer', 'usa.1', '770', ('top', 'concacaf'),
     ('mls', 'usa.1')),
    ('Liga MX', 'mex.1', '760', ('top', 'concacaf'),
     ('mexican liga bbva mx', 'bbva mx', 'mex.1')),
    ('Copa Libertadores', 'conmebol.libertadores', '783', ('south-america',),
     ('conmebol libertadores',)),
    ('FIFA World Cup', 'fifa.world', '606', ('top', 'internationals'),
     ('world cup', 'fifa.world')),
    ('FIFA World Cup Qualifiers (UEFA)', 'fifa.worldq.uefa', '786',
     ('top', 'internationals'),
     ('fifa world cup qualifying - uefa', 'fifa world cup qualifying',
      'fifa world cup qualifiers', 'world cup qualifiers',
      'uefa world cup qualifiers', 'fifa.worldq.uefa')),
    ('FIFA World Cup Qualifiers (CONMEBOL)', 'fifa.worldq.conmebol', '787',
     ('top', 'internationals'),
     ('fifa world cup qualifying - conmebol', 'conmebol world cup qualifiers',
      'fifa.worldq.conmebol')),
    ('FIFA World Cup Qualifiers (CAF)', 'fifa.worldq.caf', '790',
     ('top', 'internationals'),
     ('fifa world cup qualifying - caf', 'caf world cup qualifiers',
      'fifa.worldq.caf')),
    ('FIFA World Cup Qualifiers (CONCACAF)', 'fifa.worldq.concacaf', '788',
     ('top', 'internationals'),
     ('fifa world cup qualifying - concacaf', 'concacaf world cup qualifiers',
      'fifa.worldq.concacaf')),
    ('Spanish Segunda División', 'esp.2', '3921', ('europe',),
     ('spanish laliga 2', 'spanish laliga2', 'segunda división', 'segunda division',
      'la liga 2', 'esp.2')),
    ('CONCACAF Champions Cup', 'concacaf.champions', '5699', ('concacaf',),
     ('concacaf champions cup', 'concacaf champions league',
      'concacaf.champions')),
    ('Leagues Cup', 'concacaf.leagues.cup', '19425', ('concacaf',),
     ('concacaf.leagues.cup',)),
    ('USL Championship', 'usa.usl.1', '4002', ('concacaf',),
     ('usa.2', 'usa.usl.1')),
    # --- Top extras ---
    ('English Women\'s Super League', 'eng.w.1', '8097', ('top', 'europe'), ()),
    ('UEFA Women\'s Champions League', 'uefa.wchampions', '19483', ('top', 'europe'), ()),
    ('NWSL', 'usa.nwsl', '8301', ('top', 'concacaf'), ()),
    ('FIFA World Cup Qualifiers (AFC)', 'fifa.worldq.afc', '789',
     ('top', 'internationals'),
     ('fifa world cup qualifying - afc', 'fifa.worldq.afc')),
    # --- CONCACAF extras ---
    ('Costa Rican Primera Division', 'crc.1', '4005', ('concacaf',), ()),
    ('Honduran Liga Nacional', 'hon.1', '3929', ('concacaf',), ()),
    ('USL Cup', 'usa.usl.l1.cup', '22059', ('concacaf',),
     ('usa.usl.l1.cup',)),
    ('Concacaf Central American Cup', 'concacaf.central.american.cup', '22947',
     ('concacaf',), ()),
    ('NWSL Challenge Cup', 'usa.nwsl.cup', '19868', ('concacaf',), ()),
    ('Concacaf W Champions Cup', 'concacaf.w.champions_cup', '22946',
     ('concacaf',), ()),
    ('NCAA Women\'s Soccer', 'usa.ncaa.w.1', '5499', ('concacaf',), ()),
    ('U.S. Open Cup', 'usa.open', '5337', ('concacaf',),
     ('us open cup', 'u.s. open cup')),
    ('Mexican Liga de Expansión MX', 'mex.2', '3932', ('concacaf',),
     ('mexican liga de expansion mx', 'liga de expansión mx', 'mex.2')),
    ('Guatemalan Liga Nacional', 'gua.1', '3928', ('concacaf',), ()),
    ('Salvadoran Primera Division', 'slv.1', '3943', ('concacaf',), ()),
    ('USL League One', 'usa.usl.l1', '19915', ('concacaf',), ()),
    ('NCAA Men\'s Soccer', 'usa.ncaa.m.1', '5487', ('concacaf',), ()),
    ('USL Super League', 'usa.w.usl.1', '23633', ('concacaf',), ()),
    ('Northern Super League', 'can.w.nsl', '23286', ('concacaf',), ()),
    # --- Europe extras ---
    ('Spanish Copa del Rey', 'esp.copa_del_rey', '3951', ('europe',), ()),
    ('German Cup', 'ger.dfb_pokal', '3954', ('europe',),
     ('dfb pokal', 'dfb-pokal')),
    ('Coppa Italia', 'ita.coppa_italia', '3956', ('europe',), ()),
    ('Coupe de France', 'fra.coupe_de_france', '3952', ('europe',), ()),
    ('Taca de Portugal', 'por.taca.portugal', '20922', ('europe',),
     ('taça de portugal',)),
    ('Dutch KNVB Beker', 'ned.cup', '3957', ('europe',), ()),
    ('Belgian Pro League', 'bel.1', '3901', ('europe',), ()),
    ('Scottish Cup', 'sco.tennents', '3959', ('europe',), ()),
    ('Scottish League Challenge Cup', 'sco.challenge', '5331', ('europe',), ()),
    ('Turkish Super Lig', 'tur.1', '3946', ('europe',),
     ('süper lig', 'super lig')),
    ('Austrian Bundesliga', 'aut.1', '3907', ('europe',), ()),
    ('Norwegian Eliteserien', 'nor.1', '3960', ('europe',), ()),
    ('Italian Serie B', 'ita.2', '3931', ('europe',), ()),
    ('Dutch Keuken Kampioen Divisie', 'ned.2', '3933', ('europe',), ()),
    ('English League Two', 'eng.4', '3916', ('europe',), ()),
    ('English National League', 'eng.5', '3917', ('europe',), ()),
    ('UEFA Europa League Qualifying', 'uefa.europa_qual', '19887', ('europe',), ()),
    ('English Women\'s FA Cup', 'eng.w.fa', '20226', ('europe',), ()),
    ('Spanish Copa de la Reina', 'esp.copa_de_la_reina', '20381', ('europe',), ()),
    ('Dutch Vrouwen Eredivisie', 'ned.w.1', '19945', ('europe',), ()),
    ('Scottish Premiership', 'sco.1', '735', ('europe',), ()),
    ('Scottish League Cup', 'sco.cis', '5330', ('europe',), ()),
    ('Scottish Championship', 'sco.2', '3940', ('europe',), ()),
    ('Greek Super League', 'gre.1', '3955', ('europe',), ()),
    ('Danish Superliga', 'den.1', '3913', ('europe',), ()),
    ('Swedish Allsvenskan', 'swe.1', '3945', ('europe',), ()),
    ('German 2. Bundesliga', 'ger.2', '3927', ('europe',), ()),
    ('French Ligue 2', 'fra.2', '3926', ('europe',), ()),
    ('English League One', 'eng.3', '3915', ('europe',), ()),
    ('English EFL Trophy', 'eng.trophy', '18481', ('europe',),
     ('efl trophy',)),
    ('UEFA Champions League Qualifying', 'uefa.champions_qual', '19874',
     ('europe',), ()),
    ('UEFA Conference League Qualifying', 'uefa.europa.conf_qual', '20221',
     ('europe',), ()),
    ('Spanish Liga F', 'esp.w.1', '20956', ('europe',), ()),
    ('French Première Ligue', 'fra.w.1', '20955', ('europe',),
     ('french premiere ligue',)),
    ('Dutch KNVB Beker Vrouwen', 'ned.w.knvb_cup', '20115', ('europe',), ()),
    ('Russian Premier League', 'rus.1', '3939', ('europe',), ()),
    # --- Internationals extras ---
    ('FIFA World Cup Qualifiers (OFC)', 'fifa.worldq.ofc', '792',
     ('internationals',),
     ('fifa world cup qualifying - ofc', 'fifa.worldq.ofc')),
    ('FIFA Under-17 World Cup', 'fifa.world.u17', '5697', ('internationals',), ()),
    ('International Friendly', 'fifa.friendly', '3922', ('internationals',), ()),
    ('UEFA European Championship', 'uefa.euro', '781', ('internationals',),
     ('euros', 'uefa euro')),
    ('UEFA Nations League', 'uefa.nations', '2395', ('internationals',), ()),
    ('UEFA Women\'s Nations League', 'uefa.w.nations', '23088',
     ('internationals',), ()),
    ('UEFA European Under-21 Championship Qualifying', 'uefa.euro_u21_qual',
     '20114', ('internationals',), ()),
    ('Concacaf Gold Cup', 'concacaf.gold', '4004', ('internationals',), ()),
    ('Concacaf Nations League', 'concacaf.nations.league', '19267',
     ('internationals',), ()),
    ('SheBelieves Cup', 'fifa.shebelieves', '19728', ('internationals',), ()),
    ('Copa América Femenina', 'conmebol.america.femenina', '20703',
     ('internationals',),
     ('copa america femenina',)),
    ('Africa Cup of Nations Qualifying', 'caf.nations_qual', '8315',
     ('internationals',), ()),
    ('African Nations Championship', 'caf.championship', '8365',
     ('internationals',), ()),
    ('AFC Asian Cup Qualifiers', 'afc.cupq', '5662', ('internationals',), ()),
    ('SAFF Championship', 'afc.saff.championship', '18914', ('internationals',), ()),
    ('FIFA Intercontinental Cup', 'fifa.intercontinental_cup', '22902',
     ('internationals',), ()),
    ('Women\'s Olympic Soccer Tournament', 'fifa.w.olympics', '3925',
     ('internationals',), ()),
    ('FIFA Women\'s World Cup', 'fifa.wwc', '795', ('internationals',), ()),
    ('FIFA Women\'s World Cup Qualifying - UEFA', 'fifa.wworldq.uefa', '20649',
     ('internationals',), ()),
    ('FIFA Under-20 World Cup', 'fifa.world.u20', '5694', ('internationals',), ()),
    ('FIFA Under-17 Women\'s World Cup', 'fifa.wworld.u17', '20865',
     ('internationals',), ()),
    ('Women\'s International Friendly', 'fifa.friendly.w', '3923',
     ('internationals',), ()),
    ('UEFA European Championship Qualifying', 'uefa.euroq', '3947',
     ('internationals',), ()),
    ('UEFA Women\'s European Championship', 'uefa.weuro', '17915',
     ('internationals',), ()),
    ('UEFA European Under-21 Championship', 'uefa.euro_u21', '5693',
     ('internationals',), ()),
    ('UEFA European Under-19 Championship', 'uefa.euro.u19', '5698',
     ('internationals',), ()),
    ('Concacaf W Gold Cup', 'concacaf.w.gold', '22060', ('internationals',), ()),
    ('Concacaf W Championship', 'concacaf.womens.championship', '18969',
     ('internationals',), ()),
    ('Copa América', 'conmebol.america', '780', ('internationals',),
     ('copa america',)),
    ('Africa Cup of Nations', 'caf.nations', '3908', ('internationals',),
     ('afcon',)),
    ('Women\'s Africa Cup of Nations', 'caf.w.nations', '23523',
     ('internationals',), ()),
    ('AFC Asian Cup', 'afc.asian.cup', '20219', ('internationals',), ()),
    ('ASEAN Championship', 'aff.championship', '5672', ('internationals',), ()),
    ('FIFA Club World Cup', 'fifa.cwc', '5501', ('internationals',), ()),
    ('Men\'s Olympic Soccer Tournament', 'fifa.olympics', '3924',
     ('internationals',), ()),
    ('Pinatar Cup', 'global.pinatar_cup', '20571', ('internationals',), ()),
    # --- South America extras ---
    ('Argentine Liga Profesional de Fútbol', 'arg.1', '745',
     ('south-america',),
     ('argentine liga profesional de futbol', 'liga profesional')),
    ('Argentine Nacional B', 'arg.2', '3903', ('south-america',),
     ('primera nacional',)),
    ('Brazilian Serie A', 'bra.1', '630', ('south-america',),
     ('brasileirão', 'brasileirao')),
    ('Copa do Brasil', 'bra.copa_do_brazil', '8306', ('south-america',),
     ('copa do brazil', 'bra.copa_do_brazil', 'copa brasil')),
    ('Brazilian Campeonato Gaucho', 'bra.camp.gaucho', '2272',
     ('south-america',),
     ('campeonato gaúcho', 'campeonato gaucho')),
    ('Brazilian Campeonato Paulista', 'bra.camp.paulista', '8207',
     ('south-america',), ()),
    ('Copa Colombia', 'col.copa', '8313', ('south-america',), ()),
    ('Copa Chile', 'chi.copa_chi', '8312', ('south-america',), ()),
    ('Paraguayan Primera División', 'par.1', '3934', ('south-america',),
     ('paraguayan primera division',)),
    ('Peruvian Liga 1', 'per.1', '670', ('south-america',), ()),
    ('Bolivian Liga Profesional', 'bol.1', '620', ('south-america',), ()),
    ('CONMEBOL Sudamericana', 'conmebol.sudamericana', '5454',
     ('south-america',),
     ('copa sudamericana',)),
    ('Copa Argentina', 'arg.copa', '8107', ('south-america',), ()),
    ('Argentine Primera B', 'arg.3', '3904', ('south-america',), ()),
    ('Brazilian Serie B', 'bra.2', '4007', ('south-america',), ()),
    ('Brazilian Campeonato Carioca', 'bra.camp.carioca', '2265',
     ('south-america',), ()),
    ('Brazilian Campeonato Mineiro', 'bra.camp.mineiro', '10872',
     ('south-america',), ()),
    ('Colombian Primera A', 'col.1', '650', ('south-america',), ()),
    ('Chilean Primera División', 'chi.1', '640', ('south-america',),
     ('chilean primera division',)),
    ('LigaPro Ecuador', 'ecu.1', '660', ('south-america',), ()),
    ('Liga AUF Uruguaya', 'uru.1', '680', ('south-america',), ()),
    ('Venezuelan Primera División', 'ven.1', '3949', ('south-america',),
     ('venezuelan primera division',)),
    # --- Asia ---
    ('AFC Champions League Elite', 'afc.champions', '3902', ('asia',), ()),
    ('Australian A-League Men', 'aus.1', '3906', ('asia',),
     ('a-league men', 'a league men')),
    ('Chinese Super League', 'chn.1', '8376', ('asia',), ()),
    ('Indian Super League', 'ind.1', '8316', ('asia',), ()),
    ('Saudi Pro League', 'ksa.1', '21231', ('asia',), ()),
    ('Australian A-League Women', 'aus.w.1', '18992', ('asia',),
     ('a-league women',)),
    ('Japanese J.League', 'jpn.1', '750', ('asia',),
     ('j league', 'j.league')),
    # --- Africa ---
    ('CAF Champions League', 'caf.champions', '2391', ('africa',), ()),
    ('South African Premiership', 'rsa.1', '3937', ('africa',), ()),
    ('CAF Confederation Cup', 'caf.confed', '18000', ('africa',), ()),
)

# Extra endpoints kept for logo hydrate / legacy fetches — not shown in the picker.
_EXTRA_ENDPOINTS = {
    'AFC Champions League Two': 'afc.cup',
}

SOCCER_LEAGUE_ORDER = [row[0] for row in _LEAGUES]

SOCCER_LEAGUE_ENDPOINTS = {row[0]: row[1] for row in _LEAGUES}
SOCCER_LEAGUE_ENDPOINTS.update(_EXTRA_ENDPOINTS)

SOCCER_LEAGUE_NUMERIC_IDS = {row[2]: row[0] for row in _LEAGUES}

SOCCER_LEAGUE_REGIONS = {row[0]: row[3] for row in _LEAGUES}

SOCCER_REGION_LABELS = {key: label for key, label in SOCCER_REGION_DEFS}
SOCCER_REGION_ORDER = [key for key, _label in SOCCER_REGION_DEFS]

# Background score sync stays on the original curated set so expanding the
# picker does not fire 100+ scoreboard requests.
SOCCER_SCORE_UPDATE_LEAGUES = (
    'English Premier League',
    'UEFA Champions League',
    'UEFA Europa League',
    'UEFA Europa Conference League',
    'Spanish LaLiga',
    'German Bundesliga',
    'Italian Serie A',
    'French Ligue 1',
    'Dutch Eredivisie',
    'Portuguese Primeira Liga',
    'EFL Championship',
    'FA Cup',
    'EFL Cup',
    'Major League Soccer',
    'Liga MX',
    'Copa Libertadores',
    'FIFA World Cup',
    'FIFA World Cup Qualifiers (UEFA)',
    'FIFA World Cup Qualifiers (CONMEBOL)',
    'FIFA World Cup Qualifiers (CAF)',
    'FIFA World Cup Qualifiers (CONCACAF)',
    'Spanish Segunda División',
    'CONCACAF Champions Cup',
    'Leagues Cup',
    'USL Championship',
)


def _build_canonical() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, slug, _lid, _regions, extras in _LEAGUES:
        out[name.strip().lower()] = name
        out[slug.strip().lower()] = name
        for alias in extras:
            if alias:
                out[alias.strip().lower()] = name
    return out


_SOCCER_LEAGUE_CANONICAL = _build_canonical()


def soccer_region_from_slug(slug: str | None):
    if not slug:
        return None
    key = slug.strip().lower()
    if key in SOCCER_REGION_LABELS:
        return key
    return None


def soccer_region_label(slug: str | None) -> str:
    key = soccer_region_from_slug(slug)
    if not key:
        return ''
    return SOCCER_REGION_LABELS[key]


def soccer_leagues_for_region(slug: str | None, live_names: list[str] | None = None) -> list[str]:
    key = soccer_region_from_slug(slug)
    if not key:
        return list(SOCCER_LEAGUE_ORDER)
    if key == 'live':
        allowed = {n for n in (live_names or []) if n}
        return [name for name in SOCCER_LEAGUE_ORDER if name in allowed]
    return [name for name in SOCCER_LEAGUE_ORDER if key in SOCCER_LEAGUE_REGIONS.get(name, ())]


def soccer_primary_region(league_name: str) -> str | None:
    regions = SOCCER_LEAGUE_REGIONS.get(league_name) or ()
    return regions[0] if regions else None


def soccer_league_in_region(
    league_name: str,
    region_slug: str | None,
    live_names: list[str] | None = None,
) -> bool:
    key = soccer_region_from_slug(region_slug)
    if not key:
        return True
    if key == 'live':
        if live_names is None:
            return True
        return league_name in set(live_names)
    return key in (SOCCER_LEAGUE_REGIONS.get(league_name) or ())


def soccer_espn_slug(league_name: str | None) -> str | None:
    """ESPN core/scoreboard slug for a catalog league name or alias."""
    if not league_name:
        return None
    key = str(league_name).strip()
    if not key:
        return None
    if key in SOCCER_LEAGUE_ENDPOINTS:
        return SOCCER_LEAGUE_ENDPOINTS[key]
    canon = _SOCCER_LEAGUE_CANONICAL.get(key.lower())
    if canon:
        return SOCCER_LEAGUE_ENDPOINTS.get(canon)
    return None
