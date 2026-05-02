"""Read-only MCP server for Open Wearables health data."""

import os
from typing import Any

import httpx
from fastmcp import FastMCP

API_URL = os.environ.get("OPEN_WEARABLES_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("OPEN_WEARABLES_API_KEY", "")

mcp = FastMCP(
    "Open Wearables",
    instructions=(
        "Read-only access to health data stored in Open Wearables. "
        "All data is returned from the configured Open Wearables instance. "
        "Dates should be ISO 8601 strings (e.g. '2026-01-01' or '2026-01-01T00:00:00Z'). "
        "Series types for timeseries include: heart_rate, resting_heart_rate, "
        "heart_rate_variability_sdnn, heart_rate_variability_rmssd, steps, weight, "
        "vo2_max, garmin_stress_level, garmin_body_battery, oxygen_saturation, "
        "body_fat_percentage, energy, basal_energy, and many more."
    ),
)


def _client() -> httpx.Client:
    headers = {"X-Open-Wearables-API-Key": API_KEY} if API_KEY else {}
    return httpx.Client(base_url=API_URL, headers=headers, timeout=30.0)


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    with _client() as client:
        r = client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        r.raise_for_status()
        return r.json()


@mcp.tool
def list_users(
    limit: int = 20,
    page: int = 1,
    search: str | None = None,
) -> dict:
    """List all users registered in Open Wearables.

    Args:
        limit: Number of users per page (default 20, max 100).
        page: Page number (1-indexed).
        search: Optional search string to filter users by name or email.
    """
    return _get("/api/v1/users", {"limit": limit, "page": page, "search": search})


@mcp.tool
def get_user(user_id: str) -> dict:
    """Get details for a specific user.

    Args:
        user_id: UUID of the user.
    """
    return _get(f"/api/v1/users/{user_id}")


@mcp.tool
def get_sleep_summary(
    user_id: str,
    start_date: str,
    end_date: str,
    limit: int = 50,
    sort_order: str = "asc",
) -> dict:
    """Get daily sleep metrics for a user (provider-priority-filtered).

    Returns one record per calendar day, selecting the highest-priority provider
    when multiple sources recorded sleep on the same night.

    Args:
        user_id: UUID of the user.
        start_date: Start date as ISO 8601 string (e.g. '2026-01-01').
        end_date: End date as ISO 8601 string (e.g. '2026-01-31').
        limit: Max records to return (1–100, default 50).
        sort_order: 'asc' or 'desc' by date (default 'asc').
    """
    return _get(
        f"/api/v1/users/{user_id}/summaries/sleep",
        {"start_date": start_date, "end_date": end_date, "limit": limit, "sort_order": sort_order},
    )


@mcp.tool
def get_activity_summary(
    user_id: str,
    start_date: str,
    end_date: str,
    limit: int = 50,
    sort_order: str = "asc",
) -> dict:
    """Get daily activity metrics for a user (steps, energy, heart rate, etc.).

    Args:
        user_id: UUID of the user.
        start_date: Start date as ISO 8601 string.
        end_date: End date as ISO 8601 string.
        limit: Max records to return (1–400, default 50).
        sort_order: 'asc' or 'desc' by date (default 'asc').
    """
    return _get(
        f"/api/v1/users/{user_id}/summaries/activity",
        {"start_date": start_date, "end_date": end_date, "limit": limit, "sort_order": sort_order},
    )


@mcp.tool
def get_body_summary(
    user_id: str,
    average_period_days: int = 7,
) -> dict:
    """Get current body metrics for a user (weight, BMI, body fat, VO2 max, etc.).

    Returns the latest values averaged over the specified period.

    Args:
        user_id: UUID of the user.
        average_period_days: Days to average vitals over (1–7, default 7).
    """
    return _get(
        f"/api/v1/users/{user_id}/summaries/body",
        {"average_period": average_period_days},
    )


@mcp.tool
def get_sleep_events(
    user_id: str,
    start_date: str,
    end_date: str,
    limit: int = 50,
) -> dict:
    """Get raw sleep sessions for a user (all providers, including duplicates).

    Unlike get_sleep_summary, this returns all recorded sessions without
    deduplication — useful for comparing providers or inspecting raw data.

    Args:
        user_id: UUID of the user.
        start_date: Start date as ISO 8601 string.
        end_date: End date as ISO 8601 string.
        limit: Max records to return (1–100, default 50).
    """
    return _get(
        f"/api/v1/users/{user_id}/events/sleep",
        {"start_date": start_date, "end_date": end_date, "limit": limit},
    )


@mcp.tool
def get_workouts(
    user_id: str,
    start_date: str,
    end_date: str,
    workout_type: str | None = None,
    limit: int = 50,
) -> dict:
    """Get workout sessions for a user.

    Args:
        user_id: UUID of the user.
        start_date: Start date as ISO 8601 string.
        end_date: End date as ISO 8601 string.
        workout_type: Optional filter by type (e.g. 'RUNNING', 'CYCLING', 'SWIMMING').
        limit: Max records to return (1–100, default 50).
    """
    return _get(
        f"/api/v1/users/{user_id}/events/workouts",
        {"start_date": start_date, "end_date": end_date, "record_type": workout_type, "limit": limit},
    )


@mcp.tool
def get_timeseries(
    user_id: str,
    start_time: str,
    end_time: str,
    types: list[str] | None = None,
    resolution: str = "raw",
    limit: int = 50,
) -> dict:
    """Get granular time-series biometric or activity data for a user.

    Args:
        user_id: UUID of the user.
        start_time: Start time as ISO 8601 string (e.g. '2026-01-01T00:00:00Z').
        end_time: End time as ISO 8601 string.
        types: List of series types to include. Common values:
            heart_rate, resting_heart_rate, heart_rate_variability_sdnn,
            heart_rate_variability_rmssd, steps, weight, vo2_max,
            oxygen_saturation, body_fat_percentage, energy, basal_energy,
            garmin_stress_level, garmin_body_battery, skin_temperature,
            respiratory_rate, blood_glucose. Leave empty to get all types.
        resolution: Aggregation — 'raw', '1min', '5min', '15min', or '1hour'.
        limit: Max samples per page (1–100, default 50).
    """
    params: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "resolution": resolution,
        "limit": limit,
    }
    if types:
        params["types"] = types
    with _client() as client:
        r = client.get(
            f"/api/v1/users/{user_id}/timeseries",
            params=params,
        )
        r.raise_for_status()
        return r.json()


