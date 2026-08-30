from app.config import settings
from app.schemas.enums import SeriesType

# Activity-details metric key → SeriesType, for per-sample workout rows.
#
# Deliberately mirrors garmin/coverage.py::ACTIVITY_SAMPLE_SERIES one-for-one —
# same eight series, same list[tuple[str, SeriesType]] shape — so the two Garmin
# providers agree on what a workout sample set contains. Only the source key
# differs: the webhook API names fields (heartRate, speedMetersPerSecond, ...)
# while Garmin Connect returns a metricDescriptors index map with directXxx keys.
#
# stepsPerMinute on the webhook side is total cadence, so directDoubleCadence
# (154 for a run) is the match, NOT directRunCadence (77, per-leg).
#
# Ingestion is split — see ACTIVITY_SAMPLE_ALWAYS below. A 9-hour activity is
# ~8.4k rows PER series, so the trace columns are the expensive part.
ACTIVITY_SAMPLE_SERIES: list[tuple[str, SeriesType]] = [
    ("directHeartRate", SeriesType.heart_rate),
    ("directSpeed", SeriesType.speed),
    ("directDoubleCadence", SeriesType.cadence),
    ("directPower", SeriesType.power),
    ("directElevation", SeriesType.elevation),
    ("directLatitude", SeriesType.latitude),
    ("directLongitude", SeriesType.longitude),
    ("directAirTemperature", SeriesType.air_temperature),
]

# Heart rate is ingested UNCONDITIONALLY; the rest only when
# settings.ingest_workout_samples is on. Two reasons, both measured:
#
# 1. The platform aggregates workout heart rate. get_daily_intensity_minutes
#    buckets heart_rate per minute and bins it by HR zone, so ActivitySummary's
#    light/moderate/vigorous minutes depend on sample DENSITY. Dropping the
#    per-second stream leaves only the 2-minute daily stream and roughly halves
#    minute-bucket coverage inside a workout (measured over 30 days: 132 buckets
#    -> 64), silently undercounting intensity minutes. heart_rate is also read by
#    the daily HR aggregates.
#
# 2. The other seven have NO consumer anywhere in repositories or services —
#    they are write-only, reachable solely through the raw /timeseries endpoint —
#    and they cost 5x the rows (measured 6.00x total across 10 activities, every
#    one exposing 6 of the 8 columns).
#
# This also matches the flag's own documentation in config.py, which describes it
# as "per-second workout samples (speed, cadence, power, GPS, etc.)" — it never
# meant heart rate. Gating HR behind it (as this provider did briefly) conflated
# a storage-cost switch with a data-correctness one.
ACTIVITY_SAMPLE_ALWAYS: frozenset[SeriesType] = frozenset({SeriesType.heart_rate})

# Timestamp column in the same metricDescriptors block.
ACTIVITY_SAMPLE_TIMESTAMP_KEYS: tuple[str, ...] = (
    "directTimestamp",
    "TIMESTAMP",
    "directTimestampGMT",
)

# Every other REST provider ships a coverage.py; garmin_connect did not, so
# GarminConnectStrategy fell through to base_strategy's empty ProviderCoverage()
# and GET /api/v1/meta/coverage advertised this provider as delivering nothing —
# despite it writing the series below. Any gap analysis driven off that endpoint
# was blind to it.

# Written by data_247.py. Comments name the endpoint each one comes from, since
# request cost is the binding constraint for this provider (per-day endpoints
# multiply by the number of days in the sync window).
TIMESERIES: frozenset[SeriesType] = frozenset(
    {
        # get_heart_rates (per-day)
        SeriesType.heart_rate,
        SeriesType.resting_heart_rate,
        # get_stats (per-day)
        SeriesType.steps,
        SeriesType.energy,
        SeriesType.basal_energy,
        SeriesType.distance_walking_running,
        SeriesType.flights_climbed,
        SeriesType.exercise_time,
        SeriesType.garmin_stress_level,
        # get_stress_data (per-day)
        # -> garmin_stress_level, already listed above
        # get_hrv_data (per-day) + the avgSleepHRV field on get_sleep_data
        SeriesType.heart_rate_variability_rmssd,
        # get_sleep_data (per-day) — nightly averages carried in the same payload
        SeriesType.oxygen_saturation,
        SeriesType.respiratory_rate,
        # get_body_composition (one request per range)
        SeriesType.weight,
        SeriesType.body_mass_index,
        SeriesType.body_fat_percentage,
        SeriesType.skeletal_muscle_mass,
        SeriesType.lean_body_mass,
        # get_max_metrics (one request per range — Garmin only recomputes VO2max
        # after a qualifying activity, so per-day polling would be wasted calls)
        SeriesType.vo2_max,
        # get_activity_details, per workout. Only heart_rate is unconditional;
        # the trace series are advertised only when they will actually be
        # written, so /meta/coverage does not promise rows that a deployment
        # running the default flag never produces — the "advertised, zero rows"
        # failure this provider already shipped once with SpO2 and respiration.
        *(st for _, st in ACTIVITY_SAMPLE_SERIES if st in ACTIVITY_SAMPLE_ALWAYS or settings.ingest_workout_samples),
    }
)

# EventRecordDetail fields populated by workouts.py
WORKOUT_FIELDS: frozenset[str] = frozenset(
    {
        "heart_rate_avg",
        "heart_rate_max",
        "energy_burned",
        "distance",
        "steps_count",
        "moving_time_seconds",
        "total_elevation_gain",
        "elev_high",
        "elev_low",
        "average_speed",
        "max_speed",
        "average_watts",
        "max_watts",
    }
)

# EventRecordDetail fields populated by data_247.py (sleep records)
SLEEP_FIELDS: frozenset[str] = frozenset(
    {
        "sleep_total_duration_minutes",
        "sleep_time_in_bed_minutes",
        "sleep_deep_minutes",
        "sleep_rem_minutes",
        "sleep_light_minutes",
        "sleep_awake_minutes",
        "sleep_stages",
    }
)
