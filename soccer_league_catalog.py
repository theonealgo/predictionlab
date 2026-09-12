"""Soccer competition catalog: ESPN slugs, numeric IDs, and continent groups.

Display names for leagues that were already curated stay unchanged so existing
DB rows and tests keep matching. New competitions use official ESPN names.
Slugs and numeric IDs come from sports.core.api.espn.com/v2/sports/soccer/leagues.
"""
from __future__ import annotations

import re

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
     ('top', 'internationals', 'africa'),
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
      'concacaf.champions', 'concacaf.champions_cup')),
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
     ('internationals', 'africa'), ()),
    ('African Nations Championship', 'caf.championship', '8365',
     ('internationals', 'africa'), ()),
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
    ('Africa Cup of Nations', 'caf.nations', '3908', ('internationals', 'africa'),
     ('afcon',)),
    ('Women\'s Africa Cup of Nations', 'caf.w.nations', '23523',
     ('internationals', 'africa'), ()),
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
    # --- remaining ESPN soccer leagues (core API) ---
    ('AFC Champions League Two', 'afc.cup', '2466', ('asia',), ()),
    ('English FA Community Shield', 'eng.charity', '5329', ('europe',), ()),
    ('Spanish Supercopa', 'esp.super_cup', '8102', ('europe',), ()),
    ('Campeones Cup', 'campeones.cup', '18771', ('concacaf',), ()),
    ('FIFA Women\'s Champions Cup', 'fifa.w.champions_cup', '24081', ('internationals',), ()),
    ('German Bundesliga Promotion/Relegation Playoff', 'ger.playoff.relegation', '8304', ('europe',), ()),
    ('French Trophee des Champions', 'fra.super_cup', '8357', ('europe',), ()),
    ('Italian Supercoppa', 'ita.super_cup', '8103', ('europe',), ()),
    ('German Supercup', 'ger.super_cup', '8101', ('europe',), ()),
    ('English Women\'s Super League Promotion/Relegation Playoff', 'eng.w.promotion.relegation', '24405', ('europe',), ()),
    ('Club Friendly', 'club.friendly', '19834', ('europe',), ()),
    ('FIFA World Cup Qualifying - Playoff Tournament', 'fifa.wcq.ply', '23449', ('internationals',), ()),
    ('FIFA Women\'s World Cup Qualifying - Playoff Tournament', 'fifa.wwcq.ply', '', ('internationals',), ()),
    ('UEFA Women\'s Champions League Qualifying', 'uefa.wchampions_qual', '24458', ('europe',), ()),
    ('English Women\'s League Cup', 'eng.w.league_cup', '23390', ('europe',), ()),
    ('Saudi King\'s Cup', 'ksa.kings.cup', '22057', ('asia',), ()),
    ('Concacaf Gold Cup Qualifying', 'concacaf.gold_qual', '19778', ('concacaf',), ()),
    ('Concacaf Cup', 'concacaf.confederations_playoff', '8360', ('concacaf',), ()),
    ('UEFA Super Cup', 'uefa.super_cup', '5462', ('europe',), ()),
    ('CONMEBOL-UEFA Cup of Champions', 'global.finalissima', '20704', ('internationals',), ()),
    ('CONMEBOL-UEFA U20 Intercontinental Cup', 'global.u20.intercontinental_cup', '22781', ('internationals',), ()),
    ('CONMEBOL-UEFA Women\'s Cup of Champions', 'global.w.finalissima', '21191', ('internationals',), ()),
    ('AFC Women\'s Asian Cup', 'afc.w.asian.cup', '23537', ('asia',), ()),
    ('UEFA Women\'s Europa Cup', 'uefa.w.europa', '24079', ('europe',), ()),
    ('AFC Champions League Elite Qualifying', 'afc.champions_qual', '24452', ('asia',), ()),
    ('AFC Champions League Two Qualifying', 'afc.cup_qual', '24455', ('asia',), ()),
    ('Non-FIFA Friendly', 'nonfifa', '19725', ('internationals',), ()),
    ('Russian Premier League Relegation/Promotion Playoffs', 'rus.1.promotion.relegation', '20731', ('europe',), ()),
    ('Belgian Pro League Promotion/Relegation Playoffs', 'bel.promotion.relegation', '20116', ('europe',), ()),
    ('French Ligue 1 Promotion/Relegation Playoffs', 'fra.1.promotion.relegation', '20159', ('europe',), ()),
    ('Portuguese Primeira Liga Promotion/Relegation Playoffs', 'por.1.promotion.relegation', '20186', ('europe',), ()),
    ('CONMEBOL-UEFA Club Challenge', 'global.club_challenge', '21597', ('internationals',), ()),
    ('Dutch Johan Cruyff Shield', 'ned.supercup', '10749', ('europe',), ()),
    ('Emirates Cup', 'friendly.emirates_cup', '11108', ('internationals',), ()),
    ('Trofeo Joan Gamper', 'esp.joan_gamper', '17929', ('europe',), ()),
    ('Japanese J.League World Challenge', 'jpn.world_challenge', '17931', ('asia',), ()),
    ('Arnold Clark Cup', 'global.arnold.clark_cup', '20566', ('internationals',), ()),
    ('CONMEBOL Pre-Olympic Tournament', 'fifa.conmebol.olympicsq', '19727', ('internationals',), ()),
    ('Men\'s Olympic Qualifying Playoff', 'fifa.concacaf.olympicsq', '19831', ('internationals',), ()),
    ('Concacaf Women\'s Olympic Qualifying', 'fifa.w.concacaf.olympicsq', '5342', ('internationals',), ()),
    ('Under-21 International Friendly', 'fifa.friendly_u21', '20132', ('internationals',), ()),
    ('German Bundesliga 2. Promotion/Relegation Playoffs', 'ger.2.promotion.relegation', '19871', ('europe',), ()),
    ('English FA Cup Qualifying', 'eng.fa_qual', '23480', ('europe',), ()),
    ('Scottish Premiership Promotion/Relegation Playoffs', 'sco.1.promotion.relegation', '20133', ('europe',), ()),
    ('Scottish Championship Promotion/Relegation Playoffs', 'sco.2.promotion.relegation', '20134', ('europe',), ()),
    ('Scottish Cup Qualifying', 'sco.tennents_qual', '24457', ('europe',), ()),
    ('Dutch Eredivisie Promotion/Relegation Playoffs', 'ned.playoff.relegation', '8305', ('europe',), ()),
    ('Dutch Tweede Divisie Promotion/Relegation Playoffs', 'ned.3.promotion.relegation', '20798', ('europe',), ()),
    ('Swedish Allsvenskan Promotion/Relegation Playoffs', 'swe.1.promotion.relegation', '19968', ('europe',), ()),
    ('Norwegian Eliteserien Promotion/Relegation Playoffs', 'nor.1.promotion.relegation', '19989', ('europe',), ()),
    ('CONMEBOL Recopa', 'conmebol.recopa', '8333', ('south-america',), ()),
    ('Argentine Copa de la Superliga', 'arg.copa_de_la_superliga', '19264', ('south-america',), ()),
    ('Argentine Trofeo de Campeones', 'arg.trofeo_de_la_campeones', '19705', ('south-america',), ()),
    ('Argentine Supercopa', 'arg.supercopa', '8346', ('south-america',), ()),
    ('Argentine Supercopa Internacional', 'arg.supercopa.internacional', '23348', ('south-america',), ()),
    ('Brazilian Supercopa Rei', 'bra.supercopa_do_brazil', '19721', ('south-america',), ()),
    ('Chilean Supercopa', 'chi.super_cup', '8364', ('south-america',), ()),
    ('Chilean Primera División Promotion/Relegation Playoffs', 'chi.1.promotion.relegation', '20524', ('south-america',), ()),
    ('Segunda División de Uruguay', 'uru.2', '3948', ('south-america',), ()),
    ('Colombian Superliga', 'col.superliga', '19112', ('south-america',), ()),
    ('Paraguayan Supercopa', 'par.1.supercopa', '20526', ('south-america',), ()),
    ('Bolivian Liga Profesional Promotion/Relegation Playoffs', 'bol.ply.rel', '20525', ('south-america',), ()),
    ('Copa Bolivia', 'bol.copa', '23284', ('south-america',), ()),
    ('Mexican Campeon de Campeones', 'mex.campeon', '17893', ('concacaf',), ()),
    ('CONCACAF U23 Tournament', 'concacaf.u23', '3911', ('concacaf',), ()),
    ('Intercontinental Cup (India)', 'fifa.intercontinental.cup', '782', ('internationals',), ()),
    ('Chinese Super League Promotion/Relegation Playoffs', 'chn.1.promotion.relegation', '19948', ('asia',), ()),
    ('Arabian Gulf Cup', 'global.gulf_cup', '23107', ('internationals',), ()),
    ('COSAFA Cup', 'caf.cosafa', '20220', ('africa',), ()),
)

