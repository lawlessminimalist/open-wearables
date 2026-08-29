"""Garmin Connect API client wrapper using python-garminconnect."""

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)

_PROVIDER = "garmin_connect"


class GarminConnectClientError(Exception):
    """Raised when the Garmin Connect client cannot be used."""


class GarminConnectClient:
    """Thin wrapper around garminconnect.Garmin.

    Handles:
    - Login with email/password from settings
    - Token persistence to disk so re-auth is not needed on every request
    - Automatic re-authentication when the session expires
    """

    def __init__(self) -> None:
        self._api: Any = None  # garminconnect.Garmin instance
        self._device_model: str | None = None
        self._device_model_cached: bool = False

    def _get_credentials(self) -> tuple[str, str]:
        email = settings.garmin_connect_email
        password = settings.garmin_connect_password

        if not email or not password:
            raise GarminConnectClientError(
                "GARMIN_CONNECT_EMAIL and GARMIN_CONNECT_PASSWORD must be set in environment"
            )

        secret = password.get_secret_value() if hasattr(password, "get_secret_value") else str(password)
        return email, secret

    def _token_store_path(self) -> Path:
        raw = settings.garmin_connect_token_store or "/tmp/garminconnect_tokens"
        return Path(raw)

    def _build_api(self) -> Any:
        try:
            import garminconnect  # noqa: PLC0415
        except ImportError as exc:
            raise GarminConnectClientError(
                "garminconnect package is not installed. Add it to pyproject.toml dependencies."
            ) from exc
        email, password = self._get_credentials()
        return garminconnect.Garmin(email, password)

    def _hydrate_profile(self, api: Any) -> bool:
        """Populate api.display_name after restoring a session from disk.

        `garminconnect` sets display_name only inside login(), from
        GET /userprofile-service/socialProfile. Restoring a token with
        client.load() authenticates the session but leaves display_name None —
        and several endpoints interpolate it straight into their URL:

            get_stats            -> raises "Display name is not set"
            get_heart_rates      -> builds ".../None" and Garmin answers 403

        So a session-restored client silently loses daily stats and heart rate.
        This did not surface until the token store was moved onto a PVC: before
        that /tmp was wiped on every pod restart, so every sync did a full login
        and display_name was always set as a side effect.

        One lightweight authenticated request, and far cheaper than the login it
        replaces — login walks up to five strategies against the endpoint Garmin
        rate-limits. Returns False so the caller falls back to a full login,
        which sets display_name itself.
        """
        try:
            prof = api.client.connectapi("/userprofile-service/socialProfile")
        except Exception as exc:
            log_structured(
                logger,
                "warning",
                "Could not hydrate Garmin profile from restored session; re-authenticating",
                provider=_PROVIDER,
                error=str(exc),
            )
            return False

        if not isinstance(prof, dict) or not prof.get("displayName"):
            return False

        api.display_name = prof["displayName"]
        api.full_name = prof.get("fullName", "")
        return True

    def _try_load_saved_session(self, api: Any) -> bool:
        """Return True if we successfully loaded a saved token."""
        token_path = self._token_store_path()
        if not token_path.exists():
            return False
        try:
            api.client.load(str(token_path))
            if not api.client.is_authenticated:
                return False
            # An authenticated session is not a usable one until display_name is
            # set — see _hydrate_profile.
            if not self._hydrate_profile(api):
                return False
            log_structured(logger, "info", "Loaded saved Garmin Connect session", provider=_PROVIDER)
            return True
        except Exception as exc:
            log_structured(
                logger,
                "warning",
                "Saved Garmin Connect session is invalid, will re-authenticate",
                provider=_PROVIDER,
                error=str(exc),
            )
            return False

    def _login(self, api: Any) -> None:
        try:
            api.login()
            token_path = self._token_store_path()
            token_path.mkdir(parents=True, exist_ok=True)
            api.client.dump(str(token_path))
            log_structured(logger, "info", "Garmin Connect login successful, session saved", provider=_PROVIDER)
        except Exception as exc:
            raise GarminConnectClientError(f"Garmin Connect authentication failed: {exc}") from exc

    def _get_api(self) -> Any:
        """Return an authenticated garminconnect.Garmin instance, logging in if necessary."""
        if self._api is not None:
            return self._api

        api = self._build_api()
        if not self._try_load_saved_session(api):
            self._login(api)
        self._api = api
        return self._api

    def _call_with_reauth(self, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call a garminconnect method, re-authenticating once on session expiry."""
        api = self._get_api()
        try:
            return getattr(api, fn_name)(*args, **kwargs)
        except Exception as first_exc:
            err_str = str(first_exc).lower()
            is_auth_error = any(
                keyword in err_str for keyword in ("token", "auth", "401", "403", "expired", "session", "login")
            )
            if not is_auth_error:
                raise

            log_structured(
                logger,
                "warning",
                "Garmin Connect session expired, re-authenticating",
                provider=_PROVIDER,
                error=str(first_exc),
            )
            self._api = None
            api = self._build_api()
            self._login(api)
            self._api = api
            return getattr(api, fn_name)(*args, **kwargs)

    # -------------------------------------------------------------------------
    # Data access methods
    # -------------------------------------------------------------------------

    def get_activities_by_date(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """Return activity summaries between start_date and end_date (inclusive)."""
        result = self._call_with_reauth(
            "get_activities_by_date",
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )
        return result if isinstance(result, list) else []

    def get_sleep_data(self, cdate: date) -> dict[str, Any]:
        """Return sleep data for a single calendar date."""
        result = self._call_with_reauth("get_sleep_data", cdate.strftime("%Y-%m-%d"))
        return result if isinstance(result, dict) else {}

    def get_heart_rates(self, cdate: date) -> dict[str, Any]:
        """Return heart rate data for a single calendar date."""
        result = self._call_with_reauth("get_heart_rates", cdate.strftime("%Y-%m-%d"))
        return result if isinstance(result, dict) else {}

    def get_stats(self, cdate: date) -> dict[str, Any]:
        """Return daily stats (steps, calories, stress, etc.) for a calendar date."""
        result = self._call_with_reauth("get_stats", cdate.strftime("%Y-%m-%d"))
        return result if isinstance(result, dict) else {}

    def get_stress_data(self, cdate: date) -> dict[str, Any]:
        """Return time-series stress data for a calendar date."""
        result = self._call_with_reauth("get_stress_data", cdate.strftime("%Y-%m-%d"))
        return result if isinstance(result, dict) else {}

    def get_body_composition(self, start_date: date, end_date: date) -> dict[str, Any]:
        """Return body composition data for a date range."""
        result = self._call_with_reauth(
            "get_body_composition",
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )
        return result if isinstance(result, dict) else {}

    def get_max_metrics(self, cdate: date) -> list[dict[str, Any]]:
        """Return max-metrics (VO2max / fitness age) for a single calendar date.

        Garmin only writes this on days with a qualifying activity, so most
        dates return an empty list. Callers should fetch it once per sync range
        rather than per-day — see GarminConnect247Data.save_vo2max_for_range.
        """
        result = self._call_with_reauth("get_max_metrics", cdate.strftime("%Y-%m-%d"))
        return result if isinstance(result, list) else []

    def get_hrv_data(self, cdate: date) -> dict[str, Any]:
        """Return HRV status data for a calendar date."""
        result = self._call_with_reauth("get_hrv_data", cdate.strftime("%Y-%m-%d"))
        return result if isinstance(result, dict) else {}

    def get_last_used_device_model(self) -> str | None:
        """Return the display name of the most recently used Garmin device.

        Cached on the instance: the strategy shares one client across the
        workouts and 24/7 handlers, so a whole sync run costs a single extra
        request rather than one per date. That distinction matters — the per-day
        loop is what got this provider IP rate-limited.

        The 24/7 endpoints do not report which device produced the data, so
        without this every 24/7 record persisted device_model=None, leaving
        device_type UNKNOWN and the UI rendering its "unknown device" fallback
        on sleep, HRV and body-metrics rows (workouts were unaffected because
        activities carry deviceName directly).
        """
        if self._device_model_cached:
            return self._device_model
        self._device_model_cached = True
        try:
            info = self._call_with_reauth("get_device_last_used")
        except Exception as exc:
            log_structured(
                logger,
                "warning",
                "Could not resolve Garmin device; 24/7 records will have no device_model",
                provider=_PROVIDER,
                error=str(exc),
            )
            return None
        if not isinstance(info, dict):
            return None
        # Garmin has used both keys across firmware/API generations.
        self._device_model = info.get("lastUsedDeviceName") or info.get("displayName") or None
        return self._device_model

    def iter_dates(self, start_date: date, end_date: date) -> list[date]:
        """Return list of calendar dates from start_date through end_date."""
        dates: list[date] = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates
