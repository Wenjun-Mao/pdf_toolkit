# Recent Jobs Active Guard Design

## Problem

The uncommitted Recent Jobs cleanup feature deletes every job row and its stored uploads, results, previews, and contact sheets. Production jobs run through RQ, so clearing a queued or running job can remove state that its worker still needs.

## Decision

`Clear Recent Jobs` remains an all-or-nothing operation. Before deleting anything, the jobs service checks for jobs in `queued`, `analyzing`, `awaiting_settings`, or `processing` status. If any exist, cleanup is refused and no database rows or artifacts are removed.

Only `completed`, `failed`, and `expired` jobs may be cleared. `awaiting_settings` is protected because it can transition back to `queued` and begin using its stored inputs.

The service serializes cleanup through the database: SQLite acquires an immediate write transaction and other databases lock the selected job rows. While holding that lock, it stages each terminal-job directory under a per-root trash directory, deletes the rows, and commits. Any staging, deletion, or commit failure restores every staged directory. After a successful commit, staged directories are purged with retries; a final purge failure is logged and leaves only inaccessible quarantined data.

The service raises a dedicated domain error containing the number of protected jobs. The web route renders a complete home page with the current jobs and an inline notice. The browser confirmation remains unchanged.

## Alternatives

- Clear only inactive jobs: rejected because the button promises to clear all Recent Jobs and partial success would be surprising.
- Cancel RQ jobs before clearing: rejected for now because cancellation needs queue-state coordination and cannot safely stop work that is already executing.

## Guardrails

- Parameterized regression tests prove that every protected status blocks the complete operation and preserves rows and all four artifact roots.
- Existing tests continue to prove that cleanup removes every terminal status and its storage.
- Failure injection proves that a database error restores staged artifacts and preserves job rows.
- A two-request regression proves concurrent cleanup calls serialize and cannot restore orphaned artifacts.
- The guard lives in the jobs service so every caller receives the same protection.
