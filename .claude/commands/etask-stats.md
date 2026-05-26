# eTask: Task Statistics & Analytics

Show task statistics and analytics dashboards.

## Arguments

`$ARGUMENTS` — optional scope or focus. Examples:
- `/etask-stats` → show overall statistics
- `/etask-stats org` → show org-wide stats
- `/etask-stats overdue <list_id>` → show overdue tasks in a list
- `/etask-stats trends` → show completion trends (prompts for date range)

## Steps

1. Read `@.claude/skills/etask-automation/SKILL.md`
2. Read `@.claude/skills/etask-automation/cookbook/analytics-reporting.md`
3. Read `@.claude/skills/etask-automation/tools/analytics.py`
4. Run `python3 analytics.py stats` for the high-level overview
5. Branch by `$ARGUMENTS`:
   - empty / "my" → show overall stats + status breakdown (`by-status`)
   - "org" → show org-scoped stats (`by-status --scope org` + `by-org --scope org`)
   - "overdue <list_id>" → run `analytics.py overdue <list_id>`
   - "trends" → ask for start/end dates + period, run `analytics.py trends`
   - "priority" → run `analytics.py by-priority`
   - "unassigned" → run `analytics.py unassigned`
   - "history" → run `analytics.py history`
6. Format output as human-readable summary with key numbers highlighted
7. Offer to drill deeper (e.g., "Want completion trends for last 30 days?")
