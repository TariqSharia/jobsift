# Keeping your setup private: engine + private instance

This repo is the **engine**: code, tests, example configs. It holds nothing personal.
Your own filters, sources, job history and Telegram settings live in a second, **private**
repo (the "instance"). The instance runs the engine, so engine improvements reach you
automatically and your private files are never copied into anything public.

```
 public repo (engine)                     private repo (instance)
 ┌────────────────────┐   checked out     ┌─────────────────────────────┐
 │ main.py, filters.py│ ───────────────▶  │ config.yaml  sources.yaml   │
 │ sources.py, ...    │   on every run    │ prefs.json   inbox.json     │
 │ config.example.yaml│                   │ seen_jobs.json  matches.md  │
 │ tests + CI         │                   │ .github/workflows/          │
 └────────────────────┘                   │   job_alert.yml             │
                                          └─────────────────────────────┘
```

## Set it up

1. Create a **private** repo for the instance.
2. Copy `config.example.yaml` to `config.yaml` and `sources.example.yaml` to
   `sources.yaml` in it, then edit them to your taste.
3. Copy `examples/instance-workflow.yml` to `.github/workflows/job_alert.yml` in the
   private repo and replace `YOUR_USER` with the owner of the engine repo.
4. In the private repo add the secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
   (Settings → Secrets and variables → Actions).
5. Optional Telegram control: follow `worker/SETUP.md` and point `GITHUB_REPO` at the
   **private** repo, because that is where `config.yaml` and `prefs.json` live.

## What lives where

| Engine (public) | Instance (private) |
|---|---|
| all `.py` code, tests, CI, Dockerfile, worker code | `config.yaml`, `sources.yaml`, `prefs.json` |
| `config.example.yaml`, `sources.example.yaml` | `seen_jobs.json`, `inbox.json`, `matches.md` |
| README and docs | the scheduled workflow and the Telegram secrets |

The engine's `.gitignore` already ignores the instance files, so they cannot be committed
to it by accident.

## Settings that describe you (all optional, all in the instance's `config.yaml`)

```yaml
home_region_terms: []   # words that make a location count as open to you, e.g. your country
home_utc_offsets: []    # UTC offsets you can work in; sources with tz_field drop the rest
flag_label: "check employer"   # label shown next to a flagged employer
flag_patterns: []       # regexes for employers you want to double-check (never blocked)
max_years: 5            # skip postings asking for more years of experience (0 = off)
```

## Staying safe when the engine changes

The instance runs `ref: main` of the engine by default. To stay on a version you trust,
tag a release in the engine repo (for example `v1`) and set `ref: v1` in the workflow.
Move the tag when you have tested a newer version.
