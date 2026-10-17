"""One-time helper: bring back jobs the old bot hid, without re-sending ones you already got.

The old main.py marked jobs beyond max_per_run as "seen" without sending them. This keeps in
seen_jobs.json only the jobs that were really delivered (the ones in inbox.json) and drops the
rest, so the hidden ones can come back, best score first.

    python revive_hidden.py            # dry run: only prints the numbers
    python revive_hidden.py --write    # does it (keeps a backup: seen_jobs.json.bak)
"""
import hashlib
import json
import shutil
import sys

SEEN, INBOX = "seen_jobs.json", "inbox.json"


def ref(job_id):
    return hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:6]


def main():
    seen = json.load(open(SEEN, encoding="utf-8"))
    try:
        inbox = json.load(open(INBOX, encoding="utf-8"))
    except (OSError, ValueError):
        inbox = []
    refs = {j.get("ref") for j in inbox}
    ids = [s for s in seen if not str(s).startswith("k:")]
    keep = [s for s in ids if ref(s) in refs]
    print(f"seen ids: {len(ids)} | delivered and still in inbox.json: {len(keep)}"
          f" (inbox holds {len(inbox)} of max 150)")
    print(f"hidden jobs that will come back: about {len(ids) - len(keep)}")
    if len(inbox) >= 150:
        print("WARNING: inbox is full, so older delivered jobs may come back too.")
    if "--write" not in sys.argv:
        print("Dry run only. Run again with --write to apply.")
        return
    shutil.copyfile(SEEN, SEEN + ".bak")
    json.dump(keep, open(SEEN, "w", encoding="utf-8"))
    print("Done. Backup saved as seen_jobs.json.bak")


if __name__ == "__main__":
    main()