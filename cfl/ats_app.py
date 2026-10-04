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
    
    # Get all games with prediction data
    conn = sqlite3.connect('sports_predictions_original.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id, g.status,
               bl.spread, bl.total,
               p.xgboost_home_prob, p.catboost_home_prob, p.elo_home_prob, p.meta_home_prob
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        LEFT JOIN predictions p ON g.game_id = p.game_id AND g.sport = p.sport
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
        game_id, date, home, away, status, spread, total, xgb_prob, cat_prob, elo_prob, meta_prob = game
        key = (away, home)
        
        # Get picks for this game
        ml_pick = ml_by_game.get(game_id)
        sp_pick = spread_by_game.get(game_id)
        tot_pick = total_by_game.get(game_id)
        
        # Convert probabilities to percentages
        xgb_pct = f"{xgb_prob*100:.1f}%" if xgb_prob else "--"
        cat_pct = f"{cat_prob*100:.1f}%" if cat_prob else "--"
        elo_pct = f"{elo_prob*100:.1f}%" if elo_prob else "--"
        
        games_by_date[date][key] = {
            'matchup': f"{away} @ {home}",
            'ml': ml_pick['pick_team'] if ml_pick else None,
            'spread': f"{sp_pick['pick_team']} ({sp_pick['model_spread']:+.1f})" if sp_pick else None,
            'total': f"{tot_pick['pick_type']} ({tot_pick['model_total']})" if tot_pick else None,
            'status': status,
            'xgboost': xgb_pct,
            'catboost': cat_pct,
            'elo': elo_pct
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
            display: none;
        }
        .date-section.visible {
            display: block;
        }
        .date-header {
            font-size: 1.5em;
            margin-bottom: 25px;
            color: #fbbf24;
            font-weight: 600;
        }
        .games-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(520px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .game-card {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px;
            overflow: hidden;
            transition: all 0.2s;
        }
        .game-card:hover {
            background: rgba(255,255,255,0.08);
            border-color: #fbbf24;
        }
        .game-status {
            background: rgba(251, 191, 36, 0.15);
            padding: 8px 15px;
            font-size: 0.85em;
            color: #fbbf24;
            text-transform: uppercase;
            font-weight: 600;
        }
        .game-body {
            display: flex;
            padding: 20px;
            gap: 20px;
        }
        .teams-section {
            flex: 1;
            min-width: 0;
        }
        .team-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .team-row:last-child {
            border-bottom: none;
        }
        .team-row.winner {
            font-weight: bold;
        }
        .team-info {
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
        }
        .team-name {
            font-size: 1.05em;
        }
        .team-record {
            color: #64748b;
            font-size: 0.85em;
        }
        .pick-indicator {
            color: #10b981;
            font-size: 1.3em;
            font-weight: bold;
        }
        .pick-value {
            color: #10b981;
            font-weight: 600;
            font-size: 0.95em;
        }
        .prediction-panel {
            background: rgba(139, 92, 246, 0.1);
            border-left: 3px solid #8b5cf6;
            padding: 15px;
            min-width: 180px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .prediction-panel-title {
            font-size: 0.75em;
            color: #a78bfa;
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 5px;
        }
        .model-prediction {
            display: flex;
            justify-content: space-between;
            font-size: 0.9em;
            padding: 4px 0;
        }
        .model-name {
            color: #c4b5fd;
            font-weight: 500;
        }
        .model-value {
            color: white;
            font-weight: 600;
        }
        .meta-pick {
            background: rgba(16, 185, 129, 0.2);
            border: 1px solid #10b981;
            color: #10b981;
            padding: 8px;
            border-radius: 6px;
            text-align: center;
            font-weight: bold;
            margin-top: 5px;
            font-size: 0.95em;
        }
        .no-picks {
            text-align: center;
            padding: 60px;
            opacity: 0.7;
            font-size: 1.3em;
        }
        .date-nav {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 15px;
            margin: 30px 0;
            padding: 20px;
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
        }
        .nav-arrow {
            background: rgba(251, 191, 36, 0.2);
            border: 2px solid #fbbf24;
            color: #fbbf24;
            font-size: 1.5em;
            width: 45px;
            height: 45px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s;
            user-select: none;
        }
        .nav-arrow:hover {
            background: rgba(251, 191, 36, 0.4);
            transform: scale(1.1);
        }
        .date-bubbles {
            display: flex;
            gap: 10px;
            overflow-x: auto;
            padding: 5px;
            max-width: 900px;
        }
        .date-bubble {
            background: rgba(255,255,255,0.1);
            border: 2px solid rgba(255,255,255,0.2);
            border-radius: 25px;
            padding: 12px 20px;
            min-width: 120px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
            font-weight: 500;
        }
        .date-bubble:hover {
            background: rgba(255,255,255,0.15);
            border-color: #fbbf24;
        }
        .date-bubble.active {
            background: #fbbf24;
            border-color: #fbbf24;
            color: #0f172a;
            font-weight: bold;
        }
        .date-bubble.today {
            border-color: #10b981;
            color: #10b981;
        }
        .date-bubble.active.today {
            background: #10b981;
            color: white;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="back-btn">← Back to Sports</a>
        <a href="/sport/{{ sport }}/trends" class="back-btn" style="margin-left: 10px;">📈 Team Trends</a>
        <a href="/results/{{ sport.lower() }}" class="back-btn" style="margin-left: 10px;">📊 View Results</a>
        
        <div class="header">
            <h1>{{ icon }} {{ sport }} PICKS</h1>
            <p style="font-size: 1.1em; opacity: 0.8;">System Teams Only</p>
        </div>
        
        <div class="date-nav">
            <div class="nav-arrow" onclick="previousWeek()">‹</div>
            <div class="date-bubbles" id="dateBubbles"></div>
            <div class="nav-arrow" onclick="nextWeek()">›</div>
        </div>
        
        {% if sorted_dates %}
            {% for date in sorted_dates %}
            <div class="date-section" id="{{ date }}">
                <div class="date-header">📅 {{ date }}{% if date == today %} 🔴 TODAY{% endif %}</div>
                <div class="games-grid">
                    {% for key, game in games_by_date[date].items() %}
                    <div class="game-card">
                        <div class="game-status">{{ game.status }}</div>
                        <div class="game-body">
                            <div class="teams-section">
                                <!-- Away Team -->
                                <div class="team-row{% if game.ml == key[0] %} winner{% endif %}">
                                    <div class="team-info">
                                        <div class="team-name">{{ key[0] }}</div>
                                    </div>
                                    {% if game.ml == key[0] %}<div class="pick-indicator">▶</div>{% endif %}
                                </div>
                                <!-- Home Team -->
                                <div class="team-row{% if game.ml == key[1] %} winner{% endif %}">
                                    <div class="team-info">
                                        <div class="team-name">{{ key[1] }}</div>
                                    </div>
                                    {% if game.ml == key[1] %}<div class="pick-indicator">▶</div>{% endif %}
                                </div>
                                <!-- Spread & Total Picks -->
                                {% if game.spread or game.total %}
                                <div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid rgba(255,255,255,0.1);">
                                    {% if game.spread %}<div class="pick-value">📊 {{ game.spread }}</div>{% endif %}
                                    {% if game.total %}<div class="pick-value">🎯 {{ game.total }}</div>{% endif %}
                                </div>
                                {% endif %}
                            </div>
                            <div class="prediction-panel">
                                <div class="prediction-panel-title">Prediction Models</div>
                                <div class="model-prediction">
                                    <span class="model-name">XGBoost</span>
                                    <span class="model-value">{{ game.xgboost }}</span>
                                </div>
                                <div class="model-prediction">
                                    <span class="model-name">CatBoost</span>
                                    <span class="model-value">{{ game.catboost }}</span>
                                </div>
                                <div class="model-prediction">
                                    <span class="model-name">Elo Rating</span>
                                    <span class="model-value">{{ game.elo }}</span>
                                </div>
                                {% if game.ml %}
                                <div class="meta-pick">META: {{ game.ml }}</div>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
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
    
    <script>
        const allDates = {{ sorted_dates|tojson }};
        const today = '{{ today }}';
        let currentWeekStart = 0;
        let activeDate = today;
        const datesPerWeek = 7;
        
        function formatDate(dateStr) {
            const d = new Date(dateStr + 'T12:00:00');
            const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            return days[d.getDay()] + ', ' + months[d.getMonth()] + ' ' + d.getDate();
        }
        
        function showDate(date) {
            // Hide all date sections
            document.querySelectorAll('.date-section').forEach(section => {
                section.classList.remove('visible');
            });
            
            // Show selected date
            const section = document.getElementById(date);
            if (section) {
                section.classList.add('visible');
                activeDate = date;
            }
        }
        
        function renderDateBubbles() {
            const container = document.getElementById('dateBubbles');
            container.innerHTML = '';
            
            const end = Math.min(currentWeekStart + datesPerWeek, allDates.length);
            const weekDates = allDates.slice(currentWeekStart, end);
            
            // If active date is not in current week, show first date of week
            if (!weekDates.includes(activeDate)) {
                activeDate = weekDates[0];
                showDate(activeDate);
            }
            
            weekDates.forEach((date) => {
                const bubble = document.createElement('div');
                bubble.className = 'date-bubble';
                if (date === today) bubble.classList.add('today');
                if (date === activeDate) bubble.classList.add('active');
                
                bubble.innerHTML = formatDate(date);
                bubble.onclick = () => {
                    document.querySelectorAll('.date-bubble').forEach(b => b.classList.remove('active'));
                    bubble.classList.add('active');
                    showDate(date);
                };
                
                container.appendChild(bubble);
            });
        }
        
        function previousWeek() {
            if (currentWeekStart > 0) {
                currentWeekStart = Math.max(0, currentWeekStart - datesPerWeek);
                renderDateBubbles();
            }
        }
        
        function nextWeek() {
            if (currentWeekStart + datesPerWeek < allDates.length) {
                currentWeekStart += datesPerWeek;
                renderDateBubbles();
            }
        }
        
        // Initialize on page load
        document.addEventListener('DOMContentLoaded', () => {
            // Find today's index and center the week around it
            const todayIdx = allDates.indexOf(today);
            if (todayIdx >= 0) {
                currentWeekStart = Math.max(0, todayIdx - 3);
                activeDate = today;
            } else {
                // If no today, show first date
                activeDate = allDates[0];
            }
            
            showDate(activeDate);
            renderDateBubbles();
        });
    </script>
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
    from collections import defaultdict
    from datetime import datetime, timedelta
    import sqlite3

    sport = sport.upper()
    valid_sports = [
        'NBA', 'NFL', 'NHL', 'NCAAF', 'NCAAB',
        'NCAAM', 'NCAAW', 'WNBA', 'MLS', 'SOCCER', 'NCAAMF'
    ]
    if sport not in valid_sports:
        return "Sport not found", 404

    conn = sqlite3.connect('sports_predictions_original.db')
    cursor = conn.cursor()

    today = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    end_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # Get picks
    cursor.execute("""
        SELECT sp.game_date, sp.pick_type, sp.pick_team, sp.pick_value, sp.result,
               g.home_team_id, g.away_team_id, g.home_score, g.away_score,
               bl.spread, bl.total
        FROM system_picks sp
        JOIN games g ON sp.game_id = g.game_id
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE sp.sport = ? AND g.status = 'final'
          AND date(sp.game_date) >= date(?)
          AND date(sp.game_date) < date(?)
        ORDER BY sp.game_date DESC, g.home_team_id
    """, (sport, start_date, today))
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
          AND date(game_date) >= date(?)
          AND date(game_date) < date(?)
        GROUP BY game_date
        ORDER BY game_date DESC
    """, (sport, start_date, today))
    daily_records = cursor.fetchall()
    conn.close()

    # Calculate totals
    total_ml_w = sum(r[1] for r in daily_records)
    total_ml_l = sum(r[2] for r in daily_records)
    total_sp_w = sum(r[3] for r in daily_records)
    total_sp_l = sum(r[4] for r in daily_records)
    total_tot_w = sum(r[5] for r in daily_records)
    total_tot_l = sum(r[6] for r in daily_records)

    # Group picks by date and game
    picks_by_date = defaultdict(lambda: defaultdict(list))
    for pick in picks:
        date = pick[0]
        game_key = (pick[5], pick[6])
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
        .pick-list { list-style: none; padding-left: 0; }
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
        <a href="/" class="back-btn" style="margin-left: 10px;">🏠 Home</a>
        
        <div class="header">
            <h1>{{ icon }} {{ sport }} RESULTS</h1>
            <p style="font-size: 1.1em; opacity: 0.8;">Last 30 Days Performance</p>
        </div>
        
        {% if daily_records %}
        <div class="summary-grid">
            <div class="summary-card">
                <h3>💰 Moneyline</h3>
                <div class="record">
                    <span class="win">{{ total_ml_w }}</span> - <span class="loss">{{ total_ml_l }}</span>
                </div>
            </div>
            <div class="summary-card">
                <h3>📊 Spread</h3>
                <div class="record">
                    <span class="win">{{ total_sp_w }}</span> - <span class="loss">{{ total_sp_l }}</span>
                </div>
            </div>
            <div class="summary-card">
                <h3>🎯 Totals</h3>
                <div class="record">
                    <span class="win">{{ total_tot_w }}</span> - <span class="loss">{{ total_tot_l }}</span>
                </div>
            </div>
        </div>
        
        {% for record in daily_records %}
        <div class="date-section">
            <div class="date-header">
                📅 {{ record[0] }}<br>
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
    icon={'NBA': '🏀', 'NFL': '🏈', 'NHL': '🏒', 'NCAAF': '🏟️', 'NCAAB': '🏀', 'NCAAM': '🏀', 'NCAAW': '🏀', 'WNBA': '🏀', 'MLS': '⚽', 'SOCCER': '⚽', 'NCAAMF': '🏈'}[sport],
    daily_records=daily_records,
    picks_by_date=picks_by_date,
    today=today,
    total_ml_w=total_ml_w,
    total_ml_l=total_ml_l,
    total_sp_w=total_sp_w,
    total_sp_l=total_sp_l,
    total_tot_w=total_tot_w,
    total_tot_l=total_tot_l)

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