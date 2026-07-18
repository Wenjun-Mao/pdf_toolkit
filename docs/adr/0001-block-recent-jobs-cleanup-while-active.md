# 0001: Block Recent Jobs Cleanup While Active

## Status

Accepted

## Context

Recent Jobs cleanup removes both database rows and per-job storage. Jobs run through RQ, so deleting a queued or running job can remove state that its worker still needs. Filesystem deletion and queue execution cannot be made atomic with the application database.

## Decision

Cleanup is all-or-nothing. It is refused before any deletion when a job is `queued`, `analyzing`, `awaiting_settings`, or `processing`. Only terminal jobs are clearable. Awaiting-settings jobs are protected because they can transition back to queued work.

Cleanup calls serialize through the database. SQLite uses an immediate write transaction; other databases lock the selected job rows. While holding that lock, terminal-job directories are renamed into per-root quarantine directories and rows are deleted. Staging, deletion, or commit failures restore those directories. After a successful database commit, quarantine directories are purged with retries; an unrecoverable purge failure is logged and leaves inaccessible quarantined data rather than broken job records.

The jobs service owns this guard and raises a dedicated error. The web layer renders that expected error as an inline notice on the complete home page without logging it as an operational failure.

## Rejected Alternatives

- Partial cleanup of inactive jobs would conflict with the button's promise to clear all Recent Jobs.
- Queue cancellation would require reliable coordination with both queued and already-executing RQ work.

## Consequences

Users must finish active or awaiting-settings work before clearing history. Regression tests must prove that blocked and failed operations preserve every row and artifact.
