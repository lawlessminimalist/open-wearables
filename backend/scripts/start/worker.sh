#!/bin/bash
# FORK DIVERGENCE (ow-patches: celery-worker-signal-forwarding, structural).
# This script is PID 1 in the celery-worker container. The kernel ignores a
# SIGTERM whose disposition is the default for PID 1, and bash does not forward
# signals to children it is waiting on, so upstream's version never delivered the
# kubelet's SIGTERM to either celery process: every rollout ran the full
# terminationGracePeriodSeconds (600s) and ended in SIGKILL, leaving the platform
# without a worker for ten minutes under the Recreate strategy (observed
# 2026-09-12 22:10-22:20Z). Forward the signal to every process in the container
# and wait for both workers to finish their warm shutdown.
set -x

forward() {
  echo "worker.sh: forwarding $1 to celery workers"
  kill -s "$1" -- -1 2>/dev/null
}
trap 'forward TERM' TERM
trap 'forward INT' INT

echo "Starting I/O worker..."
uv run celery -A app.main:celery_app worker --loglevel=info --pool=threads -Q default,sdk_sync,garmin_sync,webhook_sync -n io@%h &

echo "Starting CPU worker..."
uv run celery -A app.main:celery_app worker --loglevel=info --pool=prefork --concurrency=2 -Q xml_sync -n cpu@%h &

# `wait` returns early when a trapped signal arrives; loop until every child is gone
# so the container exits only after both workers have drained.
while wait; [ -n "$(jobs -p)" ]; do :; done
