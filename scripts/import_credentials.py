"""
Import per-athlete credentials from a CSV file into the credentials DB table.

CSV format (header row required):
  athlete_id,strava_access_token,strava_refresh_token,strava_token_expires_at,garmin_email,garmin_password

Fields that are empty or absent are left unchanged in the DB.

Usage:
  python scripts/import_credentials.py credentials.csv
"""

import csv
import sys
import os

# Resolve project root (one level up from scripts/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from db.schema import init_db, save_credentials


def _load(path: str) -> None:
    imported = 0
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            athlete_id = row.get("athlete_id", "").strip()
            if not athlete_id:
                print(f"  skip: missing athlete_id in row {reader.line_num}")
                continue

            kwargs: dict = {"athlete_id": athlete_id}

            def _val(key: str) -> str:
                v = row.get(key, "").strip()
                return "" if v.startswith("<") and v.endswith(">") else v

            access   = _val("strava_access_token")
            refresh  = _val("strava_refresh_token")
            exp_raw  = _val("strava_token_expires_at")
            g_email  = _val("garmin_email")
            g_pass   = _val("garmin_password")

            if access:  kwargs["strava_access_token"]     = access
            if refresh: kwargs["strava_refresh_token"]    = refresh
            if exp_raw: kwargs["strava_token_expires_at"] = int(exp_raw)
            if g_email: kwargs["garmin_email"]            = g_email
            if g_pass:  kwargs["garmin_password"]         = g_pass

            save_credentials(**kwargs)
            has_strava  = bool(access or refresh)
            has_garmin  = bool(g_email or g_pass)
            parts = []
            if has_strava: parts.append("strava")
            if has_garmin: parts.append("garmin (password encrypted)")
            print(f"  imported {athlete_id}: {', '.join(parts) or 'no fields set'}")
            imported += 1

    print(f"\nDone — {imported} athlete(s) imported.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/import_credentials.py <credentials.csv>")
        sys.exit(1)

    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print(f"Error: file not found: {csv_path}")
        sys.exit(1)

    init_db()
    _load(csv_path)
