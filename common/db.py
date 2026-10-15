import logging
from collections.abc import Callable
from functools import wraps

from django.db import OperationalError, connection

LOGGER = logging.getLogger(__name__)


def drop_connection_if_safe() -> bool:
    """Closes the DB connection so the next query opens a fresh one. Returns whether it did.

    Long steps (OCR) can outlive the connection: the database host may close idle
    connections (e.g. scale-to-zero) or restart. Django reconnects lazily, so closing is
    cheap. Inside a transaction the connection can't be swapped, so this does nothing and
    returns False.
    """
    if connection.in_atomic_block:
        return False
    connection.close()
    return True


def retry_on_lost_connection[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Retries `func` once on a fresh connection if it fails with `OperationalError`.

    Only for writes that are safe to repeat (upserts, saves of the same values). This also
    catches errors that aren't a lost connection, such as deadlocks; repeating is still fine
    for those. Inside a transaction there's no retry and the error surfaces.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except OperationalError:
            if not drop_connection_if_safe():
                raise
            LOGGER.warning("DB connection lost in %s, retrying once", func.__qualname__)
            return func(*args, **kwargs)

    return wrapper
