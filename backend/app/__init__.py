import importlib.util
import os
import sys
import traceback
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("open-wearables")
except PackageNotFoundError:  # package not installed (e.g. running from a bare checkout)
    __version__ = "unknown"

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

    Set OW_PATCHES_REQUIRED=1 to make a missing ow-patches directory a hard
    startup failure instead of a silent skip. Deployments of THIS fork should
    always set it: ow-patches lives at the repo root, outside the ./backend
    build context, so it is trivially easy to ship an image without it — and
    without this guard every fork patch silently no-ops with no log line and a
    perfectly healthy-looking boot. That is exactly what happened on the
    homelab k8s cluster (2026-08-20): all 14 patches inert for weeks because
    the deployment had no ow-patches mount and nothing complained.
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
        if os.environ.get("OW_PATCHES_REQUIRED", "").strip().lower() in ("1", "true", "yes"):
            searched = "\n  ".join(str(c) for c in candidates)
            raise RuntimeError(
                "OW_PATCHES_REQUIRED is set but ow-patches/apply.py was not found. "
                "Every fork patch would silently no-op. Searched:\n  " + searched + "\n"
                "Fix: build the image via Dockerfile.ow-patches (which copies ow-patches "
                "to /root_project/ow-patches), or set OW_PATCHES_DIR / mount the directory."
            )
        # Pure upstream checkout, or a deliberately un-patched run.
        print("ow-patches: directory not found; running unpatched (upstream behaviour)", file=sys.stderr)
        return

    spec = importlib.util.spec_from_file_location("_ow_patches_apply", apply_path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ow_patches_apply"] = module
    spec.loader.exec_module(module)
    module.apply_patches()


_apply_ow_patches()
