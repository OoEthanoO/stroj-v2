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

-- Front-page announcements, written by admins.
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT    NOT NULL UNIQUE,
    title      TEXT    NOT NULL,
    body       TEXT    NOT NULL DEFAULT '',   -- markdown
    author_id  INTEGER          REFERENCES users(id) ON DELETE SET NULL,
    pinned     INTEGER NOT NULL DEFAULT 0,    -- 1 = held at the top of the stream
    published  INTEGER NOT NULL DEFAULT 1,    -- 0 = draft, visible to admins only
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_feed ON posts(pinned DESC, created_at DESC);

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
    subtask     INTEGER NOT NULL DEFAULT 0,   -- 0 = ungrouped or sample
    UNIQUE (problem_id, idx)
);

-- What each subtask is worth, as a percentage of the problem's points.
-- Solving every test in a subtask awards that percentage; a problem with
-- no rows here falls back to the share of individual tests passed.
CREATE TABLE IF NOT EXISTS problem_subtasks (
    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    percent    INTEGER NOT NULL,
    PRIMARY KEY (problem_id, idx)
);

-- Categories an admin assigns to a problem ('graphs', 'dp', ...), used to
-- filter the problem list. Free-form, but stored lowercased so that one
-- spelling of a type is one type.
CREATE TABLE IF NOT EXISTS problem_types (
    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    type       TEXT    NOT NULL,
    PRIMARY KEY (problem_id, type)
);

-- Per-language limits, set by the problem setter from measured runs of the
-- intended solutions. A language with no row here falls back to the base limit
-- scaled by that language's multiplier, which is only ever a guess: the real
-- gap between runtimes is a property of the problem, not of the language.
CREATE TABLE IF NOT EXISTS problem_limits (
    problem_id      INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    language        TEXT    NOT NULL,
    time_limit_ms   INTEGER NOT NULL,
    memory_limit_mb INTEGER NOT NULL,
    PRIMARY KEY (problem_id, language)
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
    -- Share of the problem's points this submission earned, 0-100. Stored
    -- as a percentage rather than an absolute so that re-pricing a problem
    -- updates everyone's standing instead of stranding old submissions.
    earned_percent INTEGER NOT NULL DEFAULT 0,
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
