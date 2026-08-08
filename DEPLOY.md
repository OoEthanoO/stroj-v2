# Deploying stroj

The judge does not fit on Vercel, and no amount of configuration changes that.
Vercel's Python runtime is AWS Lambda; the judge needs `fork`/`execve`/`wait4`/
`killpg` to supervise submissions, `g++` and `javac` on PATH, a persistent
filesystem for SQLite and test data, background threads that outlive a request,
and minutes of wall time. Lambda provides none of those.

So the deployment is split:

```
  stroj.ethanyanxu.com          (Vercel, static)
     │  /                       → index.html + /static/*
     │  /api/*  ── rewrite ──▶  https://<judge-origin>/api/*
                                (container host: FastAPI + judge workers + volume)
```

Rewrites proxy server-side, so the browser only ever sees one origin. Session
cookies keep working untouched — no CORS, no `SameSite=None`.

---

## Read this before exposing it publicly

A public judge means **anyone with an account runs arbitrary code on your
host**. What you get per submission:

| | macOS | Linux container |
|---|---|---|
| CPU / wall / output limits | yes | yes |
| Memory ceiling (RSS sampling + kill) | yes | yes |
| Process-group kill on timeout | yes | yes |
| Network denied | yes (`sandbox-exec`) | yes (`unshare --net`, probed at startup) |
| Writes confined to the submission's box | yes (`sandbox-exec`) | **no** |

On Linux there is no filesystem confinement — a submission can write anywhere
the judge process can. **The container is the boundary**, which is why the judge
must run in a disposable container with a volume mounted only at `/data`, never
directly on a host you care about.

`python -m stroj doctor` and the footer of the web UI both report the isolation
actually in force. If it says `none`, submissions have no confinement at all.

For genuinely untrusted users, put each submission in its own sandbox — gVisor,
nsjail, or a per-submission container. `sandbox.run()` in
`stroj/judge/sandbox.py` is the single seam where that swaps in.

---

## What this costs

An online judge is unusually hostile to cheap hosting, for a reason specific to
what it does: **verdicts depend on reproducible CPU timing.** If the CPU is
throttled or shared with noisy neighbours, the same submission gets `AC` on one
run and `TLE` on the next, and the time limits stop meaning anything. That rules
out the "free tier" shape that suits a normal web app.

Sizing: the JVM wants ~512 MB on its own, so with two workers you want **2 GB+
RAM and 2 consistent vCPU**. ARM is fine — g++, the JDK and CPython are all
first-class on arm64, which is what makes the cheap ARM instances a good fit.

The frontend half is free regardless: Vercel Hobby covers static hosting and a
custom domain at $0 (its terms prohibit commercial use).

### Genuinely free, indefinitely

| Option | Catch |
|---|---|
| **Oracle Cloud Always Free** | 4 ARM cores / 24 GB / 200 GB — far more than this needs, at $0 forever, with consistent CPU and root access. Two real gotchas: A1 capacity is frequently unavailable in popular regions (retry, or pick a quieter one), and Oracle reclaims persistently idle Always Free instances. Signup wants card verification but does not charge it. |
| **Cloudflare Tunnel → a machine you own** | $0 and no signup beyond Cloudflare: a public hostname with TLS and no inbound ports. The machine has to stay on. On a Mac this is also the *only* option that keeps full `sandbox-exec` isolation, including filesystem confinement. |

### Cheapest paid, if you want it hands-off

| Option | Roughly | Notes |
|---|---|---|
| **Hetzner CAX11** (ARM, 2 vCPU, 4 GB, 40 GB) | **~€3.3/mo** | Best price/performance here, and CPU is consistent enough for meaningful time limits. Needs a one-off ~€1 identity check. |
| Hetzner CX22 (x86, 2 vCPU, 4 GB) | ~€3.8/mo | Same, x86 if you prefer. |
| Fly.io (shared-cpu-1x 1 GB + 3 GB volume) | ~$6/mo | Nicest deploy story, but *shared* CPU means timing noise. |
| DigitalOcean / Vultr basic droplet | ~$5–6/mo | Fine, just pricier than Hetzner for the same specs. |

Prices drift — check current rates before committing.

### Not viable, despite being free

| Option | Why |
|---|---|
| **Render free tier** | 0.1 vCPU makes time limits meaningless, **no persistent disk** wipes the database on every restart, and it spins down after 15 min idle — which kills the worker pool. |
| **Google Cloud Run** | Stateless, scales to zero, no volume. Would need Cloud SQL + GCS and still freezes background threads. |
| **Vercel / Netlify functions** | The compute-model mismatch described at the top of this file. |

### Recommendation

For a **club running real contests**, size on concurrency rather than cost. Each
submission costs a compile plus a run per test — call it 5–10 seconds — and
everyone submits in the last ten minutes of a round. Two cores means one judge
worker and a queue that backs up exactly when it matters most.

