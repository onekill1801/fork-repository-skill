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

## Autonomy: checkpoint mode (default)

The agent runs automatically but **STOPS for human approval at three checkpoints**:

1. `after_plan` — after the plan is written, before any code is changed.
2. `before_mr` — after tests pass, before creating the merge request.
3. `before_notify` — before any `notifier.py` call (a real person will see it).

At each checkpoint: present the artifact, ask for approval, record it with
`run_log.py checkpoint <run_id> <name> approved`, then continue. Do NOT pass a
checkpoint without explicit user approval. (This is the mode the user selected; other
autonomy levels move or remove these stops.)

## Tools

All tools live in `../dev-automation/tools/` (shared). Read tool source before first use.

| Tool | Role in pipeline |
|------|------------------|
| `azure_devops.py` / `search.py` (etask) | Intake: read the task |
| `test_runner.py` | **Test gate (unit/build)** — `run --project <p> --kind test` → JSON `passed` |
| `probe_api.py` `probe_db.py` `probe_redis.py` `probe_kafka.py` | **Integration probes** — assert real API/DB/Redis/Kafka state |
| `flow_check.py` | **E2E gate** — run a JSON scenario across components (`cookbook/stack-verify.md`) |
| `jenkins.py` | **CI gate (optional)** — trigger a Jenkins build and wait for SUCCESS |
| `run_log.py` | **State machine** — resume + audit trail in `temp/runs/<run_id>.json` |
| `gitlab_api.py` | branch + MR |
| `notifier.py` | progress notifications (checkpoint `before_notify` guards these) |
| `../fork-terminal/tools/agent_parser.py` | **Parse dữ liệu Agent↔Agent** — bóc `<plan>` / `<target_files>` / `<error_context>` bằng regex (stdlib) |

> New tools added for this pipeline: `test_runner.py`, `run_log.py`, and the
> stack-verify set (`probe_*.py`, `flow_check.py`, `jenkins.py`). See
> `cookbook/pipeline.md` for the command sequence, `cookbook/stack-verify.md` for
> integration/e2e testing, and `cookbook/intake.md` for Azure DevOps / eTask intake.

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

1. **Intake.** Resolve the task. Pick a `run_id` (e.g. `<project>-<task_id>`).
   `run_log.py init <run_id> --task <id> --project <p> --type <bugfix|feature> --title "..."`.
   Resolve project from `./work/projects.json`; if absent, ask the user for the working dir.
2. **Plan.** `stage plan active`. Read the relevant code, write a concrete plan (files to
   touch, approach, test strategy). `stage plan done`.
   **✋ Checkpoint `after_plan`** — show the plan, get approval, record it.
3. **Implement.** `stage implement active`. Create the branch
   (`gitlab_api.py create-branch`), write code in the project's `clone_dir` following
   `dev-automation/cookbook/java-standards.md`, add/adjust tests. `stage implement done`.
4. **Test.** `stage test active`. Run gates in order, stop at first failure:
   - **Unit/build (required):** `test_runner.py run --project <p> --kind test`.
   - **Integration/e2e (if the task touches DB/API/Kafka/Redis):** `flow_check.py --file <scenario>`
     or individual `probe_*.py` for the changed component. See `cookbook/stack-verify.md`.
   - **CI (optional):** `jenkins.py build --job <job> --wait` to require a green Jenkins run.
   - All green → `stage test done`, continue. Any `passed: false` → read the failure, fix the
     cause, re-run. After `MAX_TEST_RETRIES` failures → `stage test failed`, STOP, report.
     Note: `{"error": true}` from a probe means "could not run" (service down / config missing),
     not "passed" — treat it as not-yet-verified, never deliver on it.
5. **✋ Checkpoint `before_mr`** — show the green test result + diff summary, get approval.
   Then `gitlab_api.py create-mr ...`, store `run_log.py field <run_id> mr_url <url>`.
   Self-review the MR using `dev-automation` Workflow 1.
6. **✋ Checkpoint `before_notify`** — confirm, then `notifier.py mr-created <task_id> <url>`
   and update task state (`azure_devops.py state <id> Resolved`). `stage deliver done`.

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
