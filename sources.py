"""
sources.py — config-driven job fetchers.

Sources are declared in sources.yaml. Every adapter returns jobs shaped:
    {"id", "title", "company", "url", "text", "source"}

A failing source never crashes the run; fetch_all() returns
(jobs, stats) where stats is one {"name", "count", "error"} per source.
"""

import html
import os
import re
import time

import feedparser
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 20


def clean_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def get_path(obj, path):
    """Dotted-path lookup: get_path(item, 'company.name'). Missing -> None."""
    for part in str(path).split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def to_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(to_text(v) for v in value)
    return str(value)


def expand_env(value):
    """Replace ${NAME} with the environment variable; error if it is unset."""
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, str):
        def sub(m):
            name = m.group(1)
            if name not in os.environ:
                raise ValueError(f"environment variable {name} is not set")
            return os.environ[name]
        return re.sub(r"\$\{(\w+)\}", sub, value)
    return value


def id_prefix(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "src"


def describe_error(e):
    """Short error label that never includes URLs (they may hold API keys)."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    if isinstance(e, requests.Timeout):
        return "timeout"
    if isinstance(e, requests.ConnectionError):
        return "connection failed"
    if isinstance(e, ValueError):
        return str(e)[:80]
    return type(e).__name__


# Location words that mean "open to candidates anywhere". Regions like "Europe", "US" or "LATAM"
# are not in this list: they usually mean candidates must live there. Add your own country or
# region with `home_region_terms` in config.yaml (see configure() below).
GEO_ALLOW = ["anywhere", "worldwide", "global", "emea", "middle east", "mena", "asia",
             "any location", "remote"]

# Set from config.yaml by configure(): extra allowed location words and your UTC offsets.
_HOME = {"terms": [], "hours": ()}


def configure(config):
    """Read `home_region_terms` and `home_utc_offsets` from the config (both optional)."""
    terms = config.get("home_region_terms") or []
    _HOME["terms"] = [str(t).lower() for t in terms if str(t).strip()]
    _HOME["hours"] = tuple(h for h in (config.get("home_utc_offsets") or [])
                           if isinstance(h, (int, float)) and not isinstance(h, bool))


def geo_ok(value, allow=None):
    """True if a location value is empty (no restriction) or names an allowed region."""
    text = to_text(value).strip().lower()
    if not text:
        return True
    return any(w in text for w in list(allow or GEO_ALLOW) + _HOME["terms"])


def tz_ok(value, hours=None):
    """True if a list of allowed UTC offsets is empty or contains one of your offsets.

    Without `home_utc_offsets` in the config (and no `hours` argument) the check is off.
    """
    hours = tuple(hours) if hours is not None else _HOME["hours"]
    if not hours or not isinstance(value, list) or not value:
        return True
    return any(h in value for h in hours)


def parse_json_items(data, src):
    name = src["name"]
    items = get_path(data, src["items_path"]) if src.get("items_path") else data
    if not isinstance(items, list):
        raise ValueError("items_path did not point to a list")
    prefix = id_prefix(name)
    jobs = []
    for item in items:
        if not isinstance(item, dict):
            continue
        req = src.get("require_field")
        if req and get_path(item, req) is None:
            continue
        geo = src.get("geo_field")
        if geo and not geo_ok(get_path(item, geo), src.get("geo_allow")):
            continue
        if src.get("tz_field") and not tz_ok(get_path(item, src["tz_field"])):
            continue
        raw_id = get_path(item, src.get("id", "id"))
        link = to_text(get_path(item, src.get("link", "url")))
        job_id = raw_id if raw_id is not None else link
        if job_id in (None, ""):
            continue
        title = clean_html(to_text(get_path(item, src.get("title", "title"))))
        company = clean_html(to_text(get_path(item, src.get("company", "company"))))
        fields = src.get("text") or [src.get("title", "title"), src.get("company", "company")]
        text = clean_html(" ".join(to_text(get_path(item, f)) for f in fields))
        jobs.append({
            "id": f"{prefix}-{job_id}",
            "title": title,
            "company": company,
            "url": link,
            "text": text or title,
            "source": name,
        })
    return jobs


def parse_rss_entries(entries, src):
    prefix = id_prefix(src["name"])
    jobs = []
    for e in entries:
        title = clean_html(e.get("title", ""))
        region = clean_html(e.get("region", ""))
        if src.get("geo_from_region") and not geo_ok(region, src.get("geo_allow")):
            continue
        link = e.get("link", "")
        job_id = e.get("id") or link
        if not job_id:
            continue
        jobs.append({
            "id": f"{prefix}-{job_id}",
            "title": title,
            "company": "",
            "url": link,
            "text": clean_html(f"{title} {region} {e.get('summary', '')}"),
            "source": src["name"],
        })
    return jobs


def fetch_json(src):
    headers = dict(HEADERS)
    headers.update(expand_env(src.get("headers") or {}))
    pages = max(1, int(src.get("pages") or 1))
    size = int(src.get("page_size") or 0)
    jobs = []
    for page in range(pages):
        params = dict(expand_env(src.get("params") or {}))
        if pages > 1 and src.get("page_param"):
            params[src["page_param"]] = page * size  # offset-style paging
        r = requests.get(expand_env(src["url"]), params=params, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        batch = parse_json_items(r.json(), src)
        jobs += batch
        if pages == 1 or not batch:
            break
    return jobs


def fetch_rss(src):
    r = requests.get(expand_env(src["url"]), headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_rss_entries(feedparser.parse(r.content).entries, src)


def fetch_hn_hiring(src):
    """Latest monthly 'Who is Hiring' thread; each top-level comment is a job."""
    cap = int(src.get("max_comments", 300))
    # The official monthly threads are posted by the "whoishiring" account.
    # Searching by title alone can match random user posts with a few comments.
    search = requests.get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"tags": "story,author_whoishiring", "hitsPerPage": 10},
        timeout=TIMEOUT,
    )
    search.raise_for_status()
    story = next(
        (h for h in search.json().get("hits", [])
         if "who is hiring" in h.get("title", "").lower()),
        None,
    )
    if not story:
        return []
    item = requests.get(
        f"https://hacker-news.firebaseio.com/v0/item/{story['objectID']}.json",
        timeout=TIMEOUT,
    ).json()
    jobs = []
    for kid in ((item or {}).get("kids") or [])[:cap]:
        try:
            c = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{kid}.json", timeout=10
            ).json()
        except Exception:
            continue
        if not c or c.get("dead") or c.get("deleted"):
            continue
        text = clean_html(c.get("text"))
        if not text:
            continue
        jobs.append({
            "id": f"hn-{kid}",
            "title": text[:80] + "...",
            "company": "",
            "url": f"https://news.ycombinator.com/item?id={kid}",
            "text": text,
            "source": src["name"],
            "loose_title": True,  # the "title" is just the first words of a free-text comment
        })
        time.sleep(0.05)
    return jobs


# ---------- company job boards (Greenhouse / Ashby / Lever) ----------
REMOTE_WORDS = [
    "remote", "anywhere", "worldwide", "global", "emea", "europe",
    "home based", "distributed", "international",
]
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.-]+")


def _company_job(ats, token, job_id, title, company, url, text, location=""):
    return {
        "board": True,
        "location": clean_html(location),
        "id": f"{ats}-{token}-{job_id}",
        "title": clean_html(title),
        "company": company or token,
        "url": url or "",
        "text": clean_html(text) or clean_html(title),
        "source": f"{company or token} ({ats})",
    }


def parse_greenhouse(data, token):
    jobs = []
    for j in (data or {}).get("jobs", []):
        loc = to_text(get_path(j, "location.name"))
        body = html.unescape(j.get("content") or "")  # Greenhouse HTML-escapes content
        jobs.append(_company_job(
            "greenhouse", token, j.get("id"), j.get("title", ""), j.get("company_name"),
            j.get("absolute_url"), f"{j.get('title', '')} {loc} {body}", loc,
        ))
    return jobs


def parse_ashby(data, token):
    jobs = []
    for j in (data or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        remote = "remote" if j.get("isRemote") or \
            str(j.get("workplaceType", "")).lower() == "remote" else ""
        locs = " ".join([to_text(j.get("location")), to_text(j.get("secondaryLocations"))])
        text = f"{j.get('title', '')} {remote} {locs} {j.get('descriptionPlain', '')}"
        jobs.append(_company_job(
            "ashby", token, j.get("id"), j.get("title", ""), None,
            j.get("jobUrl") or j.get("applyUrl"), text, f"{remote} {locs}",
        ))
    return jobs


def parse_lever(data, token):
    jobs = []
    for j in data if isinstance(data, list) else []:
        cat = j.get("categories") or {}
        remote = "remote" if str(j.get("workplaceType", "")).lower() == "remote" else ""
        text = " ".join([
            j.get("text", ""), remote, to_text(cat.get("location")),
            to_text(cat.get("allLocations")), j.get("descriptionPlain", ""),
        ])
        jobs.append(_company_job(
            "lever", token, j.get("id"), j.get("text", ""), None, j.get("hostedUrl"), text,
            " ".join([remote, to_text(cat.get("location")), to_text(cat.get("allLocations"))]),
        ))
    return jobs


ATS_FEEDS = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true", parse_greenhouse),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{t}", parse_ashby),
    "lever": ("https://api.lever.co/v0/postings/{t}?mode=json", parse_lever),
}


def fetch_company_boards(src):
    """One source = many companies on one ATS. A bad company never hides the others."""
    url_tpl, parser = ATS_FEEDS[src["type"]]
    tokens = [c for c in (src.get("companies") or []) if SAFE_TOKEN.fullmatch(str(c))]
    jobs, bad = [], []
    for token in tokens:
        try:
            r = requests.get(url_tpl.format(t=token), headers=HEADERS, timeout=30)
            r.raise_for_status()
            jobs += parser(r.json(), token)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{token}: {describe_error(e)}")
    if tokens and len(bad) == len(tokens):
        raise ValueError("all companies failed — " + "; ".join(bad[:3]))
    src.setdefault("_warnings", []).extend(bad)
    return jobs


def require_any(jobs, words):
    """Optional per-source gate: keep only jobs whose text mentions one of `words`."""
    if not words:
        return jobs
    low = [w.lower() for w in words]

    def haystack(j):
        # Company boards: judge by the location field only. Their descriptions say "remote"
        # for on-site roles too (e.g. "remote-friendly culture"), which let hundreds through.
        return (j["location"] if j.get("board") else j["text"]).lower()

    return [j for j in jobs if any(w in haystack(j) for w in low)]


ADAPTERS = {
    "json": fetch_json,
    "rss": fetch_rss,
    "hn_hiring": fetch_hn_hiring,
    "greenhouse": fetch_company_boards,
    "ashby": fetch_company_boards,
    "lever": fetch_company_boards,
}


def fetch_all(sources):
    """Fetch every source. Returns (jobs, stats); never raises."""
    jobs, stats = [], []
    for original in sources:
        src = dict(original)  # adapters may attach _warnings; keep the config untouched
        name = src.get("name", "?")
        try:
            adapter = ADAPTERS.get(src.get("type"))
            if adapter is None:
                raise ValueError(f"unknown type {src.get('type')!r}")
            found = adapter(src)
            gate = src.get("require_any")
            if gate is None and src.get("type") in ATS_FEEDS:
                gate = REMOTE_WORDS  # company boards mix on-site and remote roles
            found = require_any(found, gate)
            jobs += found
            warn = src.get("_warnings") or []
            err = None
            if warn:
                err = "; ".join(warn[:3]) + (f" (+{len(warn) - 3} more)" if len(warn) > 3 else "")
            stats.append({"name": name, "count": len(found), "error": err})
        except Exception as e:  # noqa: BLE001 - one bad source must not stop the rest
            err = describe_error(e)
            print(f"[sources] {name} failed: {err}")
            stats.append({"name": name, "count": 0, "error": err})
    print(f"[sources] fetched {len(jobs)} raw postings")
    return jobs, stats