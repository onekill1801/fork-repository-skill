#!/usr/bin/env python3
"""Database probe for the stack-verify toolkit (PostgreSQL + MySQL/MariaDB).

Runs a SQL query by wrapping the native CLI client (psql / mysql) via subprocess
— no pip drivers (stdlib only). Asserts on the result: row count, first value,
emptiness, or substring containment.

Connection (via .env / env, see config.py); flags override env:
    DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME      (generic, used by both)
  PostgreSQL also accepts:  PG_URL  (a full libpq URL: postgresql://...)
  Engine default port: postgres=5432, mysql=3306.

Requires the matching CLI on PATH: `psql` for postgres, `mysql` for mysql.

Usage:
    python probe_db.py query --engine postgres \
        --sql "select status from users where id=42" \
        --expect-rows 1 --expect-value ACTIVE
    python probe_db.py query --engine mysql --sql "select count(*) from orders" \
        --expect-value 0 --host db --user app --name shop
    python probe_db.py query --engine postgres --sql "..." --dry-run

Output: a single JSON object.
    {"passed": bool, "engine": "...", "rows": int, "first_value": "...",
     "sample": [[...]], "checks": [...], "command": "<masked>"}
"""

import argparse
import os
import subprocess
import sys

import config
import project_config
import probe_common as pc

DEFAULT_PORT = {"postgres": "5432", "mysql": "3306"}
_MASK = "***"


def _conn(args) -> dict:
    return {
        "host": args.host or config.get("DB_HOST", "localhost"),
        "port": args.port or config.get("DB_PORT") or DEFAULT_PORT[args.engine],
        "user": args.user or config.get("DB_USER"),
        "password": args.password or config.get("DB_PASSWORD"),
        "name": args.name or config.get("DB_NAME"),
    }


def _build(args, c) -> tuple[list, dict, str]:
    """Return (argv, extra_env, masked_command_string)."""
    if args.engine == "postgres":
        env = dict(os.environ)
        pg_url = config.get("PG_URL")
        if pg_url:
            argv = ["psql", pg_url, "-tAF", "|", "-c", args.sql]
            masked = f"psql {pg_url.split('@')[-1] if '@' in pg_url else pg_url} -tAF '|' -c '<sql>'"
        else:
            env.update({
                "PGHOST": c["host"], "PGPORT": str(c["port"]),
                "PGUSER": c["user"] or "", "PGPASSWORD": c["password"] or "",
                "PGDATABASE": c["name"] or "",
            })
            argv = ["psql", "-tAF", "|", "-c", args.sql]
            masked = f"PGPASSWORD={_MASK} psql -h {c['host']} -p {c['port']} -U {c['user']} -d {c['name']} -tAF '|' -c '<sql>'"
        return argv, env, masked

    # mysql / mariadb — password via MYSQL_PWD env (keeps it off the cmdline)
    env = dict(os.environ)
    if c["password"]:
        env["MYSQL_PWD"] = c["password"]
    argv = ["mysql", "-h", c["host"], "-P", str(c["port"]), "-u", c["user"] or "",
            "-N", "-B", "-e", args.sql]
    if c["name"]:
        argv.append(c["name"])
    masked = f"MYSQL_PWD={_MASK} mysql -h {c['host']} -P {c['port']} -u {c['user']} -N -B -e '<sql>' {c['name'] or ''}".strip()
    return argv, env, masked


_READ_ONLY_SQL = ("select", "show", "explain", "desc", "describe", "values", "with")


def _is_write_sql(sql: str) -> bool:
    """Conservative: anything not clearly a read statement is treated as a write."""
    first = sql.strip().lstrip("(").split(None, 1)[0].lower() if sql.strip() else ""
    return first not in _READ_ONLY_SQL


def _parse_rows(stdout: str, engine: str) -> list:
    sep = "|" if engine == "postgres" else "\t"
    rows = []
    for line in stdout.splitlines():
        if line == "":
            continue
        rows.append(line.split(sep))
    return rows


