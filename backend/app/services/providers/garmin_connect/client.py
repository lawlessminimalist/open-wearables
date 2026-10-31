"""Garmin Connect Personal API client: one per user, authenticated by that user's own key.

Garmin's Personal API serves the same Connect services the fork used to reach through the
unofficial `garminconnect` login, but with an official per-user key. Each user creates a
key in Garmin Connect; it is exchanged for a 24-hour bearer token:

    POST {BASE}/personalapitoken-service/personal-api/personal-api-key/token
    Authorization: ApiKey <key>          -> the bearer JWT as the response body

There is no refresh token: an expired bearer is replaced by exchanging the key again. Both
the key and the bearer are stored Fernet-encrypted on the user's `garmin_connect`
UserConnection (see `credentials.py`).

The sync handlers read their client through `current()`, which `bound(db, user_id)`
sets for the duration of one user's sync. That keeps a single strategy instance serving
every user without one user's client leaking into another's run.

Endpoint coverage was probed on 2026-10-31 (TODO.md, "Garmin Personal API"). Heart rate,
daily stats, HRV, the activity search and activity details use the paths garminconnect
used. Sleep uses Garmin's documented `dailySleep`, adapted to the old response shape.
VO2max, intraday stress and body composition return 404 and are not fetched.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx

from app.database import DbSession
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)

_PROVIDER = "garmin_connect"
BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
_TIMEOUT = httpx.Timeout(30.0)
# Re-exchange a bearer this long before its expiry so a sync never starts on one about to lapse.
_BEARER_MARGIN = timedelta(hours=1)
_ACTIVITY_PAGE = 100


class GarminConnectClientError(Exception):
    """A failure that will still be true for the next request in this sync (auth, missing key)."""


class GarminConnectRateLimitError(GarminConnectClientError):
    """Garmin answered 429, or the user is still cooling down from one: stop, do not retry.

    `retry_after_seconds` is how long the user's cooldown has left. The sync task reads it
    to re-queue a historical chunk for after the cooldown instead of losing the window.
    """

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


# Garmin publishes no Personal API rate limits and sends no Retry-After. A first historical
# sync at full speed (12 chunks of 30 days, ~4 calls per day) was throttled within minutes on
# 2026-10-31, so every key is paced to one request per interval.
MIN_REQUEST_INTERVAL_SECONDS = 1.0
# On a 429, wait and retry with these delays (about 7.5 minutes in all) before giving up.
BACKOFF_SECONDS: tuple[int, ...] = (30, 60, 120, 240)
_last_request_at: dict[int, float] = {}


class _Throttled(Exception):  # noqa: N818 - internal signal, never escapes the client
    """One 429 from Garmin; the backoff loop in GarminConnectClient._get decides what next."""


def _sleep(seconds: float) -> None:
    """Module-level so tests can skip the waits."""
    import time  # noqa: PLC0415

    time.sleep(seconds)


def _pace(api_key: str) -> None:
    """Sleep until MIN_REQUEST_INTERVAL_SECONDS has passed since this key's last request."""
    import time  # noqa: PLC0415

    slot = hash(api_key)
    wait = _last_request_at.get(slot, 0.0) + MIN_REQUEST_INTERVAL_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at[slot] = time.monotonic()


