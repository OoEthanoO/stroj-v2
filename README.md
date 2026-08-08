# stroj

A self-hosted online judge. Users submit code against a problem's test data; the
judge compiles it, runs it under time/memory/output limits, compares the output,
and returns a verdict. Contests come with timed windows and ICPC or IOI
scoreboards.

Python + FastAPI + SQLite on the back, dependency-free vanilla JS on the front.
No Docker, no Redis, no build step.

```bash
./run.sh
```

Then open <http://127.0.0.1:8000>. On first start the judge creates an `admin`
account and logs a generated password once — set `STROJ_ADMIN_PASSWORD` if you
would rather choose it. To get some content to look at:

```bash
.venv/bin/python -m stroj seed
```

That loads three problems (A+B, Maximum Subarray Sum, Circle Area — chosen to
exercise the token, partial-scoring and floating-point paths) and a running
contest containing all three.

## What's here

| | |
|---|---|
| Languages | Python 3, C++20 (clang++/g++), Java 21 |
| Verdicts | `AC` `WA` `TLE` `MLE` `RE` `CE` `OLE` `IE` |
| Checkers | `token` (whitespace-insensitive), `exact`, `float` (absolute *or* relative epsilon) |
| Scoring | all-or-nothing, or partial credit with per-test point values |
| Contests | timed windows, sealed problem sets, ICPC and IOI scoreboards |
| Authoring | web admin UI, zip test-data upload, Markdown statements, rejudging |

## How it fits together

```
  browser ──HTTP──▶ FastAPI ──▶ SQLite (submissions land as PENDING)
                                  │
                    judge workers ─┘  claim one at a time, atomically
                          │
                          ├─ compile in a scratch box directory
                          └─ per test: fork → setrlimit → sandbox-exec → exec
                                       wait4 for rusage, sample RSS, compare
```

- `stroj/judge/sandbox.py` — runs one process under limits. The one file worth
  reading closely.
- `stroj/judge/runner.py` — compile, loop over tests, decide a verdict.
- `stroj/judge/worker.py` — the queue: claim `PENDING`, judge, write back.
- `stroj/contest.py` — scoreboard computation.
- `stroj/api/` — HTTP routes. `stroj/web/` — the frontend.

Judging is subprocess-bound, so the workers are plain threads and SQLite runs in
WAL mode; readers never block behind a worker writing a verdict. `STROJ_WORKERS`
sets how many run in parallel.

## Sandboxing, and what it does not do

Read this before putting the judge anywhere but your own machine.

Each submission runs in a throwaway directory as a fresh process group with:

- **`RLIMIT_CPU`** — a hard backstop against a runaway loop.
- **A wall-clock watchdog** that `killpg`s the whole group, so a submission
  cannot outlive its timeout by forking.
- **`RLIMIT_FSIZE`** — caps bytes written, surfacing as `OLE`.
- **Active RSS sampling** (`libproc` on macOS, `/proc` on Linux) that kills the
  process when it crosses its memory limit. This is not belt-and-braces:
  **macOS accepts `RLIMIT_AS` and then ignores it** — a submission with a 256 MiB
  limit will happily allocate several GiB — so on macOS the sampler *is* the
  memory limit. `RLIMIT_AS` is still set where it helps (Linux, and not for the
  JVM, which reserves a large virtual arena on startup and would die).
- **`sandbox-exec`** (macOS) denying all network access and all filesystem
  writes outside the submission's own directory.

Outside macOS there is no `sandbox-exec`. On Linux the judge probes for
`unshare` at startup and uses a network namespace instead, which takes the
network away but gives **no filesystem confinement** — there, the container is
the boundary. `python -m stroj doctor` and the web UI footer both report the
isolation actually in force (`sandbox-exec`, `unshare-net`, or `none`) rather
than the one you asked for.

What this is **not**: a container, a VM, or a defence against someone who is
actually trying. Reads are unrestricted, so a submission can read files the
judge user can read. `sandbox-exec` is deprecated by Apple. There is no user
separation, no cgroup, no seccomp filter. Fork bombs are mitigated by the
process-group kill, not prevented.

Treat it as adequate for a classroom, a team practice server, or your own
machine — not for hostile submissions from the open internet. For that you want
each submission in its own container or microVM; `sandbox.run()` is the single
seam where that would be swapped in.

Set `STROJ_SANDBOX=0` to drop `sandbox-exec` and rely on rlimits alone.
`python -m stroj doctor` reports what is actually active.

## Time and memory limits per language

A problem's limit is the C++ limit. Slower runtimes are scaled, which is how
most judges handle this — otherwise every problem needs three sets of limits:

