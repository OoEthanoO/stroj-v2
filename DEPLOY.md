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

Try **Oracle Always Free** first — it is $0 indefinitely and over-specced for
this. If A1 capacity is unavailable in your region, **Hetzner CAX11 at ~€3.3/mo**
is the cheapest thing that still gives trustworthy verdicts. Use the
**Cloudflare Tunnel** path if you would rather not sign up for anything and have
a machine that stays on.

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

`doctor` should report all three languages `ok` and isolation `unshare-net`. If
isolation says `none`, the container lacks the capability for network
namespaces — add `--cap-add SYS_ADMIN`, or accept that submissions have network
access and do not open registration to the public.

### Oracle Cloud Always Free

Create an Always Free **Ampere A1** instance (Ubuntu 22.04+, 2+ OCPU, 12+ GB),
then:

```bash
sudo apt update && sudo apt install -y docker.io
sudo usermod -aG docker "$USER" && newgrp docker
git clone https://github.com/OoEthanoO/stroj-v2.git && cd stroj-v2
# …the docker build/run above…
```

Open port 443 in both the OCI security list and the host firewall, and put
Caddy in front for automatic TLS:

```bash
sudo apt install -y caddy
echo 'judge.ethanyanxu.com { reverse_proxy 127.0.0.1:8000 }' | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

Point `judge.ethanyanxu.com` at the instance's public IP with an `A` record.
That hostname is your **judge origin**.

### Hetzner (or any plain VPS)

Identical to the above once Docker is installed — Hetzner just hands you a box
that is actually available, unlike Oracle's A1 capacity:

```bash
# Ubuntu 24.04, CAX11 (ARM) or CX22 (x86)
sudo apt update && sudo apt install -y docker.io caddy
sudo usermod -aG docker "$USER" && newgrp docker
git clone https://github.com/OoEthanoO/stroj-v2.git && cd stroj-v2
docker build -t stroj-judge .            # the Dockerfile is arch-independent
# …the docker volume/run above…
echo 'judge.ethanyanxu.com { reverse_proxy 127.0.0.1:8000 }' | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

Hetzner's firewall defaults to open; restrict inbound to 22/80/443 in the
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

Set the judge origin. `scripts/build-static.sh` refuses to build while the
placeholder is present, so a misconfigured frontend cannot ship:

```bash
sed -i '' 's|JUDGE_ORIGIN|judge.ethanyanxu.com|g' vercel.json
bash scripts/build-static.sh          # sanity-check locally
git commit -am 'Point Vercel rewrites at the judge'
```

Then deploy. Both commands need your credentials, so run them yourself:

```bash
vercel login
vercel --prod
```

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

## Operational notes

- **Back up `/data`.** It holds the database and every problem's test data.
  `docker run --rm -v stroj-data:/data -v "$PWD:/backup" alpine tar czf /backup/stroj-$(date +%F).tar.gz /data`
- **Change the seeded admin password** — `python -m stroj passwd admin` also
  revokes existing sessions.
- **`STROJ_WORKERS` should not exceed your core count.** Oversubscribing makes
  submissions contend and produces spurious `TLE`s.
- **Redeploying the frontend is independent of the judge.** Vercel only serves
  three static files; the judge keeps running.
