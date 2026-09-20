
# Open Wearables

<div align="left">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-blue.svg)](https://github.com/the-momentum/open-wearables/issues)
![Built with: FastAPI + React + Tanstack](https://img.shields.io/badge/Built%20with-FastAPI%20%2B%20React%20%2B%20Tanstack-green.svg)
[![Discord](https://img.shields.io/badge/Discord-Join%20Chat-5865F2?logo=discord&logoColor=white)](https://discord.gg/qrcfFnNE6H)

<a href="https://www.producthunt.com/products/open-wearables?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-open-wearables-3" target="_blank" rel="noopener noreferrer"><img alt="Open Wearables - Open infrastructure for wearable-powered health products. | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1132023&theme=light&t=1777448243573"></a>

</div>

---

**Documentation**: https://openwearables.io/docs

---

> [!TIP]
> **Curious what we're cooking right now?** Take a look at our [roadmap](https://openwearables.io/docs/roadmap) ✨

Open-source platform that unifies wearable device data from multiple providers behind a single API and makes it available to AI. Build health applications faster with normalized health data, webhooks, and mobile SDKs, and connect LLMs and AI agents to your users' wearable data through the built-in MCP server.

## What It Does

Open Wearables provides a unified API and developer portal to connect and sync data from multiple wearable devices and fitness platforms. Instead of implementing separate integrations for each provider (e.g., Garmin, Whoop, Apple Health), you can use a single platform to access normalized health data.

<div align="center">
<img width="597" height="449" alt="image" src="https://github.com/user-attachments/assets/b626405d-99a3-4ff7-b044-442483a3edea" />
</div>

> [!IMPORTANT]
> **For Individuals**: This platform isn't just for developers - individuals can self-host it to take control of their own wearable data, stored on their own infrastructure. Connect your devices, explore your health metrics through the unified API, or chat with your data in Claude or Cursor via the MCP server.

## Why Use It

**For Developers building health apps:**
- 🔌 Integrate multiple wearable providers through one API instead of maintaining separate implementations
- 📊 Access normalized health data across different devices (heart rate, sleep, activity, steps, etc.)
- 🏠 Self-hosted solution - deploy on your own infrastructure with full data control
- 🚀 No third-party dependencies for core functionality - run it locally with `docker compose up`
- 🔔 Get notified via webhooks when new data arrives for your users
- 🤖 Give LLMs and AI agents access to wearable data through the built-in MCP server

**The Problem It Solves:**

Building a health app that supports multiple wearables typically requires:
- Significant development effort per provider (Garmin, Whoop, Apple Health, etc.) to implement OAuth flows, data mapping, and sync logic
- Managing different OAuth flows and APIs for each service
- Handling various data formats and units
- Maintaining multiple SDKs and dealing with API changes

Open Wearables handles this complexity so you can focus on building your product 🚀

## Use Cases

- 🤖 **AI Health Agents & Coaches**: Ground LLM answers in users' real sleep, activity, and workout data instead of generic advice
- 🏃 **Fitness Coaching Apps**: Connect user wearables to provide personalized training recommendations. Running coaches can create users and share connection links via WhatsApp
- 🏥 **Healthcare Platforms**: Aggregate patient health data from various devices and get notified via webhooks when new data arrives
- 💪 **Wellness Applications**: Track and analyze user activity across different wearables
- 🔬 **Research Projects**: Collect standardized health data from multiple sources
- 🧪 **Product Pilots**: Non-technical product owners can test platform functionality by sharing connection links with users without needing their own app
- 👤 **Personal Use**: Individuals can self-host the platform to connect their own wearables, keep the data on their own infrastructure, chat with it through the MCP server, and build any kind of automation on top of webhooks and the API (e.g. with n8n)

## Getting Started

Get Open Wearables up and running in minutes.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/the-momentum/open-wearables.git
   cd open-wearables
   ```

2. **Configure environment variables:**
   
   **Backend configuration:**
   ```bash
   cp ./backend/config/.env.example ./backend/config/.env
   ```
   
   **Frontend configuration:**
   ```bash
   cp ./frontend/.env.example ./frontend/.env
   ```

3. **Start the application**
   
   **Using Docker (Recommended):**
   
   The easiest way to get started is with Docker Compose:
   ```bash
   docker compose up -d
   ```
   
   For local development setup without Docker take a look at [docs](https://openwearables.io/docs/quickstart#local-development-setup)

   > **Production:** `docker compose up` builds from local source and is meant for development. For production, run the official [`themomentum/open-wearables-backend`](https://hub.docker.com/r/themomentum/open-wearables-backend) and [`themomentum/open-wearables-frontend`](https://hub.docker.com/r/themomentum/open-wearables-frontend) images pinned to a stable release tag (e.g. `0.7.0`), not `nightly` or a build of `main`. See [Deploying with Docker](https://openwearables.io/docs/deployment/docker).

4. **Log in to the developer portal:**

   An admin account is automatically created on startup using the `ADMIN_EMAIL` and `ADMIN_PASSWORD` environment variables (defaults: `admin@admin.com` / `your-secure-password`). The seed runs only while the developer table is empty: once any developer account exists it is skipped, so changing `ADMIN_PASSWORD` later does not update an existing account - **change the default password from the developer portal right after your first login**. To add further accounts, invite them from the developer portal.

   Open http://localhost:3000 to access the developer portal and create API keys.

5. **Seed sample data** (optional):
   If you want test users and sample activity data:
   ```bash
   make seed
   ```

   This will create:
   - Test users
   - Sample activity data for test users


6. **View API documentation:**

   Open http://localhost:8000/docs in your browser to explore the interactive Swagger UI.

## Core Features

### Provider Support
- **Cloud-based**: Garmin, Oura, Whoop, Suunto, Polar, Ultrahuman, Strava, Fitbit, Withings, Google Health
- **SDK-based**: Apple Health, Samsung Health, Google Health Connect
- **Apple Health XML import**: Upload a full Apple Health export, including large files via S3 multipart upload

See [supported providers](https://openwearables.io/docs/providers/supported) and [data coverage](https://openwearables.io/docs/providers/coverage) for details.

### AI Integration
Connect LLMs and AI agents to wearable data from any supported provider - Garmin, Oura, Whoop, Apple Health, and more - through one normalized data model.

- **MCP Server**: Built-in [Model Context Protocol](https://modelcontextprotocol.io) server that works with Claude Desktop, Cursor, and other MCP clients
- **Natural language queries**: Ask "How did John sleep last week?" or "Compare workouts of these two users" - the AI fetches the right data itself
- **Available data**: Users, activity summaries, sleep, workouts, time series (heart rate, HRV, SpO2, weight, and more), and menstrual cycles

More AI capabilities are on the way - see the [roadmap](https://openwearables.io/docs/roadmap).

### Unified Data Model & API
One REST API with consistent data regardless of the source device:
- **Daily summaries**: Activity, sleep, body, and recovery
- **Time series**: Heart rate, HRV, SpO2, weight, steps, and [many more data types](https://openwearables.io/docs/architecture/data-types)
- **Events**: Workouts and sleep sessions
- **Data priorities**: Decide which provider and device type wins when data from multiple sources overlaps
- **Multi-account sync**: One provider account can be linked to multiple user profiles

### Connections & Sync
- **OAuth flow management**: Generate a connection link or use the connect widget - users authenticate with their provider and data syncs automatically
- **Historical backfill**: Pull past data on first connection (within each provider's limits)
- **Sync status**: Live sync progress stream (SSE) plus sync run history via the API and the portal

### Webhooks
Register HTTPS endpoints to get notified when new data arrives for your users. Filter by event type or user, verify signatures, send test events, and inspect delivery attempts. See the [webhooks guide](https://openwearables.io/docs/api-reference/guides/webhooks).

### Mobile Sync SDKs
Native SDKs for push-based health data sync from on-device health stores:
- **[iOS SDK](https://github.com/the-momentum/open_wearables_ios_sdk)** (Swift) - Apple HealthKit
- **[Android SDK](https://github.com/the-momentum/open_wearables_android_sdk)** (Kotlin) - Samsung Health & Google Health Connect
- **[Flutter SDK](https://github.com/the-momentum/open_wearables_health_sdk)** (Dart) - Cross-platform Flutter wrapper around native SDKs
- **[React Native SDK](https://github.com/the-momentum/open-wearables-react-native-sdk)** (TypeScript) - Cross-platform React Native wrapper around native SDKs

### Developer Portal
Web-based dashboard for managing your deployment:
- **Dashboard**: Users and data points at a glance
- **Users**: Add users, view connected data sources, and explore their data with visualizations
- **Coverage & Syncs**: See which data types each provider delivers and monitor sync runs
- **Webhooks**: Manage endpoints and debug deliveries
- **Settings**: API keys and provider credentials, data priorities, data lifecycle (archival and retention), team invitations, and a seed data generator

## Architecture

Built with:
- 🐍 **Backend**: FastAPI (Python)
- ⚛️ **Frontend**: React + TanStack Start + TypeScript (Vite)
- 🗄️ **Database**: PostgreSQL + Redis
- ⚙️ **Task Queue**: Celery (background jobs for data syncing and processing)
- 🔐 **Authentication**: Self-contained (no external auth services required)
- 📡 **API Style**: RESTful with OpenAPI/Swagger documentation

The platform is designed for self-hosting, meaning each deployment serves a single organization. No multi-tenancy complexity.

## Join the Discord

Join our Discord community to connect with other developers, get help, share ideas, and stay updated on the latest developments:

[![Discord](https://img.shields.io/badge/Discord-Join%20Chat-5865F2?logo=discord&logoColor=white)](https://discord.gg/qrcfFnNE6H)

## Fork Patches

This repository is a fork of [the-momentum/open-wearables](https://github.com/the-momentum/open-wearables).
Local fixes are tracked under [`ow-patches/`](ow-patches/) so we keep a clean record
of where we diverge from upstream and can A/B compare upstream behavior against ours
without restoring code from git history.

**Layout:**

```
ow-patches/
├── PATCHES.md           # registry — one entry per patch, explains intent + retire condition
├── apply.py             # imports each patch and monkey-patches at import time
├── check_upstream.py    # `python ow-patches/check_upstream.py` — equivalence + drift checks
├── .upstream-baseline   # upstream/main SHA we last reconciled against (drift origin)
└── local/<patch_id>.py  # patched implementation, one file per patch_id
```

`apply.py` is invoked once from [`backend/app/__init__.py`](backend/app/__init__.py),
so every entry point (FastAPI app, Celery worker, migrations, pytest) ends up with
the same patches applied.

**Check whether upstream has caught up — or silently diverged:**

```bash
# one-time setup if not already configured
git remote add upstream https://github.com/the-momentum/open-wearables.git

python ow-patches/check_upstream.py
```

The script fetches `upstream/main` and runs **two independent checks** per patch:

1. **Equivalence (heuristic).** Greps `upstream/main` for the patch's
   `upstream_equivalent_check` marker (path-qualified with `path::pattern`). A hit
   *suggests* upstream shipped its own version. This is a weak signal — the marker
   is a string unique to *our* code, so it only fires when upstream happens to use
   the same token, and is blind to an upstream change that does the same thing
   differently.

2. **Drift (deterministic).** For every file a patch depends on (its `file:` field),
   runs `git log <baseline>..upstream/main -- <file>` to see whether upstream has
   *touched* it since we last reconciled (`.upstream-baseline`). This is the check
   that catches the dangerous blind spot the equivalence grep misses:

   > A monkey-patch that **wholesale-replaces** an upstream method produces **no git
   > merge conflict** (we never edit the upstream source file), so `git merge` is
   > silent. If upstream rewrites that method, our patch keeps shadowing it with a
   > stale copy — silently dropping upstream's improvements. This is exactly how
   > `avg_hrv_rmssd_ms` went null after upstream rewrote `get_sleep_summaries`.

   Drift is escalated by each patch's `replacement_kind` (see PATCHES.md): drift on
   a `wholesale-replace` patch is a **SHADOW RISK** (re-verify/rebase — the script
   exits non-zero); drift on a `decorate` patch is lower-risk (re-verify only). The
   drift check focuses on monkey-patch patches — `structural` source-edit patches
   surface upstream drift as ordinary git conflicts at merge time. The check is
   file-granular: it flags broadly, then you diff upstream's change against the patch
   to confirm whether your *specific* replaced symbol actually moved.

Nothing is auto-retired or auto-rebased — all changes are manual.

**Reconcile-and-merge workflow:**

```bash
python ow-patches/check_upstream.py          # 1. see what upstream touched since baseline
git merge upstream/main                       # 2. merge (resolve dep/lock conflicts, unify alembic heads)
# 3. for every SHADOW RISK / re-verify row: diff upstream's new body vs the patch,
#    then rebase the patch (decorate > wholesale-replace where possible) or retire it
cd backend && uv run pytest -q                # 4. full suite green with patches applied
python ow-patches/check_upstream.py --update-baseline   # 5. record the new reconciled SHA
```

Skipping step 5 is safe (the baseline just stays older and the next run re-flags the
same drift); never run it *before* steps 3–4, or you erase the signal.

**Disable a single patch (A/B test):**

Edit [`ow-patches/apply.py`](ow-patches/apply.py) and flip the relevant entry in
`PATCHES_ENABLED` to `False`. Restart the backend. That patch reverts to upstream
behavior; nothing else moves. Composed patches (e.g. `fix-hrv-nightly-aggregate`,
`fix-sleep-stages-missing`, and `fix-sleep-timezone` all live inside the same
`get_sleep_summaries` replacement) toggle independently — see `compose()` in
`apply.py`.

A small number of changes are **structural** and not toggleable from `apply.py`:
the `User.timezone` column + migration, and the `basal_calories_kcal` /
`timezone` / `start_time_local` / `end_time_local` fields added to response
schemas. Disabling the corresponding patch causes those fields to come back as
`null`, which is upstream-equivalent enough — see `PATCHES.md` for which patches
have a structural component.

**Container deployment.** `docker-compose.yml` bind-mounts `./ow-patches` into
the app, celery-worker, and celery-beat containers at `/root_project/ow-patches`
and exports `OW_PATCHES_DIR=/root_project/ow-patches`. `app/__init__.py` resolves
the patch directory in this order: `$OW_PATCHES_DIR` → sibling of `app/` (container
layout) → repo root (host layout). To pick up patch changes without rebuilding the
image, run `docker compose restart app celery-worker celery-beat` (or `docker compose
watch` for live reloads).

**Retire a patch when upstream covers it:**

1. Verify upstream's implementation matches: same provider scope, same field
   names, same units, nullability equal or stricter than ours.
2. In `ow-patches/PATCHES.md`, change `status:` from `local_only` /
   `upstream_candidate` to `retired`.
3. In `ow-patches/apply.py`, set the patch's `PATCHES_ENABLED` entry to `False`.
4. Keep `ow-patches/local/<patch_id>.py` for reference (don't delete) — it's
   our institutional memory of what we changed and why.
5. Re-run the test suite to confirm upstream covers the cases our patch did.

## Contributing

Contributions are welcome! This project aims to be a community-driven solution for wearable data integration.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on:
- 🛠️ Setting up the development environment
- 📝 Code style and testing requirements
- 🔀 Pull request process

## License

[MIT License](LICENSE) - Use it freely in commercial and open-source projects.

## Community

- 💬 [GitHub Discussions](https://github.com/the-momentum/open-wearables/discussions) - Questions and ideas

---

**Note**: This is an early-stage project under active development. APIs may change before version 1.0. In production, pin the official images to a specific release version (see [Deploying with Docker](https://openwearables.io/docs/deployment/docker)) and follow the changelog for updates.

---

The backend part of this project was generated from the [Python AI Kit](https://github.com/the-momentum/python-ai-kit).

Built with ❤️ by [Momentum](https://themomentum.ai/)
