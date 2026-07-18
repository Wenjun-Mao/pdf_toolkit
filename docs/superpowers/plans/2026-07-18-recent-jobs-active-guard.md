# Recent Jobs Active Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Recent Jobs cleanup from deleting database rows or artifacts while any background job is queued or running.

**Architecture:** Keep the guard in `clear_jobs_and_artifacts`, the shared service boundary used by the web route. Serialize cleanup with an immediate SQLite transaction or row locks, protect every non-terminal status, stage terminal-job directories by same-root rename, restore staging on database failure, and render expected conflicts on the complete home page.

**Tech Stack:** Python 3.12, SQLAlchemy 2, FastAPI, pytest

## Global Constraints

- Cleanup remains all-or-nothing.
- Protected statuses are exactly `queued`, `analyzing`, `awaiting_settings`, and `processing`.
- A blocked cleanup leaves every job row and artifact untouched.
- `completed`, `failed`, and `expired` jobs remain clearable.
- A staging or database failure restores every staged artifact directory.
- Concurrent cleanup requests serialize through the database.
- Queue cancellation is out of scope.

---

### Task 1: Protect Cleanup From Active Jobs

**Files:**
- Create: `docs/adr/0001-block-recent-jobs-cleanup-while-active.md`
- Modify: `pdf_toolkit/jobs.py:34-101`
- Modify: `pdf_toolkit/storage.py:1-105`
- Modify: `pdf_toolkit/web/app.py:13-183`
- Modify: `pdf_toolkit/web/templates/home.html:22-45`
- Modify: `tests/test_jobs.py:7-64`
- Modify: `tests/test_recent_jobs.py:3-25`

**Interfaces:**
- Consumes: `Job.status`, `JobStatus`, `session_scope`, and the existing `clear_jobs_and_artifacts(settings: Settings | None = None) -> int` service.
- Produces: `ActiveJobsError(active_count: int)`, `staged_job_artifacts(job_ids, settings)`, an all-or-nothing protected-status guard, and full-page conflict rendering.

- [ ] **Step 1: Write failing service and route tests**

Update the existing success tests so their jobs are explicitly completed, then add service and route coverage for the blocked state:

```python
from pdf_toolkit.jobs import (
    ActiveJobsError,
    clear_jobs_and_artifacts,
    create_job,
    get_job,
    list_recent_jobs,
    update_job_fields,
)


def test_clear_jobs_and_artifacts_refuses_to_clear_when_a_job_is_active(
    app_client,
    sample_merge_pdfs: list[Path],
) -> None:
    completed_job = create_job("merge", "Completed", [sample_merge_pdfs[0]])
    update_job_fields(completed_job.id, status=JobStatus.COMPLETED.value)
    active_job = create_job("merge", "Active", [sample_merge_pdfs[1]])
    completed_artifact_dir = job_result_dir(completed_job.id)
    active_artifact_dir = job_result_dir(active_job.id)
    (completed_artifact_dir / "completed.pdf").write_bytes(b"completed")
    (active_artifact_dir / "active.pdf").write_bytes(b"active")

    with pytest.raises(ActiveJobsError, match="1 job is active"):
        clear_jobs_and_artifacts()

    assert get_job(completed_job.id) is not None
    assert get_job(active_job.id) is not None
    assert completed_artifact_dir.exists()
    assert active_artifact_dir.exists()
```

```python
def test_clear_recent_jobs_submission_reports_active_job(open_app_client, sample_merge_pdfs) -> None:
    job = create_job("merge", "Active", [sample_merge_pdfs[0]])

    response = open_app_client.post("/jobs/clear")

    assert response.status_code == 400
    assert "1 job is active" in response.text
    assert get_job(job.id) is not None
```

- [ ] **Step 2: Run the tests and verify the active-job cases fail**

Run:

```powershell
uv run pytest -q tests/test_jobs.py::test_clear_jobs_and_artifacts_refuses_to_clear_when_a_job_is_active tests/test_recent_jobs.py::test_clear_recent_jobs_submission_reports_active_job
```

Expected: collection fails because `ActiveJobsError` does not exist, or the assertions fail because cleanup is not yet guarded.

- [ ] **Step 3: Implement the service-level guard**

Add the domain error and active status set near the top of `jobs.py`:

```python
ACTIVE_JOB_STATUSES = frozenset(
    {
        JobStatus.QUEUED.value,
        JobStatus.ANALYZING.value,
        JobStatus.AWAITING_SETTINGS.value,
        JobStatus.PROCESSING.value,
    }
)


class ActiveJobsError(RuntimeError):
    def __init__(self, active_count: int) -> None:
        self.active_count = active_count
        job_phrase = "job is" if active_count == 1 else "jobs are"
        super().__init__(
            f"Recent Jobs cannot be cleared while {active_count} {job_phrase} active. "
            "Wait for active jobs to finish and try again."
        )
```

Update the initial query in `clear_jobs_and_artifacts` so no filesystem operation occurs before the guard passes:

```python
with session_scope(active_settings) as session:
    job_rows = session.query(Job.id, Job.status).all()

active_count = sum(row.status in ACTIVE_JOB_STATUSES for row in job_rows)
if active_count:
    raise ActiveJobsError(active_count)
job_ids = [row.id for row in job_rows]
```

Wrap database deletion in `staged_job_artifacts`. Its context manager renames existing job directories into per-root quarantine paths, restores them on exceptions, and purges quarantine after a successful commit.

Import `ActiveJobsError` in `web/app.py` and render the complete home page with `clear_jobs_error` before the unexpected-error branch:

```python
except ActiveJobsError as exc:
    return render_home_page(request, status_code=400, clear_jobs_error=str(exc))
```

Record the all-or-nothing cleanup contract and rejected partial-clear and queue-cancellation alternatives in `docs/adr/0001-block-recent-jobs-cleanup-while-active.md`.

- [ ] **Step 4: Run focused tests and verify they pass**

Run:

```powershell
uv run pytest -q tests/test_jobs.py::test_clear_jobs_and_artifacts_removes_rows_and_job_storage tests/test_jobs.py::test_clear_jobs_and_artifacts_refuses_to_clear_when_a_job_is_active tests/test_recent_jobs.py
```

Expected: all focused status, rollback, concurrency, and route tests pass.

- [ ] **Step 5: Run the non-slow suite**

Run:

```powershell
uv run pytest -q -m "not slow"
```

Expected: all selected tests pass.

- [ ] **Step 6: Review the final patch**

Run:

```powershell
git diff --check
git status --short
git diff -- pdf_toolkit/jobs.py tests/test_jobs.py tests/test_recent_jobs.py
```

Expected: no whitespace errors; only the intended Recent Jobs feature, guard, documentation, and tests remain uncommitted.
