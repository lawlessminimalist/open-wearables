"""Cooperative cancellation for pull-provider sync runs.

FORK-OWNED. Deliberately a separate module rather than additions to
sync_status_service.py: upstream PR #1448 (`SyncRun` tracking) rewrites large
parts of that file, and keeping this out of its way means the reconcile is a
new-file add rather than a conflict inside a rewritten module.

Why a flag rather than Celery revoke
------------------------------------
`celery_app.control.revoke(task_id, terminate=True)` sends SIGTERM to the
executing thread's process. On the threads pool (scripts/start/worker.sh) that
kills the whole worker and every other task sharing it -- the same blast radius
that made one OOM take down two syncs on 2026-08-30. Revoking without
`terminate` only prevents a task that has not started yet.

So cancellation is cooperative: the API sets a flag, the task polls it between
units of work and exits through its normal error handling, which emits a proper
terminal event. That is exactly the mechanism the Garmin webhook backfill
already uses (`set_garmin_cancel_flag` + a check between data types), so the
two cancel paths behave the same way from the UI's perspective.

The trade-off is honest: cancellation takes effect at the next checkpoint, not
instantly. A provider call already in flight still completes.
"""

from logging import getLogger
from typing import TYPE_CHECKING

from app.integrations.redis_client import get_redis_client

if TYPE_CHECKING:
    from app.schemas.sync_status import SyncStatusEvent

logger = getLogger(__name__)

# Comfortably longer than any single chunk, short enough that an abandoned flag
# cannot cancel a run started days later that happens to reuse the id.
CANCEL_TTL_SECONDS = 6 * 60 * 60


def _cancel_key(run_id: str) -> str:
    return f"sync:cancel:{run_id}"


def request_cancel(run_id: str) -> None:
    """Ask the task owning ``run_id`` to stop at its next checkpoint."""
    get_redis_client().setex(_cancel_key(run_id), CANCEL_TTL_SECONDS, "1")


def is_cancel_requested(run_id: str) -> bool:
    """True if a cancel has been requested for this run.

    Never raises. A Redis blip must not abort an otherwise healthy sync, so an
    unreachable broker reads as "not cancelled" -- failing open on the side of
    letting work finish.
    """
    try:
        return bool(get_redis_client().exists(_cancel_key(run_id)))
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Could not read sync cancel flag for %s: %s", run_id, exc)
        return False


def clear_cancel(run_id: str) -> None:
    """Drop the flag once the run has acted on it."""
    with_client = get_redis_client()
    with_client.delete(_cancel_key(run_id))


class SyncCancelledError(Exception):
    """Raised inside a sync task when a cancel has been requested."""


def get_run_event(run_id: str) -> "SyncStatusEvent | None":
    """Latest status event for ``run_id``, or None if unknown/expired.

    Reuses sync_status_service's own key builder rather than re-deriving the
    key format here: two copies of a Redis key convention drift, and this one
    would drift silently (a stale cancel simply never matches).
    """
    from app.schemas.sync_status import SyncStatusEvent  # noqa: PLC0415
    from app.services.sync_status_service import _run_key  # noqa: PLC0415

    raw = get_redis_client().get(_run_key(run_id))
    if not raw:
        return None
    try:
        return SyncStatusEvent.model_validate_json(raw)
    except (ValueError, TypeError):
        return None
