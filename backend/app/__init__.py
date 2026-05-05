import importlib.util
import os
import sys
import traceback
from pathlib import Path

try:
    from app.models import *  # noqa: F403
except ImportError:
    traceback.print_exc()
    raise


def _apply_ow_patches() -> None:
    """Load ow-patches/apply.py and apply enabled patches.

    See ow-patches/PATCHES.md for the registry. Disabling a patch (set its
    PATCHES_ENABLED entry to False) reverts that fix to upstream behavior with
    no source-file edits.

    Discovery candidates, in order:
      1. $OW_PATCHES_DIR (explicit override)
      2. Sibling of app/ — `<__file__>/../../ow-patches`  (works inside
         the Docker image layout where the app sits at /root_project/app)
      3. Repo root — `<__file__>/../../../ow-patches`  (works on the host
         where backend/app/ is two dirs deep)
      4. A pre-loaded sys.modules['_ow_patches_apply'] (e.g. an A/B test
         harness that pre-loads with PATCHES_ENABLED already mutated)
    """
    pre_loaded = sys.modules.get("_ow_patches_apply")
    if pre_loaded is not None:
        pre_loaded.apply_patches()
        return

    candidates: list[Path] = []
    env_dir = os.environ.get("OW_PATCHES_DIR")
    if env_dir:
        candidates.append(Path(env_dir) / "apply.py")
    base = Path(__file__).resolve()
    candidates.append(base.parents[1] / "ow-patches" / "apply.py")
    candidates.append(base.parents[2] / "ow-patches" / "apply.py")

    apply_path = next((c for c in candidates if c.exists()), None)
    if apply_path is None:
        return  # Fork patches not present (e.g. running against pure upstream checkout)

    spec = importlib.util.spec_from_file_location("_ow_patches_apply", apply_path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ow_patches_apply"] = module
    spec.loader.exec_module(module)
    module.apply_patches()


_apply_ow_patches()
