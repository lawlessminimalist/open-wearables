# patch_id:        fix-hrv-source-unknown
# upstream_file:   backend/app/services/providers/ultrahuman/data_247.py
# upstream_symbol: Ultrahuman247Data.save_activity_samples
# retire_when:     Ultrahuman247Data.save_activity_samples passes `source=` (not just `provider=`) to TimeSeriesSampleCreate, OR the TimeSeriesSampleCreate constructor itself populates source from provider when source is omitted.

"""Pass `source=` (in addition to upstream's `provider=`) to TimeSeriesSampleCreate
so the data_source row actually carries the provider label. Upstream's call wrote
`provider=` only, which left `data_source.source` as NULL — and the
get_timeseries response surfaces that as `"unknown"`.

Replaces: Ultrahuman247Data.save_activity_samples

Rebased onto upstream's current body (post-merge): keeps upstream's
ACTIVITY_SAMPLE_SERIES.get(key) lookup (#1206 ce2934a) and the
is_daily_total=daily_total_flag(...) argument (#1232 ca6932d). Our delta is the
single added `source=self.provider_name` argument on the TimeSeriesSampleCreate
call.

Composition note: this replacement OWNS save_activity_samples and composes with
fix-spo2-respiratory-missing, which produces the "spo2" / "respiratory_rate"
buckets. Those keys resolve via ACTIVITY_SAMPLE_SERIES (extended by that patch's
coverage change), so no inline type map is needed here anymore.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.database import DbSession
from app.schemas.enums import daily_total_flag
from app.schemas.model_crud.activities.data_point_series import TimeSeriesSampleCreate
from app.services.providers.ultrahuman.coverage import ACTIVITY_SAMPLE_SERIES


def save_activity_samples(
    self,
    db: DbSession,
    user_id: UUID,
    normalized_samples: dict[str, list[dict[str, Any]]],
) -> int:
    """Save normalized activity samples (HR, HRV, etc.) to DataPointSeries."""
    count = 0

    for key, samples in normalized_samples.items():
        series_type = ACTIVITY_SAMPLE_SERIES.get(key)
        if not series_type:
            continue

        for sample in samples:
            recorded_at_str = sample.get("recorded_at")
            try:
                # Parse timestamp
                if not recorded_at_str:
                    continue

                recorded_at = datetime.fromisoformat(recorded_at_str.replace("Z", "+00:00"))

                # Create sample.
                # The fix — pass `source=` alongside upstream's `provider=`. The
                # TimeSeriesSample response is built off DataSource.source (see
                # TimeSeriesService.get_timeseries), so this is the field
                # consumers actually see; upstream left it NULL ("unknown").
                ts_sample = TimeSeriesSampleCreate(
                    id=uuid4(),
                    user_id=user_id,
                    provider=self.provider_name,
                    source=self.provider_name,
                    recorded_at=recorded_at,
                    value=Decimal(str(sample.get("value"))),
                    series_type=series_type,
                    is_daily_total=daily_total_flag(series_type, is_daily=False),
                )

                self.data_point_repo.create(db, ts_sample)
                count += 1
            except Exception as e:
                # Log but continue for other samples
                # Use warning level for first few errors to help debug issues
                self.logger.warning(
                    f"Failed to save {key} sample for user {user_id} at {recorded_at_str or 'unknown time'}: {e}"
                )

    return count


def install() -> None:
    """Monkey-patch Ultrahuman247Data.save_activity_samples."""
    from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data

    Ultrahuman247Data.save_activity_samples = save_activity_samples
