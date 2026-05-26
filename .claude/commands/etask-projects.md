# eTask: Browse Projects & Sprints

List projects, sprints, workspaces, and boards in eTask.

## Arguments

`$ARGUMENTS` — optional filter. Examples:
- `/etask-projects` → list all my projects
- `/etask-projects Payment` → filter projects by name "Payment"

## Steps

1. Read `@.claude/skills/etask-automation/SKILL.md`
2. Read `@.claude/skills/etask-automation/cookbook/project-sprint-navigation.md`
3. Read `@.claude/skills/etask-automation/tools/projects.py`
4. Run `python3 projects.py my-projects` (with `--filter "$ARGUMENTS"` if provided)
5. Display projects in a table: ID | Name | Description
6. Ask: "Want to see sprints or lists for any project?"
7. If yes:
   - Sprints: `python3 projects.py sprints <project_id>`
   - Lists: ask for workspace ID → `python3 projects.py lists <workspace_id>`
8. Offer to drill further: tasks in a sprint, tasks in a list
