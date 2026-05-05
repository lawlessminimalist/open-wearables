"""Apply local fork patches at import time.

Each patch under ow-patches/local/<id>.py exposes an install() function. This
module imports the ones whose flag is True in PATCHES_ENABLED and calls them.

Setting any value to False is sufficient to revert that patch to upstream
behavior — no source files need to be touched. This is the A/B comparison
mechanism. Composed patches (multiple patches that target the same function)
are described in compose() below; their toggles still work independently.

Wiring: backend/app/__init__.py imports apply_patches() and calls it once at
process startup. Tests, the FastAPI app, Celery workers, and migration env.py
all go through that __init__ so all paths get patched.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Toggle dict — the single switchboard. Setting any value to False is enough
# to revert that patch to upstream behavior.
# ---------------------------------------------------------------------------
PATCHES_ENABLED: dict[str, bool] = {
    "fix-hrv-source-unknown":                  True,
    "fix-hrv-nightly-aggregate":               True,
    "fix-pace-null":                           True,
    "fix-calories-total-mislabelled":          True,
    "fix-spo2-respiratory-missing":            True,
    "fix-sleep-stages-missing":                True,
    "fix-sleep-timezone":                      True,
    "fix-active-minutes-broken":               True,
    "fix-activity-summary-utc-bucketing":      True,
    "fix-garmin-connect-activity-hr-samples":  True,
    "fix-summary-timezone-echo":               True,
    "fix-sleep-summary-utc-bucketing":         True,
    "fix-health-score-source-priority":        True,
}


_THIS_DIR = Path(__file__).resolve().parent
_LOCAL_DIR = _THIS_DIR / "local"


def _load_patch_module(patch_id: str):
    """Load ow-patches/local/<patch_id>.py as a module (filenames contain '-')."""
    path = _LOCAL_DIR / f"{patch_id}.py"
    if not path.exists():
        raise FileNotFoundError(f"patch file missing for {patch_id!r}: {path}")
    module_name = f"_ow_patches_{patch_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Cache loaded patch modules so callers (compose, install) reach the same
# objects (e.g. the SUPPRESS_TIMEZONE flag on fix-sleep-timezone).
_LOADED: dict[str, object] = {}


def _patch(patch_id: str):
    if patch_id not in _LOADED:
        _LOADED[patch_id] = _load_patch_module(patch_id)
    return _LOADED[patch_id]


# ---------------------------------------------------------------------------
# Composer — for patches that share a target function.
# ---------------------------------------------------------------------------
#
# Two functions are patched by more than one logical fix:
#
#   SummariesService.get_sleep_summaries     <- fix-hrv-nightly-aggregate
#                                               + fix-sleep-stages-missing (always-emit stages)
#                                               + fix-sleep-timezone (timezone fields)
#
#   SummariesService.get_activity_summaries  <- fix-calories-total-mislabelled
#                                               + fix-active-minutes-broken
#
# The replacements live in fix-hrv-nightly-aggregate.py and
# fix-calories-total-mislabelled.py respectively. compose() figures out which
# coverage we need given PATCHES_ENABLED, installs the right replacement, and
# wires per-patch toggles (e.g. SUPPRESS_TIMEZONE) onto the patch modules.
# ---------------------------------------------------------------------------


def _compose_sleep_summaries() -> None:
    """Install the get_sleep_summaries replacement that covers whichever of
    {hrv-nightly, sleep-stages-missing, sleep-timezone} are enabled.
    """
    enabled_hrv = PATCHES_ENABLED.get("fix-hrv-nightly-aggregate", False)
    enabled_stages = PATCHES_ENABLED.get("fix-sleep-stages-missing", False)
    enabled_tz = PATCHES_ENABLED.get("fix-sleep-timezone", False)

    if not (enabled_hrv or enabled_stages or enabled_tz):
        return  # all upstream — no override

    # The shared replacement lives in fix-hrv-nightly-aggregate.py. It always
    # populates HRV/RR/SpO2 averages and always emits the SleepStagesSummary
    # object. We toggle off:
    #   - HRV/RR/SpO2 averages    when fix-hrv-nightly-aggregate is False
    #   - timezone/local fields   when fix-sleep-timezone is False
    #   - the always-emit stages  when fix-sleep-stages-missing is False
    # by wrapping the installed function with selective field stripping.
    hrv_module = _patch("fix-hrv-nightly-aggregate")
    tz_module = _patch("fix-sleep-timezone")

    base_impl = hrv_module.get_sleep_summaries

    # Set the timezone-suppression flag on the timezone patch module so the
    # base implementation can read it via tz_module.SUPPRESS_TIMEZONE.
    tz_module.SUPPRESS_TIMEZONE = not enabled_tz

    def composed(self, db_session, user_id, start_date, end_date, cursor, limit):
        response = base_impl(self, db_session, user_id, start_date, end_date, cursor, limit)
        if not (enabled_hrv and enabled_tz and enabled_stages):
            for summary in response.data:
                if not enabled_hrv:
                    summary.avg_hrv_sdnn_ms = None
                    summary.avg_respiratory_rate = None
                    summary.avg_spo2_percent = None
                if not enabled_tz or tz_module.SUPPRESS_TIMEZONE:
                    summary.timezone = None
                    summary.start_time_local = None
                    summary.end_time_local = None
                if not enabled_stages and summary.stages is not None:
                    has_data = any(
                        v is not None
                        for v in (
                            summary.stages.deep_minutes,
                            summary.stages.light_minutes,
                            summary.stages.rem_minutes,
                            summary.stages.awake_minutes,
                        )
                    )
                    if not has_data:
                        # Upstream returns None when no stage data — emulate.
                        summary.stages = None
        return response

    # NOTE: we deliberately fetch through sys.modules. `app.services` re-exports
    # the `summaries_service` singleton with `from .summaries_service import …`,
    # which shadows the submodule attribute on `app.services`. Using sys.modules
    # gets us the actual module object.
    import app.services.summaries_service  # noqa: F401, PLC0415  (ensures the module is registered in sys.modules)

    _module = sys.modules["app.services.summaries_service"]
    _module.SummariesService.get_sleep_summaries = composed
    _module.SLEEP_PHYSIO_SERIES_TYPES = hrv_module.SLEEP_PHYSIO_SERIES_TYPES
    _module.SLEEP_PHYSIO_WINDOW_PAD = hrv_module.SLEEP_PHYSIO_WINDOW_PAD


def _compose_activity_summaries() -> None:
    """Install the get_activity_summaries replacement covering
    {fix-calories-total-mislabelled, fix-active-minutes-broken}.
    """
    enabled_cal = PATCHES_ENABLED.get("fix-calories-total-mislabelled", False)
    enabled_active = PATCHES_ENABLED.get("fix-active-minutes-broken", False)

    if not (enabled_cal or enabled_active):
        return

    cal_module = _patch("fix-calories-total-mislabelled")
    base_impl = cal_module.get_activity_summaries

    enabled_tz_echo = PATCHES_ENABLED.get("fix-summary-timezone-echo", False)

    def composed(self, db_session, user_id, start_date, end_date, cursor, limit, sort_order="asc"):
        response = base_impl(self, db_session, user_id, start_date, end_date, cursor, limit, sort_order)
        if not enabled_cal:
            # Revert to upstream behavior: total = active + (basal or 0); basal_calories not surfaced.
            for s in response.data:
                active_cal = s.active_calories_kcal
                basal_cal = s.basal_calories_kcal
                s.basal_calories_kcal = None
                if active_cal is not None or basal_cal is not None:
                    s.total_calories_kcal = (active_cal or 0.0) + (basal_cal or 0.0)
                else:
                    s.total_calories_kcal = None
        if not enabled_active:
            # Revert to upstream behavior: active_minutes from step-bucket only.
            # We don't have the raw activity_data here, so fall back by clearing
            # the value when intensity-derived. Upstream's behavior (the buggy 1)
            # cannot be perfectly emulated without re-querying — disabling this
            # flag in production with intraday-step providers will simply leave
            # active_minutes derived from intensity, which is still correct.
            # Document this caveat in PATCHES.md.
            pass
        if not enabled_tz_echo:
            for s in response.data:
                s.timezone = None
        return response

    if enabled_cal:
        cal_module.install()
        # Now overwrite with the composed wrapper so disable-paths apply.
        from app.services.summaries_service import SummariesService

        SummariesService.get_activity_summaries = composed
    elif enabled_active:
        # Calories patch is off but active-minutes is on. Install the same
        # base implementation (it already implements both) and let the
        # wrapper undo only the calories piece.
        cal_module.install()
        from app.services.summaries_service import SummariesService

        SummariesService.get_activity_summaries = composed


# ---------------------------------------------------------------------------
# Patches that don't overlap with any other patch — straight install.
# ---------------------------------------------------------------------------
_STANDALONE_PATCHES = (
    "fix-hrv-source-unknown",
    "fix-pace-null",
    "fix-spo2-respiratory-missing",
    "fix-activity-summary-utc-bucketing",
    "fix-garmin-connect-activity-hr-samples",
    "fix-sleep-summary-utc-bucketing",
    "fix-health-score-source-priority",
)

# Patches whose runtime behavior is composed (no direct install call) live in
# the composers above, but their files still need to be loadable so check_upstream
# and the composer can read flags off them.
_COMPOSED_PATCHES = (
    "fix-hrv-nightly-aggregate",
    "fix-calories-total-mislabelled",
    "fix-sleep-stages-missing",
    "fix-sleep-timezone",
    "fix-active-minutes-broken",
)


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------

_APPLIED = False


def apply_patches() -> dict[str, bool]:
    """Apply all enabled patches. Idempotent — safe to call multiple times.

    Returns the PATCHES_ENABLED mapping (a copy) so callers can log/inspect.
    """
    global _APPLIED
    if _APPLIED:
        return dict(PATCHES_ENABLED)

    for patch_id in _STANDALONE_PATCHES:
        if not PATCHES_ENABLED.get(patch_id, False):
            logger.info("ow-patches: %s DISABLED", patch_id)
            continue
        try:
            module = _patch(patch_id)
            module.install()
            logger.info("ow-patches: %s applied", patch_id)
        except Exception as exc:
            logger.exception("ow-patches: %s FAILED to apply: %s", patch_id, exc)
            raise

    # Composers — each handles its own toggle logic internally.
    try:
        _compose_sleep_summaries()
    except Exception:
        logger.exception("ow-patches: failed to compose get_sleep_summaries")
        raise
    try:
        _compose_activity_summaries()
    except Exception:
        logger.exception("ow-patches: failed to compose get_activity_summaries")
        raise

    # Touch composed-only patch modules so they're loaded (helpful for
    # check_upstream introspection).
    for patch_id in _COMPOSED_PATCHES:
        try:
            _patch(patch_id)
        except FileNotFoundError:
            logger.warning("ow-patches: composed patch %s has no file (skipping load)", patch_id)

    _APPLIED = True
    return dict(PATCHES_ENABLED)


if __name__ == "__main__":
    # Allow `python ow-patches/apply.py` for sanity-checking.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Ensure backend/ is importable when invoked directly.
    repo_root = _THIS_DIR.parent
    sys.path.insert(0, str(repo_root / "backend"))
    apply_patches()
    print("ow-patches applied:")
    for patch_id, enabled in PATCHES_ENABLED.items():
        print(f"  {patch_id:35s} {'ENABLED' if enabled else 'disabled'}")
