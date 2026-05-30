#!/usr/bin/env python3
"""
Push notifications for new Duke MBB recruiting news and major NBA news.

Reads dashboard.json (produced by fetch_data.py), compares against the last run's
"seen" state, and pushes any NEW items to a phone via ntfy (https://ntfy.sh).

- Duke: only RECRUITING-flagged headlines trigger a push.
- NBA: new items from ESPN's curated top-news feed, with a "major" keyword filter
  to cut noise (trades, injuries, signings, firings, playoff/Finals, awards, etc.).

First run seeds state silently so you don't get a flood of back-headlines.
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
CONFIG = DATA_DIR / "notify_config.json"
STATE = DATA_DIR / "notify_state.json"
DASHBOARD = HERE / "dashboard.json"

# Headlines that make NBA news worth a push. Keep broad but not everything.
NBA_MAJOR = (
    "trade", "traded", "sign", "signs", "signing", "agree", "deal", "waive",
    "injury", "injured", "out for", "surgery", "tear", "acl", "sprain",
    "fired", "hired", "named", "suspend", "ban", "fine",
    "mvp", "all-star", "award", "champion", "finals", "title", "sweep",
    "game 7", "eliminat", "advance", "clinch", "record", "retire", "buyout",
    "ejected", "return", "debut", "extension", "max deal", "buzzer",
)


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] notify: {msg}", file=sys.stderr)


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def is_major_nba(article):
    text = f"{article.get('headline','')} {article.get('description','')}".lower()
    return any(w in text for w in NBA_MAJOR)


def send_push(cfg, title, body, click_url, tags):
    server = cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    topic = cfg.get("topic")
    if not topic:
        log("no topic configured; skipping push")
        return False
    url = f"{server}/{topic}"
    headers = {
        "Title": title.encode("utf-8", "ignore").decode("latin-1", "ignore"),
        "Tags": tags,
        "Priority": "default",
    }
    if click_url:
        headers["Click"] = click_url
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        log(f"push failed: {e}")
        return False


def main():
    cfg = load_json(CONFIG, None)
    if not cfg:
        log(f"missing {CONFIG}; run setup first. Skipping notifications.")
        return

    data = load_json(DASHBOARD, None)
    if not data:
        log("no dashboard.json; nothing to do")
        return

    state = load_json(STATE, {"seen_duke": [], "seen_nba": [], "initialized": False})
    seen_duke = set(state.get("seen_duke", []))
    seen_nba = set(state.get("seen_nba", []))

    duke_news = (data.get("duke") or {}).get("news", []) or []
    nba_news = (data.get("nba") or {}).get("news", []) or []

    # Candidate new items
    new_duke = [a for a in duke_news if a.get("recruiting") and a.get("link") not in seen_duke]
    new_nba = [a for a in nba_news if is_major_nba(a) and a.get("link") not in seen_nba]

    # Always record everything we've now observed (even non-notified) so links don't re-fire.
    all_duke_links = [a.get("link") for a in duke_news if a.get("link")]
    all_nba_links = [a.get("link") for a in nba_news if a.get("link")]

    if not state.get("initialized"):
        # Seed silently on first run so the user isn't flooded with old headlines.
        state = {
            "seen_duke": all_duke_links[:300],
            "seen_nba": all_nba_links[:300],
            "initialized": True,
        }
        STATE.write_text(json.dumps(state, indent=2))
        log(f"seeded state silently ({len(all_duke_links)} duke, {len(all_nba_links)} nba); no pushes sent")
        return

    max_per_run = int(cfg.get("max_per_run", 6))
    sent = 0

    for a in new_duke[:max_per_run]:
        ok = send_push(
            cfg,
            title="Duke Recruiting",
            body=a.get("headline", ""),
            click_url=a.get("link"),
            tags="duke,basketball",
        )
        sent += 1 if ok else 0

    for a in new_nba[: max(0, max_per_run - len(new_duke[:max_per_run]))]:
        ok = send_push(
            cfg,
            title="NBA News",
            body=a.get("headline", ""),
            click_url=a.get("link"),
            tags="basketball,rotating_light",
        )
        sent += 1 if ok else 0

    # Update seen sets with everything currently in the feeds.
    seen_duke.update(all_duke_links)
    seen_nba.update(all_nba_links)
    state["seen_duke"] = list(seen_duke)[-300:]
    state["seen_nba"] = list(seen_nba)[-300:]
    state["initialized"] = True
    STATE.write_text(json.dumps(state, indent=2))
    log(f"new duke={len(new_duke)} new nba={len(new_nba)} pushes_sent={sent}")


if __name__ == "__main__":
    main()
