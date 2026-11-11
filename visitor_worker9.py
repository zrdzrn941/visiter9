"""
Link visitor worker — pure HTTP (no browser, no Selenium, no Chrome).

Reads links directly from the formatted_urls_partN.txt file (comma-separated),
and wakes each Streamlit Community Cloud app via the two-request flow:
    1. GET  <app>/api/v2/app/status  -> grab x-csrf-token + cookies
    2. POST <app>/api/v2/app/resume  -> 200/204 means accepted

Spawned as a subprocess by the Streamlit wrapper (visiterkeepalive.py).
No browser installation required.
"""

import time
import subprocess
import sys
import importlib
import datetime
import os
import signal
import uuid

import requests

# ============ CONFIGURATION ============
FORMATTED_URLS_FILE = "formatted_urls_part9.txt"     # comma-separated URLs
HTTP_TIMEOUT_SECONDS = 15                  # per-request timeout
PAUSE_BETWEEN_LINKS_SECONDS = 5            # small breather between links
RESTART_PAUSE_SECONDS = 300                # pause between full passes
# =======================================

running = True


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)


def signal_handler(sig, frame):
    global running
    log("🛑 Shutdown signal received. Will exit after current step.")
    running = False


try:
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
except (ValueError, OSError):
    pass


# ---------- Package installation (only requests) ----------
def pip_install(package):
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package, "--quiet"]
        )
        return True
    except subprocess.CalledProcessError as e:
        log(f"⚠️ pip install {package} failed: {e}")
        return False


def ensure_python_deps():
    for pkg in ["requests"]:
        try:
            importlib.import_module(pkg)
        except ImportError:
            log(f"📦 Installing {pkg}...")
            pip_install(pkg)


ensure_python_deps()


# ---------- Links loading ----------
def load_links():
    """
    Read URLs directly from FORMATTED_URLS_FILE.
    Format: comma-separated, but also tolerates newlines / whitespace.
    Returns a deduplicated list preserving order.
    """
    if not os.path.exists(FORMATTED_URLS_FILE):
        log(f"⚠️ {FORMATTED_URLS_FILE} not found")
        return []

    try:
        with open(FORMATTED_URLS_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        log(f"⚠️ Could not read {FORMATTED_URLS_FILE}: {e}")
        return []

    parts = []
    for line in raw.replace(",", "\n").splitlines():
        url = line.strip()
        if url:
            parts.append(url)

    seen = set()
    unique = []
    for u in parts:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    log(f"📄 Loaded {len(unique)} links from {FORMATTED_URLS_FILE}")
    return unique


# ---------- HTTP resume (the whole point) ----------
def resume_url(app_url):
    """
    Wake a Streamlit Community Cloud app via the two-request flow.
    Returns (ok, info_dict).
    """
    app_url = app_url.rstrip('/')
    status_url = f"{app_url}/api/v2/app/status"
    resume_url_ = f"{app_url}/api/v2/app/resume"
    machine_id = str(uuid.uuid4())
    headers = {
        "User-Agent": "Mozilla/5.0 (visitor-worker-http)",
        "Origin": app_url,
        "Referer": app_url + "/",
    }
    s = requests.Session()

    t0 = time.time()
    try:
        # 1. status -> csrf + cookies
        r = s.get(status_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
        if r.status_code != 200:
            return False, {"url": app_url, "error": f"status HTTP {r.status_code}"}

        csrf = r.headers.get("x-csrf-token") or r.headers.get("X-Csrf-Token")
        if not csrf:
            return False, {"url": app_url, "error": "no x-csrf-token header"}

        # 2. resume
        h = dict(headers)
        h["x-csrf-token"] = csrf
        h["x-streamlit-machine-id"] = machine_id
        h["Content-Type"] = "application/json"
        r2 = s.post(
            resume_url_,
            headers=h,
            json={"machineId": machine_id},
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        elapsed = round(time.time() - t0, 2)
        if r2.status_code in (200, 204):
            return True, {"url": app_url, "seconds": elapsed, "code": r2.status_code}
        return False, {"url": app_url, "error": f"resume HTTP {r2.status_code}",
                       "seconds": elapsed}

    except Exception as e:
        return False, {"url": app_url, "error": str(e)[:200]}


# ---------- Main loop ----------
def main():
    log("=" * 60)
    log("🚀 Link Visitor worker started (HTTP-only, no browser)")
    log(f"   Links source        : {FORMATTED_URLS_FILE}")
    log(f"   HTTP timeout        : {HTTP_TIMEOUT_SECONDS}s per request")
    log(f"   PID                 : {os.getpid()}")
    log("=" * 60)

    links = load_links()
    if not links:
        log(f"❌ No links found in {FORMATTED_URLS_FILE}. "
            "Worker cannot proceed.")
        return
    log(f"🎯 Total links to visit per pass: {len(links)}")

    total_visits = 0
    total_failures = 0
    pass_num = 0

    while running:
        pass_num += 1
        log(f"\n===== PASS #{pass_num} ({len(links)} links) =====")

        for i, url in enumerate(links, 1):
            if not running:
                break

            log(f"[{i}/{len(links)}] → {url}")
            ok, info = resume_url(url)

            if ok:
                total_visits += 1
                log(f"   ✅ {info['seconds']}s | HTTP {info.get('code')}")
            else:
                total_failures += 1
                log(f"   ❌ {info.get('error', 'unknown')}")

            if PAUSE_BETWEEN_LINKS_SECONDS and running:
                time.sleep(PAUSE_BETWEEN_LINKS_SECONDS)

        if not running:
            break

        log(f"😴 Pass #{pass_num} complete "
            f"(✅ {total_visits} success / ❌ {total_failures} fail). "
            f"Sleeping {RESTART_PAUSE_SECONDS}s before next pass...")

        end_wait = time.time() + RESTART_PAUSE_SECONDS
        while time.time() < end_wait and running:
            time.sleep(2)

        # Reload the txt file each pass in case it changed
        new_links = load_links()
        if new_links:
            links = new_links

    log(f"👋 Worker stopped. Total successes: {total_visits}, "
        f"failures: {total_failures}")


if __name__ == "__main__":
    main()