import requests
from datetime import datetime, timedelta

def check_espn():
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20251211"
    print(f"Fetching {url}")
    r = requests.get(url)
    data = r.json()
    events = data.get('events', [])
    print(f"Found {len(events)} events.")
    for event in events:
        status = event.get('status', {}).get('type', {}).get('name')
        print(f"  - {event.get('name')} : {status}")
        
if __name__ == "__main__":
    check_espn()
