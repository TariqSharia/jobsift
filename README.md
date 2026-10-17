# jobsift

A small job-alert pipeline: it pulls remote job postings from many sources, filters and
scores them against your own rules, removes duplicates, and sends the best ones to Telegram.
It runs for free on GitHub Actions, and you can steer it from your phone through a
Cloudflare Worker.

```
sources ──▶ filter ──▶ score ──▶ dedupe ──▶ cap per run/company ──▶ Telegram
 (JSON, RSS,   (keywords,   (0-10)   (title +      (best first,         + inbox
  HN, ATS      walls, years)           company)      rest queued)         (inbox.json)
  boards)
```

## What it does

- **Many sources, no code to add one.** Generic adapters for JSON APIs, RSS/Atom feeds,
  the monthly Hacker News "Who is hiring" thread, and company boards on Greenhouse, Ashby
  and Lever. A source is a block in `sources.yaml`.
- **Filters that remove walls.** Required and excluded keywords, title rules, regex walls
  (single-country residency, security clearance, region-locked postings), a maximum years of
  experience read from the posting text, and location and time-zone checks.
- **A 0-10 fit score** from title, stack keywords, seniority, location signals and a pay
  mention. Alerts go out best first, and a star marks the strongest.
- **Never floods you.** A cap per run and per company queues the rest for the next runs.
- **Employer flags.** Words you want to double-check are shown as a label, not blocked.
- **Telegram control.** `/run`, `/status`, `/inbox`, `/job`, `/include`, `/exclude`,
  `/strong`, `/maxyears`, `/addcompany`, `/addrss` and more, handled by a single-file
  Cloudflare Worker that stores changes in `prefs.json`.
- **Tested.** Python tests for filters, sources, preferences and the inbox, JavaScript tests
  for the Worker, and CI with lint and a Docker build.

## Try it locally (no accounts needed)

```bash
pip install -r requirements.txt
python main.py
```

With no `config.yaml`, it falls back to `config.example.yaml` and `sources.example.yaml`.
Without Telegram credentials it writes matches to `matches.md`.

```bash
pip install -r requirements-dev.txt
ruff check . && pytest -q && node --test worker/worker.test.mjs
```

## Run it for real

Run it from a **private instance repo** that holds your own `config.yaml`, `sources.yaml`
and job history, and checks out this engine on every run. The setup is in
[docs/private-instance.md](docs/private-instance.md), with a ready workflow in
[examples/instance-workflow.yml](examples/instance-workflow.yml).

Telegram needs a bot token and your chat id, stored as GitHub secrets. Details for the
phone control Worker are in [worker/SETUP.md](worker/SETUP.md).

## Tuning

Everything personal is configuration, not code. See the comments in
[config.example.yaml](config.example.yaml): keywords, title rules, walls, scoring lists,
`max_years`, per-run and per-company caps, and optional `home_region_terms` and
`home_utc_offsets` that describe where you can work.

## Project layout

| File | Role |
|---|---|
| `main.py` | one run: fetch, filter, dedupe, cap, notify, save state |
| `sources.py` | source adapters (JSON, RSS, HN, Greenhouse, Ashby, Lever) |
| `filters.py` | walls, years of experience, flags, scoring |
| `notifier.py` | Telegram messages, with a markdown fallback |
| `prefs.py` | merges Telegram-made changes (`prefs.json`) over the config |
| `worker/` | Cloudflare Worker for Telegram commands |
| `tests/` | Python tests |

## License

MIT, see [LICENSE](LICENSE).
