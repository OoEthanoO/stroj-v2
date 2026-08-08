"""Command line entry point: ``python -m stroj <command>``."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import auth, config, db, seed as seed_module
from .judge import languages, sandbox


def _init() -> None:
    config.ensure_dirs()
    db.init_db()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    _init()
    uvicorn.run(
        "stroj.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    _init()
    result = seed_module.seed(with_contest=not args.no_contest)
    print(f"problems: {', '.join(result['problems'])}")
    print(f"contest:  {result['contest'] or '(already present)'}")
    return 0


def cmd_adduser(args: argparse.Namespace) -> int:
    _init()
    password = args.password or getpass.getpass("password: ")
    try:
        auth.create_user(args.username, password, role=args.role)
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created {args.role} {args.username!r}")
    return 0


def cmd_passwd(args: argparse.Namespace) -> int:
    _init()
    password = args.password or getpass.getpass("new password: ")
    try:
        auth.validate_credentials(args.username, password)
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    changed = db.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?",
        (auth.hash_password(password), args.username),
    ).rowcount
    if not changed:
        print(f"error: no user named {args.username!r}", file=sys.stderr)
        return 1
    db.execute(
        "DELETE FROM sessions WHERE user_id ="
        " (SELECT id FROM users WHERE username = ?)",
        (args.username,),
    )
    print(f"password updated for {args.username!r}; existing sessions were revoked")
    return 0


def cmd_rejudge(args: argparse.Namespace) -> int:
    from .judge.runner import PENDING

    _init()
    if args.problem:
        problem = db.one("SELECT id FROM problems WHERE slug = ?", (args.problem,))
        if problem is None:
            print(f"error: no problem {args.problem!r}", file=sys.stderr)
            return 1
        count = db.execute(
            "UPDATE submissions SET verdict = ? WHERE problem_id = ?",
            (PENDING, problem["id"]),
        ).rowcount
    else:
        count = db.execute("UPDATE submissions SET verdict = ?", (PENDING,)).rowcount
    print(f"requeued {count} submission(s)")

    if args.now:
        from .judge import worker

        print(f"judged {worker.drain()} submission(s)")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Snapshot the database and test data into a single tarball."""
    import sqlite3
    import tarfile
    import tempfile
    from datetime import datetime, timezone

    _init()
    destination = Path(args.into)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = destination / f"stroj-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / "stroj.db"
        # sqlite's own backup API takes a consistent copy of a live database;
        # copying the file while the judge is writing would not.
        source = db.connect()
        with sqlite3.connect(snapshot) as target:
            source.backup(target)

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(snapshot, arcname="stroj.db")
            if config.PROBLEM_DIR.exists():
                tar.add(config.PROBLEM_DIR, arcname="problems")

    size_mb = archive_path.stat().st_size / 1e6
    print(f"wrote {archive_path} ({size_mb:.1f} MB)")

    if args.keep > 0:
        archives = sorted(destination.glob("stroj-*.tar.gz"))
        for stale in archives[: max(0, len(archives) - args.keep)]:
            stale.unlink()
            print(f"pruned {stale.name}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"data directory : {config.DATA_DIR}")
    print(f"judge workers  : {config.JUDGE_WORKERS}")
    target = sandbox.privilege_drop_target()
    if target is None:
        print("privilege sep  : NONE — submissions run as the judge itself")
        print("  ! They can read and write everything the judge can, including")
        print("    the database. Run the container as root with a")
        print(f"    {sandbox.RUNNER_USER!r} account so privileges can be dropped.")
    else:
        print(f"privilege sep  : submissions run as uid {target[0]} ({sandbox.RUNNER_USER})")

    mode = sandbox.isolation_mode() if config.USE_SANDBOX else "off (STROJ_SANDBOX=0)"
    print(f"isolation      : {mode}")
    if mode == "none":
        print("  ! No isolation is usable here. Submissions run with rlimits and")
        print("    RSS monitoring only — no network or filesystem confinement.")
    elif mode == "unshare-net":
        print("  ! Network namespaces only; there is no filesystem confinement.")
        print("    Run the judge inside a disposable container.")
    print("languages:")
    ok = True
    for lang_id, lang in languages.LANGUAGES.items():
        installed = languages.is_available(lang_id)
        ok &= installed
        mark = "ok  " if installed else "MISS"
        print(f"  [{mark}] {lang_id:<8} {languages.version_string(lang_id)}")
    return 0 if ok else 1


def cmd_calibrate(args: argparse.Namespace) -> int:
    from . import calibrate as calibrate_module

    print(
        f"Running a CPU-bound workload through the sandbox for ~{args.seconds}s.\n"
        "Leave the machine otherwise idle.\n"
    )

    # Only animate on a terminal; redirected output would collect one line per
    # repetition and bury the report.
    interactive = sys.stdout.isatty()

    def progress(index: int, sample) -> None:
        if interactive:
            print(f"\r  rep {index:>4}  {sample.wall_ms:>5} ms", end="", flush=True)

    try:
        report = calibrate_module.calibrate(seconds=args.seconds, progress=progress)
    except RuntimeError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    if interactive:
        print("\r" + " " * 40 + "\r", end="")
    print(calibrate_module.format_report(report))
    return 0 if report.rating[0] != "UNRELIABLE" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stroj", description="stroj online judge")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    seed = sub.add_parser("seed", help="insert the sample problems and contest")
    seed.add_argument("--no-contest", action="store_true")
    seed.set_defaults(func=cmd_seed)

    adduser = sub.add_parser("adduser", help="create an account")
    adduser.add_argument("username")
    adduser.add_argument("--password")
    adduser.add_argument("--role", choices=("user", "admin"), default="user")
    adduser.set_defaults(func=cmd_adduser)

    passwd = sub.add_parser("passwd", help="change an account's password")
    passwd.add_argument("username")
    passwd.add_argument("--password")
    passwd.set_defaults(func=cmd_passwd)

    rejudge = sub.add_parser("rejudge", help="requeue submissions")
    rejudge.add_argument("--problem", help="slug; omit to rejudge everything")
    rejudge.add_argument(
        "--now", action="store_true", help="judge them here instead of leaving them queued"
    )
    rejudge.set_defaults(func=cmd_rejudge)

    doctor = sub.add_parser("doctor", help="report toolchain and sandbox status")
    doctor.set_defaults(func=cmd_doctor)

    calibrate = sub.add_parser(
        "calibrate", help="measure whether this host's timings are consistent"
    )
    calibrate.add_argument(
        "--seconds", type=float, default=90.0,
        help="how long to hold the machine under load (default: 90)",
    )
    calibrate.set_defaults(func=cmd_calibrate)

    backup = sub.add_parser("backup", help="snapshot the database and test data")
    backup.add_argument("--into", default="/data/backups", help="destination directory")
    backup.add_argument(
        "--keep", type=int, default=14,
        help="how many archives to retain; 0 keeps everything (default: 14)",
    )
    backup.set_defaults(func=cmd_backup)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
