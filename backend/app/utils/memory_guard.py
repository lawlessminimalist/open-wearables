"""RSS budget guard for long-running Celery tasks.

FORK-OWNED.

Why not the Celery settings
---------------------------
Celery's memory controls -- ``worker_max_memory_per_child`` and
``worker_max_tasks_per_child`` -- recycle a *child process* and are enforced
only on the prefork pool. This worker runs ``--pool=threads``
(scripts/start/worker.sh), so both are inert here, exactly as
``task_time_limit`` is; celery/core.py already declines to set that for the
same reason. Setting them would be config that reads as protection and does
nothing, which is the failure mode this fork keeps getting bitten by.

There is also no Python equivalent of Go's GOMEMLIMIT: the interpreter has no
soft heap ceiling that triggers collection and back-pressure. ``resource
.setrlimit(RLIMIT_AS)`` is the closest primitive, but it caps *address space*
process-wide, so on a threads pool one task's limit applies to every other
thread in the process -- and an allocation failure surfaces as a MemoryError
from an arbitrary line, not from the task that overspent.

What this does instead
----------------------
Polls RSS at explicit checkpoints between units of work and raises when the
process is close to the container limit. That converts a SIGKILL -- which runs
no Python, emits no status event, and leaves the run stuck at in_progress --
into an ordinary exception the task's own error handling can report.

The threads pool shares one process across all tasks, so memory does NOT reset
between them; the guard is therefore a property of the worker, not of any one
task. A task that trips it is not necessarily the one that overspent, which is
why the error names the reading rather than blaming the caller.
"""

import os
from logging import getLogger

logger = getLogger(__name__)

_PROC_STATUS = "/proc/self/status"


class MemoryBudgetExceededError(RuntimeError):
    """Raised when the worker process is close to its container memory limit."""


def current_rss_bytes() -> int | None:
    """Resident set size of this process, or None if it cannot be read.

    Reads /proc rather than using ``resource.getrusage``: ru_maxrss is a
    high-water mark that never goes down, so it cannot see memory being
    released between chunks. Returns None off Linux (local macOS dev), where
    the guard degrades to a no-op rather than guessing.
    """
    try:
        with open(_PROC_STATUS) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def container_memory_limit_bytes() -> int | None:
    """The cgroup memory limit, or None when unlimited/unreadable.

    cgroup v2 first (``memory.max``, which reads "max" when unbounded), then
    the v1 path. Kubernetes sets this from resources.limits.memory, so the
    guard tracks whatever the manifest says without needing it duplicated in
    application config.
    """
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as fh:
                raw = fh.read().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        # cgroup v1 reports a sentinel near 2^63 for "unlimited".
        if value <= 0 or value >= 1 << 62:
            return None
        return value
    return None


def check_memory_budget(checkpoint: str, threshold: float = 0.85) -> None:
    """Raise :class:`MemoryBudgetExceededError` if RSS is over ``threshold`` of the limit.

    Args:
        checkpoint: Short label for where this was called, echoed in the error
            so a failed run says which unit of work it died between.
        threshold: Fraction of the container limit that counts as too close.
            0.85 of 704Mi leaves ~105Mi of headroom, comfortably more than a
            single chunk's working set, so the guard fires before the kernel
            does rather than racing it.

    No-ops when either reading is unavailable (non-Linux, no cgroup limit):
    a guard that cannot measure must not invent a failure.
    """
    limit = container_memory_limit_bytes()
    if limit is None:
        return
    rss = current_rss_bytes()
    if rss is None:
        return

    if rss >= limit * threshold:
        raise MemoryBudgetExceededError(
            f"RSS {rss // (1024 * 1024)}MiB is at/over {int(threshold * 100)}% of the "
            f"{limit // (1024 * 1024)}MiB container limit at checkpoint '{checkpoint}'. "
            "Stopping before the kernel OOM-kills the worker, which would leave this "
            "run stuck at in_progress. Reduce the sync window or raise the memory limit."
        )


def log_rss(checkpoint: str) -> None:
    """Debug-level RSS breadcrumb; safe to call anywhere."""
    rss = current_rss_bytes()
    if rss is not None and os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
        logger.debug("rss_checkpoint checkpoint=%s rss_mib=%d", checkpoint, rss // (1024 * 1024))