**Hetzner CAX21** (4 ARM vCPU, 8 GB, ~€6.5/mo) is the pick: Ampere Altra has no
turbo boost, so every core runs at a fixed clock and the boost-then-sag
behaviour that ruins laptop timings simply does not exist. Three judge workers,
consistent verdicts, no capacity lottery.

**Oracle A1 Always Free** is the same silicon for $0 and worth trying first —
but Always Free accounts are deprioritised for A1 capacity. Upgrading to Pay As
You Go keeps the free allowance at $0 and removes that deprioritisation; the
catch is that a card is then on file, so exceeding 4 OCPU / 24 GB starts
charging you.

A dedicated laptop over **Cloudflare Tunnel** is fine for solo practice — and on
macOS it keeps full `sandbox-exec` isolation, which no Linux host gives you —
but a thin-chassis machine will thermally throttle under contest load. Measure
before trusting it: `python -m stroj calibrate --seconds 180`.

---

## Part 1 — the judge backend

Build and run the container (any Docker host):

```bash
docker build -t stroj-judge .
docker volume create stroj-data
docker run -d --name stroj-judge \
  -p 8000:8000 \
  -v stroj-data:/data \
  -e STROJ_ADMIN_PASSWORD='pick-something-long' \
  -e STROJ_WORKERS=2 \
  -e STROJ_SECURE_COOKIES=1 \
  --restart unless-stopped \
  stroj-judge
```

Then seed and sanity-check it:

```bash
docker exec stroj-judge python -m stroj doctor   # toolchains + isolation
docker exec stroj-judge python -m stroj seed     # sample problems + contest
curl -fsS http://localhost:8000/healthz
```

### Expect `isolation: none` in a container, and fix it at the host

`doctor` will almost certainly report `isolation: none` inside a stock
container, and that is not a misconfiguration you can talk your way out of:
`unshare --net` needs `CAP_SYS_ADMIN`, which Docker does not grant by default,
and the `--user --map-root-user` fallback runs into the default seccomp
profile's restriction on `CLONE_NEWUSER`. The probe degrades safely rather than
pretending, which is why the UI says `none`.

The obvious fix — `--cap-add SYS_ADMIN` — is the wrong one. On Linux the
container *is* the security boundary, and handing submissions `SYS_ADMIN`
undermines exactly the thing protecting you.

Take the network away at the host instead. It needs no container privileges,
and the judge has no use for outbound internet once the image is built:

```bash
docker network create stroj-net --opt com.docker.network.bridge.name=stroj0
sudo iptables -I DOCKER-USER -i stroj0 -j DROP
sudo netfilter-persistent save
```

`DOCKER-USER` is consulted ahead of Docker's own rules and survives daemon
restarts. Matching on `-i` (traffic *leaving* the bridge) leaves published-port
ingress, which arrives with `-o`, untouched.

Then run the container with `--network stroj-net`, and **verify it rather than
trusting it**:

```bash
docker exec stroj-judge /usr/bin/python3 -c \
  "import socket;socket.setdefaulttimeout(5);socket.create_connection(('1.1.1.1',53));print('NOT BLOCKED')"
```

That must fail. If it prints `NOT BLOCKED`, submissions can reach the internet —
do not open registration until it doesn't.

### Oracle Cloud Always Free

Create an Always Free **Ampere A1** instance (Ubuntu 24.04, 2+ OCPU, 12+ GB).
The image is multi-arch, so aarch64 is fine. Then:

```bash
git clone https://github.com/OoEthanoO/stroj-v2.git && cd stroj-v2
./scripts/bootstrap-judge.sh judge.ethanyanxu.com
```

That script does everything above — docker and caddy, the dedicated bridge, the
egress rule, the build, a volume, the container bound to `127.0.0.1` so only
Caddy can reach it, and TLS. It finishes by running `doctor` and the egress test
and printing both, plus a generated admin password. It is idempotent; re-run it
freely.

Two things it cannot do for you:

- **Open 80 and 443 in the VCN security list.** Oracle's firewall is separate
  from the host's, and forgetting this is the single most common reason a fresh
  A1 instance appears dead. Ubuntu images also ship restrictive local
  `iptables`; `sudo netfilter-persistent save` after opening them.
- **Point DNS.** Add an `A` record for `judge.ethanyanxu.com` to the instance's
  public IP. Caddy cannot issue a certificate until that resolves.

If A1 capacity is unavailable in your region — common — either retry (capacity
frees up), pick a quieter region, or fall back to Hetzner below. The script is
identical there.

### Hetzner (or any plain VPS)

Same script, and Hetzner hands you a box that is actually available:

```bash
# Ubuntu 24.04, CAX11 (ARM) or CX22 (x86) — the image is multi-arch
git clone https://github.com/OoEthanoO/stroj-v2.git && cd stroj-v2
./scripts/bootstrap-judge.sh judge.ethanyanxu.com
```

