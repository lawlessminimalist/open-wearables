"""Behaviour of ow-patch fix-sleep-stages-missing's normalize_sleep wrapper.

Pins the spelling tolerance the patch exists for and upstream #1953's rule that an
unreported or untimed stage is unknown (None), never zero minutes.
"""

from uuid import uuid4

from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data


def _stages(raw_stages: list[dict]) -> dict:
    data = Ultrahuman247Data.__new__(Ultrahuman247Data)
    data.provider_name = "ultrahuman"
    raw = {"sleep_stages": raw_stages, "quick_metrics": {}, "bedtime_start": 1, "bedtime_end": 2}
    return Ultrahuman247Data.normalize_sleep(data, raw, uuid4())["stages"]


def test_spaced_and_hyphenated_stage_names_match() -> None:
    stages = _stages([{"type": "Deep Sleep", "stage_time": 3240}, {"type": "rem-sleep", "duration": 600}])
    assert stages["deep_seconds"] == 3240
    assert stages["rem_seconds"] == 600


def test_untimed_and_unreported_stages_stay_none_but_a_real_zero_stays_zero() -> None:
    stages = _stages([{"type": "rem_sleep", "stage_time": None}, {"type": "awake", "stage_time": 0}])
    assert stages["rem_seconds"] is None
    assert stages["awake_seconds"] == 0
    assert stages["deep_seconds"] is None
    assert stages["light_seconds"] is None
