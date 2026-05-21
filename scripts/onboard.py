#!/usr/bin/env python3
"""
Interactive onboarding — creates an athlete profile and saves it to the DB.

Usage:
    source .venv/bin/activate
    python scripts/onboard.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from datetime import date


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    while True:
        val = input(display).strip()
        if val:
            return val
        if default:
            return default
        print("  This field is required.")


def ask_optional(prompt: str, default: str = "") -> str:
    display = f"{prompt} [{default}] (Enter to skip): " if default else f"{prompt} (Enter to skip): "
    return input(display).strip() or default


def ask_choice(prompt: str, choices: list[str], default: str) -> str:
    options = "/".join(choices)
    while True:
        val = ask(f"{prompt} [{options}]", default).lower()
        if val in choices:
            return val
        print(f"  Please enter one of: {options}")


def ask_date(prompt: str) -> date:
    while True:
        raw = ask(prompt + " (YYYY-MM-DD)")
        try:
            d = date.fromisoformat(raw)
            if d <= date.today():
                print("  Race date must be in the future.")
                continue
            return d
        except ValueError:
            print("  Invalid date — use YYYY-MM-DD format (e.g. 2027-01-19).")


def ask_int(prompt: str, default: int | None = None, min_val: int = 1, max_val: int = 9999) -> int:
    default_str = str(default) if default is not None else ""
    while True:
        raw = ask(prompt, default_str)
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
            print(f"  Must be between {min_val} and {max_val}.")
        except ValueError:
            print("  Please enter a whole number.")


def ask_float(prompt: str, default: float | None = None, min_val: float = 0) -> float:
    default_str = str(default) if default is not None else ""
    while True:
        raw = ask(prompt, default_str)
        try:
            val = float(raw)
            if val >= min_val:
                return val
            print(f"  Must be at least {min_val}.")
        except ValueError:
            print("  Please enter a number.")


def ask_time(prompt: str) -> int | None:
    """Ask for HH:MM finish time, return total seconds or None."""
    raw = input(f"{prompt} (HH:MM, or Enter to skip — finish only): ").strip()
    if not raw:
        return None
    try:
        parts = raw.split(":")
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 24 and 0 <= m < 60:
            return h * 3600 + m * 60
    except (ValueError, IndexError):
        pass
    print("  Invalid format — skipping finish time.")
    return None


# ---------------------------------------------------------------------------
# Location helpers
# ---------------------------------------------------------------------------

def resolve_location(label: str, default_country: str = "India") -> "Location":
    from tools.location import geocode_city
    from models.goal import Location

    while True:
        city    = ask(f"{label} city")
        country = ask(f"{label} country", default_country)

        print(f"  Looking up {city}, {country}...", end=" ", flush=True)
        loc = geocode_city(city, country)
        if loc:
            print(f"lat {loc.latitude:.3f}, lng {loc.longitude:.3f}, altitude {loc.altitude_m:.0f}m ✓")
            return loc

        # Geocoding unavailable or city not found — ask manually
        print("not found via API.")
        print("  Enter coordinates manually:")
        lat = ask_float("  Latitude  (e.g. 12.97)", min_val=-90)
        lng = ask_float("  Longitude (e.g. 77.59)", min_val=-180)
        alt = ask_float("  Altitude in metres (e.g. 920)", min_val=0)
        return Location(city=city, country=country,
                        latitude=lat, longitude=lng, altitude_m=alt)


# ---------------------------------------------------------------------------
# Strava connection
# ---------------------------------------------------------------------------

def get_strava_athlete_id() -> str | None:
    """Try to read the athlete ID from the existing Strava token. Returns None on failure."""
    try:
        from tools.strava import _valid_access_token
        from stravalib import Client
        c = Client()
        c.access_token = _valid_access_token()
        athlete = c.get_athlete()
        return str(athlete.id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main onboarding flow
# ---------------------------------------------------------------------------

def onboard() -> None:
    from db.schema import AthleteRow, engine, init_db
    from models.goal import RaceType
    from sqlalchemy.orm import Session

    init_db()

    print()
    print("=" * 55)
    print("  AI Running Coach — Athlete Onboarding")
    print("=" * 55)
    print()

    # --- Identity ---
    print("── About you ──────────────────────────────────────────")
    name = ask("Your full name")
    raw_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    athlete_id = ask("Athlete ID (used in the DB and CLI)", raw_id)

    # Check for existing profile
    with Session(engine) as s:
        if s.get(AthleteRow, athlete_id):
            overwrite = ask_choice(
                f"Profile '{athlete_id}' already exists. Overwrite?",
                ["y", "n"], "n",
            )
            if overwrite != "y":
                print("Onboarding cancelled.")
                return

    # --- Strava ---
    print()
    print("── Strava connection ───────────────────────────────────")
    strava_id = get_strava_athlete_id()
    if strava_id:
        print(f"  Strava athlete ID detected: {strava_id} ✓")
    else:
        print("  Could not auto-detect Strava ID.")
        print("  Run `python scripts/strava_auth.py <code>` to connect Strava,")
        print("  or enter your Strava numeric athlete ID manually.")
        strava_id = ask_optional("  Strava athlete ID")
    strava_id = strava_id or None

    # --- Race goal ---
    print()
    print("── Race goal ───────────────────────────────────────────")
    race_name = ask("Race name", "Mumbai Marathon 2027")
    race_type = ask_choice(
        "Race type",
        ["marathon", "half_marathon", "10k", "5k", "ultra"],
        "marathon",
    )
    race_date = ask_date("Race date")
    print()
    print("  Race location:")
    race_location = resolve_location("Race")

    # --- Training location ---
    print()
    print("  Training location:")
    training_location = resolve_location("Training")

    # --- Finish time goal ---
    print()
    print("── Performance goal ────────────────────────────────────")
    target_seconds = ask_time("Target finish time")
    if target_seconds:
        h, m = divmod(target_seconds // 60, 60)
        race_km = {"marathon": 42.195, "half_marathon": 21.1, "10k": 10, "5k": 5}.get(race_type)
        pace_str = f"  → {h}h {m:02d}min" + (f"  ({target_seconds / 60 / race_km:.2f} min/km)" if race_km else "")
        print(pace_str)

    # --- Training baseline ---
    print()
    print("── Training baseline ───────────────────────────────────")
    fitness_level = ask_choice(
        "Fitness level",
        ["beginner", "intermediate", "advanced"],
        "intermediate",
    )
    weekly_km = ask_float("Current weekly running (km)", default=30.0, min_val=1)

    print()
    raw_max_hr = input("Max heart rate in bpm (Enter to estimate from age): ").strip()
    if raw_max_hr:
        try:
            max_hr = max(100, min(220, int(raw_max_hr)))
        except ValueError:
            max_hr = None
    else:
        max_hr = None

    if max_hr is None:
        age = ask_int("Your age", min_val=10, max_val=99)
        max_hr = 220 - age
        print(f"  → Estimated max HR: {max_hr} bpm (220 − {age})")

    resting_hr_raw = input("Resting heart rate in bpm (Enter to skip): ").strip()
    resting_hr = int(resting_hr_raw) if resting_hr_raw.isdigit() else None

    # --- Summary ---
    print()
    print("=" * 55)
    print("  Summary")
    print("=" * 55)
    print(f"  Name:            {name}")
    print(f"  Athlete ID:      {athlete_id}")
    print(f"  Strava:          {strava_id or '(not connected)'}")
    print(f"  Race:            {race_name} ({race_type}) on {race_date}")
    print(f"  Race location:   {race_location.city}, {race_location.country} "
          f"({race_location.altitude_m:.0f}m)")
    print(f"  Training city:   {training_location.city}, {training_location.country} "
          f"({training_location.altitude_m:.0f}m)")
    altitude_diff = training_location.altitude_m - race_location.altitude_m
    if altitude_diff > 50:
        print(f"  Altitude drop:   {altitude_diff:.0f}m on race day → expect faster pace in {race_location.city}")
    if target_seconds:
        h, m = divmod(target_seconds // 60, 60)
        print(f"  Goal:            {h}h {m:02d}min")
    else:
        print(f"  Goal:            Finish (no time target)")
    print(f"  Weekly target:   {weekly_km:.0f} km/week")
    print(f"  Max HR:          {max_hr} bpm")
    print(f"  Fitness:         {fitness_level}")
    print()

    confirm = ask_choice("Save this profile?", ["y", "n"], "y")
    if confirm != "y":
        print("Onboarding cancelled.")
        return

    # --- Persist ---
    with Session(engine) as s:
        existing = s.get(AthleteRow, athlete_id)
        if existing:
            s.delete(existing)
            s.commit()

        s.add(AthleteRow(
            id=athlete_id,
            name=name,
            fitness_level=fitness_level,
            weekly_mileage_target_km=weekly_km,
            resting_heart_rate=resting_hr,
            max_heart_rate=max_hr,
            injury_flags=[],
            strava_athlete_id=strava_id,
            goal={
                "race_name":   race_name,
                "race_type":   race_type,
                "race_date":   race_date.isoformat(),
                "race_location": race_location.model_dump(),
                "training_location": training_location.model_dump(),
                "target_finish_seconds": target_seconds,
                "course_elevation_gain_m": None,
            },
        ))
        s.commit()

    print()
    print(f"✓ Profile saved for {name}.")
    print()
    print(f"  Start coaching with:")
    print(f"    python main.py {athlete_id}")
    print()


if __name__ == "__main__":
    onboard()