# Extra endpoints kept for logo hydrate / legacy fetches — not shown in the picker.
_EXTRA_ENDPOINTS = {
    'AFC Champions League Two': 'afc.cup',
}

# ESPN soccer scoreboard browse headings — do not drop from the picker.
ESPN_BROWSE_HEADINGS = (
    'Top Competitions',
    'USA, Mexico & CONCACAF',
    'Europe',
    'Internationals',
    'South America',
    'Asia',
    'Africa',
)

# ESPN soccer scoreboard league names (unique). Checker fails if any are missing
# from the All-continents dropdown. Aliases above map these onto catalog names.
ESPN_BROWSE_LEAGUES = (
    'AFC Asian Cup',
    'AFC Asian Cup Qualifiers',
    'AFC Champions League Elite',
    'ASEAN Championship',
    'Africa Cup of Nations',
    'Africa Cup of Nations Qualifying',
    'African Nations Championship',
    'Argentine Liga Profesional de Fútbol',
    'Argentine Nacional B',
    'Argentine Primera B',
    'Australian A-League Men',
    'Australian A-League Women',
    'Austrian Bundesliga',
    'Belgian Pro League',
    'Bolivian Liga Profesional',
    'Brazilian Campeonato Carioca',
    'Brazilian Campeonato Gaucho',
    'Brazilian Campeonato Mineiro',
    'Brazilian Campeonato Paulista',
    'Brazilian Serie A',
    'Brazilian Serie B',
    'CAF Champions League',
    'CAF Confederation Cup',
    'Chilean Primera División',
    'Chinese Super League',
    'Colombian Primera A',
    'Concacaf Central American Cup',
    'Concacaf Champions Cup',
    'Concacaf Gold Cup',
    'Concacaf Nations League',
    'Concacaf W Champions Cup',
    'Concacaf W Championship',
    'Concacaf W Gold Cup',
    'CONMEBOL Libertadores',
    'CONMEBOL Sudamericana',
    'Copa América',
    'Copa América Femenina',
    'Copa Argentina',
    'Copa Chile',
    'Copa Colombia',
    'Copa do Brasil',
    'Coppa Italia',
    'Costa Rican Primera Division',
    'Coupe de France',
    'Danish Superliga',
    'Dutch Eredivisie',
    'Dutch KNVB Beker',
    'Dutch KNVB Beker Vrouwen',
    'Dutch Keuken Kampioen Divisie',
    'Dutch Vrouwen Eredivisie',
    'English Carabao Cup',
    'English EFL Trophy',
    'English FA Cup',
    'English League Championship',
    'English League One',
    'English League Two',
    'English National League',
    'English Premier League',
    'English Women\'s FA Cup',
    'English Women\'s Super League',
    'FIFA Club World Cup',
    'FIFA Intercontinental Cup',
    'FIFA Under-17 Women\'s World Cup',
    'FIFA Under-17 World Cup',
    'FIFA Under-20 World Cup',
    'FIFA Women\'s World Cup',
    'FIFA Women\'s World Cup Qualifying - UEFA',
    'FIFA World Cup',
    'FIFA World Cup Qualifying - AFC',
    'FIFA World Cup Qualifying - CAF',
    'FIFA World Cup Qualifying - CONMEBOL',
    'FIFA World Cup Qualifying - Concacaf',
    'FIFA World Cup Qualifying - OFC',
    'FIFA World Cup Qualifying - UEFA',
    'French Ligue 1',
    'French Ligue 2',
    'French Première Ligue',
    'German 2. Bundesliga',
    'German Bundesliga',
    'German Cup',
    'Greek Super League',
    'Guatemalan Liga Nacional',
    'Honduran Liga Nacional',
    'Indian Super League',
    'International Friendly',
    'Italian Serie A',
    'Italian Serie B',
    'Japanese J.League',
    'Leagues Cup',
    'Liga AUF Uruguaya',
    'LigaPro Ecuador',
    'MLS',
    'Men\'s Olympic Soccer Tournament',
    'Mexican Liga BBVA MX',
    'Mexican Liga de Expansión MX',
    'NCAA Men\'s Soccer',
    'NCAA Women\'s Soccer',
    'NWSL',
    'NWSL Challenge Cup',
    'Northern Super League',
    'Norwegian Eliteserien',
    'Paraguayan Primera División',
    'Peruvian Liga 1',
    'Pinatar Cup',
    'Portuguese Primeira Liga',
    'Russian Premier League',
    'SAFF Championship',
    'Salvadoran Primera Division',
    'Saudi Pro League',
    'Scottish Championship',
    'Scottish Cup',
    'Scottish League Challenge Cup',
    'Scottish League Cup',
    'Scottish Premiership',
    'SheBelieves Cup',
    'South African Premiership',
    'Spanish Copa de la Reina',
    'Spanish Copa del Rey',
    'Spanish LALIGA',
    'Spanish LALIGA 2',
    'Spanish Liga F',
    'Swedish Allsvenskan',
    'Taca de Portugal',
    'Turkish Super Lig',
    'U.S. Open Cup',
    'UEFA Champions League',
    'UEFA Champions League Qualifying',
    'UEFA Conference League',
    'UEFA Conference League Qualifying',
    'UEFA Europa League',
    'UEFA Europa League Qualifying',
    'UEFA European Championship',
    'UEFA European Championship Qualifying',
    'UEFA European Under-19 Championship',
    'UEFA European Under-21 Championship',
    'UEFA European Under-21 Championship Qualifying',
    'UEFA Nations League',
    'UEFA Women\'s Champions League',
    'UEFA Women\'s European Championship',
    'UEFA Women\'s Nations League',
    'USL Championship',
    'USL Cup',
    'USL League One',
    'USL Super League',
    'Venezuelan Primera División',
    'Women\'s Africa Cup of Nations',
    'Women\'s International Friendly',
    'Women\'s Olympic Soccer Tournament',
)

