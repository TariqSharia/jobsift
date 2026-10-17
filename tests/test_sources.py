import pytest

import sources
from notifier import format_job, format_summary, post_message, send_jobs


def test_get_path():
    assert sources.get_path({"a": {"b": [1, 2]}}, "a.b.1") == 2
    assert sources.get_path({"a": 1}, "x.y") is None


def test_expand_env(monkeypatch):
    monkeypatch.setenv("KEY", "s3")
    assert sources.expand_env({"k": "Bearer ${KEY}"}) == {"k": "Bearer s3"}
    with pytest.raises(ValueError):
        sources.expand_env("${NOPE_NOT_SET}")


def test_parse_json_items_dotted_and_skip():
    src = {"name": "My API", "items_path": "data.jobs", "id": "id", "title": "t",
           "company": "co.name", "link": "u", "text": ["t", "tags"], "require_field": "id"}
    data = {"data": {"jobs": [
        {"id": 1, "t": "Dev", "co": {"name": "Acme"}, "u": "http://x", "tags": ["go", "rust"]},
        {"meta": True},
    ]}}
    jobs = sources.parse_json_items(data, src)
    assert len(jobs) == 1
    assert jobs[0]["id"] == "myapi-1" and jobs[0]["company"] == "Acme"
    assert "rust" in jobs[0]["text"]


def test_parse_rss():
    entry = {"title": "A &amp; B", "link": "http://l", "summary": "<p>hi</p>"}
    jobs = sources.parse_rss_entries([entry], {"name": "Feed"})
    assert jobs[0]["title"] == "A & B" and jobs[0]["text"].endswith("hi")


def test_fetch_all_never_raises():
    jobs, stats = sources.fetch_all([{"name": "bad", "type": "nope"}, {"name": "x"}])
    assert jobs == [] and all(s["error"] for s in stats)


def test_format_job_escapes_html():
    out = format_job({"title": "R&D <Lead>", "source": "S", "url": "http://u?a=1&b=2"})
    assert "R&amp;D &lt;Lead&gt;" in out


class FakeResp:
    def __init__(self, status, body=None):
        self.status_code, self._b, self.ok, self.text = status, body or {}, status < 400, ""

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), 0

    def post(self, *a, **k):
        self.calls += 1
        return FakeResp(self.statuses.pop(0))


def test_send_jobs_only_reports_delivered():
    s = FakeSession([200, 400])
    jobs = [{"id": "a", "title": "t", "source": "s", "url": "u"},
            {"id": "b", "title": "t", "source": "s", "url": "u"}]
    assert send_jobs(jobs, "t", "c", s) == ["a"]


def test_post_message_retries_429(monkeypatch):
    monkeypatch.setattr("notifier.time.sleep", lambda *_: None)
    s = FakeSession([429, 200])
    assert post_message("t", "c", "hi", s) is True and s.calls == 2


def test_summary_lists_errors():
    out = format_summary(10, 3, 1, [{"name": "X", "count": 0, "error": "HTTP 403"}])
    assert "Fetched 10, kept 3, new 1" in out and "X: HTTP 403" in out