def cmd_query(args) -> dict:
    c = _conn(args)
    argv, env, masked = _build(args, c)

    if args.dry_run:
        return {"dry_run": True, "engine": args.engine, "command": masked, "sql": args.sql}

    try:
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=args.timeout)
    except FileNotFoundError:
        cli = "psql" if args.engine == "postgres" else "mysql"
        return {"error": True, "passed": False, "message": f"'{cli}' not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"error": True, "passed": False, "message": f"query timed out after {args.timeout}s"}

    if proc.returncode != 0:
        return {"error": True, "passed": False, "engine": args.engine,
                "command": masked, "message": (proc.stderr or proc.stdout)[-1000:]}

    rows = _parse_rows(proc.stdout, args.engine)
    first_value = rows[0][0] if rows and rows[0] else None

    checks = []
    if args.expect_rows is not None:
        checks.append({"check": "rows", "expected": args.expect_rows,
                       "actual": len(rows), "passed": len(rows) == args.expect_rows})
    if args.expect_empty:
        checks.append({"check": "empty", "actual": len(rows), "passed": len(rows) == 0})
    if args.expect_value is not None:
        ok = pc.match_value(first_value, args.expect_value)
        checks.append({"check": "first_value", "expected": args.expect_value,
                       "actual": first_value, "passed": ok})
    for substr in args.expect_contains or []:
        checks.append({"check": "contains", "expected": substr,
                       "passed": substr in proc.stdout})

    passed = all(ch["passed"] for ch in checks) if checks else True
    return {
        "passed": passed,
        "engine": args.engine,
        "rows": len(rows),
        "first_value": first_value,
        "sample": rows[:5],
        "checks": checks,
        "command": masked,
    }


def _check_db_verdict(current, expect_db, engine, masked) -> dict:
    """Pure verdict for check-db: connected, but is it the EXPECTED (isolated) DB?"""
    result = {"passed": True, "engine": engine, "current_database": current,
              "expected_database": expect_db, "command": masked}
    if expect_db is not None and current != expect_db:
        # We connected, but NOT to the isolated DB — an integration probe here would
        # falsely test the wrong (likely shared) database. Treat as error, not pass.
        result["passed"] = False
        result["error"] = True
        result["message"] = (f"connected to '{current}', expected isolated DB "
                             f"'{expect_db}' — isolation not applied to the probe's config")
    return result


def cmd_check_db(args) -> dict:
    """Pre-flight for integration probes on an isolated env.

    Guards the runtime_isolator false-pass: isolation only RENAMES the DB in config,
    it never creates it. This asserts the DB is reachable AND (with --expect-db) that
    we are actually on the isolated database, returning {error:true} otherwise so a
    missing/unprovisioned/wrong DB never counts as a passing probe.
    """
    c = _conn(args)
    ident_sql = "select current_database()" if args.engine == "postgres" else "select database()"
    probe_args = argparse.Namespace(
        engine=args.engine, sql=ident_sql, host=args.host, port=args.port,
        user=args.user, password=args.password, name=args.name,
        dry_run=args.dry_run, timeout=args.timeout)
    argv, env, masked = _build(probe_args, c)
    if args.dry_run:
        return {"dry_run": True, "engine": args.engine, "command": masked,
                "expected_database": args.expect_db}
    try:
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=args.timeout)
    except FileNotFoundError:
        cli = "psql" if args.engine == "postgres" else "mysql"
        return {"error": True, "passed": False, "message": f"'{cli}' not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"error": True, "passed": False,
                "message": f"connectivity check timed out after {args.timeout}s"}
    if proc.returncode != 0:
        return {"error": True, "passed": False, "engine": args.engine, "command": masked,
                "message": ("cannot connect to database (does it exist / is the isolated DB "
                            "provisioned?): " + (proc.stderr or proc.stdout)[-500:])}
    rows = _parse_rows(proc.stdout, args.engine)
    current = rows[0][0] if rows and rows[0] else None
    return _check_db_verdict(current, args.expect_db, args.engine, masked)


def main() -> int:
    parser = argparse.ArgumentParser(description="DB probe (psql/mysql CLI wrapper)")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("query")
    p.add_argument("--engine", required=True, choices=["postgres", "mysql"])
    p.add_argument("--sql", required=True)
    p.add_argument("--host")
    p.add_argument("--port")
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--name", help="database name")
    p.add_argument("--expect-rows", type=int)
    p.add_argument("--expect-empty", action="store_true")
    p.add_argument("--expect-value", help="match the first cell of the first row ('*'=any)")
    p.add_argument("--expect-contains", action="append")
    p.add_argument("--dry-run", action="store_true", help="print resolved command without running")
    p.add_argument("--project", help="load this project's stack config from ./work/projects.json")
    p.add_argument("--env", help="environment (local|dev|uat|sandbox|prod); default from project")
    p.add_argument("--allow-prod", action="store_true", help="permit a write query against a protected env")
    p.add_argument("--timeout", type=int, default=60)

    c = sub.add_parser("check-db", help="assert the (isolated) DB is reachable and is the expected one")
    c.add_argument("--engine", required=True, choices=["postgres", "mysql"])
    c.add_argument("--expect-db", help="isolated DB name that MUST be the connected one "
                                       "(e.g. etask_task_123); mismatch -> error, not pass")
    c.add_argument("--host")
    c.add_argument("--port")
    c.add_argument("--user")
    c.add_argument("--password")
    c.add_argument("--name", help="database name to connect to")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--project", help="load this project's stack config from ./work/projects.json")
    c.add_argument("--env", help="environment (local|dev|uat|sandbox|prod); default from project")
    c.add_argument("--timeout", type=int, default=60)

    args = parser.parse_args()
    if args.action == "check-db":
        err = project_config.apply_args(args, mutating=False)
        if err:
            return pc.emit(err)
        return pc.emit(cmd_check_db(args))
    err = project_config.apply_args(args, mutating=_is_write_sql(args.sql))
    if err:
        return pc.emit(err)
    return pc.emit(cmd_query(args))


if __name__ == "__main__":
    sys.exit(main())
