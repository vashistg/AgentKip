#!/usr/bin/env python3
"""
Self-service athlete onboarding form.

Athletes fill in their profile + Garmin credentials in one go.
Garmin email is used as the athlete_id — guaranteed unique.

Usage:
    source .venv/bin/activate
    python onboarding_app.py

Share link:  http://<your-ip>:8080/onboard
"""
import os
import sys
import re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, render_template_string
from db.schema import init_db, load_athlete, save_credentials, AthleteRow, engine
from sqlalchemy.orm import Session

app = Flask(__name__)
app.secret_key = os.environ.get("CREDENTIALS_ENCRYPTION_KEY", "dev-secret")

# Coordinates for common Indian training cities
_CITY_COORDS = {
    "bengaluru":  (12.9716, 77.5946, 920.0),
    "bangalore":  (12.9716, 77.5946, 920.0),
    "mumbai":     (19.0760, 72.8777,  10.0),
    "delhi":      (28.6139, 77.2090, 216.0),
    "new delhi":  (28.6139, 77.2090, 216.0),
    "hyderabad":  (17.3850, 78.4867, 536.0),
    "pune":       (18.5204, 73.8567, 560.0),
    "chennai":    (13.0827, 80.2707,   6.0),
    "kolkata":    (22.5726, 88.3639,   9.0),
    "ahmedabad":  (23.0225, 72.5714,  53.0),
    "jaipur":     (26.9124, 75.7873, 431.0),
}

_RACE_DISTANCES = {
    "5k": 5.0, "10k": 10.0,
    "half_marathon": 21.0975, "marathon": 42.195, "ultra": 80.0,
}

