"""
notifier.py — sends results to Telegram (or matches.md without credentials).

send_jobs() returns the ids that were actually delivered, so main.py only
marks those as seen; a failed send is retried on the next run.
"""

import html
import os
import time

import requests

STAR = 7  # keep in sync with star_score in config.yaml


def format_job(job):
    esc = html.escape
    tag = "✅ " if job.get("strong_signal") else ""
    money = f"  💰 {esc(' / '.join(job['money']))}" if job.get("money") else ""
    company = f" — {esc(job['company'])}" if job.get("company") else ""
    years = f"  📅 {job['years']}+ yrs" if job.get("years") else ""
    score = f"  {'⭐' if job.get('score', 0) >= STAR else '🎯'} {job['score']}/10" \
        if job.get("score") is not None else ""
    part = "  🕒 part-time/contract" if job.get("part_time") else ""
    game = "  🎮 game industry" if job.get("game") else ""
    level = {"senior": "  🧭 senior title", "early": "  🌱 junior/mid"}.get(job.get("level"), "")
    flags = job.get("flags")
    flag_label = esc(job.get("flag_label", "check"))
    flagged = f"\n⚠️ {flag_label}: {esc(', '.join(flags))}" if flags else ""
    ref = f"\nref: <code>{esc(job['ref'])}</code>" if job.get("ref") else ""
    return (
        f"{tag}<b>{esc(job['title'])}</b>{score}{money}{years}{level}{part}{game}\n"
        f"{esc(job['source'])}{company}{flagged}\n{esc(job['url'])}{ref}"
    )


def post_message(token, chat_id, text, session=requests, retries=2):
    """Send one message; honours Telegram 429 retry_after. True on success."""
    for _ in range(retries + 1):
        try:
            resp = session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[notifier] Telegram send failed: {type(e).__name__}")
            return False
        if resp.ok:
            return True
        if resp.status_code == 429:
            try:
                wait = resp.json().get("parameters", {}).get("retry_after", 3)
            except ValueError:
                wait = 3
            time.sleep(min(int(wait), 30))
            continue
        print(f"[notifier] Telegram error {resp.status_code}: {resp.text[:200]}")
        return False
    return False


def send_jobs(jobs, token, chat_id, session=requests):
    delivered = []
    for job in jobs:
        if post_message(token, chat_id, format_job(job), session):
            delivered.append(job["id"])
        time.sleep(0.05)
    print(f"[notifier] sent {len(delivered)}/{len(jobs)} Telegram messages")
    return delivered


def write_markdown_fallback(jobs, path="matches.md"):
    with open(path, "a", encoding="utf-8") as f:
        if jobs:
            f.write(f"\n## New matches ({len(jobs)})\n\n")
        for job in jobs:
            tag = "✅ " if job.get("strong_signal") else ""
            money = f" — 💰 {' / '.join(job['money'])}" if job.get("money") else ""
            f.write(f"- {tag}**{job['title']}**{money} — {job['source']} — {job['url']}\n")
    print(f"[notifier] wrote {len(jobs)} matches to {path}")


def format_summary(fetched, kept, new, stats):
    line = f"📊 Fetched {fetched}, kept {kept}, new {new}"
    ok = [s for s in stats if not s.get("error") or s.get("count")]
    if ok:
        line += "\n" + " · ".join(f"{html.escape(s['name'])} {s['count']}" for s in ok)
    bad = [s for s in stats if s.get("error")]
    if bad:
        errs = "; ".join(f"{html.escape(s['name'])}: {html.escape(s['error'])}" for s in bad)
        line += f"\n⚠️ {errs}"
    return line


def notify(jobs, summary=None, session=requests):
    """Deliver jobs + summary. Returns the set of delivered job ids."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        delivered = send_jobs(jobs, token, chat_id, session)
        if summary:
            post_message(token, chat_id, summary, session)
        return set(delivered)
    print("[notifier] no Telegram credentials set — writing to matches.md instead")
    if jobs:
        write_markdown_fallback(jobs)
    if summary:
        print(summary)
    return {j["id"] for j in jobs}