SOCCER_LEAGUE_ORDER = [row[0] for row in _LEAGUES]

SOCCER_LEAGUE_ENDPOINTS = {row[0]: row[1] for row in _LEAGUES}
SOCCER_LEAGUE_ENDPOINTS.update(_EXTRA_ENDPOINTS)

SOCCER_LEAGUE_NUMERIC_IDS = {row[2]: row[0] for row in _LEAGUES if row[2]}

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


def _clean_league_option_label(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = (
        text.replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&nbsp;", " ")
    )
    text = re.sub(r"\s*·\s*Live\s*$", "", text, flags=re.I)
    text = re.sub(r"^●\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def soccer_league_option_labels(html: str) -> list[str]:
    """Customer-facing league names from the soccer <select id=league>."""
    sel = re.search(
        r'<select[^>]*\bid=["\']league["\'][^>]*>([\s\S]*?)</select>',
        html or "",
        flags=re.I,
    )
    if not sel:
        return []
    out: list[str] = []
    for m in re.finditer(r"<option\b[^>]*>([\s\S]*?)</option>", sel.group(1), flags=re.I):
        label = _clean_league_option_label(m.group(1))
        if not label or label.lower() in ("all", "all leagues"):
            continue
        out.append(label)
    return out


def missing_espn_browse_leagues(html_or_labels) -> list[str]:
    """ESPN browse names whose catalog league is not in the dropdown."""
    if isinstance(html_or_labels, str):
        labels = soccer_league_option_labels(html_or_labels)
    else:
        labels = [str(x or "") for x in (html_or_labels or [])]
    mapped = set()
    for lab in labels:
        clean = _clean_league_option_label(lab)
        canon = _SOCCER_LEAGUE_CANONICAL.get(clean.lower())
        if canon:
            mapped.add(canon)
    missing: list[str] = []
    for espn_name in ESPN_BROWSE_LEAGUES:
        canon = _SOCCER_LEAGUE_CANONICAL.get(espn_name.lower())
        if not canon or canon not in mapped:
            missing.append(espn_name)
    return missing


def _site_league_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")


def espn_browse_league_pages() -> list[dict[str, str]]:
    """One picks URL + one results URL per ESPN soccer scoreboard league."""
    out: list[dict[str, str]] = []
    for espn_name in ESPN_BROWSE_LEAGUES:
        canon = _SOCCER_LEAGUE_CANONICAL.get(espn_name.lower()) or ""
        slug = _site_league_slug(canon) if canon else ""
        out.append(
            {
                "espn": espn_name,
                "catalog": canon,
                "slug": slug,
                "picks": f"/soccer-picks?league={slug}" if slug else "",
                "results": f"/soccer-results?league={slug}" if slug else "",
            }
        )
    return out


def league_page_selection_issues(
    html: str, *, slug: str, espn_name: str, catalog_name: str = ""
) -> list[str]:
    """FAIL reasons when a league URL is not that league's own page."""
    html = html or ""
    issues: list[str] = []
    if 'id="league-controls"' not in html or 'id="league"' not in html:
        issues.append(f"{espn_name}: page has no league dropdown")
        return issues
    sel = re.search(
        r'<select[^>]*\bid=["\']league["\'][^>]*>([\s\S]*?)</select>',
        html,
        flags=re.I,
    )
    body = sel.group(1) if sel else ""
    picked = re.search(
        rf'<option\b[^>]*\bvalue=["\']{re.escape(slug)}["\'][^>]*\bselected\b',
        body,
        flags=re.I,
    ) or re.search(
        rf'<option\b[^>]*\bselected\b[^>]*\bvalue=["\']{re.escape(slug)}["\']',
        body,
        flags=re.I,
    )
    if not picked:
        issues.append(f"{espn_name}: picks/results URL did not select this league")
    all_picked = re.search(
        r'<option\b[^>]*\bvalue=["\']["\'][^>]*\bselected\b|'
        r'<option\b[^>]*\bselected\b[^>]*\bvalue=["\']["\']',
        body,
        flags=re.I,
    )
    if all_picked and not picked:
        issues.append(f"{espn_name}: page fell back to All leagues")
    scope = ""
    sm = re.search(
        r'id=["\']soccer-showing-scope["\'][^>]*>([\s\S]*?)</p>',
        html,
        flags=re.I,
    )
    if sm:
        scope = _clean_league_option_label(sm.group(1)).lower()
    if scope and "all leagues" in scope and not picked:
        issues.append(f"{espn_name}: showing line is All leagues")
    return issues


def missing_in_season_green_leagues(html: str) -> list[str]:
    """In-season dropdown rows that are not painted green on the custom menu."""
    html = html or ""
    marked = []
    for m in re.finditer(
        r"<option\b([^>]*)>([\s\S]*?)</option>",
        html,
        flags=re.I,
    ):
        attrs, inner = m.group(1) or "", m.group(2) or ""
        if not re.search(r'\bdata-in-season=["\']1["\']', attrs, flags=re.I):
            continue
        label = _clean_league_option_label(inner)
        if not label or label.lower() in ("all", "all leagues"):
            continue
        marked.append(label)
    if not marked:
        return []
    green = set()
    for m in re.finditer(
        r'<button\b[^>]*class="[^"]*\bin-season\b[^"]*"[^>]*>([\s\S]*?)</button>',
        html,
        flags=re.I,
    ):
        lab = _clean_league_option_label(m.group(1))
        if lab:
            green.add(lab.lower())
    return [n for n in marked if n.lower() not in green]


def soccer_heading_labels(html: str) -> list[str]:
    """Continent / ESPN browse headings from the region select + optgroups."""
    html = html or ""
    labels: list[str] = []
    region = re.search(
        r'<select[^>]*\bid=["\']soccer-region["\'][^>]*>([\s\S]*?)</select>',
        html,
        flags=re.I,
    )
    if region:
        for m in re.finditer(
            r"<option\b[^>]*>([\s\S]*?)</option>", region.group(1), flags=re.I
        ):
            lab = _clean_league_option_label(m.group(1))
            if lab and lab.lower() not in ("all", "all continents"):
                labels.append(lab)
    for m in re.finditer(r'<optgroup\b[^>]*label="([^"]+)"', html, flags=re.I):
        lab = _clean_league_option_label(m.group(1).replace("&amp;", "&"))
        if lab:
            labels.append(lab)
    return labels


def missing_espn_browse_headings(html: str) -> list[str]:
    """Continent / heading labels ESPN shows that the picker dropped."""
    have = {lab.lower() for lab in soccer_heading_labels(html)}
    return [h for h in ESPN_BROWSE_HEADINGS if h.lower() not in have]


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
