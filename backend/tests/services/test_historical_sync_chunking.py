"""Chunking of historical syncs — the fix for the OOM/redelivery crash loop.

A year-long backfill used to be dispatched as ONE Celery task. It OOM-killed
the worker ~3 minutes in; because sync_vendor_data sets acks_late the message
was never acked, so Redis redelivered it every visibility_timeout (6h) to OOM
again — a loop that could not progress and never gave up (observed 2026-08-30).

These pin the properties that make that impossible, not the implementation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.providers.base_strategy import HISTORICAL_CHUNK_DAYS, _chunk_ranges


def _span(chunks: list[tuple[datetime, datetime]]) -> timedelta:
    return chunks[-1][1] - chunks[0][0]


class TestChunkRanges:
    def test_year_splits_into_bounded_chunks(self):
        end = datetime(2026, 8, 30, tzinfo=timezone.utc)
        start = end - timedelta(days=365)

        chunks = _chunk_ranges(start, end, HISTORICAL_CHUNK_DAYS)

        assert len(chunks) == 13, "365 days at 30/chunk is 12 full chunks plus a 5-day remainder"
        assert all(
            (c_end - c_start) <= timedelta(days=HISTORICAL_CHUNK_DAYS) for c_start, c_end in chunks
        ), "a chunk longer than the cap could exceed visibility_timeout and be redelivered mid-flight"

    def test_covers_the_whole_window_without_gaps_or_overlap(self):
        """Every day must land in exactly one chunk.

        A gap silently loses days of history; an overlap re-fetches them, and
        request volume is the binding constraint that got this account IP
        rate-limited.
        """
        end = datetime(2026, 8, 30, tzinfo=timezone.utc)
        start = end - timedelta(days=100)

        chunks = _chunk_ranges(start, end, HISTORICAL_CHUNK_DAYS)

        assert chunks[0][0] == start
        assert chunks[-1][1] == end
        for earlier, later in zip(chunks, chunks[1:]):
            assert earlier[1] == later[0], "chunk boundaries must meet exactly"
        assert _span(chunks) == end - start

    def test_short_window_is_a_single_chunk(self):
        """A live sync must not pay a chain's overhead."""
        end = datetime(2026, 8, 30, tzinfo=timezone.utc)
        start = end - timedelta(days=3)

        assert _chunk_ranges(start, end, HISTORICAL_CHUNK_DAYS) == [(start, end)]

    def test_exact_multiple_does_not_emit_an_empty_trailing_chunk(self):
        """60 days at 30/chunk is two chunks, not two plus a zero-length third.

        An empty chunk would dispatch a task that fetches nothing and still
        costs an authenticated round trip per data type.
        """
        end = datetime(2026, 8, 30, tzinfo=timezone.utc)
        start = end - timedelta(days=60)

        chunks = _chunk_ranges(start, end, HISTORICAL_CHUNK_DAYS)

        assert len(chunks) == 2
        assert all(c_end > c_start for c_start, c_end in chunks)

    @pytest.mark.parametrize("days", [0, -5])
    def test_non_positive_window_stays_a_single_chunk(self, days: int):
        """Degenerate ranges must not loop forever building chunks."""
        end = datetime(2026, 8, 30, tzinfo=timezone.utc)
        start = end - timedelta(days=days)

        assert len(_chunk_ranges(start, end, HISTORICAL_CHUNK_DAYS)) == 1

    def test_chunk_cap_stays_well_inside_visibility_timeout(self):
        """Guards the invariant linking this constant to the broker config.

        visibility_timeout is 6h. If a chunk can plausibly outlast it, Redis
        redelivers it to a second worker and the SAME window syncs twice
        concurrently — which is how garmin_connect got IP rate-limited.
        """
        assert HISTORICAL_CHUNK_DAYS <= 30
