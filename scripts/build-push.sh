#!/usr/bin/env bash
#
# build-push.sh — cross-build the open-wearables images for the homelab and push
# them to the in-cluster registry, then roll the deployments.
#
# SCOPE: this is the FAST LOCAL ITERATION path. The canonical publish route is
# CI -> GHCR (.github/workflows/publish-ghcr.yml), which builds the same images
# as ghcr.io/<owner>/open-wearables-{platform,frontend}. Use this script when you
# want a change on the cluster without waiting for a push + Actions run.
#
# One command:
#
#   ./scripts/build-push.sh            # both images
#   ./scripts/build-push.sh backend    # just open-wearables-platform (app + celery)
#   ./scripts/build-push.sh frontend   # just open-wearables-frontend
#
# WHY each step is the way it is (single-node homelab specifics):
#   * The node is linux/amd64 but this Mac/podman is arm64 -> we MUST cross-build
#     with --platform=linux/amd64, or the pods die with "exec format error".
#   * The registry NodePort (192.168.1.118:30500) is firewalled off the LAN, so we
#     reach it via `kubectl port-forward`. podman runs inside a VM, so from the
#     build it reaches the Mac's forwarded port as host.containers.internal, NOT
#     localhost. A registry keys images by repo PATH, so pushing to
#     host.containers.internal:30500/<name> is the same object the node later pulls
#     as localhost:30500/<name>.
#   * The registry is plain HTTP -> podman push --tls-verify=false.
#   * Deploys use imagePullPolicy: Always + :latest, so a rollout restart re-pulls.
#
# Prereqs: `podman machine start` running, and a `kubectl` context pointing at the
# homelab cluster.
#
set -euo pipefail

# ---- config -----------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS=open-wearables
PF_PORT=30500                              # local port the forward binds
REG="host.containers.internal:${PF_PORT}"  # how the podman VM reaches the forward
PLATFORM=linux/amd64

# Build-time API URL. NOTE: this is only a fallback now — the frontend resolves
# VITE_API_URL at RUNTIME from the container env via window.__APP_CONFIG__ (see
# frontend/src/lib/api/runtime-config.ts), which is why CI can publish one generic
# image. Kept here so a locally-built image works even if the deployment env var
# is missing. Override via env if the tunnel hostname changes.
VITE_API_URL="${VITE_API_URL:-https://api-wearables.homelab-dhlaw.uk}"

BACKEND_DEPLOYS="deploy/app deploy/celery-beat deploy/celery-worker"
FRONTEND_DEPLOYS="deploy/frontend"
TARGET="${1:-all}"                         # all | backend | frontend

# ---- port-forward lifecycle (start if needed, stop only what we started) ----
PF_PID=""
cleanup(){ [ -n "$PF_PID" ] && kill "$PF_PID" 2>/dev/null || true; }
trap cleanup EXIT

ensure_forward(){
  if curl -sf -o /dev/null "http://localhost:${PF_PORT}/v2/" 2>/dev/null; then
    echo ">> registry already reachable on localhost:${PF_PORT}"; return
  fi
  echo ">> starting registry port-forward (localhost:${PF_PORT} -> svc/registry:5000)"
  kubectl port-forward -n registry svc/registry "${PF_PORT}:5000" >/tmp/ow-pf-registry.log 2>&1 &
  PF_PID=$!
  for _ in $(seq 1 30); do
    curl -sf -o /dev/null "http://localhost:${PF_PORT}/v2/" && { echo "   up."; return; }
    sleep 0.5
  done
  echo "!! port-forward never came up; see /tmp/ow-pf-registry.log"; exit 1
}

# ---- build + push (with a hard amd64 gate) ----------------------------------
build_push(){                              # name  context  [extra build args...]
  local name="$1" context="$2"; shift 2
  echo ">> building ${name}  (${PLATFORM})"
  podman build --platform="${PLATFORM}" "$@" -t "${REG}/${name}:latest" "${context}"
  local arch; arch="$(podman image inspect "${REG}/${name}:latest" --format '{{.Architecture}}')"
  [ "${arch}" = "amd64" ] || { echo "!! ${name} built ${arch}, expected amd64 — aborting"; exit 1; }
  echo ">> pushing ${name}"
  podman push --tls-verify=false "${REG}/${name}:latest"
}

# The backend needs a second layer: ow-patches lives at the repo root, outside the
# ./backend build context, so upstream's Dockerfile cannot COPY it. Skipping this
# ships an image where every fork patch silently no-ops. See Dockerfile.ow-patches.
build_push_backend(){
  local name="open-wearables-platform"
  local base="localhost/${name}-base:latest"
  echo ">> building ${name} base  (${PLATFORM})"
  podman build --platform="${PLATFORM}" -f backend/Dockerfile -t "${base}" ./backend
  echo ">> layering ow-patches onto ${name}"
  podman build --platform="${PLATFORM}" -f Dockerfile.ow-patches \
    --build-arg "BASE_IMAGE=${base}" -t "${REG}/${name}:latest" .
  local arch; arch="$(podman image inspect "${REG}/${name}:latest" --format '{{.Architecture}}')"
  [ "${arch}" = "amd64" ] || { echo "!! ${name} built ${arch}, expected amd64 — aborting"; exit 1; }
  # Fail here rather than discover it in prod three weeks later.
  podman run --rm --entrypoint sh "${REG}/${name}:latest" -c \
    'test -f /root_project/ow-patches/apply.py' \
    || { echo "!! ow-patches missing from ${name} image — aborting"; exit 1; }
  echo ">> pushing ${name}"
  podman push --tls-verify=false "${REG}/${name}:latest"
}

# ---- run --------------------------------------------------------------------
cd "${REPO_ROOT}"
ensure_forward

RESTART=""
if [ "${TARGET}" = "all" ] || [ "${TARGET}" = "backend" ]; then
  build_push_backend
  RESTART="${RESTART} ${BACKEND_DEPLOYS}"
fi
if [ "${TARGET}" = "all" ] || [ "${TARGET}" = "frontend" ]; then
  build_push open-wearables-frontend ./frontend --build-arg "VITE_API_URL=${VITE_API_URL}"
  RESTART="${RESTART} ${FRONTEND_DEPLOYS}"
fi

echo ">> rolling:${RESTART}"
kubectl rollout restart -n "${NS}" ${RESTART}
echo ">> registry catalog:"; curl -s "http://localhost:${PF_PORT}/v2/_catalog"; echo
echo ">> done — watch rollout with:  kubectl get pods -n ${NS} -w"
