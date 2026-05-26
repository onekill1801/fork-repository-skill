# eTask: Create Task

Create a new task in eTask.

## Arguments

`$ARGUMENTS` — `<task name> [list_id]`. Examples:
- `/etask-create Fix login timeout` → prompts for list ID
- `/etask-create "Deploy v2.1" list-abc123` → creates immediately

## Steps

1. Read `@.claude/skills/etask-automation/SKILL.md`
2. Read `@.claude/skills/etask-automation/cookbook/task-crud.md`
3. Read `@.claude/skills/etask-automation/tools/tasks.py`
4. Parse `$ARGUMENTS` for task name and optional list ID
5. If list ID is missing:
   - Run `python3 projects.py my-lists` (cd to `tools/` first)
   - Show available lists; ask user to pick one
6. Ask user for optional fields: priority, due date, description
7. Run `python3 tasks.py create --name "..." --list <list_id> [optional flags]`
8. Show created task ID and name; offer to add checklist items or comments
