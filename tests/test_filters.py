import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import yaml  # noqa: E402

from filters import extract_money_mentions, filter_jobs, passes_filters  # noqa: E402

CONFIG = {
    "include_keywords": ["node.js", "backend", "python"],
    "exclude_keywords": ["security clearance", "casino"],
    "strong_signal_keywords": ["work from anywhere", "worldwide"],
    "include_appian": True,
}


def make_job(text, title="Backend Engineer"):
    return {"id": "t-1", "title": title, "company": "Acme", "url": "http://x",
            "text": text, "source": "test"}


def test_keeps_job_matching_include_keyword():
    result = filter_jobs([make_job("We need a Node.js developer")], CONFIG)
    assert len(result) == 1


def test_rejects_job_with_excluded_phrase():
    job = make_job("Backend role, requires Security Clearance")
    assert filter_jobs([job], CONFIG) == []


def test_rejects_job_matching_no_include_keyword():
    assert filter_jobs([make_job("Senior accountant wanted")], CONFIG) == []


def test_exclude_wins_over_include():
    job = make_job("Python backend at a casino")
    assert filter_jobs([job], CONFIG) == []


def test_flags_strong_signal():
    job = make_job("Python role, work from anywhere")
    result = filter_jobs([job], CONFIG)
    assert result[0]["strong_signal"] is True


def test_no_strong_signal_by_default():
    result = filter_jobs([make_job("Python role in an office")], CONFIG)
    assert result[0]["strong_signal"] is False


def test_matching_is_case_insensitive():
    result = filter_jobs([make_job("NODE.JS DEVELOPER")], CONFIG)
    assert len(result) == 1


def test_appian_toggle_off_drops_appian_titles():
    config = dict(CONFIG, include_appian=False, include_keywords=["appian"])
    job = make_job("Appian developer", title="Appian Developer")
    assert filter_jobs([job], config) == []


def test_extract_money_finds_dollar_amount():
    found = extract_money_mentions("Salary $120,000 per year")
    assert any("120,000" in m for m in found)


def test_extract_money_finds_euro_k_amount():
    found = extract_money_mentions("Pay: €70k")
    assert any("70k" in m for m in found)


def test_extract_money_returns_nothing_when_no_figures():
    assert extract_money_mentions("Great team, no figures here") == []


def test_regex_walls_and_seeker_posts():
    cfg = dict(CONFIG)
    cfg["include_keywords"] = ["node.js", "python"]
    cfg["exclude_patterns"] = [
        r"remote\s*[\(\-,:/]\s*(usa?|uk|germany)\b",
        r"\b(usa|uk|canada)[\s-]+only\b",
        r"willing to relocate",
        r"looking for:",
    ]

    def ok(text):
        job = {"title": "t", "text": text, "id": "1", "source": "s", "url": "u"}
        return filter_jobs([job], cfg)

    assert ok("Remote worldwide. We use Node.js")
    assert not ok("Remote (Germany) Node.js backend")
    assert not ok("REMOTE - US, python")
    assert not ok("Canada only. Python role")
    assert not ok("Location: X. Willing to relocate: yes. Node.js Looking for: roles")
    assert ok("Remote, EMEA timezones. Python")


def test_remote_united_states_wall():
    import re
    pat = r"remote\s*[\(\-,:/]\s*(usa?|u\.s\.a?|united\s+states|united\s+kingdom|uk)\b"
    assert re.search(pat, "remote - united states")
    assert re.search(pat, "remote, uk")
    assert not re.search(pat, "remote - emea")


def _real_config():
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, "config.example.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_mid_level_and_support_titles_pass_title_gate():
    cfg = _real_config()
    for title in ["Junior Web Developer", "Software Developer", "Customer Success Engineer",
                  "Application Integration Specialist", "Integration Consultant",
                  "Technical Support Specialist"]:
        job = make_job("Remote worldwide, node.js and react", title=title)
        ok, _ = passes_filters(job, cfg)
        assert ok, title


def test_unrelated_titles_still_blocked():
    cfg = _real_config()
    for title in ["Marketing Consultant", "Account Executive", "Android Developer"]:
        job = make_job("Remote worldwide, node.js and react", title=title)
        ok, _ = passes_filters(job, cfg)
        assert not ok, title


