# Weekly automation with GitHub Actions

**Status:** the workflow ([`.github/workflows/watch.yml`](../.github/workflows/watch.yml)) is written and
statically validated by the test suite (`tests/test_workflow.py`). Its persistence script was exercised
against a local bare Git repository (first run, no-op run, push conflict with rebase). It is optional example
automation, not a service. It has **not been verified on GitHub**, so expect to fix small things on a first real
run. Disable it in the Actions tab if you do not want weekly runs.

## What a run does

1. Checks out the code (no push credentials held).
2. Makes sure the orphan branch `watchdog-data` exists (creates it on the first run) and checks it out
   into `state/`.
3. Installs the project with `pip install -c constraints.txt -e '.[dev,browser]'` and Chromium (needed
   only for the Playwright fallback), on Python 3.12.
4. Runs `pytest`, then `docs-watchdog --validate-only`.
5. Runs `docs-watchdog --no-llm --state-dir state --out state/reports`: deterministic, no API key, no secrets.
6. Appends the report to the job summary.
7. Commits `state/` to `watchdog-data` and pushes (also when the monitor failed, so healthy sources keep
   their advanced baselines and the failing run's report is kept). The report is also uploaded as an
   artifact.

The job fails (red run) when tests fail, any source fails or is anomalous, or state cannot be pushed. A run
is never reported as successful if it was incomplete; the report itself says **INCOMPLETE** as well.

## Schedule and timezone

`cron: "17 6 * * 1"` = every Monday at 06:17 **UTC** (07:17 CET in winter, 08:17 CEST in summer). GitHub
cron is always UTC and has no timezone setting. The odd minute avoids the busiest start of the hour;
scheduled runs can still start late. `workflow_dispatch` allows a manual run from the Actions tab.

## How state is kept

Layout of the `watchdog-data` branch (the code branch never contains generated data):

```
snapshots/<connector>/<source>.txt    latest normalized content (what the next run diffs against)
snapshots/<connector>/<source>.json   source URL, fetch time (UTC), sha256, length
reports/<UTC timestamp>.md            one report per run, never overwritten
reports/latest.md                     copy of the newest report
```

Why a branch, and not the alternatives:

| Option | Why not |
|---|---|
| Actions cache | Entries are evicted when unused for 7 days and are best-effort; a weekly job sits right at that limit. A lost cache would silently turn the next run into "baseline created" and hide real changes. |
| Artifacts | Expire (default 90 days) and cannot be used as the next run's input without extra plumbing. |
| Commits to the main branch | Mixes generated data with code, triggers CI and conflicts with branch protection. |
| External storage | Extra service, credentials and cost. |

Properties of the chosen design:

- **History is append-only.** Each run adds one commit; the workflow never force-pushes or rewrites the
  branch, so every previous snapshot and report stays recoverable with `git log`/`git show`.
- **Concurrency control.** `concurrency: group: docs-watchdog-state, cancel-in-progress: false` serialises
  runs; a second run waits instead of racing. As a safety net, a rejected push is retried up to three times
  with `git pull --rebase`; after that the job fails loudly.
- **Least privilege.** The workflow default is `contents: read`. Only the job that pushes the data branch
  has `contents: write`, and the code checkout does not persist the token.
- **No secrets in state.** The deterministic run uses no secrets. Reports contain public documentation
  URLs and quoted text from vendor pages only. Use a **private** repository anyway.
- **Cache is only a download cache.** `setup-python` caches pip downloads; losing that cache costs time,
  never data.

## Activating it once there is a remote

1. Create a **private** GitHub repository and add it as a remote of this project, then push the code
   branch (you decide when; nothing in this project does it for you).
2. In the repository, check *Settings → Actions → General*: Actions enabled, and workflow permissions
   allow the job's `contents: write` (if the first run fails with a 403 on push, this is the place).
3. If you protect branches, do not protect `watchdog-data` against pushes from `github-actions[bot]`.
4. *Actions → weekly-watch → Run workflow* for a first manual run. It creates `watchdog-data` and stores
   baselines; the report will say "baseline created" for every source and contains no findings. Real
   change detection starts with the second run.
5. Read the job summary and `reports/latest.md` on `watchdog-data`. Fix whatever the first real run
   reveals (see *Not yet verified* below).

## Checking that it is still running

GitHub can disable scheduled workflows in repositories with no activity for a long period (it documents
60 days of inactivity for public repositories; check GitHub's current documentation for private ones, and
do not assume the workflow's own commits to `watchdog-data` count as activity). A disabled workflow stops
silently, so check periodically:

```bash
gh workflow list                                  # is weekly-watch listed as active?
gh run list --workflow weekly-watch --limit 5     # did it run this week, and did it succeed?
git log -1 --format=%cd origin/watchdog-data      # date of the last persisted run
```

If it was disabled: *Actions → weekly-watch → Enable workflow* (or `gh workflow enable weekly-watch`). A
simple habit is to look at the `watchdog-data` commit date once a month.

## Recovering or inspecting state

```bash
git fetch origin watchdog-data
git worktree add state origin/watchdog-data        # read-only view of the current state
docs-watchdog --no-llm --state-dir state --out state/reports   # run locally against the same state
git log --oneline origin/watchdog-data -- snapshots/meta-ads/marketing-api-changelog.txt
```

- **Roll back a bad run:** on the data branch, `git revert <commit>` and push (a normal, history-preserving
  commit). Do not reset or force-push.
- **Start over:** delete `snapshots/` (or the branch). The next run creates fresh baselines and, by design,
  raises no findings that run.
- **A source keeps failing the length guard** (`min_length_ratio`) because the page really shrank: set
  `min_length_ratio: 0` for that source in `connectors.yaml` for one run, then restore it.

## Not yet verified

- The workflow has not run on GitHub; action versions, Chromium installation time, and permissions are
  untested there.
- Chromium installation (`playwright install --with-deps chromium`) was not run locally; it is only needed
  for the Playwright fallback of one Meta source. `constraints.txt` was regenerated on Python 3.12 and
  verified there: a fresh 3.12 environment built from it reproduces the identical freeze, and the suite
  passes.
- The workflow pins `actions/checkout@v4`, `actions/setup-python@v5` and `actions/upload-artifact@v4`. I
  confirmed on 2026-11-03 that those tags exist, but the current releases of all three are newer majors
  (v7.x). Whether the old majors keep working, or only emit runtime deprecation warnings, can only be seen on
  an actual run. Pinning to a commit SHA would be stricter; I have not done that.
- `actionlint` was not run: it was not installed, and I did not fetch and execute it for this check.
- Failure notifications rely on GitHub's own run-failure emails; this project configures no Slack, email or
  other external notification.