Hetzner's firewall defaults to open, so restrict inbound to 22/80/443 in the
console. Point `judge.ethanyanxu.com` at the IP with an `A` record.

### Cloudflare Tunnel alternative

No inbound ports, no server, works from the machine you already have:

```bash
brew install cloudflared
cloudflared tunnel login                       # opens your browser
cloudflared tunnel create stroj
cloudflared tunnel route dns stroj judge.ethanyanxu.com
cloudflared tunnel run --url http://127.0.0.1:8000 stroj
```

Run `./run.sh` alongside it. The judge origin is `https://judge.ethanyanxu.com`.

---

## Part 2 — the frontend on Vercel

### Shipping the static half first (optional)

The build refuses to run while `JUDGE_ORIGIN` is a placeholder, so that a
frontend pointing at nothing cannot reach production. If you want to validate
the domain, DNS and TLS *in parallel* with provisioning the backend, set
`STROJ_FRONTEND_ONLY=1` in the Vercel project's environment variables. The build
then warns instead of failing, and the deployed site loads and reports "No judge
backend connected" until you set a real origin.

Remove that variable once the judge is live, so the guard protects you again.

### The real thing

Set the judge origin. `scripts/build-static.sh` refuses to build while the
placeholder is present, so a misconfigured frontend cannot ship:

```bash
sed -i '' 's|JUDGE_ORIGIN|judge.ethanyanxu.com|g' vercel.json
bash scripts/build-static.sh          # sanity-check locally
```

**Know which deploy path you are on — they read different files:**

- **Git integration** (the repo is connected in the Vercel dashboard): Vercel
  clones the *pushed commit*. Uncommitted files do not exist as far as the build
  is concerned. Deploy by pushing:

  ```bash
  git commit -am 'Point Vercel rewrites at the judge' && git push
  ```

- **CLI**: `vercel --prod` uploads your *local working directory*, so it will
  happily deploy files you have not committed — which is convenient for a
  one-off and confusing if you forget. Needs `vercel login` first.

Pick one. If the repo is connected, pushing is the path, and running the CLI as
well just creates a second deployment from a different source.

Attach the domain:

```bash
vercel domains add stroj.ethanyanxu.com
vercel alias set <deployment-url> stroj.ethanyanxu.com
```

Vercel will print the DNS record to create at whoever hosts `ethanyanxu.com` —
usually a `CNAME` to `cname.vercel-dns.com`. Propagation is minutes; TLS is
issued automatically.

---

## Verifying

```bash
curl -fsS https://stroj.ethanyanxu.com/healthz            # proxied to the judge
curl -fsS https://stroj.ethanyanxu.com/api/languages | head -c 200
```

Then open the site and check the footer: it should list the three languages and
the isolation mode. Sign in, submit a solution, and confirm the verdict lands —
that exercises the rewrite, the cookie, and the worker pool in one go.

## Running a contest

- **Set a scoreboard freeze** when creating the contest — 60 minutes is
  conventional for a 3-hour round. The board stops resolving submissions for
  the final stretch, so nobody can tell whether the team above them just solved
  something. Attempts still count and still appear as a hidden count; organisers
  see through the freeze automatically. It lifts when the contest ends.
- **Registration is invite-only by default.** `bootstrap-judge.sh` generates a
  code and prints it; share that with the club. `STROJ_REGISTRATION=open` opens
  it up, `closed` means you create accounts with `python -m stroj adduser`.
- **Time limits are specific to this machine.** A submission's runtime is
  measured on whatever hardware the judge sits on, so limits calibrated
  elsewhere do not transfer. Set them at 2–3x a reference solution, and if you
  ever move hosts, `python -m stroj rejudge` everything — previously accepted
  submissions can start failing.
- **Check the host is steady before a round**: `python -m stroj calibrate`.
  Watch `drift`; anything above 15% means identical submissions will get
  different verdicts as the machine heats up.
- **`STROJ_WORKERS` should not exceed your core count.** Oversubscribing makes
  submissions contend and produces spurious `TLE`s.

## Backups

`/data` holds the database and every problem's test data. Losing it mid-contest
is unrecoverable, so take snapshots:

```bash
docker exec stroj-judge python -m stroj backup --into /data/backups --keep 14
```

That uses SQLite's backup API rather than copying the file, so it is consistent
even while the judge is writing verdicts. Run it nightly, and once more just
before a contest:

```bash
(crontab -l 2>/dev/null; echo "17 4 * * * docker exec stroj-judge python -m stroj backup >/dev/null 2>&1") | crontab -
```

Those live inside the volume, which protects you from a bad rejudge but not from
losing the box. Copy them off periodically:

```bash
docker cp stroj-judge:/data/backups ./stroj-backups
```

## Operational notes
- **Redeploying the frontend is independent of the judge.** Vercel only serves
  three static files; the judge keeps running.
