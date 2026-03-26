from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from redis import Redis

from .settings import Settings, get_settings

if TYPE_CHECKING:
    from rq import Queue


def get_redis_connection(settings: Settings | None = None) -> Redis:
    active_settings = settings or get_settings()
    return Redis.from_url(active_settings.redis_url)


def get_queue(settings: Settings | None = None) -> Queue:
    from rq import Queue

    active_settings = settings or get_settings()
    return Queue(
        active_settings.queue_name,
        connection=get_redis_connection(active_settings),
        default_timeout=active_settings.job_timeout_seconds,
    )


def dispatch_job(
    task: Callable,
    /,
    *args,
    settings: Settings | None = None,
    **kwargs,
):
    active_settings = settings or get_settings()
    if active_settings.run_jobs_inline:
        return task(*args, **kwargs)
    return get_queue(active_settings).enqueue(task, *args, **kwargs)
