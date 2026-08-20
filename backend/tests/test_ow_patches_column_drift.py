"""Guard against wholesale-replace patches dropping upstream ORM columns.

The failure mode this exists for
--------------------------------
Several ow-patches replace an upstream repository method outright. When upstream
later adds a column to that method's SELECT / GROUP BY / result dict, our copy
silently wins and the column is simply gone. Nothing conflicts (we never edit the
upstream file), nothing raises, and every consumer reads the field with
`result.get(...)` — so the API just returns null forever.

That is not hypothetical: upstream #1414 added `DataSource.device_type` to both
`get_daily_activity_aggregates` and `get_sleep_summaries`, and the fork's two
UTC-bucketing patches shadowed it away. Activity and sleep summaries reported
`device_type: null` for every record and the frontend fell back to a generic
device badge. `check_upstream.py` flagged the file as drifted but could not see
the semantic difference.

This test compares, per method, the set of `DataSource.<column>` attributes the
patch references against the set upstream references, and fails if the patch is
missing any. It is deliberately generic: any future upstream column addition to a
patched method trips it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PATCH_DIR = _REPO_ROOT / "ow-patches" / "local"

# (patch file, patched function name in the patch, upstream file, upstream method)
_CASES: list[tuple[str, str, str, str]] = [
    (
        "fix-activity-summary-utc-bucketing.py",
        "get_daily_activity_aggregates",
        "backend/app/repositories/data_point_series_repository.py",
        "get_daily_activity_aggregates",
    ),
    (
        "fix-activity-summary-utc-bucketing.py",
        "get_daily_active_minutes",
        "backend/app/repositories/data_point_series_repository.py",
        "get_daily_active_minutes",
    ),
    (
        "fix-activity-summary-utc-bucketing.py",
        "get_daily_intensity_minutes",
        "backend/app/repositories/data_point_series_repository.py",
        "get_daily_intensity_minutes",
    ),
    (
        "fix-sleep-summary-utc-bucketing.py",
        "get_sleep_summaries",
        "backend/app/repositories/event_record_repository.py",
        "get_sleep_summaries",
    ),
    (
        "fix-sleep-summary-utc-bucketing.py",
        "_get_sleep_sessions",
        "backend/app/repositories/event_record_repository.py",
        "_get_sleep_sessions",
    ),
    (
        "fix-health-score-source-priority.py",
        "get_with_filters",
        "backend/app/repositories/health_score_repository.py",
        "get_with_filters",
    ),
]


def _find_function(tree: ast.AST, name: str) -> ast.AST | None:
    """Locate a top-level function or a method nested in a class."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _datasource_columns(node: ast.AST) -> set[str]:
    """Collect every `DataSource.<attr>` referenced inside a function body."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "DataSource":
            found.add(sub.attr)
    return found


@pytest.mark.parametrize(
    ("patch_file", "patch_func", "upstream_file", "upstream_func"),
    _CASES,
    ids=[f"{c[0].removesuffix('.py')}::{c[1]}" for c in _CASES],
)
def test_patch_does_not_drop_upstream_datasource_columns(
    patch_file: str, patch_func: str, upstream_file: str, upstream_func: str
) -> None:
    patch_path = _PATCH_DIR / patch_file
    upstream_path = _REPO_ROOT / upstream_file
    if not patch_path.exists():  # pragma: no cover - patch retired
        pytest.skip(f"{patch_file} not present")
    assert upstream_path.exists(), f"upstream target missing: {upstream_file}"

    patch_node = _find_function(ast.parse(patch_path.read_text()), patch_func)
    upstream_node = _find_function(ast.parse(upstream_path.read_text()), upstream_func)
    assert patch_node is not None, f"{patch_func} not found in {patch_file}"
    assert upstream_node is not None, f"{upstream_func} not found in {upstream_file}"

    patch_cols = _datasource_columns(patch_node)
    upstream_cols = _datasource_columns(upstream_node)
    missing = upstream_cols - patch_cols

    assert not missing, (
        f"{patch_file}::{patch_func} references DataSource columns "
        f"{sorted(patch_cols)} but upstream {upstream_file}::{upstream_func} uses "
        f"{sorted(upstream_cols)}.\n"
        f"MISSING: {sorted(missing)}\n"
        "A wholesale-replace patch has fallen behind upstream — the column will be "
        "silently null in the API response. Re-apply it to the patch's SELECT, "
        "GROUP BY and result dict, then re-run. See ow-patches/PATCHES.md."
    )


def test_cases_cover_every_wholesale_repository_patch() -> None:
    """If a new repository-targeting patch appears, make someone add it here."""
    covered = {c[0] for c in _CASES}
    repo_patches = {
        p.name for p in _PATCH_DIR.glob("*.py") if "DataSource." in p.read_text() and "repositories" in p.read_text()
    }
    uncovered = repo_patches - covered
    assert not uncovered, (
        f"These patches touch repository code with DataSource columns but are not "
        f"covered by _CASES: {sorted(uncovered)}. Add them so upstream column "
        "additions can't be silently dropped."
    )
