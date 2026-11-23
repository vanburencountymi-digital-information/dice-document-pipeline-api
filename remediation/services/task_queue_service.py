import logging

from django_tasks_db.models import DBTaskResult

LOGGER = logging.getLogger(__name__)


class TaskQueueService:
    """Read-only view of the database task queue (ADR 0020), for `run_queued_tasks`
    (ADR 0023) to decide whether a scheduled run has anything to do.
    """

    def has_ready_tasks(self) -> bool:
        """True if any task is waiting and due now — the same `ready()` filter `db_worker`
        uses to pick work, so a deferred webhook retry that isn't due yet doesn't count.
        """
        return DBTaskResult.objects.ready().filter(backend_name="default").exists()
