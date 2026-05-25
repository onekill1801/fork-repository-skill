# Code Review Output Template

Use this template to structure code review comments posted on GitLab merge requests.
Fill in each section based on the review findings.

---

## Code Review Summary

**MR:** !<mr_iid> - <title>
**Reviewer:** AI Agent
**Date:** <date>
**Overall Verdict:** <APPROVE | REQUEST_CHANGES | COMMENT>

---

### Overview

<1-2 sentences summarizing what this MR does and the overall code quality>

---

### Critical Issues (must fix before merge)

<If none, write "No critical issues found.">

- [ ] **[`<file_path>`:<line>]** <description>
  - **Why:** <explanation of the risk/impact>
  - **Suggestion:**
    ```java
    // suggested fix
    ```

---

### Major Suggestions (strongly recommended)

<If none, write "No major suggestions.">

- [ ] **[`<file_path>`:<line>]** <description>
  - **Why:** <explanation>
  - **Suggestion:** <how to improve>

---

### Minor / Nitpicks

<If none, write "Code looks clean.">

- [ ] **[`<file_path>`:<line>]** <description>

---

### Standards Compliance

| Category | Status | Notes |
|----------|--------|-------|
| Correctness | pass/fail | |
| Code Quality | pass/fail | |
| Architecture | pass/fail | |
| Security | pass/fail | |
| Performance | pass/fail | |
| Testing | pass/fail | |

---

### Positive Observations

<Highlight good practices, clean patterns, or well-written code>

- ...

---

### Files Reviewed

| File | Lines Changed | Issues Found |
|------|--------------|--------------|
| `<path>` | +X / -Y | Z |
