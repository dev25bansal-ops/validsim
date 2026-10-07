"""Asynchronous validation job queue (public API).

Wire the queue into ``app.state.job_queue`` and include
:data:`validsim.jobs.router` to expose the async endpoints; import
:func:`create_job_queue` for the env-driven (memory or Redis) backend factory.
"""

from __future__ import annotations

from validsim.jobs.models import TERMINAL_STATUSES, JobRecord, JobSpec, JobStatus
from validsim.jobs.queue import JobQueue, RedisJobQueue, create_job_queue
from validsim.jobs.router import EnqueueJobRequest, get_queue, router
from validsim.jobs.worker import JobWorker

__all__ = [
    "TERMINAL_STATUSES",
    "EnqueueJobRequest",
    "JobQueue",
    "JobRecord",
    "JobSpec",
    "JobStatus",
    "JobWorker",
    "RedisJobQueue",
    "create_job_queue",
    "get_queue",
    "router",
]