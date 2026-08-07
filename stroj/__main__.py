"""Command line entry point: ``python -m stroj <command>``."""

from __future__ import annotations

import argparse
import getpass
import sys

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


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"data directory : {config.DATA_DIR}")
    print(f"judge workers  : {config.JUDGE_WORKERS}")
    available = sandbox.sandbox_available()
    wanted = config.USE_SANDBOX
    state = "on" if (wanted and available) else ("unavailable" if wanted else "off")
    print(f"sandbox-exec   : {state}")
    if wanted and not available:
        print("  ! sandbox-exec was requested but is not usable on this platform;")
        print("    submissions will run with rlimits only.")
    print("languages:")
    ok = True
    for lang_id, lang in languages.LANGUAGES.items():
        installed = languages.is_available(lang_id)
        ok &= installed
        mark = "ok  " if installed else "MISS"
        print(f"  [{mark}] {lang_id:<8} {languages.version_string(lang_id)}")
    return 0 if ok else 1


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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
