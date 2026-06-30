# Sync Incident Pipeline

Event-driven automation that watches a Superset fork for upstream-sync breakages and dispatches **Devin** (live API) to diagnose the failure and open a fix PR. A dashboard streams each session and tracks PRs opened and time-to-fix.

**Demo video:** https://youtu.be/LpeIIUUzumE

## Scenario

I've created a scenario where Mark, the VP of Engineering, is feeling some pressure from the C-suite and is exploring options that will help his team ship more features, with more confidence.

They've been maintaining their own version of Superset, which powers client-facing analytics. There are several paper cuts in the way, and one of them is the constant drift tax that comes with maintaining a custom fork of a large project.

Mark wants more frequent merges so the team can benefit from upstream security patches. He also wants to avoid another episode of letting the fork lag six months behind and then spending a week resolving conflicts and the side effects that come with them.

For the demo, I've set up a new GitHub organisation with three repos:

- **superset** — the custom fork: https://github.com/cognition-demo/superset
- **superset-mirror** — a pinned stand-in for upstream, so the demo stays reproducible
- **pipeline** — orchestrates Devin (this repo): https://github.com/cognition-demo/pipeline

Org: https://github.com/cognition-demo

- Issue created programmatically by the pipeline: https://github.com/cognition-demo/superset/issues/22
- PR Devin opened autonomously to remediate the test failures: https://github.com/cognition-demo/superset/pull/23

## How it works

- A nightly GitHub Action rebases the fork onto upstream. If the custom-extension tests fail, it opens an issue and POSTs the pipeline webhook (`/webhook/trigger`).
- The pipeline creates a Devin session with the repo, failing tests, and upstream commits.
- Devin traces the failure to its root cause, fixes it properly (not by deleting tests), and opens a PR.
- The dashboard tracks sessions, PRs opened, and average time-to-fix.

## How to test

### Live

1. Go to https://pipeline-production-3990.up.railway.app and click **Trigger upstream sync** in the top right to start the GitHub workflow.
2. This pulls changes from upstream, but CI won't pass — the custom-extension tests fail.
3. A GitHub issue is created and Devin is tasked with resolving it.
4. Once done, Devin opens a PR in the superset fork.

The issue, the PR, and any workflow runs can be monitored here: https://github.com/cognition-demo/superset

### Locally

The pipeline calls the real Devin API — there is no mock mode. Without `DEVIN_API_KEY` and `DEVIN_ORG_ID` it fails fast.

**Prerequisites:** Docker (or Python 3.11+), a Devin API key + org ID, and a GitHub fork you control.

**Setup** — Devin works against a fork *you* own, since you won't have write access to ours:

1. Fork `cognition-demo/superset` into your org.
2. Connect the Devin GitHub app to that fork (lets Devin push branches and open PRs).
3. `cp .env.example .env`, then set:
   - `DEVIN_API_KEY`, `DEVIN_ORG_ID` — required
   - `TARGET_REPO=your-org/superset` — required
   - `GITHUB_TOKEN` — **optional**, only enables the **Trigger upstream sync** button (see below). The local run does not need it.

**Run:**

```bash
docker compose up --build
```

Dashboard: http://localhost:8765. On the initial run the pipeline **automatically dispatches a sample incident** to Devin against your fork — no `GITHUB_TOKEN` and no button click needed — then streams the session through to the PR it opens.

The **Trigger upstream sync** button is an optional alternative: it kicks off a fresh upstream sync via GitHub Actions, which feeds a real incident back into the pipeline. That path — and only that path — needs `GITHUB_TOKEN`.

Without Docker:

```bash
pip install -e .
pipeline run          # dispatch the incident + serve the dashboard
pipeline dashboard    # webhook receiver only (production entrypoint)
```

**Environment:**

| Var | Required | Purpose |
|-----|----------|---------|
| `DEVIN_API_KEY` | yes | Devin API auth |
| `DEVIN_ORG_ID` | yes | Devin org the session runs in |
| `TARGET_REPO` | yes | `owner/repo` Devin fixes and opens the PR against |
| `GITHUB_TOKEN` | no | Actions-write token for the **Trigger upstream sync** button |
| `WEBHOOK_SECRET` | no | Shared secret for the GitHub Action trigger |

## Acknowledgements

- The dashboard is unsecured and exposed to the internet. For the purposes of this demo I didn't want to spend too long on security hardening; any such vulnerabilities are out of scope.
- Testing is limited to the tests relevant to the demo — we deliberately don't run the entire suite.
- The pipeline doesn't handle merge conflicts. That's out of scope here, since the focus is on using Devin for non-trivial tasks.
- The `superset-mirror` repo makes the demo easier to replicate: it pins upstream so new changes can't introduce surprise conflicts.
- The dashboard stores sessions and metrics in SQLite, with no persistence across deploys/restarts — again, to limit scope.
