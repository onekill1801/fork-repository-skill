---
name: Auto Dev Orchestrator
description: >
  End-to-end coding pipeline: take a request (Azure DevOps / eTask task, or a direct
  description) and drive it through Plan -> Implement -> Test -> Deliver with human
  checkpoints. Gates the merge request on a green test run. Trigger phrases:
  "auto dev", "tự động làm task", "chạy pipeline", "auto-dev task", "làm task tự động",
  "run the pipeline", "automate this task end to end".
---

# Auto Dev Orchestrator

Master skill that chains the existing `dev-automation` and `etask-automation` tools into
one pipeline: **Intake → Plan → Implement → Test → Deliver**. It does not replace those
skills — it sequences them and adds the two missing pieces: a **test gate** and a
**resumable run-log**.

## Autonomy: Hybrid by complexity

Triage at Intake classifies each run into a **tier** + **mode** (see `triage.py`):

| tier | mode | checkpoints | gate behaviour |
|------|------|-------------|----------------|
| `trivial` | `auto` | skipped | a failed gate **BLOCKS** the transition (no human in the loop) |
| `standard` / `complex` | `checkpoint` | the three below | a failed gate **INFORMS** the human approver (does not auto-block) |

The three checkpoints (checkpoint mode):
1. `after_plan` — after the plan, before any code change.
2. `before_mr` — after tests pass + review, before the merge request.
3. `before_notify` — before any `notifier.py` call (a real person will see it).

At each checkpoint: present the artifact, ask for approval, record it with
`run_log.py checkpoint <run_id> <name> approved`, then continue. Never pass a checkpoint
without explicit approval.

### Evidence-gated transitions (core principle)

A stage is advanced with `run_log.py advance <run_id> <stage>` — which only marks it `done`
when the **evidence** it requires has been recorded (`run_log.py record-gate ...`). The gate
guards the state machine; it is not advisory narration. **Do not use `stage <id> <s> done`** to
self-declare progress in an automated run (that legacy command trusts the caller and exists only
for manual unstick / backward compatibility). `advance` reads the run's `mode` to decide
block-vs-inform — that single fork is the entire difference between the autonomy levels.

Required gates: `plan→clarity`, `implement→grounding`, `test→test+lint` (lint mandatory, waivable),
`deliver→review+ac`. `build`/`integration` are advisory (reported, never block alone).

## Tools

Pipeline tools live in `tools/` (this skill) and `../dev-automation/tools/` (shared). Read tool
source before first use.

| Tool | Role in pipeline |
|------|------------------|
| `tools/clarify.py` | **Intake gate `clarity`** — surface ambiguities (scope/io/acceptance/edge/non-functional) as blocking-vs-assumed questions; `brief` folds answers into `temp/runs/<id>_brief.md`. Heuristic-first, optional `--backend`. Required gate on stage `plan` |
| `tools/triage.py` | **Intake: triage** — classify tier (trivial/standard/complex) + mode (auto/checkpoint); heuristic-first, optional `--backend` agent |
| `tools/debate_engine.py` | **Plan: Agent Debate** — Dev/Architect/Moderator via subscription CLI agent (claude/cursor/agy, headless; no API key) → `temp/runs/<task_id>_plan.xml`. Loops critique↔rebuttal up to `--rounds` (default 2; Architect `<verdict>APPROVE</verdict>` converges early). Skipped for `tier=trivial` |
| `tools/agent_runner.py` | **Shared headless-agent primitive** — `run_turn()`; reused by triage / grounding / review_gate |
| `tools/grounding.py` | **Implement gate `grounding`** — gather target files + neighbours + stack into an artifact before coding |
| `tools/review_gate.py` | **Deliver gate `review`** — review the real `git diff` pre-MR, JSON verdict, posts NOTHING |
| `azure_devops.py` / `search.py` (etask) | Intake: read the task + acceptance criteria |
| `test_runner.py` | **Test gates** — `run --project <p> --kind test\|lint\|build` → JSON `passed` |
| `probe_*.py` (`probe_db.py check-db` guards the isolated DB) `flow_check.py` `jenkins.py` | **Integration / e2e / CI** — see `cookbook/stack-verify.md` |
| `run_log.py` | **Evidence-gated state machine** — `record-gate` / `advance` / AC ledger; resume + audit in `temp/runs/<run_id>.json` |
| `gitlab_api.py` | branch + MR |
| `notifier.py` | progress notifications (checkpoint `before_notify` guards these) |
| `../fork-terminal/tools/agent_parser.py` | **Parse Agent↔Agent data** — `<target_files>` / `<error_context>` / `<review>` via stdlib regex |

> New for the evidence-gated pipeline: `triage.py`, `agent_runner.py`, `grounding.py`,
> `review_gate.py`, the `run_log.py` gate/AC commands, and `probe_db.py check-db`. See
> `cookbook/pipeline.md` for the command sequence, `cookbook/intake.md` for triage + intake,
> `cookbook/stack-verify.md` for integration/e2e. Unit tests for the framework live in `tests/`
> (`python -m unittest discover -s tests`).