# Sensible weekly mileage defaults: (fitness_level, race_type) → km
_MILEAGE_DEFAULTS = {
    ("beginner",     "5k"):            15,
    ("beginner",     "10k"):           20,
    ("beginner",     "half_marathon"): 25,
    ("beginner",     "marathon"):      30,
    ("intermediate", "5k"):            25,
    ("intermediate", "10k"):           35,
    ("intermediate", "half_marathon"): 45,
    ("intermediate", "marathon"):      55,
    ("advanced",     "5k"):            50,
    ("advanced",     "10k"):           60,
    ("advanced",     "half_marathon"): 70,
    ("advanced",     "marathon"):      80,
}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_FORM = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentKip — Get Started</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f5;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      min-height: 100vh;
      padding: 32px 16px;
    }
    .card {
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 2px 16px rgba(0,0,0,.08);
      padding: 40px;
      width: 100%;
      max-width: 480px;
    }
    .logo { font-size: 24px; font-weight: 700; color: #1a1a1a; }
    .tagline { color: #888; font-size: 14px; margin-bottom: 32px; margin-top: 2px; }
    .section-title {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #aaa;
      margin: 28px 0 16px;
    }
    .section-title:first-of-type { margin-top: 0; }
    .row { display: flex; gap: 12px; }
    .row .field { flex: 1; }
    .field { margin-bottom: 16px; }
    label {
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: #444;
      margin-bottom: 5px;
    }
    label .opt { font-weight: 400; color: #aaa; }
    input, select {
      width: 100%;
      padding: 10px 12px;
      border: 1.5px solid #e0e0e0;
      border-radius: 8px;
      font-size: 15px;
      outline: none;
      transition: border-color .15s;
      background: #fff;
      color: #1a1a1a;
    }
    input:focus, select:focus { border-color: #4f46e5; }
    .btn {
      width: 100%;
      padding: 13px;
      background: #4f46e5;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 8px;
      transition: background .15s;
    }
    .btn:hover { background: #4338ca; }
    .error {
      background: #fff1f1;
      border: 1.5px solid #fca5a5;
      color: #b91c1c;
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 13px;
      margin-bottom: 20px;
    }
    .note {
      font-size: 12px;
      color: #aaa;
      margin-top: 14px;
      text-align: center;
      line-height: 1.6;
    }
    .divider {
      border: none;
      border-top: 1px solid #f0f0f0;
      margin: 24px 0 0;
    }
  </style>
</head>
<body>
<div class="card">
  <div class="logo">AgentKip 🏃</div>
  <div class="tagline">Your AI running coach — let's get you set up</div>

  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}

  <form method="POST" autocomplete="off">

    <div class="section-title">About you</div>

    <div class="field">
      <label for="name">Full name</label>
      <input type="text" id="name" name="name" required placeholder="Shagun Vashist" value="{{ v.name }}">
    </div>

    <div class="row">
      <div class="field">
        <label for="fitness_level">Fitness level</label>
        <select id="fitness_level" name="fitness_level">
          <option value="beginner"     {% if v.fitness_level == 'beginner'     %}selected{% endif %}>Beginner</option>
          <option value="intermediate" {% if v.fitness_level == 'intermediate' %}selected{% endif %}>Intermediate</option>
          <option value="advanced"     {% if v.fitness_level == 'advanced'     %}selected{% endif %}>Advanced</option>
        </select>
      </div>
      <div class="field">
        <label for="training_city">Training city</label>
        <input type="text" id="training_city" name="training_city" required
               placeholder="Bengaluru" value="{{ v.training_city }}">
      </div>
    </div>

    <div class="row">
      <div class="field">
        <label for="resting_hr">Resting HR <span class="opt">(optional)</span></label>
        <input type="number" id="resting_hr" name="resting_hr" min="30" max="120"
               placeholder="60" value="{{ v.resting_hr }}">
      </div>
      <div class="field">
        <label for="max_hr">Max HR <span class="opt">(optional)</span></label>
        <input type="number" id="max_hr" name="max_hr" min="100" max="220"
               placeholder="190" value="{{ v.max_hr }}">
      </div>
    </div>

    <hr class="divider">
    <div class="section-title">Your race goal</div>

    <div class="field">
      <label for="race_name">Race name</label>
      <input type="text" id="race_name" name="race_name" required
             placeholder="Mumbai Marathon 2027" value="{{ v.race_name }}">
    </div>

    <div class="row">
      <div class="field">
        <label for="race_type">Distance</label>
        <select id="race_type" name="race_type">
          <option value="5k"            {% if v.race_type == '5k'            %}selected{% endif %}>5K</option>
          <option value="10k"           {% if v.race_type == '10k'           %}selected{% endif %}>10K</option>
          <option value="half_marathon" {% if v.race_type == 'half_marathon' %}selected{% endif %}>Half Marathon</option>
          <option value="marathon"      {% if v.race_type == 'marathon'      %}selected{% endif %}>Marathon</option>
          <option value="ultra"         {% if v.race_type == 'ultra'         %}selected{% endif %}>Ultra</option>
        </select>
      </div>
      <div class="field">
        <label for="race_date">Race date</label>
        <input type="date" id="race_date" name="race_date" required value="{{ v.race_date }}">
      </div>
    </div>

    <div class="row">
      <div class="field">
        <label for="target_hours">Target finish — hours</label>
        <input type="number" id="target_hours" name="target_hours" min="0" max="24"
               placeholder="4" value="{{ v.target_hours }}">
      </div>
      <div class="field">
        <label for="target_minutes">minutes</label>
        <input type="number" id="target_minutes" name="target_minutes" min="0" max="59"
               placeholder="0" value="{{ v.target_minutes }}">
      </div>
    </div>

    <div class="field">
      <label for="race_city">Race city <span class="opt">(if different from training)</span></label>
      <input type="text" id="race_city" name="race_city"
             placeholder="Mumbai" value="{{ v.race_city }}">
    </div>

    <hr class="divider">
    <div class="section-title">Garmin Connect</div>

    <div class="field">
      <label for="garmin_email">Email</label>
      <input type="email" id="garmin_email" name="garmin_email" required
             placeholder="you@example.com" value="{{ v.garmin_email }}">
    </div>

    <div class="field">
      <label for="garmin_password">Password</label>
      <input type="password" id="garmin_password" name="garmin_password" required
             placeholder="••••••••">
    </div>

    <button type="submit" class="btn">Start training →</button>
  </form>

  <p class="note">
    Your Garmin password is encrypted before storage and never visible to your coach.
  </p>
</div>
</body>
</html>
"""

_SUCCESS = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentKip — You're in!</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f5;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; padding: 24px;
    }
    .card {
      background: #fff; border-radius: 12px;
      box-shadow: 0 2px 16px rgba(0,0,0,.08);
      padding: 48px 40px; width: 100%; max-width: 420px; text-align: center;
    }
    .tick { font-size: 52px; margin-bottom: 20px; }
    h2 { font-size: 22px; color: #1a1a1a; margin-bottom: 10px; }
    p { color: #666; font-size: 15px; line-height: 1.7; }
    .detail { margin-top: 24px; background: #f9f9f9; border-radius: 8px; padding: 16px; text-align: left; }
    .detail div { font-size: 13px; color: #555; padding: 3px 0; }
    .detail strong { color: #1a1a1a; }
  </style>
</head>
<body>
<div class="card">
  <div class="tick">✅</div>
  <h2>You're all set, {{ name }}!</h2>
  <p>Your profile and Garmin credentials have been saved.<br>
     Your coach will set up your first training plan shortly.</p>
  <div class="detail">
    <div><strong>Goal:</strong> {{ race_name }}</div>
    <div><strong>Date:</strong> {{ race_date }}</div>
    <div><strong>Target:</strong> {{ target }}</div>
  </div>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _city_location(city: str, default_city: str = "Bengaluru") -> dict:
    key = city.strip().lower() if city.strip() else default_city.lower()
    lat, lng, alt = _CITY_COORDS.get(key, _CITY_COORDS["bengaluru"])
    return {"city": city.strip() or default_city, "country": "India",
            "latitude": lat, "longitude": lng, "altitude_m": alt}


def _blank() -> dict:
    return dict(name="", fitness_level="intermediate", training_city="",
                resting_hr="", max_hr="", race_name="", race_type="marathon",
                race_date="", target_hours="", target_minutes="",
                race_city="", garmin_email="")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@app.route("/onboard", methods=["GET", "POST"])
def onboard():
    error = ""
    v = _blank()

    if request.method == "POST":
        # Pull form values
        v = {k: request.form.get(k, "").strip() for k in v}

        # --- Validate required fields ---
        required = ["name", "fitness_level", "training_city",
                    "race_name", "race_type", "race_date",
                    "garmin_email", "garmin_password"]
        missing = [f for f in required if not request.form.get(f, "").strip()]
        if missing:
            error = f"Please fill in all required fields ({', '.join(missing)})."
        else:
            garmin_email    = v["garmin_email"]
            garmin_password = request.form.get("garmin_password", "").strip()
            athlete_id      = garmin_email   # email = unique ID

            try:
                hours   = int(v["target_hours"]   or 0)
                minutes = int(v["target_minutes"] or 0)
                target_seconds = (hours * 3600 + minutes * 60) or None

                training_loc = _city_location(v["training_city"])
                race_city    = v["race_city"] or v["training_city"]
                race_loc     = _city_location(race_city, v["training_city"])

                goal = {
                    "race_name":              v["race_name"],
                    "race_type":              v["race_type"],
                    "race_date":              v["race_date"],
                    "race_location":          race_loc,
                    "training_location":      training_loc,
                    "target_finish_seconds":  target_seconds,
                    "course_elevation_gain_m": None,
                }

                weekly_km = _MILEAGE_DEFAULTS.get(
                    (v["fitness_level"], v["race_type"]), 30
                )

                with Session(engine) as s:
                    existing = s.get(AthleteRow, athlete_id)
                    if existing:
                        s.delete(existing)
                        s.flush()
                    s.add(AthleteRow(
                        id=athlete_id,
                        name=v["name"],
                        fitness_level=v["fitness_level"],
                        weekly_mileage_target_km=weekly_km,
                        resting_heart_rate=int(v["resting_hr"]) if v["resting_hr"] else None,
                        max_heart_rate=int(v["max_hr"]) if v["max_hr"] else None,
                        injury_flags=[],
                        goal=goal,
                    ))
                    s.commit()

                save_credentials(athlete_id=athlete_id,
                                 garmin_email=garmin_email,
                                 garmin_password=garmin_password)

                target_str = f"{hours}h {minutes:02d}min" if target_seconds else "No target set"
                return render_template_string(
                    _SUCCESS,
                    name=v["name"],
                    race_name=v["race_name"],
                    race_date=v["race_date"],
                    target=target_str,
                )

            except Exception as e:
                app.logger.exception("onboarding_failed")
                error = f"Something went wrong: {e}"

    return render_template_string(_FORM, v=v, error=error)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("ONBOARDING_PORT", 8080))
    print(f"\n  AgentKip onboarding server")
    print(f"  http://localhost:{port}/onboard\n")
    app.run(host="0.0.0.0", port=port, debug=False)
