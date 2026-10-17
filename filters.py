"""
filters.py — decides which raw postings are worth telling you about.

Logic (see config.yaml to tune the word lists themselves):
  1. Reject anything matching an exclude_keyword (clearance, single-country
     residency, banks, gambling, etc).
  2. Keep only postings matching at least one include_keyword.
  3. Flag (not reject) postings matching a strong_signal_keyword — these
     are the ones most likely to be genuinely open to you.
  4. Pull out any $ / € figures mentioned, just to help you eyeball pay.
"""

import re


def _contains_any(text_lower, phrases):
    return any(p.lower() in text_lower for p in phrases)


def _matches_any_pattern(text_lower, patterns):
    """Regex walls, e.g. 'remote (germany)' or 'canada only'. Bad patterns are skipped."""
    for p in patterns:
        try:
            if re.search(p, text_lower):
                return True
        except re.error:
            continue
    return False


def extract_money_mentions(text):
    # crude but useful: grabs things like "$120,000", "$45/hr", "€70k"
    amount = r"\d{2,3}(?:,\d{3})*[kK]?"
    pattern = (
        rf"[$€£]\s?{amount}"                         # first amount, e.g. $120,000 or €70k
        rf"(?:\s?[-–]\s?[$€£]?\s?{amount})?"         # optional range end, e.g. - $150k
        r"(?:\s?/\s?(?:hr|hour|yr|year|mo|month))?"  # optional unit, e.g. /hr
    )
    found = list(dict.fromkeys(re.findall(pattern, text)))
    return [m for m in found if _looks_like_pay(m)][:4]  # dedupe, cap at 4


def _looks_like_pay(mention):
    """Drops small amounts such as a '$500 laptop allowance' that are not a salary."""
    has_k = bool(re.search(r"\d[kK]\b", mention))
    values = []
    for n in re.findall(r"\d{1,3}(?:,\d{3})*", mention):
        v = int(n.replace(",", ""))
        values.append(v * 1000 if has_k and v < 1000 else v)
    if not values:
        return False
    top = max(values)
    unit = re.search(r"/\s?(hr|hour|yr|year|mo|month)", mention)
    if unit:
        kind = unit.group(1)
        if kind in ("hr", "hour"):
            return 8 <= top <= 400
        if kind in ("mo", "month"):
            return top >= 1000
        return top >= 15000
    return top >= 20000


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_NUM = r"(\d{1,2}|" + "|".join(_NUM_WORDS) + r")"
_YEARS = re.compile(
    _NUM + r"\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*" + _NUM + r"\s*)?(?:or more\s+)?(?:years?|yrs?)\b"
)


def _to_int(tok):
    return int(tok) if tok.isdigit() else _NUM_WORDS.get(tok.lower())


def required_years(text):
    """Highest 'N years of experience' requirement in the text, or None.

    Only counts mentions near the word 'experience' (so '10 years of company history' is
    ignored); a range like '5-8 years' counts as its lower end, '5+' as 5.
    """
    low = text.lower()
    found = []
    for m in _YEARS.finditer(low):
        near = low[max(0, m.start() - 40): m.end() + 60]
        if "experience" not in near and "exp." not in near:
            continue
        n = _to_int(m.group(1))
        if n is not None and 1 <= n <= 30:
            found.append(n)
    return max(found) if found else None


def flag_matches(job, config):
    """Words that make you double-check the employer (a label, never a block).

    Patterns come from `flag_patterns` in config.yaml, and the label shown next to the match
    from `flag_label`. Only the title, company and the start of the description count, so
    "health insurance" in a benefits list at the bottom does not trigger it.
    """
    head = f"{job['title']} {job.get('company', '')} {job['text'][:1200]}".lower()
    patterns = config.get("flag_patterns", [])
    found = []
    for p in patterns:
        try:
            m = re.search(p, head)
        except re.error:
            continue
        if m and m.group(0).strip() not in found:
            found.append(m.group(0).strip())
    return found[:3]


_SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|lead)\b", re.I)
_EARLY_TITLE = re.compile(r"\b(junior|jr\.?|entry[- ]level|associate|graduate|mid|mid-level|"
                          r"intermediate|midweight)\b", re.I)


_PART_TIME = re.compile(
    r"\b(part[- ]?time|contract(or)?|freelance|hourly|fixed[- ]term|temporary)\b", re.I)


def part_time_flag(job):
    """True when the title or text says part-time / contract / hourly work."""
    return bool(_PART_TIME.search(job["title"]) or _PART_TIME.search(job["text"][:1500]))


