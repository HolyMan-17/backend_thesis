# Session Rules

## Long-context persistence

When planning or implementing a significant multi-step change (e.g., auth system, new feature, major refactor), ask the user for permission to create or update planning documents in the `remember-me/` directory. This avoids losing context as sessions grow long.

- **`remember-me/PLAN.md`** — Active plan for the current work. Created when scoping a major change. Updated as decisions are made. Deleted or archived when the work lands.
- **`remember-me/PROJECT-SO-FAR.md`** — Running log of what has been implemented, what's pending, and what was decided. Appended to across sessions.

If the `remember-me/` directory doesn't exist, create it. If a file already exists, append or update — don't overwrite from scratch.

Do not create these files for minor fixes or single-step tasks. Only when the scope warrants persistent context across turns or sessions.

Always ask the user before writing. Do not create planning documents unprompted.

## Trigger rules

Always trigger this persistence workflow when using skills like `/grill-me` or any other multi-turn planning tool. If the session involves extended decision-making, create or update `PLAN.md` before implementation begins.
