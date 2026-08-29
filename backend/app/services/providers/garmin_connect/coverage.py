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
# Ingestion is gated on settings.ingest_workout_samples, as it is for garmin and
# strava — a 9-hour activity is ~8.4k rows PER series.
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
        # get_activity_details, per workout, gated on settings.ingest_workout_samples
        *(st for _, st in ACTIVITY_SAMPLE_SERIES),
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