@mcp.tool
def get_health_scores(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    provider: str | None = None,
    limit: int = 50,
) -> dict:
    """Get health scores (sleep, recovery, readiness, etc.) for a user.

    Args:
        user_id: UUID of the user.
        start_date: Optional start date as ISO 8601 string.
        end_date: Optional end date as ISO 8601 string.
        category: Optional category filter (e.g. 'sleep', 'recovery', 'readiness').
        provider: Optional provider filter (e.g. 'garmin', 'ultrahuman', 'oura').
        limit: Max records to return (1–1000, default 50).
    """
    return _get(
        f"/api/v1/users/{user_id}/health-scores",
        {
            "start_date": start_date,
            "end_date": end_date,
            "category": category,
            "provider": provider,
            "limit": limit,
        },
    )


@mcp.tool
def list_user_connections(user_id: str) -> dict:
    """List the wearable provider connections for a user.

    Shows which providers (Garmin, Ultrahuman, Oura, etc.) are connected
    and when they were last synced.

    Args:
        user_id: UUID of the user.
    """
    connections = _get(f"/api/v1/users/{user_id}/connections")
    return {"connections": connections} if isinstance(connections, list) else connections



@mcp.tool
def list_tombstones(
    user_id: str,
    category: str | None = None,
) -> dict:
    """List soft-deleted event tombstones for a user.

    Tombstones represent records that were removed via the 'Remove' action.
    They prevent the record from being re-imported on future syncs. Delete a
    tombstone to re-enable sync import of that session.

    Args:
        user_id: UUID of the user.
        category: Optional filter — 'sleep' or 'workout'.
    """
    tombstones = _get(f"/api/v1/users/{user_id}/tombstones", {"category": category})
    return {"tombstones": tombstones} if isinstance(tombstones, list) else tombstones


@mcp.tool
def delete_tombstone(user_id: str, tombstone_id: str) -> dict:
    """Remove a tombstone, re-enabling sync import of the associated record.

    After removal the record will be re-imported on the next sync from its provider.

    Args:
        user_id: UUID of the user.
        tombstone_id: UUID of the tombstone to remove.
    """
    with _client() as client:
        r = client.delete(f"/api/v1/users/{user_id}/tombstones/{tombstone_id}")
        r.raise_for_status()
        return {"success": True, "tombstone_id": tombstone_id}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
