"""RSS guard — turns an OOM-kill into a reportable failure.

A SIGKILL runs no Python: no terminal status event, no ack, and with acks_late
the message is redelivered every visibility_timeout to OOM again. The guard
exists so the task fails through its own error handling instead.

Celery's own controls (worker_max_memory_per_child, worker_max_tasks_per_child)
are prefork-only and would be inert on this worker's --pool=threads, which is
why they are not used; see the module docstring.
"""

import pytest

from app.utils import memory_guard
from app.utils.memory_guard import MemoryBudgetExceededError, check_memory_budget


class TestCheckMemoryBudget:
    def test_raises_when_rss_crosses_the_threshold(self, monkeypatch):
        monkeypatch.setattr(memory_guard, "container_memory_limit_bytes", lambda: 704 * 1024 * 1024)
        monkeypatch.setattr(memory_guard, "current_rss_bytes", lambda: 650 * 1024 * 1024)

        with pytest.raises(MemoryBudgetExceededError) as exc:
            check_memory_budget("before garmin_connect sync")

        # The message must name the checkpoint: on a threads pool the process is
        # shared, so the task that trips the guard is not necessarily the one
        # that overspent, and the reading is what makes that diagnosable.
        assert "before garmin_connect sync" in str(exc.value)
        assert "650" in str(exc.value)

    def test_allows_normal_usage(self, monkeypatch):
        """201Mi of 704Mi is the observed idle baseline and must not trip."""
        monkeypatch.setattr(memory_guard, "container_memory_limit_bytes", lambda: 704 * 1024 * 1024)
        monkeypatch.setattr(memory_guard, "current_rss_bytes", lambda: 201 * 1024 * 1024)

        check_memory_budget("idle")

    @pytest.mark.parametrize(
        ("limit", "rss"),
        [(None, 650 * 1024 * 1024), (704 * 1024 * 1024, None), (None, None)],
    )
    def test_no_ops_when_a_reading_is_unavailable(self, monkeypatch, limit, rss):
        """No cgroup limit (local dev) or unreadable RSS must not invent a failure.

        A guard that cannot measure has to stay out of the way, or every macOS
        dev run and every uncapped deployment fails syncs for no reason.
        """
        monkeypatch.setattr(memory_guard, "container_memory_limit_bytes", lambda: limit)
        monkeypatch.setattr(memory_guard, "current_rss_bytes", lambda: rss)

        check_memory_budget("unmeasurable")

    def test_threshold_leaves_headroom_below_the_kernel(self, monkeypatch):
        """At exactly the limit the kernel wins; the guard must fire before it.

        595Mi is 84.5% of 704Mi — under the 85% default, so this pins that the
        guard is not so eager it kills healthy runs, while 650Mi (92%) above
        shows it still fires with ~54Mi to spare.
        """
        monkeypatch.setattr(memory_guard, "container_memory_limit_bytes", lambda: 704 * 1024 * 1024)
        monkeypatch.setattr(memory_guard, "current_rss_bytes", lambda: 595 * 1024 * 1024)

        check_memory_budget("just under")
