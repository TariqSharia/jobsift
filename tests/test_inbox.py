import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402


def job(i, text="full text"):
    return {"id": f"x-{i}", "ref": main.make_ref(f"x-{i}"), "title": f"T{i}", "company": "C",
            "source": "S", "url": f"https://u/{i}", "strong_signal": True, "money": [],
            "text": text}


def test_ref_is_stable_and_short():
    assert main.make_ref("a") == main.make_ref("a") and len(main.make_ref("a")) == 6


def test_inbox_prepends_dedupes_and_truncates(tmp_path):
    path = str(tmp_path / "inbox.json")
    main.update_inbox([job(1), job(2)], path)
    main.update_inbox([job(3), job(1, "newer")], path)
    data = json.load(open(path, encoding="utf-8"))
    assert [d["title"] for d in data] == ["T3", "T1", "T2"]
    assert data[1]["text"] == "newer"
    main.update_inbox([job(9, "x" * 10000)], path)
    assert len(json.load(open(path, encoding="utf-8"))[0]["text"]) == main.INBOX_TEXT_MAX


def test_inbox_caps_size(tmp_path):
    path = str(tmp_path / "inbox.json")
    main.update_inbox([job(i) for i in range(main.INBOX_MAX + 20)], path)
    assert len(json.load(open(path, encoding="utf-8"))) == main.INBOX_MAX


def test_every_source_needs_a_matching_title_except_hn():
    from filters import filter_jobs
    cfg = {"include_keywords": ["python"], "title_keywords": ["backend"]}
    jobs = [
        {"id": "a", "title": "Backend Engineer", "text": "x python"},
        {"id": "b", "title": "Account Executive", "text": "uses python daily"},
        {"id": "c", "title": "Anything", "text": "python role", "loose_title": True},
    ]
    assert [j["id"] for j in filter_jobs(jobs, cfg)] == ["a", "c"]