def exchange_api_key(api_key: str) -> tuple[str, datetime]:
    """Trade a Personal API key for a bearer token and its expiry. Raises GarminConnectClientError."""
    _pace(api_key)
    try:
        response = httpx.post(
            BASE_URL + TOKEN_PATH,
            headers={"Accept": "*/*", "Authorization": f"ApiKey {api_key}"},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GarminConnectClientError(f"Could not reach Garmin to exchange the API key: {exc}") from exc
    if response.status_code == 429:
        raise GarminConnectRateLimitError("Garmin rate-limited the API key exchange")
    if response.status_code != 200 or not response.text.strip():
        raise GarminConnectClientError(
            f"Garmin rejected the Personal API key (HTTP {response.status_code}); create a new key in Garmin Connect"
        )
    bearer = response.text.strip()
    return bearer, _jwt_expiry(bearer)


def _jwt_expiry(token: str) -> datetime:
    """The bearer's `exp` claim, read without verification (Garmin verifies it); 23h if unreadable."""
    import base64  # noqa: PLC0415
    import json  # noqa: PLC0415

    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except (IndexError, KeyError, ValueError, TypeError):
        return datetime.now(timezone.utc) + timedelta(hours=23)


class GarminConnectClient:
    """Read-only access to one user's Garmin Connect data through the Personal API."""

    def __init__(
        self,
        api_key: str,
        bearer: str | None = None,
        bearer_expires_at: datetime | None = None,
        device_model: str | None = None,
        on_new_bearer: Any = None,
        on_key_rejected: Any = None,
        on_rate_limited: Any = None,
    ) -> None:
        self._api_key = api_key
        self._bearer = bearer
        self._bearer_expires_at = bearer_expires_at
        self._device_model = device_model
        self._device_model_resolved = device_model is not None
        # Called with (bearer, expires_at) after a re-exchange so the caller can persist it.
        self._on_new_bearer = on_new_bearer
        # Called when Garmin rejects the key itself, so the connection can be marked expired.
        self._on_key_rejected = on_key_rejected
        # Called on a 429; returns the cooldown in seconds it recorded for this user.
        self._on_rate_limited = on_rate_limited

    # -- auth -----------------------------------------------------------------
    def _ensure_bearer(self, force: bool = False) -> str:
        fresh = (
            self._bearer is not None
            and self._bearer_expires_at is not None
            and self._bearer_expires_at - _BEARER_MARGIN > datetime.now(timezone.utc)
        )
        if force or not fresh:
            try:
                self._bearer, self._bearer_expires_at = exchange_api_key(self._api_key)
            except GarminConnectRateLimitError as exc:
                raise _Throttled(str(exc)) from exc
            except GarminConnectClientError:
                if self._on_key_rejected is not None:
                    self._on_key_rejected()
                raise
            if self._on_new_bearer is not None:
                self._on_new_bearer(self._bearer, self._bearer_expires_at)
        assert self._bearer is not None
        return self._bearer

    def _rate_limited(self, message: str) -> GarminConnectRateLimitError:
        """Record the user's cooldown (if a recorder is wired) and build the error carrying it."""
        retry_after = self._on_rate_limited() if self._on_rate_limited is not None else None
        return GarminConnectRateLimitError(message, retry_after_seconds=retry_after)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a Connect path as JSON, backing off exponentially on 429.

        After the last delay in BACKOFF_SECONDS the user's cooldown is recorded and
        GarminConnectRateLimitError raised, so the rest of the sync stops instead of
        waiting through the same cycle for every remaining request.
        """
        for delay in (*BACKOFF_SECONDS, None):
            try:
                return self._get_once(path, params)
            except _Throttled:
                if delay is None:
                    raise self._rate_limited(f"Garmin rate-limited {path}") from None
                log_structured(
                    logger, "warning", "Garmin rate-limited; backing off", provider=_PROVIDER, path=path, wait_s=delay
                )
                _sleep(delay)
        return None

    def _get_once(self, path: str, params: dict[str, Any] | None) -> Any:
        """One GET (two if a 401 forces a re-exchange). 404 and 204 read as empty; 429 is _Throttled."""
        for attempt in (1, 2):
            bearer = self._ensure_bearer(attempt == 2)
            _pace(self._api_key)
            try:
                response = httpx.get(
                    BASE_URL + path,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {bearer}",
                    },
                    timeout=_TIMEOUT,
                )
            except httpx.HTTPError as exc:
                log_structured(
                    logger, "warning", "Garmin request failed", provider=_PROVIDER, path=path, error=str(exc)
                )
                return None
            if response.status_code == 401 and attempt == 1:
                continue
            if response.status_code in (401, 403):
                raise GarminConnectClientError(
                    f"Garmin refused the bearer token (HTTP {response.status_code}); the API key may be revoked"
                )
            if response.status_code == 429:
                raise _Throttled(path)
            if response.status_code in (204, 404) or not response.content:
                return None
            if response.status_code >= 400:
                log_structured(
                    logger,
                    "warning",
                    "Garmin request failed",
                    provider=_PROVIDER,
                    path=path,
                    status=response.status_code,
                )
                return None
            try:
                return response.json()
            except ValueError:
                return None
        return None

    # -- data -----------------------------------------------------------------
    def get_activities_by_date(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """Activity summaries between start_date and end_date (inclusive), all pages."""
        activities: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self._get(
                "/activitylist-service/activities/search/activities",
                {
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                    "start": start,
                    "limit": _ACTIVITY_PAGE,
                },
            )
            if not isinstance(page, list) or not page:
                return activities
            activities.extend(page)
            if len(page) < _ACTIVITY_PAGE:
                return activities
            start += _ACTIVITY_PAGE

    def get_sleep_data(self, cdate: date) -> dict[str, Any]:
        """Sleep for one date, in the garminconnect `dailySleepData` shape the handler reads.

        `dailySleep` returns the DTO flat; it is wrapped as `dailySleepDTO`. The stage
        timeline (`sleepLevels`) and the SpO2/respiration epoch lists are passed through
        when present; whether the Personal API returns them is unverified.
        """
        dto = self._get("/sleep-service/sleep/dailySleep", {"date": cdate.isoformat()})
        if not isinstance(dto, dict) or not dto:
            return {}
        if "dailySleepDTO" in dto:
            return dto
        wrapped: dict[str, Any] = {"dailySleepDTO": dto}
        for key in ("sleepLevels", "wellnessEpochSPO2DataDTOList", "wellnessEpochRespirationDataDTOList"):
            if key in dto:
                wrapped[key] = dto[key]
        return wrapped

    def get_heart_rates(self, cdate: date) -> dict[str, Any]:
        result = self._get("/wellness-service/wellness/dailyHeartRate", {"date": cdate.isoformat()})
        return result if isinstance(result, dict) else {}

    def get_stats(self, cdate: date) -> dict[str, Any]:
        # The trailing slash is where garminconnect put the display name; the Personal API
        # resolves the user from the token (probe 2026-10-31).
        result = self._get("/usersummary-service/usersummary/daily/", {"calendarDate": cdate.isoformat()})
        return result if isinstance(result, dict) else {}

    def get_stress_data(self, _cdate: date) -> dict[str, Any]:
        """Intraday stress is not served by the Personal API (dailyStress 404)."""
        return {}

    def get_body_composition(self, _start_date: date, _end_date: date) -> dict[str, Any]:
        """Not fetched: `weight/dateRange` 404s, and Withings owns weight in this deployment."""
        return {}

    def get_max_metrics(self, _cdate: date) -> list[dict[str, Any]]:
        """VO2max is not served by the Personal API (maxmet 404)."""
        return []

    def get_hrv_data(self, cdate: date) -> dict[str, Any]:
        result = self._get(f"/hrv-service/hrv/{cdate.isoformat()}")
        return result if isinstance(result, dict) else {}

    def get_activity_details(self, activity_id: int | str, maxchart: int = 10000) -> dict[str, Any]:
        """Per-sample metrics for one activity: metricDescriptors + activityDetailMetrics."""
        result = self._get(f"/activity-service/activity/{activity_id}/details", {"maxChartSize": maxchart})
        return result if isinstance(result, dict) else {}

    def get_last_used_device_model(self) -> str | None:
        """The device model for 24/7 rows, resolved once per client.

        `mylastused` 404s on the Personal API. `for_user` passes the model already on the
        user's garmin_connect data source, so history stays on one source; a new user
        falls back to the device of their latest activity in the past 30 days.
        """
        if self._device_model_resolved:
            return self._device_model
        self._device_model_resolved = True
        today = datetime.now(timezone.utc).date()
        for activity in reversed(self.get_activities_by_date(today - timedelta(days=30), today)):
            name = activity.get("deviceName") or (activity.get("metadataDTO") or {}).get("deviceName")
            if name:
                self._device_model = str(name)
                break
        return self._device_model

    def iter_dates(self, start_date: date, end_date: date) -> list[date]:
        """Calendar dates from start_date through end_date."""
        return [start_date + timedelta(days=n) for n in range((end_date - start_date).days + 1)]


# -- per-user binding ------------------------------------------------------------------
_current: ContextVar[GarminConnectClient | None] = ContextVar("garmin_connect_client", default=None)


def current() -> GarminConnectClient:
    client = _current.get()
    if client is None:
        raise GarminConnectClientError("No Garmin client is bound; sync entry points must use bound(db, user_id)")
    return client


def for_user(db: DbSession, user_id: UUID) -> GarminConnectClient:
    """Build the client for one user from their garmin_connect connection."""
    from app.services.providers.garmin_connect import credentials  # noqa: PLC0415

    return credentials.client_for(db, user_id)


@contextmanager
def bound(db: DbSession, user_id: UUID) -> Generator[GarminConnectClient, None, None]:
    """Bind this user's client for the duration of a sync."""
    client = for_user(db, user_id)
    token = _current.set(client)
    try:
        yield client
    finally:
        _current.reset(token)
