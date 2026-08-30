"""
Tests for start_historical_sync on BaseProviderStrategy.

Tests cover:
- Default pull-based implementation (Oura, Whoop, etc.)
- Garmin override (webhook backfill)
- Providers that don't support historical sync (Apple, Google, Samsung)
- HistoricalSyncResult dataclass contract
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.providers.apple.strategy import AppleStrategy
from app.services.providers.base_strategy import HistoricalSyncResult
from app.services.providers.garmin.strategy import GarminStrategy
from app.services.providers.oura.strategy import OuraStrategy
from app.services.providers.whoop.strategy import WhoopStrategy
from app.utils.exceptions import UnsupportedProviderError


class TestHistoricalSyncResult:
    """Tests for the HistoricalSyncResult dataclass."""

    def test_all_fields_present(self) -> None:
        result = HistoricalSyncResult(
            task_id="abc-123",
            method="pull_api",
            message="Synced",
            days=90,
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2026-04-01T00:00:00+00:00",
        )
        assert result.task_id == "abc-123"
        assert result.method == "pull_api"
        assert result.message == "Synced"
        assert result.days == 90
        assert result.start_date is not None
        assert result.end_date is not None

    def test_optional_fields_default_to_none(self) -> None:
        result = HistoricalSyncResult(
            task_id="abc-123",
            method="webhook_backfill",
            message="Started",
            days=None,
        )
        assert result.start_date is None
        assert result.end_date is None


class TestPullBasedHistoricalSync:
    """Tests for the default start_historical_sync (pull-based providers).

    FORK DIVERGENCE: upstream dispatches the whole window as ONE task via
    celery_app.send_task and these tests asserted that. The fork splits the
    window into HISTORICAL_CHUNK_DAYS chunks dispatched as a sequential chain,
    because a single year-long task OOM-killed the worker and -- never being
    acked, per the per-task acks_late -- was redelivered every visibility_timeout
    to OOM again, an unbounded crash loop. These assert the chunked contract.
    This is a STRUCTURAL edit to an upstream test file and will surface as a git
    conflict on future merges; that is intended, so the divergence gets
    re-examined rather than silently reverted.
    """

    @patch("app.services.providers.base_strategy.chain")
    @patch("app.services.providers.base_strategy.celery_app")
    def test_oura_dispatches_pull_sync(self, mock_celery: MagicMock, mock_chain: MagicMock) -> None:
        """Pull-based provider should dispatch sync_vendor_data with is_historical=True."""
        mock_chain.return_value.apply_async.return_value = MagicMock(id="task-oura-123")
        user_id = uuid4()

        result = OuraStrategy().start_historical_sync(user_id, days=90)

        assert isinstance(result, HistoricalSyncResult)
        assert result.task_id == "task-oura-123"
        assert result.method == "pull_api"
        assert result.days == 90
        assert result.start_date is not None
        assert result.end_date is not None

        # 90 days at 30/chunk is exactly three, each carrying the same
        # user/provider/is_historical contract upstream asserted.
        assert mock_celery.signature.call_count == 3
        for call in mock_celery.signature.call_args_list:
            call_kwargs = call[1]["kwargs"]
            assert call_kwargs["user_id"] == str(user_id)
            assert call_kwargs["providers"] == ["oura"]
            assert call_kwargs["is_historical"] is True
            # Immutable: a chunk must not receive the previous chunk's return
            # value as a positional argument.
            assert call[1]["immutable"] is True

    @patch("app.services.providers.base_strategy.chain")
    @patch("app.services.providers.base_strategy.celery_app")
    def test_whoop_dispatches_pull_sync(self, mock_celery: MagicMock, mock_chain: MagicMock) -> None:
        """Another pull-based provider should also use the default implementation."""
        mock_chain.return_value.apply_async.return_value = MagicMock(id="task-whoop-456")
        user_id = uuid4()

        result = WhoopStrategy().start_historical_sync(user_id, days=30)

        assert result.task_id == "task-whoop-456"
        assert result.method == "pull_api"
        assert result.days == 30
        assert mock_celery.signature.call_count == 1, "30 days is exactly one chunk"
        assert mock_celery.signature.call_args[1]["kwargs"]["providers"] == ["whoop"]

    @patch("app.services.providers.base_strategy.chain")
    @patch("app.services.providers.base_strategy.celery_app")
    def test_respects_days_parameter(self, mock_celery: MagicMock, mock_chain: MagicMock) -> None:
        """The date range should span the requested number of days."""
        mock_chain.return_value.apply_async.return_value = MagicMock(id="task-123")
        user_id = uuid4()

        result = OuraStrategy().start_historical_sync(user_id, days=7)

        assert result.days == 7
        start = datetime.fromisoformat(result.start_date)
        end = datetime.fromisoformat(result.end_date)
        assert (end - start).days == 7

    @patch("app.services.providers.base_strategy.chain")
    @patch("app.services.providers.base_strategy.celery_app")
    def test_chunks_tile_the_window_exactly(self, mock_celery: MagicMock, mock_chain: MagicMock) -> None:
        """Chunk kwargs must cover the whole range with no gap and no overlap.

        A gap silently loses days of history; an overlap re-fetches them, and
        request volume is the binding constraint that got this account IP
        rate-limited.
        """
        mock_chain.return_value.apply_async.return_value = MagicMock(id="task-1")

        result = OuraStrategy().start_historical_sync(uuid4(), days=95)

        windows = [
            (
                datetime.fromisoformat(c[1]["kwargs"]["start_date"]),
                datetime.fromisoformat(c[1]["kwargs"]["end_date"]),
            )
            for c in mock_celery.signature.call_args_list
        ]
        assert len(windows) == 4, "95 days at 30/chunk is three full chunks plus a 5-day remainder"
        assert windows[0][0] == datetime.fromisoformat(result.start_date)
        assert windows[-1][1] == datetime.fromisoformat(result.end_date)
        for earlier, later in zip(windows, windows[1:]):
            assert earlier[1] == later[0], "chunk boundaries must meet exactly"

    @patch("app.services.providers.base_strategy.chain")
    @patch("app.services.providers.base_strategy.celery_app")
    def test_chunks_run_sequentially_not_in_parallel(self, mock_celery: MagicMock, mock_chain: MagicMock) -> None:
        """Chunks must be chained, never fanned out.

        Firing every chunk at a provider concurrently is precisely what got
        garmin_connect IP rate-limited and then account-locked (FORK.md).
        """
        mock_chain.return_value.apply_async.return_value = MagicMock(id="task-1")

        OuraStrategy().start_historical_sync(uuid4(), days=90)

        mock_chain.assert_called_once()
        assert len(mock_chain.call_args[0]) == 3, "all chunks belong to one chain"
        mock_celery.send_task.assert_not_called()


class TestGarminHistoricalSync:
    """Tests for Garmin's overridden start_historical_sync."""

    @patch("app.services.providers.garmin.strategy.start_garmin_full_backfill")
    def test_dispatches_backfill_task(self, mock_backfill: MagicMock) -> None:
        """Garmin should dispatch start_garmin_full_backfill, not sync_vendor_data."""
        mock_backfill.delay.return_value = MagicMock(id="task-garmin-789")
        user_id = uuid4()

        result = GarminStrategy().start_historical_sync(user_id, days=90)

        assert isinstance(result, HistoricalSyncResult)
        assert result.task_id == "task-garmin-789"
        assert result.method == "webhook_backfill"
        assert result.days is None  # Garmin ignores days param
        assert result.start_date is None
        assert result.end_date is None
        mock_backfill.delay.assert_called_once_with(str(user_id))

    @patch("app.services.providers.garmin.strategy.start_garmin_full_backfill")
    def test_ignores_days_parameter(self, mock_backfill: MagicMock) -> None:
        """Garmin always uses its own 30-day limit regardless of days param."""
        mock_backfill.delay.return_value = MagicMock(id="task-123")
        user_id = uuid4()

        result = GarminStrategy().start_historical_sync(user_id, days=365)

        assert result.days is None
        mock_backfill.delay.assert_called_once_with(str(user_id))


class TestUnsupportedHistoricalSync:
    """Tests for providers that don't support historical sync."""

    def test_apple_raises_unsupported(self) -> None:
        """SDK-only provider should raise UnsupportedProviderError."""
        user_id = uuid4()

        with pytest.raises(UnsupportedProviderError, match="apple"):
            AppleStrategy().start_historical_sync(user_id, days=90)
