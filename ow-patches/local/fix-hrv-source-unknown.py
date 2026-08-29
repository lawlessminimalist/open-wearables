# patch_id:        fix-hrv-source-unknown
# upstream_file:   backend/app/services/providers/ultrahuman/data_247.py
# upstream_symbol: Ultrahuman247Data._build_activity_samples
# retire_when:     Ultrahuman247Data._build_activity_samples passes `source=` (not just `provider=`) to TimeSeriesSampleCreate, OR the TimeSeriesSampleCreate constructor itself populates source from provider when source is omitted.

"""Pass `source=` (in addition to upstream's `provider=`) to TimeSeriesSampleCreate
so the data_source row actually carries the provider label. Upstream's call wrote
`provider=` only, which left `data_source.source` as NULL — and the
get_timeseries response surfaces that as `"unknown"`.

Replaces: Ultrahuman247Data._build_activity_samples

Rebased 2026-08-29 onto upstream f766b5a0. Upstream #1469 (152137fc, "report real
synced item counts for Ultrahuman") DELETED the method this patch used to target,
`save_activity_samples`, and removed `self.data_point_repo` from the class
entirely. It replaced per-row `repo.create()` writes with a pure builder,
`_build_activity_samples`, whose rows the caller persists in bulk via
`timeseries_service.bulk_create_samples` (an upsert that also fires the
`on_timeseries_batch_saved` webhook).

The stale copy kept calling `self.data_point_repo.create(...)`, which raised
AttributeError inside a per-sample `except`, so EVERY Ultrahuman timeseries
sample was dropped while the sync still reported success. This patch is now
upstream's current `_build_activity_samples` body with the single added
`source=self.provider_name` argument.

Composition note: this replacement OWNS _build_activity_samples and composes with
fix-spo2-respiratory-missing, which produces the "spo2" / "respiratory_rate"
buckets. Those keys resolve via ACTIVITY_SAMPLE_SERIES (extended by that patch's
coverage change), so no inline type map is needed here.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.schemas.enums import daily_total_flag
from app.schemas.model_crud.activities.data_point_series import TimeSeriesSampleCreate
from app.services.providers.ultrahuman.coverage import ACTIVITY_SAMPLE_SERIES
from app.utils.structured_logging import log_structured


def _build_activity_samples(
    self,
    user_id: UUID,
    normalized_samples: dict[str, list[dict[str, Any]]],
) -> list[TimeSeriesSampleCreate]:
    """Build TimeSeriesSampleCreate rows from normalized activity samples (HR, HRV, etc.).

    The rows are persisted in bulk by the caller via
    ``timeseries_service.bulk_create_samples`` (upsert), not written here.
    """
    samples: list[TimeSeriesSampleCreate] = []

    for key, entries in normalized_samples.items():
        series_type = ACTIVITY_SAMPLE_SERIES.get(key)
        if not series_type:
            continue

        for sample in entries:
            recorded_at_str = sample.get("recorded_at")
            if not recorded_at_str:
                continue
            try:
                recorded_at = datetime.fromisoformat(recorded_at_str.replace("Z", "+00:00"))
                samples.append(
                    TimeSeriesSampleCreate(
                        id=uuid4(),
                        user_id=user_id,
                        provider=self.provider_name,
                        # The fix — pass `source=` alongside upstream's `provider=`.
                        # The TimeSeriesSample response is built off
                        # DataSource.source (see TimeSeriesService.get_timeseries),
                        # so this is the field consumers actually see; upstream
                        # leaves it NULL, which surfaces as "unknown".
                        source=self.provider_name,
                        recorded_at=recorded_at,
                        value=Decimal(str(sample.get("value"))),
                        series_type=series_type,
                        is_daily_total=daily_total_flag(series_type, is_daily=False),
                    )
                )
            except Exception as e:
                log_structured(
                    self.logger,
                    "warning",
                    "Failed to build activity sample",
                    provider="ultrahuman",
                    task="build_activity_samples",
                    series=key,
                    user_id=str(user_id),
                    recorded_at=recorded_at_str or "unknown time",
                    error=str(e),
                )

    return samples


def install() -> None:
    """Monkey-patch Ultrahuman247Data._build_activity_samples."""
    from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data

    Ultrahuman247Data._build_activity_samples = _build_activity_samples
