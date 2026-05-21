#!/usr/bin/env python3
"""
Start the onboarding server + ngrok tunnel and print the shareable URL.

Usage:
    source .venv/bin/activate
    python scripts/share_onboarding.py
"""
import os
import sys
import time
import subprocess
import signal
import atexit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

PORT = int(os.environ.get("ONBOARDING_PORT", 8080))

_procs: list[subprocess.Popen] = []


def _cleanup():
    for p in _procs:
        try:
            p.terminate()
        except Exception:
            pass


atexit.register(_cleanup)


def _is_port_open(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _start_flask():
    if _is_port_open(PORT):
        print(f"  ✓ Onboarding server already running on :{PORT}")
        return
    print(f"  → Starting onboarding server on :{PORT} ...")
    p = subprocess.Popen(
        [sys.executable, "onboarding_app.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _procs.append(p)
    for _ in range(20):
        time.sleep(0.3)
        if _is_port_open(PORT):
            print(f"  ✓ Onboarding server started")
            return
    print("  ✗ Onboarding server failed to start — check onboarding_app.py")
    sys.exit(1)


def _start_ngrok() -> str:
    # If ngrok is already running grab the URL from its local API
    if _is_port_open(4040):
        return _get_ngrok_url()

    print("  → Starting ngrok tunnel ...")
    p = subprocess.Popen(
        ["ngrok", "http", str(PORT), "--log=stdout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _procs.append(p)
    for _ in range(20):
        time.sleep(0.5)
        if _is_port_open(4040):
            time.sleep(0.5)  # let tunnel register
            return _get_ngrok_url()

    print("  ✗ ngrok failed to start. Is it installed? (brew install ngrok)")
    sys.exit(1)


def _get_ngrok_url() -> str:
    resp = httpx.get("http://localhost:4040/api/tunnels", timeout=5)
    tunnels = resp.json().get("tunnels", [])
    https = [t["public_url"] for t in tunnels if t["public_url"].startswith("https")]
    if https:
        return https[0]
    all_urls = [t["public_url"] for t in tunnels]
    if all_urls:
        return all_urls[0]
    raise RuntimeError("No active ngrok tunnels found")


if __name__ == "__main__":
    print("\nAgentKip — onboarding share\n")

    _start_flask()
    url = _start_ngrok()
    onboard_url = f"{url}/onboard"

    print(f"\n  {'─' * 50}")
    print(f"  Share this link with your athletes:\n")
    print(f"  {onboard_url}")
    print(f"  {'─' * 50}\n")
    print("  Press Ctrl+C to stop.\n")

    try:
        signal.pause()
    except (KeyboardInterrupt, AttributeError):
        pass