def test_senior_titles_rank_below_mid_unless_years_are_low():
    from filters import score_job, title_level
    cfg = _real_config()
    base = "Remote worldwide node.js typescript postgres docker"
    senior = make_job(base, title="Senior Backend Engineer")
    mid = make_job(base, title="Mid-level Backend Engineer")
    plain = make_job(base, title="Backend Engineer")
    for j in (senior, mid, plain):
        j["years"] = None
    assert title_level(senior["title"]) == "senior"
    assert title_level(mid["title"]) == "early"
    assert score_job(senior, cfg) < score_job(plain, cfg) < score_job(mid, cfg)
    senior["years"] = 3
    assert score_job(senior, cfg) >= score_job(plain, cfg)


def test_game_industry_jobs_are_flagged_not_blocked():
    from filters import game_flag
    cfg = _real_config()
    game = make_job("Remote worldwide node.js. We build a Unity live ops platform.",
                    title="Backend Engineer")
    plain = make_job("Remote worldwide node.js. We build invoicing software.",
                     title="Backend Engineer")
    assert game_flag(game, cfg) is True
    assert game_flag(plain, cfg) is False
    out = filter_jobs([game, plain], cfg)
    assert len(out) == 2
    assert [j["game"] for j in out] == [True, False]


def test_small_amounts_are_not_shown_as_pay():
    from filters import extract_money_mentions
    assert extract_money_mentions("A $500 laptop allowance and a €300 stipend") == []
    assert extract_money_mentions("Salary $120,000 - $150,000 per year") == ["$120,000 - $150,000"]
    assert extract_money_mentions("Pay is $45/hr") == ["$45/hr"]
    assert extract_money_mentions("Range $135-$155k") == ["$135-$155k"]


def test_us_only_locations_are_blocked_but_open_ones_stay():
    from filters import geo_blocked
    assert geo_blocked("North America")
    assert geo_blocked("Remote - US")
    assert geo_blocked("United States; Canada")
    assert geo_blocked("Europe")
    assert not geo_blocked("Worldwide")
    assert not geo_blocked("Remote - EMEA")
    assert not geo_blocked("Anywhere in the world")
    assert not geo_blocked("")
    assert not geo_blocked("Remote")


def test_geo_blocked_jobs_fail_the_filters():
    from filters import passes_filters
    cfg = _real_config()
    job = {"title": "Backend Engineer", "text": "node typescript postgres remote",
           "location": "North America"}
    assert not passes_filters(job, cfg)[0]
    job["location"] = "Worldwide"
    assert passes_filters(job, cfg)[0]


def test_support_titles_rank_below_development_titles():
    from filters import score_job
    cfg = _real_config()
    base = {"text": "node typescript postgres rest docker", "strong_signal": True}
    dev = score_job({**base, "title": "Backend Engineer"}, cfg)
    sup = score_job({**base, "title": "Support Engineer"}, cfg)
    assert dev > sup


def test_real_config_max_years_is_five():
    assert _real_config()["max_years"] == 5


def test_titles_outside_the_stack_rank_lower():
    from filters import score_job
    cfg = _real_config()
    base = {"text": "python node typescript postgres docker", "strong_signal": True}
    ok = score_job({**base, "title": "Software Engineer"}, cfg)
    for title in ("Golang System Software Engineer", "C++/Rust Graphics Engineer",
                  "Software Engineer - OpenStack", "Linux Software Engineer"):
        assert score_job({**base, "title": title}, cfg) < ok, title


def test_part_time_jobs_are_flagged_and_ranked_up():
    from filters import part_time_flag, score_job
    cfg = _real_config()
    job = {"title": "Backend Developer (part-time)", "text": "python node postgres"}
    assert part_time_flag(job)
    assert not part_time_flag({"title": "Backend Developer", "text": "full time role"})
    full = score_job({**job, "part_time": False}, cfg)
    assert score_job({**job, "part_time": True}, cfg) >= full

def test_geo_blocked_respects_home_region_terms():
    from filters import geo_blocked
    assert geo_blocked("Europe")
    assert not geo_blocked("Europe, Atlantis", extra_ok=["atlantis"])
    assert geo_blocked("Europe", extra_ok=["atlantis"])
