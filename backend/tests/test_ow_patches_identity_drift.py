"""Guard against a patch or provider splitting one device across two DataSource rows.

The failure mode this exists for
--------------------------------
A `DataSource` row is keyed by the identity tuple `(user_id, device_model, source)`
— see `DataPointSeriesRepository._resolve_data_sources` and the unique index
`uq_data_source_identity` on
`(user_id, provider, COALESCE(device_model,''), COALESCE(source,''))`.

So a single *omitted keyword argument* silently mints a SECOND data_source row for
the same physical device, and the provider's history is split across the two. The
symptom is not an error — it is a dashboard that reports `source: "unknown"` or
`device_type: null`, aggregates that read low because half the samples hang off the
other identity, and a coverage tab that disagrees with itself.

Both halves of that have actually happened in this fork:

1. `fix-hrv-source-unknown` exists because upstream's Ultrahuman ingest passed
   `provider=` but not `source=`, so every sample landed on a NULL-source identity.
2. On 2026-08-29 the reconcile rebased `fix-spo2-respiratory-missing` onto
   upstream's post-#1469 `load_and_save_all` and, in copying upstream's body
   faithfully, **dropped `source=` from the `vo2_max` and `active_time`
   constructors**. Those two series immediately began writing to a NULL-source
   `data_source` row again while every other Ultrahuman series wrote to the
   correct one. Nothing raised. Every existing guard stayed green:
     - `test_ow_patches_installed` only asserts the symbol is patched;
     - `test_ow_patches_column_drift` only inspects `DataSource.<column>` refs;
     - `check_upstream.py` cannot see a missing keyword argument.
   It was caught by inspecting production `data_source` rows, which is far too
   late — by then the data is already split.
3. The same class of bug, from the other direction: `garmin_connect` set
   `device_model=` on its `EventRecordCreate` but on none of its eight
   `TimeSeriesSampleCreate` calls, so events and timeseries for one watch resolved
   to two different identities.

This module encodes the invariant that would have caught all three.

What it checks
--------------
For each provider, over the *effective* code — the ow-patch body where a symbol is
monkey-patched, the fork-owned source file where it is not:

  A. every persisted-row constructor passes `source=`;
  B. `device_model=` is all-or-nothing within a provider.

Deliberately AST-based and generic, in the same spirit as
`test_ow_patches_column_drift`: any new constructor added to a covered file is
picked up automatically rather than needing to be registered here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PATCH_DIR = _REPO_ROOT / "ow-patches" / "local"

# Constructors whose kwargs feed the DataSource identity tuple.
_IDENTITY_CONSTRUCTORS = frozenset({"TimeSeriesSampleCreate", "EventRecordCreate"})

# Files that make up the *effective* persist path for a provider.
#
# For ultrahuman the upstream module is deliberately NOT listed: its own
# `_build_activity_samples` / `load_and_save_all` bodies still omit `source=`
# (that is the bug fix-hrv-source-unknown and fix-spo2-respiratory-missing exist
# to correct), and they are replaced at import time by apply.py. Scanning
# upstream's file would assert against dead code. Scan the patches instead.
_PROVIDER_FILES: dict[str, list[Path]] = {
    "ultrahuman": [
        _PATCH_DIR / "fix-hrv-source-unknown.py",
        _PATCH_DIR / "fix-spo2-respiratory-missing.py",
    ],
    "garmin_connect": [
        _REPO_ROOT / "backend/app/services/providers/garmin_connect/data_247.py",
        _REPO_ROOT / "backend/app/services/providers/garmin_connect/workouts.py",
        _PATCH_DIR / "fix-garmin-connect-activity-hr-samples.py",
    ],
}


def _identity_calls(path: Path) -> list[tuple[int, str, set[str]]]:
    """Return (lineno, constructor, kwarg names) for each identity constructor."""
    tree = ast.parse(path.read_text())
    found: list[tuple[int, str, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in _IDENTITY_CONSTRUCTORS:
            found.append((node.lineno, name, {kw.arg for kw in node.keywords if kw.arg}))
    return found


def _all_calls(provider: str) -> list[tuple[Path, int, str, set[str]]]:
    out: list[tuple[Path, int, str, set[str]]] = []
    for path in _PROVIDER_FILES[provider]:
        assert path.exists(), f"{provider}: {path} is listed here but does not exist"
        out.extend((path, ln, ctor, kws) for ln, ctor, kws in _identity_calls(path))
    return out


@pytest.mark.parametrize("provider", sorted(_PROVIDER_FILES))
def test_every_persisted_row_sets_source(provider: str) -> None:
    """`source=` is required: a None source resolves to its own DataSource row.

    Downstream this surfaces as `source: "unknown"` on the timeseries endpoint,
    because the response is built from `DataSource.source`.
    """
    calls = _all_calls(provider)
    assert calls, f"{provider}: no identity constructors found — has the persist path moved?"

    missing = [
        f"{path.relative_to(_REPO_ROOT)}:{ln} {ctor}(...) has no source="
        for path, ln, ctor, kws in calls
        if "source" not in kws
    ]
    assert not missing, (
        f"{provider}: constructor(s) omit source=, which mints a second data_source "
        f"row and splits this provider's history:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("provider", sorted(_PROVIDER_FILES))
def test_device_model_is_all_or_nothing(provider: str) -> None:
    """`device_model=` must be consistent within a provider.

    Setting it on some rows and not others splits ONE physical device across two
    identities — the garmin_connect case: `EventRecordCreate` passed
    `device_model=`, its eight `TimeSeriesSampleCreate` calls did not, so sleep
    events resolved to the "EPIX Gen2" row while every sample resolved to a
    device-less one. It is fine for a provider to never set it (some APIs do not
    report a device); it is not fine to be inconsistent.
    """
    calls = _all_calls(provider)
    with_dm = [(p, ln, c) for p, ln, c, kws in calls if "device_model" in kws]
    without_dm = [(p, ln, c) for p, ln, c, kws in calls if "device_model" not in kws]

    if with_dm and without_dm:
        fmt = lambda rows: "\n    ".join(  # noqa: E731
            f"{p.relative_to(_REPO_ROOT)}:{ln} {c}" for p, ln, c in rows
        )
        pytest.fail(
            f"{provider}: device_model= is set on some persisted rows but not others, "
            f"so one device resolves to two data_source identities.\n"
            f"  sets device_model:\n    {fmt(with_dm)}\n"
            f"  does NOT set device_model:\n    {fmt(without_dm)}"
        )
