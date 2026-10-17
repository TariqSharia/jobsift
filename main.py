"""
main.py — orchestrates one run of the job alert bot:

    fetch (sources.py) -> filter (filters.py) -> dedupe -> notify (notifier.py)

Run it directly:
    python main.py

State (which jobs you've already been shown) lives in seen_jobs.json,
so re-running never re-sends the same posting twice.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone

import yaml

from filters import filter_jobs
from notifier import format_summary, notify
from prefs import apply_prefs, load_prefs
from sources import configure as configure_sources
from sources import fetch_all

# Paths can be overridden from the environment (e.g. by Docker) so the state
# file can live in a mounted volume instead of inside the container.
SEEN_FILE = os.environ.get("SEEN_FILE", "seen_jobs.json")
CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.yaml")
SOURCES_FILE = os.environ.get("SOURCES_FILE", "sources.yaml")
PREFS_FILE = os.environ.get("PREFS_FILE", "prefs.json")
INBOX_FILE = os.environ.get("INBOX_FILE", "inbox.json")
INBOX_MAX = 150
INBOX_TEXT_MAX = 6000


def _open_with_example_fallback(path):
    """Open `path`; if it does not exist, fall back to its `.example.yaml` sibling."""
    if not os.path.exists(path):
        example = re.sub(r"\.yaml$", ".example.yaml", path)
        if os.path.exists(example):
            print(f"[main] {path} not found, using {example}")
            path = example
    return open(path, "r", encoding="utf-8")


def load_config():
    with _open_with_example_fallback(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_sources():
    with _open_with_example_fallback(SOURCES_FILE) as f:
        return yaml.safe_load(f) or []


def make_ref(job_id):
    """Short stable code shown in Telegram so a job can be found again in inbox.json."""
    return hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:6]


def update_inbox(jobs, path=None):
    """Keep the newest INBOX_MAX delivered matches, with full text, for review in chat."""
    path = path or INBOX_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    fresh = [{
        "ref": j["ref"], "found": now, "title": j["title"], "company": j.get("company", ""),
        "source": j["source"], "url": j["url"], "strong_signal": bool(j.get("strong_signal")),
        "money": j.get("money", []), "text": j["text"][:INBOX_TEXT_MAX],
    } for j in jobs]
    have = {j["ref"] for j in fresh}
    merged = fresh + [j for j in old if j.get("ref") not in have]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged[:INBOX_MAX], f, ensure_ascii=False, indent=1)


def dedupe_key(title, company):
    """Same role listed on two boards (RemoteOK + Remotive...) gets the same key.

    Built from company + title with punctuation and the word 'remote' removed. Empty when
    the company is unknown (e.g. Hacker News posts), so those are only deduped by id.
    """
    comp = re.sub(r"[^a-z0-9]+", "", (company or "").lower())
    name = re.sub(r"\bremote\b", " ", (title or "").lower())
    name = re.sub(r"[^a-z0-9]+", "", name)
    if not comp or not name:
        return ""
    return "k:" + hashlib.sha1(f"{comp}|{name}".encode("utf-8")).hexdigest()[:12]


def drop_duplicates(jobs, seen):
    """New jobs only: unseen id AND unseen company+title key, once per run."""
    out, keys = [], set()
    for j in jobs:
        if j["id"] in seen:
            continue
        key = dedupe_key(j["title"], j.get("company", ""))
        if key and (key in seen or key in keys):
            continue
        if key:
            keys.add(key)
        out.append(j)
    return out


def inbox_keys(path=None):
    """Keys of recently delivered jobs, so duplicates of older alerts are caught too."""
    try:
        with open(path or INBOX_FILE, "r", encoding="utf-8") as f:
            return {k for j in json.load(f)
                    if (k := dedupe_key(j.get("title"), j.get("company")))}
    except (OSError, ValueError):
        return set()


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    # cap stored history so the file doesn't grow forever
    trimmed = list(seen_ids)[-20000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)


def main():
    config, sources = apply_prefs(load_config(), load_sources(), load_prefs(PREFS_FILE))
    configure_sources(config)
    seen = load_seen() | inbox_keys()

    raw_jobs, stats = fetch_all(sources)
    matched = filter_jobs(raw_jobs, config)

    new_jobs = drop_duplicates(matched, seen)
    print(f"[main] {len(new_jobs)} new (never-seen) matches this run")

    for j in new_jobs:
        j["ref"] = make_ref(j["id"])

    # Best fit score first (ties keep source order, since Python's sort is stable).
    new_jobs.sort(key=lambda j: -j.get("score", 0))
    cap = int(config.get("max_per_run") or 0)
    per_company = int(config.get("max_per_company") or 0)
    to_send, overflow, taken = [], [], {}
    for j in new_jobs:
        who = (j.get("company") or "").strip().lower()
        full = bool(cap) and len(to_send) >= cap
        company_full = bool(per_company and who) and taken.get(who, 0) >= per_company
        if full or company_full:
            overflow.append(j)
            continue
        to_send.append(j)
        taken[who] = taken.get(who, 0) + 1

    summary = format_summary(len(raw_jobs), len(matched), len(new_jobs), stats)
    if overflow:
        summary += (f"\n✂️ Sent the top {len(to_send)}; {len(overflow)} more matched and will"
                    " follow in the next runs, best first.")
    delivered = notify(to_send, summary)

    # Only jobs that were really delivered are marked seen; failures retry next run.
    # Matches that were not new are already in `seen`.
    seen.update(delivered)
    seen.update(k for j in to_send if j["id"] in delivered
                if (k := dedupe_key(j["title"], j.get("company", ""))))
    update_inbox([j for j in to_send if j["id"] in delivered])
    save_seen(seen)


if __name__ == "__main__":
    main()