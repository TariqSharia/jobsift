"""
prefs.py — merges your Telegram-made changes (prefs.json) over config.yaml
and sources.yaml. prefs.json is written by the Cloudflare Worker.

Shape:
{
  "include": {"add": [], "remove": []},   # likewise "exclude" and "strong"
  "include_appian": false,
  "max_years": 5,                         # 0 = no limit
  "sources": {"add": [{"name":..., "type":"rss", "url":"https://..."}],
              "remove": ["Name"]}
}
"""

import json
import os
import re

ATS_TYPES = ("greenhouse", "ashby", "lever")
LISTS = {
    "include": "include_keywords",
    "exclude": "exclude_keywords",
    "strong": "strong_signal_keywords",
}


def load_prefs(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        print("[prefs] prefs.json unreadable — ignoring it")
        return {}


def _merge_words(base, add, remove):
    removed = {w.lower() for w in remove}
    out = [w for w in base if w.lower() not in removed]
    have = {w.lower() for w in out}
    for w in add:
        if w.lower() not in have:
            out.append(w)
            have.add(w.lower())
    return out


def _safe_source(src):
    """Sources from prefs may not use ${ENV} (would let a chat message read secrets)."""
    if not isinstance(src, dict) or not src.get("name"):
        return False
    if src.get("type") not in ("json", "rss", "hn_hiring", *ATS_TYPES):
        return False
    if "${" in json.dumps(src):
        return False
    if src["type"] in ATS_TYPES:
        cos = src.get("companies")
        return bool(cos) and isinstance(cos, list) and all(
            isinstance(c, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", c) for c in cos
        )
    url = src.get("url", "")
    return src["type"] == "hn_hiring" or str(url).startswith("https://")


def apply_prefs(config, sources, prefs):
    """Return (effective_config, effective_sources). Inputs are not modified."""
    cfg = dict(config)
    for key, cfg_key in LISTS.items():
        p = prefs.get(key) or {}
        cfg[cfg_key] = _merge_words(
            config.get(cfg_key, []), p.get("add", []), p.get("remove", [])
        )
    if isinstance(prefs.get("include_appian"), bool):
        cfg["include_appian"] = prefs["include_appian"]

    my = prefs.get("max_years")
    if isinstance(my, int) and not isinstance(my, bool) and 0 <= my <= 30:
        cfg["max_years"] = my

    sp = prefs.get("sources") or {}
    removed = {n.lower() for n in sp.get("remove", [])}
    srcs = [s for s in sources if s.get("name", "").lower() not in removed]
    names = {s.get("name", "").lower() for s in srcs}
    for s in sp.get("add", []):
        if _safe_source(s) and s["name"].lower() not in names:
            srcs.append(s)
            names.add(s["name"].lower())
    return cfg, srcs