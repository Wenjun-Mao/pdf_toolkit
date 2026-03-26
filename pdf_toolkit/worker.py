from __future__ import annotations

from .db import init_db
from .logging import configure_logging
from .queueing import get_redis_connection
from .settings import get_settings
from .storage import ensure_storage_dirs


def main() -> None:
    from rq import Worker

    settings = get_settings()
    configure_logging(settings.debug)
    ensure_storage_dirs(settings)
    init_db(settings)
    worker = Worker([settings.queue_name], connection=get_redis_connection(settings))
    worker.work()


if __name__ == "__main__":
    main()
