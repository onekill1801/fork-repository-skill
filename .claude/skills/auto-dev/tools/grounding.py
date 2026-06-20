#!/usr/bin/env python3
"""Codebase grounding — gather the real code an implementer must respect.

Before the Implement stage edits anything, this collects the actual target files
(from the plan spec's <target_files>) plus their neighbours and a stack hint, and
writes a grounding artifact the implementing agent reads. This closes the gap
where the pipeline planned rigorously but then coded blind to repo conventions.

Mechanical-first: gathering needs no tokens. Pass --backend to additionally have an
agent distil the conventions into a short note appended to the artifact.

The result JSON carries `verdict` (pass|fail) so the orchestrator can:
    python grounding.py run --run <RID> --root <clone_dir> > g.json
    python run_log.py record-gate <RID> grounding --verdict pass --json g.json

`fail` means no target file could be located → the plan can't be grounded against
the repo (wrong paths, wrong checkout), which SHOULD block implement in auto mode.

Stdlib only.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_SKILLS, "fork-terminal", "tools"))
sys.path.insert(0, _HERE)
import agent_parser  # noqa: E402
import agent_runner  # noqa: E402

HEAD_LINES = 80
MAX_NEIGHBOURS = 12
STACK_MARKERS = {
    "pom.xml": "maven/java", "build.gradle": "gradle/java",
    "build.gradle.kts": "gradle/kotlin", "package.json": "node",
    "pyproject.toml": "python", "requirements.txt": "python", "go.mod": "go",
}


def _repo_root() -> str:
    search = _HERE
    for _ in range(6):
        if os.path.isdir(os.path.join(search, ".git")) or \
           os.path.isfile(os.path.join(search, "CLAUDE.md")):
            return search
        search = os.path.dirname(search)
    return _HERE


def _plan_path(run_id, explicit):
    if explicit:
        return explicit
    safe = "".join(c for c in str(run_id) if c.isalnum() or c in "-_.") or "debate"
    return os.path.join(_repo_root(), "temp", "runs", f"{safe}_plan.xml")


def _artifact_path(run_id):
    safe = "".join(c for c in str(run_id) if c.isalnum() or c in "-_.") or "run"
    runs = os.path.join(_repo_root(), "temp", "runs")
    os.makedirs(runs, exist_ok=True)
    return os.path.join(runs, f"{safe}_grounding.md")


def _stack_hint(root):
    hits = [label for marker, label in STACK_MARKERS.items()
            if os.path.isfile(os.path.join(root, marker))]
    return ", ".join(sorted(set(hits))) or "unknown"


def _gather_file(root, rel):
    """Return a record for one target file: existence, head excerpt, neighbours."""
    full = os.path.join(root, rel)
    rec = {"path": rel, "exists": os.path.isfile(full)}
    if not rec["exists"]:
        return rec
    with open(full, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    rec["line_count"] = len(lines)
    rec["head"] = "".join(lines[:HEAD_LINES]).rstrip()
    d = os.path.dirname(full)
    try:
        siblings = sorted(n for n in os.listdir(d)
                          if os.path.isfile(os.path.join(d, n)) and n != os.path.basename(full))
    except OSError:
        siblings = []
    rec["neighbours"] = siblings[:MAX_NEIGHBOURS]
    return rec


def _render(run_id, root, stack, records):
    out = [f"# Grounding — run {run_id}", "",
           f"- repo root: `{root}`", f"- stack hint: **{stack}**",
           f"- target files: {len(records)} "
           f"({sum(1 for r in records if r['exists'])} found)", ""]
    for r in records:
        if not r["exists"]:
            out.append(f"## `{r['path']}` — **MISSING** (plan references a path not in repo)")
            out.append("")
            continue
        out.append(f"## `{r['path']}` — {r['line_count']} lines")
        if r["neighbours"]:
            out.append(f"_neighbours_: {', '.join(r['neighbours'])}")
        out.append("")
        out.append("```")
        out.append(r["head"])
        out.append("```")
        out.append("")
    return "\n".join(out)


_AGENT_SYSTEM = (
    "You are grounding an implementer. From the gathered file excerpts, write a SHORT "
    "note (<=10 lines, plain text) on the conventions to follow: naming, layering, error "
    "handling, test placement. No Markdown headers, no fluff, only what an editor must respect."
)


def cmd_run(args) -> dict:
    plan = _plan_path(args.run, args.plan)
    if not os.path.isfile(plan):
        return {"error": True, "message": f"plan spec not found: {plan}"}
    with open(plan, encoding="utf-8") as f:
        spec = f.read()
    targets = agent_parser.extract_list_items(spec, "target_files", "file")
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        return {"error": True, "message": f"--root is not a directory: {root}"}

    stack = _stack_hint(root)
    records = [_gather_file(root, t.strip()) for t in targets if t.strip()]
    found = [r["path"] for r in records if r["exists"]]
    missing = [r["path"] for r in records if not r["exists"]]

    body = _render(args.run, root, stack, records)
    agent_note = None
    if args.backend and found:
        try:
            ctx = body[:12000]
            agent_note = agent_runner.run_turn(
                ctx, system=_AGENT_SYSTEM, backend=args.backend, model=args.model,
                dry_run_text=args.dry_run_text)
            body += "\n## Conventions (agent note)\n\n" + agent_note + "\n"
        except agent_runner.AgentRunError as e:
            body += f"\n## Conventions (agent note)\n\n_unavailable: {e}_\n"

    artifact = _artifact_path(args.run)
    with open(artifact, "w", encoding="utf-8") as f:
        f.write(body)

    # verdict: we could ground iff the plan listed files AND at least one resolves.
    # No targets at all, or none found, means the implementer would be coding blind.
    verdict = "pass" if found else "fail"
    summary = (f"grounded {len(found)}/{len(records)} target file(s); stack={stack}"
               if records else "plan listed no <target_files>")
    return {
        "ok": True,
        "run_id": args.run,
        "plan": plan,
        "artifact": artifact,
        "stack": stack,
        "target_count": len(records),
        "found": found,
        "missing": missing,
        "verdict": verdict,
        "summary": summary,
        "kind": "grounding",
        "agent_note": bool(agent_note),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Gather codebase grounding for the Implement stage.")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("run")
    p.add_argument("--run", required=True, help="run_id (locates temp/runs/<id>_plan.xml + artifact)")
    p.add_argument("--root", required=True, help="clone_dir of the target repo to read files from")
    p.add_argument("--plan", default=None, help="explicit plan xml path (overrides --run lookup)")
    p.add_argument("--backend", default=None, help="optional agent for a conventions note")
    p.add_argument("--model", default=None)
    p.add_argument("--dry-run-text", default=None)

    args = ap.parse_args()
    try:
        out = cmd_run(args)
    except (OSError, ValueError) as e:
        out = {"error": True, "message": str(e)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if isinstance(out, dict) and out.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
