# Sync Incident Pipeline

Watches a Superset fork for upstream-sync breakages and dispatches **Devin** (live API) to find the root cause and open a fix PR. A dashboard streams each session and tracks PRs opened and time-to-fix.

> The pipeline calls the real Devin API — there is no offline/mock mode. Without `DEVIN_API_KEY` and `DEVIN_ORG_ID` it fails fast.

## Prerequisites

- Docker (or Python 3.11+)
- A Devin API key + org ID
- A GitHub fork you control (see Setup)

## Setup

You won't have write access to our repo, so Devin works against a fork **you** own:

1. Fork `cognition-demo/superset` into your org.
2. Connect the Devin GitHub app to that fork (lets Devin push branches and open PRs).
3. `cp .env.example .env`, then set:
   - `DEVIN_API_KEY`, `DEVIN_ORG_ID`
   - `TARGET_REPO=your-org/superset`

## Run

```bash
docker compose up --build
```

- Dashboard: http://localhost:8765
- On startup the pipeline dispatches the demo incident to Devin against your fork, then streams the session through to the PR it opens.

Without Docker:

```bash
pip install -e .
pipeline run          # dispatch the incident + serve the dashboard
pipeline dashboard    # webhook receiver only (production entrypoint)
```

## Environment

| Var | Required | Purpose |
|-----|----------|---------|
| `DEVIN_API_KEY` | yes | Devin API auth |
| `DEVIN_ORG_ID` | yes | Devin org the session runs in |
| `TARGET_REPO` | yes | `owner/repo` Devin fixes and opens the PR against |
| `WEBHOOK_SECRET` | no | Shared secret for the GitHub Action trigger |

## How it works

- A nightly GitHub Action rebases the fork onto upstream. If the custom-extension tests fail, it opens an issue and POSTs the pipeline webhook (`/webhook/trigger`).
- The pipeline creates a Devin session with the repo, failing tests, and upstream commits.
- Devin traces the failure to its root cause, fixes it properly (not by deleting tests), and opens a PR.
- The dashboard tracks sessions, PRs opened, and average time-to-fix.

To watch the trigger side end-to-end, run the bundled `nightly-upstream-sync.yml` workflow on your fork (Actions → Run workflow); it rebases onto the public upstream mirror and produces the break that drives the pipeline.