def title_level(title):
    """'senior', 'early' (junior/mid) or '' from the job title words. A label, not a block."""
    if _EARLY_TITLE.search(title):
        return "early"
    if _SENIOR_TITLE.search(title):
        return "senior"
    return ""


def game_flag(job, config):
    """True when the posting looks like the game industry (a label, not a block)."""
    head = f"{job['title']} {job.get('company', '')} {job['text'][:1200]}".lower()
    for p in config.get("game_flag_patterns", []):
        try:
            if re.search(p, head):
                return True
        except re.error:
            continue
    return False


def score_job(job, config):
    """0-10 'how close to your profile' score used to rank alerts (see config.yaml)."""
    title = job["title"].lower()
    text = job["text"].lower()
    score = 0
    if _contains_any(title, config.get("core_title_keywords", [])):
        score += 3
    elif not job.get("loose_title"):
        score += 1
    stack = config.get("stack_keywords", [])
    score += min(3, sum(1 for w in dict.fromkeys(stack) if w.lower() in text))
    if job.get("strong_signal"):
        score += 2
    if job.get("money"):
        score += 1
    years = job.get("years")
    if years is not None and years <= 5:
        score += 1
    # Senior titles only rank high when the posting itself asks for 4 years or less;
    # junior/mid titles get a small lift so they are sent first when the cap applies.
    if _contains_any(title, config.get("deprioritize_title_keywords", [])):
        score -= 3
    if job.get("part_time"):
        score += 1
    if _matches_any_pattern(title, config.get("mismatch_title_patterns", [])):
        score -= 3
    level = title_level(job["title"])
    if level == "senior" and (years is None or years > 4):
        score -= 2
    elif level == "early":
        score += 1
    return max(0, min(score, 10))


_GEO_DENY = re.compile(
    r"\b(us|usa|u\.s\.|united states|north america|canada|americas|latam|latin america|"
    r"uk|united kingdom|europe|european union|eu)\b"
)
_GEO_OK = re.compile(
    r"\b(anywhere|worldwide|global|emea|middle east|mena|asia|any location)\b"
)


def geo_blocked(location, extra_ok=()):
    """True when a posting's location field is limited to a region that excludes you.

    'Remote - US' or 'North America' is blocked; 'Worldwide', 'EMEA' or a list that also names
    an open region is kept. `extra_ok` is a list of extra words (your own country or region,
    from `home_region_terms` in config.yaml) that also count as open. An empty location is
    never blocked.
    """
    loc = (location or "").lower()
    if not loc or not _GEO_DENY.search(loc) or _GEO_OK.search(loc):
        return False
    return not any(str(t).lower() in loc for t in extra_ok if str(t).strip())


def passes_filters(job, config):
    text_lower = job["text"].lower()

    if geo_blocked(job.get("location"), config.get("home_region_terms", [])):
        return False, []

    if not config.get("include_appian", True) and "appian" in job["title"].lower():
        return False, []

    if _contains_any(text_lower, config.get("exclude_keywords", [])):
        return False, []

    if _matches_any_pattern(text_lower, config.get("exclude_patterns", [])):
        return False, []

    if not job.get("loose_title"):
        # Judge the role by its TITLE (descriptions mention every language and tool).
        title = job["title"].lower()
        title_words = config.get("title_keywords") or config.get("board_title_keywords") or []
        if title_words and not _contains_any(title, title_words):
            return False, []
        if _matches_any_pattern(title, config.get("exclude_title_patterns", [])):
            return False, []
    if not _contains_any(text_lower, config.get("include_keywords", [])):
        return False, []

    limit = config.get("max_years") or 0
    years = required_years(job["text"])
    if limit and years and years > limit:
        return False, []

    money = extract_money_mentions(job["text"])
    return True, money


def is_strong_signal(job, config):
    text_lower = job["text"].lower()
    return _contains_any(text_lower, config.get("strong_signal_keywords", []))


def filter_jobs(jobs, config):
    """Returns a list of jobs annotated with 'strong_signal' and 'money' fields."""
    kept = []
    for job in jobs:
        ok, money = passes_filters(job, config)
        if not ok:
            continue
        job["strong_signal"] = is_strong_signal(job, config)
        job["money"] = money
        job["years"] = required_years(job["text"])
        job["flags"] = flag_matches(job, config)
        job["flag_label"] = config.get("flag_label", "check")
        job["game"] = game_flag(job, config)
        job["part_time"] = part_time_flag(job)
        job["level"] = title_level(job["title"])
        job["score"] = score_job(job, config)
        kept.append(job)
    print(f"[filters] kept {len(kept)} of {len(jobs)} postings after filtering")
    return kept