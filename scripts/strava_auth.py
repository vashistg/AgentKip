#!/usr/bin/env python3
"""
Exchange a Strava authorization code for access + refresh tokens,
then patch them into .env in-place.

Usage:
    python scripts/strava_auth.py <code_from_redirect_url>
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

if len(sys.argv) != 2:
    print("Usage: python scripts/strava_auth.py <authorization_code>")
    sys.exit(1)

code = sys.argv[1]

from stravalib import Client
c = Client()
token_response = c.exchange_code_for_token(
    client_id=os.environ["STRAVA_CLIENT_ID"],
    client_secret=os.environ["STRAVA_CLIENT_SECRET"],
    code=code,
)

access_token  = token_response["access_token"]
refresh_token = token_response["refresh_token"]
expires_at    = token_response["expires_at"]

print(f"New access_token:  {access_token}")
print(f"New refresh_token: {refresh_token}")
print(f"Expires at (unix): {expires_at}")

# Patch .env in-place
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
with open(env_path) as f:
    content = f.read()

def patch(key, value):
    nonlocal content
    if re.search(rf"^{key}=", content, re.MULTILINE):
        content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{key}={value}\n"

patch("STRAVA_ACCESS_TOKEN",    access_token)
patch("STRAVA_REFRESH_TOKEN",   refresh_token)
patch("STRAVA_TOKEN_EXPIRES_AT", str(expires_at))

with open(env_path, "w") as f:
    f.write(content)

print("\n✓ .env updated with fresh tokens")

# Verify
c2 = Client()
c2.access_token = access_token
athlete = c2.get_athlete()
print(f"✓ Verified — logged in as {athlete.firstname} {athlete.lastname} (ID: {athlete.id})")
print(f"\nYour Strava athlete ID: {athlete.id}")
print("Add this to your athlete row in the DB if needed.")