## The pipeline

```
Intake ──> Plan ──> [✋ after_plan] ──> Implement ──> Test ──> (green?) ──> [✋ before_mr]
                                            ▲             │ no
                                            └─ fix ───────┘  (retry, cap = MAX_TEST_RETRIES)
   ──> Deliver: create MR ──> self-review ──> [✋ before_notify] ──> notify ──> update task state
```

`MAX_TEST_RETRIES = 3`. If tests are still red after the cap, STOP, set the test stage
to `failed`, post the failure summary, and hand back to the human — do not deliver.

### Step sequence (checkpoint mode)

1. **Intake + Triage.** Resolve the task. `triage.py classify ...` → tier/mode. Pick a `run_id`,
   then `run_log.py init <run_id> ... --tier <t> --mode <m>`. Resolve project from
   `./work/projects.json`; if absent, ask.
1b. **Clarify (gate `clarity`).** `clarify.py analyze ...` → questions. Ask the **blocking** ones
   (checkpoint mode); auto mode does NOT ask — `needs_clarification` fails the gate and blocks the
   debate. `clarify.py brief --answers-file ... --out temp/runs/<id>_brief.md` folds answers; use
   the brief as the debate `--desc` and `ac-add` the `acceptance_seeds`. `record-gate <RID> clarity
   --verdict pass|fail`.
2. **Plan.** `stage plan active`. For `standard|complex`: run `debate_engine.py run ...` (subscription
   CLI agent, no API key; loops critique↔rebuttal up to `--rounds`, default 2 — bump to `--rounds 3`
   for `complex`) → `<final_specification>` at `temp/runs/<task_id>_plan.xml`. For
   `trivial`: skip the debate, write a lean spec (keep `<target_files>`). `stage plan done`.
   **✋ Checkpoint `after_plan`** (checkpoint mode) — present the spec as clean Markdown, get approval.
3. **Implement.** `stage implement active`. Branch off the env's target branch. **Run `grounding.py`
   and `record-gate <RID> grounding`** before editing. Write code in `clone_dir` (read the grounding
   artifact, follow `java-standards.md`), add/adjust tests. `advance <RID> implement`.
4. **Test.** `stage test active`. Run + `record-gate` each: `test` (required), `lint` (required,
   `--verdict waived` if no linter), `build` (advisory). Integration/e2e if the task touches
   DB/API/Kafka/Redis (guard the isolated DB with `probe_db.py check-db`). A probe `{"error":true}`
   = "could not run", never a pass. Fix-and-retry the `test` gate up to `MAX_TEST_RETRIES`; still
   red → STOP, report, no MR. Then `advance <RID> test`.
5. **Deliver.** `review_gate.py run ...` on the real diff → `record-gate <RID> review` (no posting;
   fix blockers before MR). `ac-map` each criterion to evidence. **✋ Checkpoint `before_mr`** —
   show test+review+AC, get approval, then `gitlab_api.py create-mr ...`, store `mr_url`.
6. **✋ Checkpoint `before_notify`** — confirm, then `notifier.py mr-created <task_id> <url>`,
   update task state, and `advance <RID> deliver` (done iff `review`+`ac` gates pass).

## Resuming a run

`run_log.py get <run_id>` shows where it stopped. `run_log.py list --open` lists unfinished
runs. Re-enter at the first stage whose status is not `done`.

## Routing

| User says... | Action |
|---|---|
| "auto-dev task #123" / "làm task 123 tự động" | Run the full pipeline for task 123 |
| "resume run etask-123" | `run_log.py get etask-123` → continue from first non-done stage |
| "show pipeline runs" | `run_log.py list --open` |

Slash command: `/auto-dev <task_id|description>`.

## Giao thức giao tiếp Agent (BẮT BUỘC)

Mọi dữ liệu trung gian Agent↔Agent và Agent↔Tool (Plan, Target Files, Error Log, lời thoại
tranh luận) dùng **thẻ HTML/XML nghiêm ngặt**, KHÔNG Markdown — chi tiết + bộ thẻ chuẩn ở
`prompts/SYSTEM_PROMPT.md`. Bóc tách bằng `../fork-terminal/tools/agent_parser.py` (không cắt
chuỗi theo dòng). **Đầu ra cuối cho người dùng vẫn là Markdown sạch** (bỏ thẻ).

## Guardrails (inherits CLAUDE.md)

1. Honor the three checkpoints. Never skip `before_notify` — a real person sees notifications.
2. Never deliver on red tests. The test gate is mandatory.
2b. Run integration/e2e tests against **non-prod** (dev/uat/sandbox) via `--project <P> --env <env>`.
    Writes to a protected env (prod) are refused unless `--allow-prod` — never pass that flag
    autonomously; if a task genuinely needs prod, ask the user to confirm first.
3. Always `run_log` each stage transition so an interrupted run can resume.
4. Confirm before any destructive op (delete branch, close task) and before final task states.
5. Never hardcode tokens/URLs — config flows through `config.py` / inline env prefix.
