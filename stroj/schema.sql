-- stroj online judge schema

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',   -- 'user' | 'admin'
    bio           TEXT    NOT NULL DEFAULT '',       -- markdown, shown on the profile
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS problems (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    statement       TEXT    NOT NULL DEFAULT '',     -- markdown
    time_limit_ms   INTEGER NOT NULL DEFAULT 1000,
    memory_limit_mb INTEGER NOT NULL DEFAULT 256,
    checker         TEXT    NOT NULL DEFAULT 'token',-- 'token' | 'exact' | 'float'
    float_eps       REAL    NOT NULL DEFAULT 1e-6,
    partial         INTEGER NOT NULL DEFAULT 0,      -- 1 = score partial credit, run every test
    visible         INTEGER NOT NULL DEFAULT 1,      -- 0 = hidden outside a running contest
    points          INTEGER NOT NULL DEFAULT 100,    -- difficulty value awarded for solving it
    author_id       INTEGER          REFERENCES users(id) ON DELETE SET NULL,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS testcases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id  INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,                    -- 1-based ordering
    input_path  TEXT    NOT NULL,
    answer_path TEXT    NOT NULL,
    is_sample   INTEGER NOT NULL DEFAULT 0,
    points      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (problem_id, idx)
);

CREATE TABLE IF NOT EXISTS contests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    starts_at   TEXT    NOT NULL,                    -- ISO-8601 UTC
    ends_at     TEXT    NOT NULL,
    scoring     TEXT    NOT NULL DEFAULT 'icpc',     -- 'icpc' | 'ioi'
    penalty_minutes INTEGER NOT NULL DEFAULT 20,
    freeze_minutes  INTEGER NOT NULL DEFAULT 0,      -- hide board this long before the end
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS contest_problems (
    contest_id INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    label      TEXT    NOT NULL,                     -- 'A', 'B', ...
    PRIMARY KEY (contest_id, problem_id),
    UNIQUE (contest_id, label)
);

CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    problem_id  INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    contest_id  INTEGER          REFERENCES contests(id) ON DELETE SET NULL,
    language    TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    verdict     TEXT    NOT NULL DEFAULT 'PENDING',
    score       INTEGER NOT NULL DEFAULT 0,
    max_score   INTEGER NOT NULL DEFAULT 0,
    time_ms     INTEGER NOT NULL DEFAULT 0,          -- slowest test
    memory_kb   INTEGER NOT NULL DEFAULT 0,          -- peak across tests
    message     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    judged_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sub_user    ON submissions(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_sub_problem ON submissions(problem_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_sub_contest ON submissions(contest_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_sub_verdict ON submissions(verdict);

CREATE TABLE IF NOT EXISTS submission_tests (
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    idx           INTEGER NOT NULL,
    verdict       TEXT    NOT NULL,
    time_ms       INTEGER NOT NULL DEFAULT 0,
    memory_kb     INTEGER NOT NULL DEFAULT 0,
    points        INTEGER NOT NULL DEFAULT 0,
    message       TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (submission_id, idx)
);
