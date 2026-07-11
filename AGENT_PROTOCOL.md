# Codex ↔ Claude coordination protocol

This repository is the shared communication channel between agents. Neither
agent should assume it can read the other's private chat/session memory.

## One task, one owner

1. Check `AGENT_STATE.md` before starting.
2. Claim a task by changing its **Owner** to `Codex` or `Claude` and status to
   **In progress** in a small, separate commit or an immediately visible
   working-tree edit.
3. Keep the claim narrow: list the files being changed in the task's Notes.
4. Do not edit a task claimed by the other agent. If scopes overlap, finish,
   commit, and hand off before the other agent proceeds.
5. Set status to **Done**, **Blocked**, or **Waiting for user** and add the
   verification/result before releasing the task.

## Change discipline

- Use a focused commit per task: `area: outcome`.
- Pull/fetch before starting and before pushing; never force-push or reset
  someone else's work.
- Treat unrelated dirty files as another agent's work. Preserve them.
- Production changes must be verified and recorded in `AGENT_STATE.md`.
- Do not use comments, commits, or handoffs to expose secret values.

## Suggested split

| Workstream | Default owner | Boundary |
|---|---|---|
| UI, product flow, static web | Claude | `web/`, visual QA |
| Data quality, scrapers, database, CI, security | Codex | Python/SQL/workflows/services |
| Cross-cutting feature | Agree in task queue first | One agent owns schema/API, the other owns UI only after the interface is committed. |

This is a convention, not a hard capability limit. Reassign when the task
calls for it, but record the decision in `AGENT_STATE.md`.

## Handoff template

Add this to `AGENT_STATE.md` after material work:

```
### YYYY-MM-DD — <agent>
- Done: <user-visible outcome>
- Changed: <commit and key files>
- Verified: <test/deployment/check>
- Next / blocked: <one concrete item>
```
