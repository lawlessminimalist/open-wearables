# patch_id:        fix-hrv-source-unknown
# upstream_file:   backend/app/services/providers/ultrahuman/data_247.py
# upstream_symbol: Ultrahuman247Data.save_activity_samples
# retire_when:     Ultrahuman247Data.save_activity_samples passes `source=` (not just `provider=`) to TimeSeriesSampleCreate, OR the TimeSeriesSampleCreate constructor itself populates source from provider when source is omitted.

"""Pass `source=` instead of `provider=` to TimeSeriesSampleCreate so the
data_source row actually carries the provider label. Upstream's call wrote
`provider=` only, which left `data_source.source` as NULL — and the
get_timeseries response surfaces that as `"unknown"`.

Replaces: Ultrahuman247Data.save_activity_samples
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.database import DbSession
from app.schemas.enums.series_types import SeriesType
from app.schemas.model_crud.activities.data_point_series import TimeSeriesSampleCreate


def save_activity_samples(
    self,
    db: DbSession,
    user_id: UUID,
    normalized_samples: dict[str, list[dict[str, Any]]],
) -> int:
    """Save normalized activity samples (HR, HRV, etc.) to DataPointSeries."""
    count = 0

    type_mapping = {
        "heart_rate": SeriesType.heart_rate,
        "hrv": SeriesType.heart_rate_variability_sdnn,
        "temperature": SeriesType.body_temperature,
        "steps": SeriesType.steps,
        "spo2": SeriesType.oxygen_saturation,
        "respiratory_rate": SeriesType.respiratory_rate,
    }

    for key, samples in normalized_samples.items():
        series_type = type_mapping.get(key)
        if not series_type:
            continue

        for sample in samples:
            recorded_at_str = sample.get("recorded_at")
            try:
                if not recorded_at_str:
                    continue

                recorded_at = datetime.fromisoformat(recorded_at_str.replace("Z", "+00:00"))

                # The fix — `source=` rather than `provider=`. The TimeSeriesSample
                # response is built off DataSource.source (see TimeSeriesService.get_timeseries),
                # so this is the field that consumers actually see.
                ts_sample = TimeSeriesSampleCreate(
                    id=uuid4(),
                    user_id=user_id,
                    source=self.provider_name,
                    recorded_at=recorded_at,
                    value=Decimal(str(sample.get("value"))),
                    series_type=series_type,
                )

                self.data_point_repo.create(db, ts_sample)
                count += 1
            except Exception as e:
                self.logger.warning(
                    f"Failed to save {key} sample for user {user_id} at {recorded_at_str or 'unknown time'}: {e}"
                )

    return count


def install() -> None:
    """Monkey-patch Ultrahuman247Data.save_activity_samples."""
    from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data

    Ultrahuman247Data.save_activity_samples = save_activity_samples
