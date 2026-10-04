#!/usr/bin/env python3
"""
ATS Picks Web App - SIMPLE VERSION THAT WORKS
"""
from flask import Flask, render_template_string
from ats_system import ATSSystem
from datetime import datetime
import sqlite3

app = Flask(__name__)

@app.route('/')
def home():
    """Home page with sport links"""
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>ATS Betting Picks</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 40px 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 {
            color: white;
            text-align: center;
            font-size: 3em;
            margin-bottom: 20px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        .subtitle {
            text-align: center;
            color: rgba(255,255,255,0.9);
            font-size: 1.2em;
            margin-bottom: 50px;
        }
        .sports-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 30px;
            margin-bottom: 40px;
        }
        .sport-card {
            background: white;
            border-radius: 20px;
            padding: 40px;
            text-align: center;
            text-decoration: none;
            color: #333;
            transition: all 0.3s;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .sport-card:hover {
            transform: translateY(-10px);
            box-shadow: 0 15px 40px rgba(0,0,0,0.3);
        }
        .sport-icon { font-size: 5em; margin-bottom: 20px; }
        .sport-name {
            font-size: 2em;
            font-weight: bold;
            margin-bottom: 10px;
        }
        .sport-desc {
            color: #666;
            font-size: 1.1em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎯 ATS Betting Picks</h1>
        <p class="subtitle">Against The Spread • Moneyline • Over/Under</p>
        
        <div class="sports-grid">
            <a href="/nba" class="sport-card">
                <div class="sport-icon">🏀</div>
                <div class="sport-name">NBA</div>
                <div class="sport-desc">Basketball Picks</div>
            </a>
            
            <a href="/nfl" class="sport-card">
                <div class="sport-icon">🏈</div>
                <div class="sport-name">NFL</div>
                <div class="sport-desc">Football Picks</div>
            </a>
            
            <a href="/nhl" class="sport-card">
                <div class="sport-icon">🏒</div>
                <div class="sport-name">NHL</div>
                <div class="sport-desc">Hockey Picks</div>
            </a>
            
            <a href="/ncaaf" class="sport-card">
                <div class="sport-icon">🏟️</div>
                <div class="sport-name">NCAAF</div>
                <div class="sport-desc">College Football</div>
            </a>
            
            <a href="/ncaab" class="sport-card">
                <div class="sport-icon">🏀</div>
                <div class="sport-name">NCAAB</div>
                <div class="sport-desc">College Basketball</div>
            </a>
        </div>
        
        <p style="text-align: center; color: white; opacity: 0.8;">
            Last updated: {{ now }}
        </p>
    </div>
</body>
</html>
    """, now=datetime.now().strftime('%Y-%m-%d %H:%M'))

@app.route('/<sport>')
def sport_picks(sport):
    """Show ATS picks for a sport"""
    sport = sport.upper()
    if sport not in ['NBA', 'NFL', 'NHL', 'NCAAF', 'NCAAB']:
        return "Sport not found", 404
    
    ats = ATSSystem()
    
    # Get past 3 days + next 7 days using threshold-based picks
    from datetime import timedelta
    from collections import defaultdict
    
    today = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    end_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
    
    # Get all games in range
    conn = sqlite3.connect('sports_predictions_original.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id, g.status,
               bl.spread, bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ? AND g.game_date >= ? AND g.game_date <= ?
        ORDER BY g.game_date ASC
    """, (sport, start_date, end_date))
    games = cursor.fetchall()
    conn.close()
    
    # Generate threshold-based picks
    ml_picks = ats.generate_moneyline_picks(sport, days_ahead=10)
    spread_picks = ats.generate_spread_picks(sport, days_ahead=10)
    total_picks = ats.generate_total_picks(sport, days_ahead=10)
    
    # Create lookup dicts by game_id
    ml_by_game = {p['game_id']: p for p in ml_picks}
    spread_by_game = {p['game_id']: p for p in spread_picks}
    total_by_game = {p['game_id']: p for p in total_picks}
    
    # Organize games by date
    games_by_date = defaultdict(dict)
    
    for game in games:
        game_id, date, home, away, status, spread, total = game
        key = (away, home)
        
        # Get picks for this game
        ml_pick = ml_by_game.get(game_id)
        sp_pick = spread_by_game.get(game_id)
        tot_pick = total_by_game.get(game_id)
        
        games_by_date[date][key] = {
            'matchup': f"{away} @ {home}",
            'ml': ml_pick['pick_team'] if ml_pick else None,
            'spread': f"{sp_pick['pick_team']} ({sp_pick['model_spread']:+.1f})" if sp_pick else None,
            'total': f"{tot_pick['pick_type']} ({tot_pick['model_total']})" if tot_pick else None,
            'status': status
        }
    
    sorted_dates = sorted(games_by_date.keys())

    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>{{ sport }} ATS Picks</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            padding: 20px;
        }
        .container { max-width: 1600px; margin: 0 auto; }
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding: 25px;
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
        }
        h1 { font-size: 2.5em; margin-bottom: 10px; }
        .back-btn {
            display: inline-block;
            padding: 10px 20px;
            background: rgba(255,255,255,0.1);
            border-radius: 8px;
            text-decoration: none;
            color: white;
            margin-bottom: 20px;
        }
        .back-btn:hover { background: rgba(255,255,255,0.2); }
        .date-section {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 25px;
        }
        .date-header {
            font-size: 1.8em;
            margin-bottom: 20px;
            color: #fbbf24;
            border-bottom: 2px solid #fbbf24;
            padding-bottom: 10px;
        }
        table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
        }
        thead th {
            background: rgba(251, 191, 36, 0.2);
            padding: 15px;
            text-align: left;
            font-size: 1.1em;
            border-bottom: 2px solid #fbbf24;
        }
        tbody tr {
            background: rgba(255,255,255,0.05);
            transition: background 0.2s;
        }
        tbody tr:hover {
            background: rgba(255,255,255,0.1);
        }
        td {
            padding: 15px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .matchup-cell {
            font-weight: bold;
            font-size: 1.1em;
            min-width: 300px;
        }
        .pick-cell { color: #10b981; font-weight: 600; }
        .empty-cell { color: #64748b; font-style: italic; }
        .no-picks {
            text-align: center;
            padding: 60px;
            opacity: 0.7;
            font-size: 1.3em;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="back-btn">← Back to Sports</a>
        <a href="/sport/{{ sport }}/trends" class="back-btn" style="margin-left: 10px;">📈 Team Trends</a>
        <a href="/results/{{ sport.lower() }}" class="back-btn" style="margin-left: 10px;">📊 View Results</a>
        <a href="#{{ today }}" class="back-btn" style="margin-left: 10px; background: #10b981;">🎯 Jump to Today</a>
        
        <div class="header">
            <h1>{{ icon }} {{ sport }} PICKS</h1>
            <p style="font-size: 1.1em; opacity: 0.8;">System Teams Only • Last 3 Days + Next 7 Days</p>
        </div>
        
        {% if sorted_dates %}
            {% for date in sorted_dates %}
            <div class="date-section" id="{{ date }}">
                <div class="date-header">📅 {{ date }}{% if date == today %} 🔴 TODAY{% endif %}</div>
                <table>
                    <thead>
                        <tr>
                            <th class="matchup-cell">MATCHUP</th>
                            <th>💰 MONEYLINE</th>
                            <th>📊 SPREAD</th>
                            <th>🎯 TOTAL</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for key, game in games_by_date[date].items() %}
                        <tr>
                            <td class="matchup-cell">{{ game.matchup }}</td>
                            <td class="pick-cell">{% if game.ml %}{{ game.ml }}{% else %}<span class="empty-cell">-</span>{% endif %}</td>
                            <td class="pick-cell">{% if game.spread %}{{ game.spread }}{% else %}<span class="empty-cell">-</span>{% endif %}</td>
                            <td class="pick-cell">{% if game.total %}{{ game.total }}{% else %}<span class="empty-cell">-</span>{% endif %}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endfor %}
        {% else %}
        <div class="date-section">
            <div class="no-picks">
                ❌ No picks available for {{ sport }}<br>
                <span style="font-size: 0.8em; opacity: 0.7;">No upcoming games or no system teams match criteria</span>
            </div>
        </div>
        {% endif %}
    </div>
</body>
</html>
    """,
    sport=sport,
    icon={'NBA': '🏀', 'NFL': '🏈', 'NHL': '🏒', 'NCAAF': '🏟️', 'NCAAB': '🏀'}[sport],
    sorted_dates=sorted_dates,
    games_by_date=games_by_date,
    today=today)

@app.route('/sport/<sport>/trends')
def sport_trends(sport):
    """Show team trends/standings for a sport"""
    sport = sport.upper()
    if sport not in ['NBA', 'NFL', 'NHL', 'NCAAF', 'NCAAB']:
        return "Sport not found", 404
    
    conn = sqlite3.connect('sports_predictions_original.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT team_name, wins, losses, win_pct,
               ats_wins, ats_losses, ats_pct,
               over_wins, under_wins, over_pct,
               vs_above_500_wins, vs_above_500_losses, vs_above_500_pct,
               vs_below_500_wins, vs_below_500_losses, vs_below_500_pct,
               last_updated
        FROM team_records
        WHERE sport = ?
        ORDER BY win_pct DESC
    """, (sport,))
    
    teams = cursor.fetchall()
    conn.close()
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>{{ sport }} Team Trends</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            padding: 20px;
        }
        .container { max-width: 2200px; margin: 0 auto; }
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding: 25px;
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
        }
        h1 { font-size: 2.5em; margin-bottom: 10px; }
        .back-btn {
            display: inline-block;
            padding: 10px 20px;
            background: rgba(255,255,255,0.1);
            border-radius: 8px;
            text-decoration: none;
            color: white;
            margin-bottom: 20px;
        }
        .back-btn:hover { background: rgba(255,255,255,0.2); }
        table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            overflow: hidden;
            font-size: 0.9em;
        }
        thead th {
            background: rgba(251, 191, 36, 0.2);
            padding: 8px 6px;
            text-align: center;
            font-size: 0.85em;
            border-bottom: 2px solid #fbbf24;
            cursor: pointer;
            user-select: none;
        }
        thead th:hover { background: rgba(251, 191, 36, 0.3); }
        tbody tr {
            background: rgba(255,255,255,0.05);
            transition: background 0.2s;
        }
        tbody tr:hover { background: rgba(255,255,255,0.1); }
        td {
            padding: 8px 6px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            text-align: center;
        }
        .team-name { font-weight: bold; text-align: left; min-width: 140px; }
        .good { color: #10b981; }
        .bad { color: #ef4444; }
        .section-header { background: rgba(139, 92, 246, 0.3) !important; font-weight: bold; }
        .updated { 
            text-align: center; 
            margin-top: 20px; 
            opacity: 0.6;
            font-size: 0.9em;
        }
    </style>
    <script>
        function sortTable(column) {
            const table = document.querySelector('tbody');
            const rows = Array.from(table.querySelectorAll('tr'));
            
            rows.sort((a, b) => {
                const aVal = parseFloat(a.children[column].textContent);
                const bVal = parseFloat(b.children[column].textContent);
                return bVal - aVal;
            });
            
            rows.forEach(row => table.appendChild(row));
        }
    </script>
</head>
<body>
    <div class="container">
        <a href="/{{ sport.lower() }}" class="back-btn">← Back to Picks</a>
        <a href="/" class="back-btn" style="margin-left: 10px;">🏠 Home</a>
        
        <div class="header">
            <h1>{{ icon }} {{ sport }} TEAM TRENDS</h1>
            <p style="font-size: 1.1em; opacity: 0.8;">ML • ATS • O/U • Situational Records</p>
        </div>
        
        {% if teams %}
        <table>
            <thead>
                <tr>
                    <th rowspan="2" class="team-name">TEAM</th>
                    <th colspan="3" class="section-header">MONEYLINE</th>
                    <th colspan="3" class="section-header">ATS</th>
                    <th colspan="3" class="section-header">OVER/UNDER</th>
                    <th colspan="3" class="section-header">VS >.500</th>
                    <th colspan="3" class="section-header">VS <.500</th>
                </tr>
                <tr>
                    <th onclick="sortTable(1)">W</th>
                    <th onclick="sortTable(2)">L</th>
                    <th onclick="sortTable(3)">%</th>
                    <th onclick="sortTable(4)">W</th>
                    <th onclick="sortTable(5)">L</th>
                    <th onclick="sortTable(6)">%</th>
                    <th onclick="sortTable(7)">O</th>
                    <th onclick="sortTable(8)">U</th>
                    <th onclick="sortTable(9)">O%</th>
                    <th onclick="sortTable(10)">W</th>
                    <th onclick="sortTable(11)">L</th>
                    <th onclick="sortTable(12)">%</th>
                    <th onclick="sortTable(13)">W</th>
                    <th onclick="sortTable(14)">L</th>
                    <th onclick="sortTable(15)">%</th>
                </tr>
            </thead>
            <tbody>
                {% for team in teams %}
                <tr>
                    <td class="team-name">{{ team[0] }}</td>
                    <td>{{ team[1] }}</td>
                    <td>{{ team[2] }}</td>
                    <td class="{% if team[3] > 0.6 %}good{% elif team[3] < 0.4 %}bad{% endif %}">
                        {{ "%.0f"|format(team[3] * 100) }}
                    </td>
                    <td>{{ team[4] }}</td>
                    <td>{{ team[5] }}</td>
                    <td class="{% if team[6] > 0.6 %}good{% elif team[6] < 0.4 %}bad{% endif %}">
                        {{ "%.0f"|format(team[6] * 100) }}
                    </td>
                    <td>{{ team[7] }}</td>
                    <td>{{ team[8] }}</td>
                    <td class="{% if team[9] > 0.6 %}good{% elif team[9] < 0.4 %}bad{% endif %}">
                        {{ "%.0f"|format(team[9] * 100) }}
                    </td>
                    <td>{{ team[10] }}</td>
                    <td>{{ team[11] }}</td>
                    <td class="{% if team[12] > 0.7 %}good{% elif team[12] < 0.3 %}bad{% endif %}">
                        {{ "%.0f"|format(team[12] * 100) }}
                    </td>
                    <td>{{ team[13] }}</td>
                    <td>{{ team[14] }}</td>
                    <td class="{% if team[15] > 0.8 %}good{% elif team[15] < 0.4 %}bad{% endif %}">
                        {{ "%.0f"|format(team[15] * 100) }}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        <div class="updated">
            Last updated: {{ teams[0][16] if teams else 'N/A' }}
        </div>
        
        <div style="margin-top: 30px; padding: 20px; background: rgba(255,255,255,0.05); border-radius: 10px;">
            <h3 style="color: #fbbf24; margin-bottom: 15px;">📖 Glossary</h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; font-size: 0.9em;">
                <div><strong>ML:</strong> Moneyline (straight win/loss)</div>
                <div><strong>ATS:</strong> Against The Spread</div>
                <div><strong>O/U:</strong> Over/Under totals</div>
                {% if sport == 'NBA' %}
                <div><strong>VS >.500:</strong> vs teams above .500</div>
                <div><strong>VS <.500:</strong> vs teams below .500</div>
                {% endif %}
            </div>
        </div>
        {% else %}
        <div style="text-align: center; padding: 60px; opacity: 0.7;">
            No team data available for {{ sport }}
        </div>
        {% endif %}
    </div>
</body>
</html>
    """,
    sport=sport,
    icon={'NBA': '🏀', 'NFL': '🏈', 'NHL': '🏒', 'NCAAF': '🏟️', 'NCAAB': '🏀'}[sport],
    teams=teams)

@app.route('/results/<sport>')
def sport_results(sport):
    """Show results and W-L records for a sport"""
    sport = sport.upper()
    if sport not in ['NBA', 'NFL', 'NHL', 'NCAAF', 'NCAAB']:
        return "Sport not found", 404
    
    conn = sqlite3.connect('sports_predictions_original.db')
    cursor = conn.cursor()
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Get last 7 days of results
    cursor.execute("""
        SELECT sp.game_date, sp.pick_type, sp.pick_team, sp.pick_value, sp.result,
               g.home_team_id, g.away_team_id, g.home_score, g.away_score,
               bl.spread, bl.total
        FROM system_picks sp
        JOIN games g ON sp.game_id = g.game_id
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE sp.sport = ? AND g.status = 'final'
        AND date(sp.game_date) >= date('now', '-7 days')
        ORDER BY sp.game_date DESC, g.home_team_id
    """, (sport,))
    
    picks = cursor.fetchall()
    
    # Get daily summaries
    cursor.execute("""
        SELECT game_date,
               SUM(CASE WHEN pick_type = 'MONEYLINE' AND result = 'WIN' THEN 1 ELSE 0 END) as ml_w,
               SUM(CASE WHEN pick_type = 'MONEYLINE' AND result = 'LOSS' THEN 1 ELSE 0 END) as ml_l,
               SUM(CASE WHEN pick_type = 'SPREAD' AND result = 'WIN' THEN 1 ELSE 0 END) as sp_w,
               SUM(CASE WHEN pick_type = 'SPREAD' AND result = 'LOSS' THEN 1 ELSE 0 END) as sp_l,
               SUM(CASE WHEN pick_type IN ('OVER', 'UNDER') AND result = 'WIN' THEN 1 ELSE 0 END) as tot_w,
               SUM(CASE WHEN pick_type IN ('OVER', 'UNDER') AND result = 'LOSS' THEN 1 ELSE 0 END) as tot_l
        FROM system_picks
        WHERE sport = ? AND result IS NOT NULL
        AND date(game_date) >= date('now', '-7 days')
        GROUP BY game_date
        ORDER BY game_date DESC
    """, (sport,))
    
    daily_records = cursor.fetchall()
    conn.close()
    
    # Group picks by date and game
    from collections import defaultdict
    picks_by_date = defaultdict(lambda: defaultdict(list))
    for pick in picks:
        date = pick[0]
        game_key = (pick[5], pick[6])  # (home_team, away_team)
        picks_by_date[date][game_key].append(pick)
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>{{ sport }} Results</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            padding: 20px;
        }
        .container { max-width: 1600px; margin: 0 auto; }
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding: 25px;
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
        }
        h1 { font-size: 2.5em; margin-bottom: 10px; }
        .back-btn {
            display: inline-block;
            padding: 10px 20px;
            background: rgba(255,255,255,0.1);
            border-radius: 8px;
            text-decoration: none;
            color: white;
            margin-bottom: 20px;
        }
        .back-btn:hover { background: rgba(255,255,255,0.2); }
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .summary-card {
            background: rgba(255,255,255,0.05);
            padding: 20px;
            border-radius: 12px;
            text-align: center;
        }
        .summary-card h3 { margin-bottom: 10px; font-size: 1.3em; }
        .record { font-size: 2em; font-weight: bold; }
        .win { color: #10b981; }
        .loss { color: #ef4444; }
        .date-section {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 25px;
        }
        .date-header {
            font-size: 1.8em;
            margin-bottom: 20px;
            color: #fbbf24;
            border-bottom: 2px solid #fbbf24;
            padding-bottom: 10px;
        }
        table { width: 100%; border-collapse: collapse; }
        thead th {
            background: rgba(251, 191, 36, 0.2);
            padding: 12px;
            text-align: left;
            border-bottom: 2px solid #fbbf24;
        }
        td { padding: 12px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .result-win { color: #10b981; font-weight: bold; }
        .result-loss { color: #ef4444; font-weight: bold; }
        .result-push { color: #fbbf24; font-weight: bold; }
        .game-card {
            background: rgba(255,255,255,0.03);
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 10px;
            border-left: 3px solid #fbbf24;
        }
        .game-header {
            font-size: 1.2em;
            font-weight: bold;
            margin-bottom: 10px;
            color: #fbbf24;
        }
        .pick-list {
            list-style: none;
            padding-left: 0;
        }
        .pick-list li {
            padding: 8px 0;
            padding-left: 20px;
            position: relative;
        }
        .pick-list li:before {
            content: '•';
            position: absolute;
            left: 0;
            color: #64748b;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/{{ sport.lower() }}" class="back-btn">← Back to Picks</a>
        <a href="#{{ today }}" class="back-btn" style="margin-left: 10px; background: #10b981;">🎯 Jump to Today</a>
        
        <div class="header">
            <h1>{{ icon }} {{ sport }} RESULTS</h1>
            <p style="font-size: 1.1em; opacity: 0.8;">Last 7 Days Performance</p>
        </div>
        
        {% if daily_records %}
        <div class="summary-grid">
            {% for record in daily_records[:1] %}
            <div class="summary-card">
                <h3>💰 Moneyline</h3>
                <div class="record">
                    <span class="win">{{ record[1] }}</span> - <span class="loss">{{ record[2] }}</span>
                </div>
            </div>
            <div class="summary-card">
                <h3>📊 Spread</h3>
                <div class="record">
                    <span class="win">{{ record[3] }}</span> - <span class="loss">{{ record[4] }}</span>
                </div>
            </div>
            <div class="summary-card">
                <h3>🎯 Totals</h3>
                <div class="record">
                    <span class="win">{{ record[5] }}</span> - <span class="loss">{{ record[6] }}</span>
                </div>
            </div>
            {% endfor %}
        </div>
        
        {% for record in daily_records %}
        <div class="date-section" id="{{ record[0] }}">
            <div class="date-header">
                📅 {{ record[0] }}{% if record[0] == today %} 🔴 TODAY{% endif %}<br>
                <span style="font-size: 0.9em; opacity: 0.9;">ML: {{ record[1] }}-{{ record[2] }} | Spread: {{ record[3] }}-{{ record[4] }} | Total: {{ record[5] }}-{{ record[6] }}</span>
            </div>
            
            {% if picks_by_date[record[0]] %}
                {% for game_key, game_picks in picks_by_date[record[0]].items() %}
                <div class="game-card">
                    <div class="game-header">
                        {{ game_picks[0][6] }} @ {{ game_picks[0][5] }} ({{ game_picks[0][8] }}-{{ game_picks[0][7] }})
                    </div>
                    <ul class="pick-list">
                        {% for pick in game_picks %}
                        <li>
                            <strong>{{ pick[1] }}:</strong> 
                            {% if pick[1] == 'MONEYLINE' %}
                                {{ pick[2] }}
                            {% elif pick[1] == 'SPREAD' %}
                                {{ pick[2] }} {% if pick[3] %}{{ pick[3] }}{% endif %}
                            {% elif pick[1] in ['OVER', 'UNDER'] %}
                                {{ pick[1] }} {% if pick[10] %}{{ pick[10] }}{% endif %}
                            {% endif %}
                            {% if pick[4] == 'WIN' %}<span style="color: #10b981;">✓</span>{% elif pick[4] == 'LOSS' %}<span style="color: #ef4444;">✗</span>{% endif %}
                        </li>
                        {% endfor %}
                    </ul>
                </div>
                {% endfor %}
            {% endif %}
        </div>
        {% endfor %}
        {% else %}
        <div class="date-section">
            <div style="text-align: center; padding: 60px; opacity: 0.7;">
                No results yet for {{ sport }}
            </div>
        </div>
        {% endif %}
    </div>
</body>
</html>
    """,
    sport=sport,
    icon={'NBA': '🏀', 'NFL': '🏈', 'NHL': '🏒', 'NCAAF': '🏟️', 'NCAAB': '🏀'}[sport],
    daily_records=daily_records,
    picks_by_date=picks_by_date,
    today=today)

if __name__ == '__main__':
    import socket
    port = 8000
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('0.0.0.0', port)) != 0:
                break
            port += 1
    
    print("\n" + "="*60)
    print("🎯 ATS BETTING PICKS - LIVE")
    print("="*60)
    print(f"🌐 Visit http://localhost:{port}")
    print("="*60 + "\n")
    
    app.run(debug=False, host='0.0.0.0', port=port)