def test_hn_uses_official_account(monkeypatch):
    seen = {}

    class R:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fake_get(url, params=None, timeout=None):
        if "algolia" in url:
            seen["tags"] = params["tags"]
            return R({"hits": [
                {"title": "Ask HN: Who wants to be hired? (October 2026)", "objectID": "1"},
                {"title": "Ask HN: Who is hiring? (October 2026)", "objectID": "2"}]})
        if url.endswith("/item/2.json"):
            return R({"kids": [10]})
        return R({"text": "ACME <p>hiring Python remote", "id": 10})

    monkeypatch.setattr(sources.requests, "get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda *_: None)
    jobs = sources.fetch_hn_hiring({"name": "HN", "max_comments": 5})
    assert "author_whoishiring" in seen["tags"]
    assert [j["id"] for j in jobs] == ["hn-10"]


def test_require_any_gate():
    jobs = [{"text": "ONSITE NYC"}, {"text": "We are REMOTE-first"}]
    assert sources.require_any(jobs, ["remote"]) == [jobs[1]]
    assert sources.require_any(jobs, None) == jobs


def test_parse_greenhouse_unescapes_content():
    data = {"jobs": [{"id": 7, "title": "Backend Engineer", "company_name": "GitLab",
                      "absolute_url": "https://x/7", "location": {"name": "Remote, EMEA"},
                      "content": "&lt;p&gt;Node.js &amp;amp; Postgres&lt;/p&gt;"}]}
    job = sources.parse_greenhouse(data, "gitlab")[0]
    assert job["id"] == "greenhouse-gitlab-7" and job["company"] == "GitLab"
    assert "Remote, EMEA" in job["text"] and "<p>" not in job["text"]


def test_parse_ashby_marks_remote_and_skips_unlisted():
    data = {"jobs": [
        {"id": "a", "title": "PM", "location": "Berlin", "isRemote": True, "isListed": True,
         "jobUrl": "https://j/a", "descriptionPlain": "x"},
        {"id": "b", "title": "Hidden", "isListed": False},
    ]}
    jobs = sources.parse_ashby(data, "checkly")
    assert len(jobs) == 1 and "remote" in jobs[0]["text"].lower()
    assert jobs[0]["url"] == "https://j/a"


def test_parse_lever():
    data = [{"id": "z", "text": "Support Engineer", "hostedUrl": "https://l/z",
             "workplaceType": "remote", "categories": {"location": "Anywhere"},
             "descriptionPlain": "help devs"}]
    job = sources.parse_lever(data, "acme")[0]
    assert job["id"] == "lever-acme-z" and "remote" in job["text"]


def test_company_boards_partial_failure_is_reported(monkeypatch):
    class R:
        def __init__(self, ok):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise sources.requests.HTTPError(response=type("X", (), {"status_code": 404})())

        def json(self):
            return {"jobs": [{"id": 1, "title": "Dev", "isRemote": True, "isListed": True}]}

    monkeypatch.setattr(sources.requests, "get",
                        lambda url, **k: R("good" in url))
    jobs, stats = sources.fetch_all([{"name": "A", "type": "ashby",
                                      "companies": ["good", "bad", "bad$$"]}])
    assert len(jobs) == 1
    assert stats[0]["count"] == 1 and "bad: HTTP 404" in stats[0]["error"]
    assert "bad$$" not in stats[0]["error"]  # unsafe names are ignored silently


def test_company_boards_default_remote_gate(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"jobs": [
                {"id": 1, "title": "Dev", "isListed": True, "location": "Berlin Office"},
                {"id": 2, "title": "Dev", "isListed": True, "location": "Remote - EMEA"},
            ]}

    monkeypatch.setattr(sources.requests, "get", lambda url, **k: R())
    jobs, _ = sources.fetch_all([{"name": "A", "type": "ashby", "companies": ["x"]}])
    assert [j["id"] for j in jobs] == ["ashby-x-2"]


def test_format_job_shows_ref():
    out = format_job({"title": "t", "source": "s", "url": "u", "ref": "ab12cd"})
    assert "<code>ab12cd</code>" in out


def test_board_jobs_gated_by_location_not_description():
    from sources import REMOTE_WORDS, parse_greenhouse, require_any
    data = {"jobs": [
        {"id": 1, "title": "Backend Engineer", "content": "x",
         "location": {"name": "Remote - EMEA"}},
        {"id": 2, "title": "Backend Engineer", "location": {"name": "Berlin"},
         "content": "we love remote-friendly culture"},
    ]}
    kept = require_any(parse_greenhouse(data, "acme"), REMOTE_WORDS)
    assert [j["id"] for j in kept] == ["greenhouse-acme-1"]


def test_geo_and_timezone_gates():
    from sources import geo_ok, parse_json_items, tz_ok
    assert geo_ok("") and geo_ok("Anywhere in the World") and geo_ok(["EMEA"])
    assert not geo_ok("USA Only") and not geo_ok(["Europe"]) and not geo_ok(["United States"])
    assert tz_ok([]) and tz_ok([-1, 0, 2, 3], hours=(2, 3))
    assert not tz_ok([-10, -9, -8, -7, -6, -5, 14], hours=(2, 3))
    assert tz_ok([-10, -9, -8])  # no home offsets configured: the check is off
    src = {"name": "H", "id": "id", "title": "t", "link": "u", "text": ["t"],
           "geo_field": "geo", "tz_field": "tz"}
    data = [
        {"id": 1, "t": "ok", "u": "https://a", "geo": ["Worldwide"], "tz": []},
        {"id": 2, "t": "us", "u": "https://b", "geo": ["United States"], "tz": []},
        {"id": 3, "t": "tz", "u": "https://c", "geo": [], "tz": [-8, -7]},
    ]
    import sources
    sources.configure({"home_utc_offsets": [2, 3]})
    try:
        assert [j["title"] for j in parse_json_items(data, {**src, "title": "t"})] == ["ok"]
    finally:
        sources.configure({})


def test_json_paging_uses_offset(monkeypatch):
    import sources

    seen = []

    class R:
        def __init__(self, offset):
            self.offset = offset

        def raise_for_status(self):
            pass

        def json(self):
            return {"jobs": [{"id": self.offset + i, "title": "t", "url": "u"} for i in range(2)]}

    def fake_get(url, params=None, **kw):
        seen.append(params["offset"])
        return R(params["offset"])

    monkeypatch.setattr(sources.requests, "get", fake_get)
    src = {"name": "P", "url": "https://x", "items_path": "jobs", "pages": 3,
           "page_param": "offset", "page_size": 20}
    assert len(sources.fetch_json(src)) == 6
    assert seen == [0, 20, 40]

def test_home_region_and_offsets_come_from_config():
    import sources
    try:
        sources.configure({"home_region_terms": ["Atlantis"], "home_utc_offsets": [1, 2]})
        assert sources.geo_ok("Atlantis only")
        assert not sources.geo_ok("Europe")
        assert sources.tz_ok([0, 1]) and not sources.tz_ok([-8, -7])
    finally:
        sources.configure({})
    assert not sources.geo_ok("Atlantis only")
