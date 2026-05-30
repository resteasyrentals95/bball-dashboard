#!/usr/bin/env python3
"""
Duke MBB + NBA dashboard data fetcher.

Pulls news (incl. recruiting), scores, and per-game top stat lines from ESPN's
free public JSON endpoints, writes dashboard.json, and renders a self-contained
index.html that can be opened directly in a browser (no server needed).

Run on a schedule (8 AM / 8 PM) via the launchd agent in this folder.
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball"
DUKE_TEAM_ID = "150"  # ESPN id for Duke Blue Devils (men's college basketball)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) dashboard/1.0"}

# Cap how many box scores we fetch per run so a busy slate can't stall the job.
MAX_BOXSCORE_FETCHES = 16


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", file=sys.stderr)


def get_json(url, timeout=15, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
            last_err = e
    log(f"FAILED {url} -> {last_err}")
    return None


RECRUIT_WORDS = (
    "recruit", "commit", "decommit", "signs", "signing", "pledge",
    "transfer", "portal", "five-star", "four-star", "5-star", "4-star",
    "class of", "prospect", "official visit", "reclassif", "blue devils land",
)


def is_recruiting(article):
    text = f"{article.get('headline','')} {article.get('description','')}".lower()
    return any(w in text for w in RECRUIT_WORDS)


def parse_articles(payload, duke_filter=False):
    out = []
    if not payload:
        return out
    for a in payload.get("articles", []) or []:
        # Some "articles" are media collections; keep only real stories/previews/recaps.
        atype = (a.get("type") or "").lower()
        if atype in ("media", "video", "highlights", "now"):
            continue
        link = ((a.get("links") or {}).get("web") or {}).get("href")
        images = a.get("images") or []
        img = images[0].get("url") if images else None
        headline = a.get("headline") or a.get("title") or ""
        if not headline or not link:
            continue
        if duke_filter:
            blob = f"{headline} {a.get('description','')}".lower()
            if "duke" not in blob and "blue devil" not in blob:
                continue
        out.append({
            "headline": headline,
            "description": a.get("description") or "",
            "published": a.get("published") or a.get("lastModified") or "",
            "link": link,
            "image": img,
            "recruiting": is_recruiting(a),
        })
    return out


def dedupe_articles(articles):
    seen, out = set(), []
    for a in sorted(articles, key=lambda x: x.get("published", ""), reverse=True):
        if a["link"] in seen:
            continue
        seen.add(a["link"])
        out.append(a)
    return out


def stat_line(athlete_entry, name_index):
    """Build 'Name — 24 PTS, 8 REB, 5 AST' from a boxscore athlete entry."""
    stats = athlete_entry.get("stats") or []
    if not stats:
        return None
    ath = athlete_entry.get("athlete") or {}
    name = ath.get("displayName") or ath.get("shortName") or "—"

    def stat(key):
        i = name_index.get(key)
        if i is None or i >= len(stats):
            return None
        return stats[i]

    pts = stat("PTS")
    parts = []
    if pts is not None:
        parts.append(f"{pts} PTS")
    reb = stat("REB")
    if reb is not None:
        parts.append(f"{reb} REB")
    ast = stat("AST")
    if ast is not None:
        parts.append(f"{ast} AST")
    return {"name": name, "pts": _to_int(pts), "line": f"{name} — " + ", ".join(parts)}


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


def top_lines_from_boxscore(summary):
    """Return {team_abbr: [stat_line, ...]} top 2 scorers per team for a final game."""
    result = {}
    box = (summary or {}).get("boxscore") or {}
    for team_block in box.get("players", []) or []:
        team = team_block.get("team") or {}
        abbr = team.get("abbreviation") or team.get("displayName") or "?"
        stats_groups = team_block.get("statistics") or []
        if not stats_groups:
            continue
        grp = stats_groups[0]
        names = grp.get("names") or []
        name_index = {n: i for i, n in enumerate(names)}
        lines = []
        for ath in grp.get("athletes", []) or []:
            sl = stat_line(ath, name_index)
            if sl:
                lines.append(sl)
        lines.sort(key=lambda x: x["pts"], reverse=True)
        result[abbr] = [l["line"] for l in lines[:2]]
    return result


def scoreboard_leaders(competition):
    """Season-average leaders attached to a scheduled game on the scoreboard."""
    out = {}
    for c in competition.get("competitors", []) or []:
        abbr = (c.get("team") or {}).get("abbreviation") or "?"
        ldrs = []
        for ld in c.get("leaders", []) or []:
            entries = ld.get("leaders") or []
            if not entries:
                continue
            athlete = (entries[0].get("athlete") or {}).get("displayName") or "—"
            val = entries[0].get("displayValue") or ""
            cat = (ld.get("shortDisplayName") or ld.get("abbreviation") or ld.get("name") or "").upper()
            ldrs.append(f"{athlete} — {val} {cat}")
        if ldrs:
            out[abbr] = ldrs[:3]
    return out


def parse_competition(event, league):
    comp = (event.get("competitions") or [{}])[0]
    status = event.get("status") or comp.get("status") or {}
    stype = status.get("type") or {}
    competitors = []
    for c in comp.get("competitors", []) or []:
        team = c.get("team") or {}
        rec = ""
        records = c.get("records") or []
        if records:
            rec = records[0].get("summary") or ""
        competitors.append({
            "abbr": team.get("abbreviation") or team.get("shortDisplayName") or "?",
            "name": team.get("shortDisplayName") or team.get("displayName") or "?",
            "logo": team.get("logo") or ((team.get("logos") or [{}])[0].get("href") if team.get("logos") else None),
            "score": c.get("score") or "0",
            "homeAway": c.get("homeAway") or "",
            "record": rec,
            "winner": bool(c.get("winner")),
        })
    return {
        "id": event.get("id"),
        "name": event.get("name") or event.get("shortName") or "",
        "date": event.get("date") or comp.get("date") or "",
        "state": stype.get("state") or "",          # pre | in | post
        "completed": bool(stype.get("completed")),
        "status": stype.get("shortDetail") or stype.get("description") or "",
        "competitors": competitors,
        "league": league,
        "comp_raw": comp,  # kept transiently for leader extraction; stripped before save
    }


def build_games(events, league, fetch_box=True, box_budget=None):
    games = []
    for ev in events or []:
        g = parse_competition(ev, league)
        leaders = {}
        if g["completed"] and fetch_box and (box_budget is None or box_budget["n"] < MAX_BOXSCORE_FETCHES):
            if box_budget is not None:
                box_budget["n"] += 1
            summary = get_json(f"{BASE}/{league}/summary?event={g['id']}")
            leaders = top_lines_from_boxscore(summary)
        if not leaders:
            leaders = scoreboard_leaders(g["comp_raw"])
        g["leaders"] = leaders
        g.pop("comp_raw", None)
        games.append(g)
    return games


def date_str(days_offset):
    d = datetime.now(timezone.utc) + timedelta(days=days_offset)
    return d.strftime("%Y%m%d")


def fetch_nba(box_budget):
    log("Fetching NBA news...")
    news = dedupe_articles(parse_articles(get_json(f"{BASE}/nba/news")))

    log("Fetching NBA scores (yesterday/today/tomorrow)...")
    events = []
    seen_ids = set()
    for off in (-1, 0, 1):
        sb = get_json(f"{BASE}/nba/scoreboard?dates={date_str(off)}")
        for ev in (sb or {}).get("events", []) or []:
            if ev.get("id") not in seen_ids:
                seen_ids.add(ev.get("id"))
                events.append(ev)
    games = build_games(events, "nba", fetch_box=True, box_budget=box_budget)
    games.sort(key=lambda g: g.get("date", ""))
    return {"news": news, "games": games}


def fetch_duke(box_budget):
    log("Fetching Duke news...")
    team_news = parse_articles(get_json(f"{BASE}/mens-college-basketball/news?team={DUKE_TEAM_ID}"))
    # Broaden recruiting/news coverage by scanning the general CBB feed for Duke mentions.
    general = parse_articles(get_json(f"{BASE}/mens-college-basketball/news"), duke_filter=True)
    news = dedupe_articles(team_news + general)

    log("Fetching Duke schedule/scores...")
    sched = get_json(f"{BASE}/mens-college-basketball/teams/{DUKE_TEAM_ID}/schedule")
    events = (sched or {}).get("events", []) or []
    # Keep the most recent finals and the next upcoming games (schedule can span a full season).
    now = datetime.now(timezone.utc)

    def ev_dt(ev):
        try:
            return datetime.fromisoformat((ev.get("date") or "").replace("Z", "+00:00"))
        except ValueError:
            return now

    past = sorted([e for e in events if ev_dt(e) <= now], key=ev_dt, reverse=True)[:5]
    upcoming = sorted([e for e in events if ev_dt(e) > now], key=ev_dt)[:5]
    chosen = list(reversed(past)) + upcoming
    games = build_games(chosen, "mens-college-basketball", fetch_box=True, box_budget=box_budget)
    return {"news": news, "games": games}


def main():
    box_budget = {"n": 0}
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duke": {"news": [], "games": []},
        "nba": {"news": [], "games": []},
        "errors": [],
    }
    try:
        data["duke"] = fetch_duke(box_budget)
    except Exception as e:  # never let one league break the whole run
        log(f"Duke section error: {e}")
        data["errors"].append(f"Duke: {e}")
    try:
        data["nba"] = fetch_nba(box_budget)
    except Exception as e:
        log(f"NBA section error: {e}")
        data["errors"].append(f"NBA: {e}")

    (HERE / "dashboard.json").write_text(json.dumps(data, indent=2))
    render_html(data)
    log(f"Done. Duke news={len(data['duke']['news'])} games={len(data['duke']['games'])} | "
        f"NBA news={len(data['nba']['news'])} games={len(data['nba']['games'])} | "
        f"box fetches={box_budget['n']}")


def render_html(data):
    template = (HERE / "template.html").read_text()
    html = template.replace("/*__DATA__*/null", json.dumps(data))
    (HERE / "index.html").write_text(html)


if __name__ == "__main__":
    main()
