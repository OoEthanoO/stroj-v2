# The judge backend. Needs a real Linux container: it compiles submissions with
# g++/javac and supervises them with fork/exec/setrlimit, none of which exist on
# a serverless platform.
#
# Temurin gives a pinned JDK 21; Ubuntu noble underneath supplies python3 and
# g++ 13. `util-linux` is here for `unshare`, which is how submissions lose
# network access on Linux (there is no sandbox-exec outside macOS).
FROM eclipse-temurin:21-jdk-noble

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        g++ \
        util-linux \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY stroj/ ./stroj/

ENV PATH=/opt/venv/bin:$PATH \
    STROJ_DATA=/data \
    STROJ_CXX=g++ \
    # Deliberately NOT the venv python: submissions should not be able to import
    # the judge's own dependencies.
    STROJ_PYTHON=/usr/bin/python3 \
    STROJ_WORKERS=2 \
    STROJ_SANDBOX=1 \
    STROJ_SECURE_COOKIES=1 \
    PYTHONUNBUFFERED=1

# SQLite and test data live here. Mount a real volume or you lose both on
# every restart.
VOLUME ["/data"]
EXPOSE 8000

# tini reaps the strays a misbehaving submission can leave behind.
ENTRYPOINT ["/usr/bin/tini", "--"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

CMD ["python", "-m", "stroj", "serve", "--host", "0.0.0.0", "--port", "8000"]
