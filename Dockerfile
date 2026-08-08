# The judge backend. Needs a real Linux container: it compiles submissions with
# g++/javac and supervises them with fork/exec/setrlimit, none of which exist on
# a serverless platform.
#
# Temurin gives a pinned JDK 21; Ubuntu noble underneath supplies python3 and
# g++ 13.
#
# `util-linux` provides `unshare`, which the judge probes for at startup to put
# submissions in an empty network namespace. Do not rely on it here: a stock
# container has neither CAP_SYS_ADMIN nor an unrestricted CLONE_NEWUSER, so the
# probe will report `isolation: none` and it is the *host* that must deny the
# container egress. See DEPLOY.md. It stays installed because it costs nothing
# and does work on hosts where those restrictions are lifted.
FROM eclipse-temurin:21-jdk-noble

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        g++ \
        util-linux \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Submissions execute with this process's privileges, so it must not be root.
# /data is created and chowned here: when a *fresh* named volume is first
# mounted over it, Docker seeds the volume with this ownership.
RUN useradd --create-home --uid 10001 judge \
    && mkdir -p /data \
    && chown judge:judge /data

WORKDIR /app

COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY stroj/ ./stroj/

# /app stays root-owned and read-only to the judge; everything it writes goes
# to /data.
USER judge

# STROJ_PYTHON is deliberately the *system* interpreter, not the venv one on
# PATH: submissions must not be able to import the judge's own dependencies.
# (Comments cannot live inside a line-continuation, hence they sit up here.)
ENV PATH=/opt/venv/bin:$PATH \
    STROJ_DATA=/data \
    STROJ_CXX=g++ \
    STROJ_PYTHON=/usr/bin/python3 \
    STROJ_WORKERS=2 \
    STROJ_SANDBOX=1 \
    STROJ_SECURE_COOKIES=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# SQLite and test data live here. Mount a real volume or you lose both on
# every restart.
VOLUME ["/data"]
EXPOSE 8000

# tini reaps the strays a misbehaving submission can leave behind.
ENTRYPOINT ["/usr/bin/tini", "--"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

CMD ["python", "-m", "stroj", "serve", "--host", "0.0.0.0", "--port", "8000"]
