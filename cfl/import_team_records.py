#!/usr/bin/env python3
"""
Import Team Records from CSV
=============================
Quick utility to import team records from CSV when scrapers fail.

CSV Format:
Team,ML_Record,ATS_Record,O/U_Record
Philadelphia 76ers,8-6,9-3,5-9
...

Run: python3 import_team_records.py NBA nba_records.csv
"""

import sys
import csv
import sqlite3
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"


def parse_record(record_str):
    """Parse '9-3' into (9, 3, 0.75)"""
    try:
        parts = record_str.strip().split('-')
        wins = int(parts[0])
        losses = int(parts[1])
        total = wins + losses
        pct = wins / total if total > 0 else 0
        return wins, losses, pct
    except:
        return 0, 0, 0


def import_records(sport, csv_path):
    """Import records from CSV file"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Importing {sport} Team Records from {csv_path}")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    imported = 0
    
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            team = row['Team']
            
            # Parse records
            ml_w, ml_l, ml_pct = parse_record(row.get('ML_Record', '0-0'))
            ats_w, ats_l, ats_pct = parse_record(row.get('ATS_Record', '0-0'))
            ou_record = row.get('O/U_Record', '0-0')
            
            # For O/U, we need to determine over/under separately
            # Format can be "9-5" (over-under)
            ou_parts = ou_record.split('-')
            over_w = int(ou_parts[0]) if len(ou_parts) > 0 else 0
            under_w = int(ou_parts[1]) if len(ou_parts) > 1 else 0
            ou_total = over_w + under_w
            over_pct = over_w / ou_total if ou_total > 0 else 0
            under_pct = under_w / ou_total if ou_total > 0 else 0
            
            # Insert into database
            cursor.execute("""
                INSERT OR REPLACE INTO team_records
                (sport, team_name, wins, losses, win_pct,
                 ats_wins, ats_losses, ats_pct,
                 over_wins, over_losses, over_pct,
                 under_wins, under_losses, under_pct,
                 last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                sport, team,
                ml_w, ml_l, ml_pct,
                ats_w, ats_l, ats_pct,
                over_w, under_w, over_pct,
                under_w, over_w, under_pct
            ))
            
            print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {team:30s} ML:{ml_w}-{ml_l} ({ml_pct:.1%})  ATS:{ats_w}-{ats_l} ({ats_pct:.1%})  O/U:{over_w}-{under_w}")
            imported += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Imported {imported} teams")
    print(f"{'='*60}{Style.RESET_ALL}\n")


def main():
    if len(sys.argv) < 3:
        print(f"{Fore.RED}Usage: python3 import_team_records.py <SPORT> <CSV_FILE>{Style.RESET_ALL}")
        print(f"\nExample: python3 import_team_records.py NBA nba_records.csv")
        print(f"\nCSV Format:")
        print(f"  Team,ML_Record,ATS_Record,O/U_Record")
        print(f"  Philadelphia 76ers,8-6,9-3,5-9")
        sys.exit(1)
    
    sport = sys.argv[1]
    csv_path = sys.argv[2]
    
    import_records(sport, csv_path)


if __name__ == "__main__":
    main()