| Language | Time | Memory |
|---|---|---|
| C++ | `t` | `m` |
| Java | `2t + 500 ms` | `m + 320 MiB` (JVM overhead; heap capped at `m` via `-Xmx`) |
| Python | `3t + 200 ms` | `m + 32 MiB` |

The submit form shows the effective numbers for the language you pick.

C++ submissions get a bundled `<bits/stdc++.h>` shim on the include path, since
that's how competitive code is written and libc++ has no such header.

## Authoring problems

From the admin page, or over the API. Test data is a zip of paired files:

```
tests.zip
├── sample1.in     ← "sample" in the name makes it a visible sample
├── sample1.out
├── 2.in           ← paired by stem, ordered naturally (2 before 10)
├── 2.out
└── …
```

Inputs may use `.in`/`.input`/`.dat`, answers `.out`/`.ans`/`.a`/`.expected`.
Uploading replaces the whole test set; rejudge afterwards to re-run existing
submissions against it.

**Checkers.** `token` splits on whitespace and compares — right for almost
everything. `exact` compares line by line, forgiving trailing whitespace and a
missing final newline. `float` is `token` plus an epsilon on numeric tokens,
matching if the absolute *or* relative error is within `float_eps`.

**Partial scoring.** Give tests point values and mark the problem `partial`, and
every test runs even after one fails; the score is the points banked. Otherwise
judging stops at the first failure — faster, and all a binary problem needs.

**Hidden problems.** `visible: false` keeps a problem off the public list. It
becomes readable automatically once a contest containing it starts.

## Contests

A contest has a window, a scoring system, and a labelled problem set that stays
sealed until the clock starts. Submissions made inside the window with the
contest attached count toward the board; practice submissions to the same
problem do not.

**ICPC** ranks by problems solved, then by penalty: for each solved problem, the
minute it was solved plus `penalty_minutes` per rejected attempt before it.
Attempts after solving are free; unsolved problems cost nothing.

**IOI** ranks by total score, each problem contributing the best percentage of
its tests any submission passed. Ties break on the time of the last improvement.

## Configuration

| Variable | Default | |
|---|---|---|
| `STROJ_DATA` | `./data` | database, test data, scratch directories |
| `STROJ_WORKERS` | `2` | judge threads |
| `STROJ_SANDBOX` | `1` | `0` disables `sandbox-exec` |
| `STROJ_ADMIN_USER` / `STROJ_ADMIN_PASSWORD` | `admin` / random | initial account |
| `STROJ_PYTHON` / `STROJ_CXX` / `STROJ_JAVAC` / `STROJ_JAVA` | from `PATH` | toolchain paths — point `STROJ_CXX` at `g++` if you prefer |
| `STROJ_MAX_SOURCE_BYTES` | `262144` | source size cap |
| `STROJ_COMPILE_TIME` | `20` | compile timeout, seconds |
| `STROJ_SESSION_TTL_DAYS` | `14` | login lifetime |
| `STROJ_SECURE_COOKIES` | `0` | mark session cookies `Secure` — turn on for HTTPS |

Everything lives under `STROJ_DATA`. Delete that directory for a clean slate.

## Deploying

See [DEPLOY.md](DEPLOY.md). Short version: the judge needs a real container with
a persistent volume, so it cannot live on a serverless platform; the static
frontend can sit on Vercel and proxy `/api/*` back to it. That file also covers
what the hosting actually costs and which free tiers are and are not viable.

## Command line

```bash
python -m stroj serve --host 0.0.0.0 --port 8000
python -m stroj seed                       # sample problems and a contest
python -m stroj adduser alice --role admin
python -m stroj passwd alice               # also revokes existing sessions
python -m stroj rejudge --problem a-plus-b --now
python -m stroj doctor                     # toolchains and sandbox status
```

## API

Cookie-session auth; every response is JSON.

```
POST   /api/auth/register|login|logout      GET /api/auth/me
GET    /api/problems                        GET /api/problems/{slug}
GET    /api/languages                       GET /api/config
POST   /api/submissions                     GET /api/submissions[?mine&problem&contest&username]
GET    /api/submissions/{id}
GET    /api/contests                        GET /api/contests/{slug}
GET    /api/contests/{slug}/scoreboard
```

Admin routes live under `/api/admin/` — problems (`POST`/`PATCH`/`DELETE`), test
data (`PUT .../tests`, `POST .../tests/upload`), contests, `rejudge`, and user
roles. Interactive docs at `/docs`.

## Tests

```bash
.venv/bin/python -m pytest
```

138 tests. The sandbox and runner suites spawn real compilers and processes and
assert on real `TLE`/`MLE`/`RE`/`CE` outcomes for every installed language, so
they take about 20 seconds; language tests skip themselves if a toolchain is
missing.
