#!/usr/bin/env python3
"""Pipeline run-log / state machine for the auto-dev orchestrator.

A long pipeline (Plan -> Implement -> Test -> Deliver) may span many turns and
human checkpoints. This tool persists the run state to a JSON file so the
orchestrator can resume after an interrupt and so a human can inspect progress.

State lives in:  <repo>/temp/runs/<run_id>.json   (temp/ is gitignored)

Stages are fixed: plan, implement, test, deliver.
Stage status:    pending | active | done | failed | skipped
Checkpoints:     after_plan, before_mr, before_notify  (recorded as approved/pending)

Zero external dependencies — Python stdlib only.

Usage:
    python run_log.py init <run_id> --task 12345 --project etask --type bugfix --title "..."
    python run_log.py stage <run_id> plan active
    python run_log.py stage <run_id> test failed
    python run_log.py checkpoint <run_id> after_plan approved
    python run_log.py note <run_id> "branch bugfix/12345-... created"
    python run_log.py field <run_id> branch bugfix/12345-foo
    python run_log.py get <run_id>
    python run_log.py list [--open]

Output: a single JSON object/array on stdout.
"""

import argparse
import json
import os
import sys

# Cross-platform: force UTF-8 stdout so JSON output doesn't crash on a Windows
# cp1252/cp437 console when notes/titles contain non-ASCII. No-op elsewhere.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

STAGES = ["plan", "implement", "test", "deliver"]
STAGE_STATUS = ["pending", "active", "done", "failed", "skipped"]
CHECKPOINTS = ["after_plan", "before_mr", "before_notify"]
CHECKPOINT_STATUS = ["pending", "approved", "rejected"]


def _repo_root() -> str:
    """Walk up from this file to find the repo root (where temp/ lives)."""
    here = os.path.dirname(os.path.abspath(__file__))
    search = here
    for _ in range(6):
        if os.path.isdir(os.path.join(search, ".git")) or os.path.isfile(os.path.join(search, "CLAUDE.md")):
            return search
        search = os.path.dirname(search)
    return here


def _runs_dir() -> str:
    d = os.path.join(_repo_root(), "temp", "runs")
    os.makedirs(d, exist_ok=True)
    return d


def _path(run_id: str) -> str:
    safe = "".join(c for c in run_id if c.isalnum() or c in "-_.")
    return os.path.join(_runs_dir(), f"{safe}.json")


def _read(run_id: str) -> dict:
    p = _path(run_id)
    if not os.path.isfile(p):
        raise ValueError(f"run not found: {run_id}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(state: dict) -> None:
    with open(_path(state["run_id"]), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cmd_init(args) -> dict:
    state = {
        "run_id": args.run_id,
        "task_id": args.task,
        "project": args.project,
        "type": args.type,
        "title": args.title,
        "branch": None,
        "mr_url": None,
        "stages": {s: "pending" for s in STAGES},
        "checkpoints": {c: "pending" for c in CHECKPOINTS},
        "notes": [],
        "test_attempts": 0,
    }
    _write(state)
    return state


def cmd_stage(args) -> dict:
    state = _read(args.run_id)
    if args.stage not in STAGES:
        raise ValueError(f"unknown stage '{args.stage}', expected one of {STAGES}")
    if args.status not in STAGE_STATUS:
        raise ValueError(f"unknown status '{args.status}', expected one of {STAGE_STATUS}")
    state["stages"][args.stage] = args.status
    if args.stage == "test" and args.status in ("done", "failed"):
        state["test_attempts"] = state.get("test_attempts", 0) + 1
    _write(state)
    return state


def cmd_checkpoint(args) -> dict:
    state = _read(args.run_id)
    if args.name not in CHECKPOINTS:
        raise ValueError(f"unknown checkpoint '{args.name}', expected one of {CHECKPOINTS}")
    if args.status not in CHECKPOINT_STATUS:
        raise ValueError(f"unknown status '{args.status}', expected one of {CHECKPOINT_STATUS}")
    state["checkpoints"][args.name] = args.status
    _write(state)
    return state


def cmd_note(args) -> dict:
    state = _read(args.run_id)
    state["notes"].append(args.text)
    _write(state)
    return state


def cmd_field(args) -> dict:
    state = _read(args.run_id)
    state[args.key] = args.value
    _write(state)
    return state


def cmd_get(args) -> dict:
    return _read(args.run_id)


def cmd_list(args) -> list:
    out = []
    for fn in sorted(os.listdir(_runs_dir())):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(_runs_dir(), fn), encoding="utf-8") as f:
            st = json.load(f)
        is_open = st.get("stages", {}).get("deliver") != "done"
        if args.open and not is_open:
            continue
        out.append({
            "run_id": st.get("run_id"),
            "task_id": st.get("task_id"),
            "project": st.get("project"),
            "type": st.get("type"),
            "stages": st.get("stages"),
            "open": is_open,
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="auto-dev pipeline run-log / state machine")
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("init")
    p.add_argument("run_id")
    p.add_argument("--task", default=None)
    p.add_argument("--project", default=None)
    p.add_argument("--type", default=None, help="bugfix | feature | ...")
    p.add_argument("--title", default=None)

    p = sub.add_parser("stage")
    p.add_argument("run_id")
    p.add_argument("stage")
    p.add_argument("status")

    p = sub.add_parser("checkpoint")
    p.add_argument("run_id")
    p.add_argument("name")
    p.add_argument("status")

    p = sub.add_parser("note")
    p.add_argument("run_id")
    p.add_argument("text")

    p = sub.add_parser("field")
    p.add_argument("run_id")
    p.add_argument("key")
    p.add_argument("value")

    p = sub.add_parser("get")
    p.add_argument("run_id")

    p = sub.add_parser("list")
    p.add_argument("--open", action="store_true", help="only runs whose deliver stage is not done")

    args = parser.parse_args()
    handlers = {
        "init": cmd_init, "stage": cmd_stage, "checkpoint": cmd_checkpoint,
        "note": cmd_note, "field": cmd_field, "get": cmd_get, "list": cmd_list,
    }
    try:
        out = handlers[args.action](args)
    except ValueError as e:
        out = {"error": True, "message": str(e)}

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if isinstance(out, dict) and out.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
