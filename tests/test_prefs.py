import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from prefs import apply_prefs  # noqa: E402

CFG = {"include_keywords": ["python", "node.js"], "exclude_keywords": ["casino"],
       "strong_signal_keywords": [], "include_appian": True}
SRC = [{"name": "A", "type": "rss", "url": "https://a"}]


def test_add_and_remove_words():
    prefs = {"include": {"add": ["Go"], "remove": ["PYTHON"]},
             "exclude": {"add": ["hybrid"]}}
    cfg, _ = apply_prefs(CFG, SRC, prefs)
    assert cfg["include_keywords"] == ["node.js", "Go"]
    assert cfg["exclude_keywords"] == ["casino", "hybrid"]
    assert CFG["include_keywords"] == ["python", "node.js"]  # input untouched


def test_appian_toggle():
    cfg, _ = apply_prefs(CFG, SRC, {"include_appian": False})
    assert cfg["include_appian"] is False


def test_sources_add_remove_and_safety():
    good = {"name": "B", "type": "rss", "url": "https://b/feed"}
    prefs = {"sources": {"add": [
        good,
        {"name": "C", "type": "rss", "url": "http://insecure"},
        {"name": "D", "type": "json", "url": "https://x/${TELEGRAM_BOT_TOKEN}"},
        {"name": "E", "type": "weird", "url": "https://e"},
        {"name": "a", "type": "rss", "url": "https://dup"},
    ], "remove": []}}
    _, srcs = apply_prefs(CFG, SRC, prefs)
    assert [s["name"] for s in srcs] == ["A", "B"]
    _, srcs = apply_prefs(CFG, SRC, {"sources": {"remove": ["a"]}})
    assert srcs == []


def test_empty_prefs_is_noop():
    cfg, srcs = apply_prefs(CFG, SRC, {})
    assert cfg == CFG and srcs == SRC


def test_company_sources_from_prefs():
    prefs = {"sources": {"add": [
        {"name": "ashby:foo", "type": "ashby", "companies": ["foo"]},
        {"name": "bad1", "type": "ashby", "companies": ["x/../y"]},
        {"name": "bad2", "type": "lever", "companies": []},
    ]}}
    _, srcs = apply_prefs(CFG, SRC, prefs)
    assert [s["name"] for s in srcs] == ["A", "ashby:foo"]