def test_main_caps_messages_and_keeps_overflow_for_next_run(tmp_path, monkeypatch):
    import json

    import main
    jobs = [{"id": f"j{i}", "title": "t", "text": "python", "source": "S", "url": "https://u",
             "strong_signal": i == 5, "score": 9 if i == 5 else 3} for i in range(5 + 1)]
    sent = []
    monkeypatch.setattr(main, "SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(main, "INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setattr(main, "load_config", lambda: {"max_per_run": 2})
    monkeypatch.setattr(main, "load_sources", lambda: [])
    monkeypatch.setattr(main, "load_prefs", lambda p: {})
    monkeypatch.setattr(main, "apply_prefs", lambda c, s, p: (c, s))
    monkeypatch.setattr(main, "fetch_all", lambda s: (jobs, []))
    monkeypatch.setattr(main, "filter_jobs", lambda j, c: j)

    def fake_notify(to_send, summary):
        sent.extend(j["id"] for j in to_send)
        assert "follow in the next runs" in summary
        return {j["id"] for j in to_send}

    monkeypatch.setattr(main, "notify", fake_notify)
    main.main()
    assert sent == ["j5", "j0"]
    assert sorted(json.load(open(tmp_path / "seen.json"))) == ["j0", "j5"]


def test_required_years_reads_the_real_requirement():
    from filters import required_years
    assert required_years("Requires 8+ years of experience building APIs") == 8
    assert required_years("5-8 years of professional experience") == 5
    assert required_years("at least ten years of backend experience") == 10
    assert required_years("3+ years of experience with Node.js, 7 years experience overall") == 7
    assert required_years("We have 10 years of history and 30 engineers") is None
    assert required_years("Great team, no requirements listed") is None


def test_max_years_is_adjustable_and_off_switch_works():
    from filters import passes_filters
    job = {"title": "Backend Engineer", "text": "node.js, 6+ years of experience"}
    base = {"include_keywords": ["node.js"]}
    assert passes_filters(job, {**base, "max_years": 6})[0]
    assert not passes_filters(job, {**base, "max_years": 5})[0]
    assert passes_filters(job, {**base, "max_years": 0})[0]
    assert passes_filters(job, base)[0]


def test_prefs_override_max_years():
    from prefs import apply_prefs
    cfg, _ = apply_prefs({"max_years": 6}, [], {"max_years": 4})
    assert cfg["max_years"] == 4
    cfg, _ = apply_prefs({"max_years": 6}, [], {"max_years": "x"})
    assert cfg["max_years"] == 6


def test_years_shown_in_telegram_message():
    from notifier import format_job
    out = format_job({"title": "T", "source": "S", "url": "https://u", "years": 5})
    assert "5+ yrs" in out


def _real_cfg():
    import yaml
    return yaml.safe_load(open("config.example.yaml", encoding="utf-8"))


def test_real_config_title_rules():
    from filters import passes_filters
    cfg = _real_cfg()

    def ok(title, text="node.js typescript remote anywhere"):
        return passes_filters({"title": title, "text": f"{title} {text}"}, cfg)[0]

    assert ok("Senior Backend Engineer")
    assert ok("Full-Stack Developer (Node.js)")
    assert ok("Solutions Engineer")
    assert not ok("Account Executive")
    assert not ok("iOS Engineer")
    assert not ok("Staff Software Engineer")
    assert not ok("Java Developer")
    assert ok("JavaScript Developer")
    assert not ok("Security Engineer")
    assert not ok("Data Engineer")


def test_real_config_timezone_wall():
    from filters import passes_filters
    cfg = _real_cfg()

    def ok(text):
        return passes_filters({"title": "Backend Engineer", "text": "node.js " + text}, cfg)[0]

    assert not ok("Must work EST hours")
    assert not ok("Overlap with US time zones required")
    assert not ok("Working hours UTC-8 to UTC-5")
    assert ok("Flexible hours, async first, UTC-4 to UTC+4")
    assert ok("We value rest and best practices")


def test_score_and_flag_label():
    from filters import filter_jobs
    cfg = _real_cfg()
    good = {"id": "g", "title": "Backend Engineer", "source": "S", "url": "https://u",
            "text": ("Node.js NestJS TypeScript PostgreSQL work from anywhere, $60,000, "
                     "3+ years of experience")}
    meh = {"id": "m", "title": "Python Developer", "source": "S", "url": "https://u",
           "text": "python at a crypto exchange"}
    out = {j["id"]: j for j in filter_jobs([good, meh], cfg)}
    assert out["g"]["score"] >= cfg["star_score"]
    assert out["m"]["score"] < out["g"]["score"]
    assert out["m"]["flags"] == ["crypto"]
    assert out["m"]["flag_label"] == cfg["flag_label"]
    assert not out["g"]["flags"]


def test_message_shows_score_and_flag_warning():
    from notifier import format_job
    out = format_job({"title": "T", "source": "S", "url": "https://u", "score": 8,
                      "flags": ["crypto"], "flag_label": "check"})
    assert "⭐ 8/10" in out and "check: crypto" in out


def test_flag_label_is_configurable():
    from notifier import format_job
    out = format_job({"title": "T", "source": "S", "url": "https://u",
                      "flags": ["lender"], "flag_label": "double-check employer"})
    assert "double-check employer: lender" in out


def test_same_role_on_two_boards_is_one_job():
    from main import dedupe_key, drop_duplicates
    a = {"id": "remoteok-1", "title": "Remote Senior React Full Stack Developer",
         "company": "Lemon.io"}
    b = {"id": "remotive-9", "title": "Senior React Full-stack Developer", "company": "Lemon.io"}
    other = {"id": "x-3", "title": "Backend Engineer", "company": "Lemon.io"}
    assert dedupe_key(a["title"], a["company"]) == dedupe_key(b["title"], b["company"])
    assert [j["id"] for j in drop_duplicates([a, b, other], set())] == ["remoteok-1", "x-3"]
    seen = {dedupe_key(a["title"], a["company"])}
    assert [j["id"] for j in drop_duplicates([b], seen)] == []
    assert dedupe_key("Developer", "") == ""


def test_one_company_cannot_fill_the_whole_batch(tmp_path, monkeypatch):
    import main
    def mk(jid, title, company, score):
        return {"id": jid, "title": title, "company": company, "text": "python",
                "source": "S", "url": "https://u", "score": score}

    jobs = [mk(f"c{i}", f"Role {i}", "BigCo", 6) for i in range(5)]
    jobs.append(mk("other", "Other role", "Acme", 5))
    sent = []
    monkeypatch.setattr(main, "SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(main, "INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setattr(main, "load_config", lambda: {"max_per_run": 10, "max_per_company": 2})
    monkeypatch.setattr(main, "load_sources", lambda: [])
    monkeypatch.setattr(main, "load_prefs", lambda p: {})
    monkeypatch.setattr(main, "apply_prefs", lambda c, s, p: (c, s))
    monkeypatch.setattr(main, "fetch_all", lambda s: (jobs, []))
    monkeypatch.setattr(main, "filter_jobs", lambda j, c: j)

    def fake_notify(to_send, summary):
        sent.extend(j["id"] for j in to_send)
        return {j["id"] for j in to_send}

    monkeypatch.setattr(main, "notify", fake_notify)
    main.main()
    assert sent == ["c0", "c1", "other"]