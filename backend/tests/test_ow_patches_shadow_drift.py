"""Fail when upstream's body of a wholesale-replaced method has changed since the
patch was last verified — the check `check_upstream.py` can only do pre-merge.

The failure mode this exists for
--------------------------------
A `wholesale-replace` patch shadows an upstream method. Merging upstream never
conflicts on it (the fork does not edit the upstream file), so when upstream
changes the method the patch keeps winning with a stale copy. Twice this has
shipped to production unnoticed: `device_type` (#1414) and, on 2026-09-13,
`name` / `entry_source` / `intensity` on the workout list (#1510). Both were
found by a manual body diff during a reconcile, hours after the merge.

How it works
------------
`ow-patches/.upstream-symbols.json` records, per patch and symbol, a hash of the
upstream method body (or signature, for `decorate` patches whose composer calls
upstream positionally) as it was at the last `check_upstream.py
--update-baseline` — i.e. as of the last time somebody verified the patch. This
test recomputes the hash from the WORKING TREE and fails on mismatch. After an
upstream merge that touches a replaced method, CI stays red until the patch is
audited and the baseline refreshed, which is the order the reconcile skill
prescribes anyway.

Docstrings and comments do not count; any code change does.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OW = _REPO_ROOT / "ow-patches"
_SYMBOLS = _OW / ".upstream-symbols.json"


def _load_symbol_hash() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_ow_symbol_hash", _OW / "symbol_hash.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cases() -> list[tuple[str, str, str, str, str]]:
    if not _SYMBOLS.exists():
        return []
    data = json.loads(_SYMBOLS.read_text())
    out = []
    for patch_id, syms in data.items():
        if patch_id.startswith("_"):
            continue
        for sym, info in syms.items():
            if info.get("file") and info.get("hash"):
                out.append((patch_id, sym, info["file"], info["hash"], info.get("kind", "body")))
    return out


_CASES = _cases()


def test_symbol_registry_exists() -> None:
    assert _SYMBOLS.exists(), (
        f"{_SYMBOLS} is missing. Run `python3 ow-patches/check_upstream.py --update-baseline` "
        "after verifying every patch against upstream; the shadow-drift guard has nothing to compare otherwise."
    )
    assert _CASES, "no symbol hashes recorded — every active wholesale/decorate patch should have at least one"


@pytest.mark.parametrize(
    ("patch_id", "symbol", "upstream_file", "recorded", "kind"),
    _CASES,
    ids=[f"{c[0]}::{c[1]}" for c in _CASES],
)
def test_upstream_method_unchanged_since_patch_was_verified(
    patch_id: str, symbol: str, upstream_file: str, recorded: str, kind: str
) -> None:
    sh = _load_symbol_hash()
    path = _REPO_ROOT / upstream_file
    assert path.exists(), f"{upstream_file} (target of {patch_id}) is gone from the tree"
    current = sh.hash_symbols_in_source(path.read_text(), [symbol], signature_only=(kind == "signature"))[symbol]
    assert current is not None, f"{symbol} no longer exists in {upstream_file}; the patch {patch_id} mis-applies"
    assert current == recorded, (
        f"Upstream's {kind} of {upstream_file}::{symbol} has changed since ow-patch {patch_id} was last "
        f"verified (recorded {recorded}, now {current}). The patch is SHADOWING that change.\n"
        f"Audit: python3 ow-patches/check_upstream.py --explain {patch_id}\n"
        f"Then rebase or retire the patch (ow-patches/PATCHES.md) and, only after that, refresh with\n"
        f"  python3 ow-patches/check_upstream.py --update-baseline"
    )
