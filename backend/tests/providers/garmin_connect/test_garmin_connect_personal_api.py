"""Garmin Connect through the Personal API: auth, per-user keys, linking, and the sync's abort rule.

Replaces the tests of the unofficial `garminconnect` login (rate-limit classification,
cooldowns, session restore), which went with that login on 2026-11-01.
"""

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import UserConnection
from app.schemas.auth.connection_status import ConnectionStatus
from app.services.providers.garmin_connect import client as garmin_client
from app.services.providers.garmin_connect import credentials
from app.services.providers.garmin_connect.client import (
    GarminConnectClient,
    GarminConnectClientError,
    GarminConnectRateLimitError,
)
from app.services.providers.garmin_connect.data_247 import GarminConnect247Data
from tests.factories import ApiKeyFactory, DataSourceFactory, EventRecordFactory, UserFactory
from tests.utils import api_key_headers


def _jwt(expires: datetime) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(expires.timestamp())}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class _Garmin:
    """Stands in for personal.connectapi.garmin.com: counts exchanges and serves one path."""

    def __init__(self, token_status: int = 200, get_statuses: list[int] | None = None) -> None:
        self.exchanges = 0
        self.token_status = token_status
        self.get_statuses = list(get_statuses or [200])
        self.bearers: list[str] = []

    def post(self, url: str, headers: dict[str, str], timeout: Any) -> httpx.Response:
        self.exchanges += 1
        assert url.endswith(garmin_client.TOKEN_PATH)
        assert headers["Authorization"].startswith("ApiKey ")
        body = _jwt(datetime.now(timezone.utc) + timedelta(hours=24)) if self.token_status == 200 else ""
        return httpx.Response(self.token_status, text=body)

    def get(self, url: str, params: Any, headers: dict[str, str], timeout: Any) -> httpx.Response:
        self.bearers.append(headers["Authorization"])
        status = self.get_statuses.pop(0) if len(self.get_statuses) > 1 else self.get_statuses[0]
        return httpx.Response(status, json={"restingHeartRate": 52} if status == 200 else {})


@pytest.fixture
def garmin(monkeypatch: pytest.MonkeyPatch) -> _Garmin:
    fake = _Garmin()
    monkeypatch.setattr(garmin_client.httpx, "post", fake.post)
    monkeypatch.setattr(garmin_client.httpx, "get", fake.get)
    # No real waiting: record the backoff delays instead, and turn pacing off.
    fake.slept = []  # type: ignore[attr-defined]
    monkeypatch.setattr(garmin_client, "_sleep", fake.slept.append)  # type: ignore[attr-defined]
    monkeypatch.setattr(garmin_client, "MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    return fake


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())


class TestClientAuth:
    def test_a_fresh_bearer_is_reused_and_an_expiring_one_is_exchanged(self, garmin: _Garmin) -> None:
        fresh = GarminConnectClient(
            "key", bearer="b", bearer_expires_at=datetime.now(timezone.utc) + timedelta(hours=5)
        )
        fresh.get_heart_rates(datetime.now(timezone.utc).date())
        assert garmin.exchanges == 0

        saved: list[Any] = []
        expiring = GarminConnectClient(
            "key",
            bearer="b",
            bearer_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            on_new_bearer=lambda bearer, at: saved.append((bearer, at)),
        )
        assert expiring.get_heart_rates(datetime.now(timezone.utc).date()) == {"restingHeartRate": 52}
        assert garmin.exchanges == 1
        assert saved
        assert saved[0][1] > datetime.now(timezone.utc) + timedelta(hours=23)

    def test_a_401_re_exchanges_once_then_succeeds(self, garmin: _Garmin) -> None:
        garmin.get_statuses = [401, 200]
        c = GarminConnectClient(
            "key", bearer="stale", bearer_expires_at=datetime.now(timezone.utc) + timedelta(hours=5)
        )
        assert c.get_heart_rates(datetime.now(timezone.utc).date()) == {"restingHeartRate": 52}
        assert garmin.exchanges == 1
        assert garmin.bearers[0] == "Bearer stale" != garmin.bearers[1]

    def test_404_reads_as_empty(self, garmin: _Garmin) -> None:
        later = datetime.now(timezone.utc) + timedelta(hours=5)
        c = GarminConnectClient("key", bearer="b", bearer_expires_at=later)
        garmin.get_statuses = [404]
        assert c.get_heart_rates(datetime.now(timezone.utc).date()) == {}

    def test_429_backs_off_exponentially_then_succeeds(self, garmin: _Garmin) -> None:
        later = datetime.now(timezone.utc) + timedelta(hours=5)
        c = GarminConnectClient("key", bearer="b", bearer_expires_at=later)
        garmin.get_statuses = [429, 429, 200]
        assert c.get_heart_rates(datetime.now(timezone.utc).date()) == {"restingHeartRate": 52}
        assert garmin.slept == [30, 60]  # type: ignore[attr-defined]

    def test_429_after_the_last_backoff_records_a_cooldown_and_raises(self, garmin: _Garmin) -> None:
        later = datetime.now(timezone.utc) + timedelta(hours=5)
        recorded: list[bool] = []
        c = GarminConnectClient(
            "key", bearer="b", bearer_expires_at=later, on_rate_limited=lambda: recorded.append(True) or 3600
        )
        garmin.get_statuses = [429]
        with pytest.raises(GarminConnectRateLimitError) as raised:
            c.get_heart_rates(datetime.now(timezone.utc).date())
        assert garmin.slept == list(garmin_client.BACKOFF_SECONDS)  # type: ignore[attr-defined]
        assert recorded == [True]
        assert raised.value.retry_after_seconds == 3600

    def test_a_rejected_key_calls_back_and_raises(self, garmin: _Garmin) -> None:
        garmin.token_status = 401
        rejected: list[bool] = []
        c = GarminConnectClient("revoked", on_key_rejected=lambda: rejected.append(True))
        with pytest.raises(GarminConnectClientError):
            c.get_heart_rates(datetime.now(timezone.utc).date())
        assert rejected == [True]

    def test_unbound_handler_refuses_to_run(self) -> None:
        with pytest.raises(GarminConnectClientError):
            garmin_client.current()


class TestCredentials:
    def test_store_encrypts_the_key_and_refuses_without_master_key(
        self, db: Session, garmin: _Garmin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = UserFactory()
        monkeypatch.delenv("MASTER_KEY", raising=False)
        with pytest.raises(credentials.MasterKeyMissingError):
            credentials.store_api_key(db, user.id, "plain-key")
        assert garmin.exchanges == 0, "must not call Garmin before it can store the result"

        monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
        connection = credentials.store_api_key(db, user.id, "  plain-key  ")
        assert connection.status == ConnectionStatus.ACTIVE
        assert connection.refresh_token
        assert "plain-key" not in connection.refresh_token
        assert credentials.decrypt(connection.refresh_token) == "plain-key"
        assert connection.token_expires_at is not None

    def test_client_for_reuses_the_known_device_and_expires_a_rejected_key(
        self, db: Session, garmin: _Garmin, master_key: None
    ) -> None:
        user = UserFactory()
        connection = credentials.store_api_key(db, user.id, "plain-key")
        source = DataSourceFactory(user=user, provider="garmin_connect", device_model="Watch Model A")
        EventRecordFactory(data_source=source)

        client = credentials.client_for(db, user.id)
        assert client.get_last_used_device_model() == "Watch Model A"

        connection.token_expires_at = datetime.now(timezone.utc)  # force a re-exchange
        db.commit()
        garmin.token_status = 401
        with pytest.raises(GarminConnectClientError):
            credentials.client_for(db, user.id).get_heart_rates(datetime.now(timezone.utc).date())
        db.refresh(connection)
        assert connection.status == ConnectionStatus.EXPIRED

    def test_cooldown_doubles_and_blocks_new_clients_until_it_ends(self, db: Session, master_key: None) -> None:
        from app.integrations.redis_client import get_redis_client  # noqa: PLC0415

        user = UserFactory()
        redis = get_redis_client()
        redis.delete(f"garmin_connect:strikes:{user.id}", f"garmin_connect:cooldown:{user.id}")

        assert credentials.record_rate_limit(user.id) == 3600
        assert credentials.record_rate_limit(user.id) == 7200
        assert 0 < credentials.cooldown_remaining(user.id) <= 7200
        with pytest.raises(GarminConnectRateLimitError) as raised:
            credentials.client_for(db, user.id)
        assert raised.value.retry_after_seconds is not None

        redis.delete(f"garmin_connect:strikes:{user.id}", f"garmin_connect:cooldown:{user.id}")
        assert credentials.cooldown_remaining(user.id) == 0

    def test_no_linked_key_is_a_clear_error(self, db: Session, master_key: None) -> None:
        with pytest.raises(GarminConnectClientError, match="pairing page"):
            credentials.client_for(db, UserFactory().id)


class TestLinkRoute:
    def test_public_route_validates_and_stores_the_key(
        self, client: TestClient, db: Session, garmin: _Garmin, master_key: None
    ) -> None:
        user = UserFactory()
        url = f"/api/v1/users/{user.id}/connections/garmin_connect/api-key"

        garmin.token_status = 401
        assert client.post(url, json={"api_key": "wrong-key"}).status_code == 400

        garmin.token_status = 200
        response = client.post(url, json={"api_key": "good-key-123"})
        assert response.status_code == 200, response.text
        assert response.json()["provider"] == "garmin_connect"
        assert "good-key" not in response.text
        row = db.query(UserConnection).filter_by(user_id=user.id, provider="garmin_connect").one()
        assert credentials.decrypt(row.refresh_token) == "good-key-123"

        missing = client.post(f"/api/v1/users/{uuid4()}/connections/garmin_connect/api-key", json={"api_key": "x" * 10})
        assert missing.status_code == 404

    def test_admin_credential_route_points_garmin_at_the_key_route(self, client: TestClient, db: Session) -> None:
        user = UserFactory()
        response = client.post(
            f"/api/v1/users/{user.id}/connections/garmin_connect", headers=api_key_headers(ApiKeyFactory().plain_key)
        )
        assert response.status_code == 400
        assert "api-key" in response.json()["detail"]


class TestLoadAndSaveAllAborts:
    """One fatal client error ends the run; ordinary per-day errors are still swallowed."""

    def _handler(self, error: Exception | None) -> tuple[GarminConnect247Data, dict[str, int]]:
        counters = {"calls": 0}

        class FakeClient:
            def iter_dates(self, start: Any, end: Any) -> list[Any]:
                return [start + timedelta(days=n) for n in range((end - start).days + 1)]

        handler = GarminConnect247Data(
            provider_name="garmin_connect",
            api_base_url="https://connect.garmin.com",
            client=FakeClient(),  # type: ignore[arg-type]
        )

        def step(_db: Any, _uid: Any, _d: Any) -> int:
            counters["calls"] += 1
            if error is not None:
                raise error
            return 1

        for name in (
            "save_sleep_for_date",
            "save_heart_rate_for_date",
            "save_daily_stats_for_date",
            "save_stress_for_date",
            "save_hrv_for_date",
        ):
            setattr(handler, name, step)
        handler.save_body_composition = lambda *_a, **_k: 0  # type: ignore[method-assign]
        handler.save_vo2max_for_range = lambda *_a, **_k: 0  # type: ignore[method-assign]
        return handler, counters

    def _run(self, handler: GarminConnect247Data) -> Any:
        return handler.load_and_save_all(
            db=SimpleNamespace(),  # type: ignore[arg-type]
            user_id=uuid4(),
            start_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        )

    def test_aborts_after_the_first_rate_limit(self) -> None:
        handler, counters = self._handler(GarminConnectRateLimitError("429"))
        with pytest.raises(GarminConnectRateLimitError):
            self._run(handler)
        assert counters["calls"] == 1

    def test_healthy_run_visits_every_pair_and_per_day_errors_are_swallowed(self) -> None:
        handler, counters = self._handler(None)
        self._run(handler)
        assert counters["calls"] == 3 * 5

        handler, counters = self._handler(KeyError("sleepMovement"))
        self._run(handler)
        assert counters["calls"] == 3 * 5


def test_workout_without_device_name_takes_the_known_device() -> None:
    """Personal API activity summaries carry no deviceName; the workout must keep the user's device."""
    from app.models import EventRecord  # noqa: PLC0415
    from app.repositories.event_record_repository import EventRecordRepository  # noqa: PLC0415
    from app.repositories.user_connection_repository import UserConnectionRepository  # noqa: PLC0415
    from app.services.providers.garmin_connect.workouts import GarminConnectWorkouts  # noqa: PLC0415

    workouts = GarminConnectWorkouts(
        workout_repo=EventRecordRepository(EventRecord),
        connection_repo=UserConnectionRepository(),
        provider_name="garmin_connect",
        api_base_url="https://connect.garmin.com",
        client=GarminConnectClient("key", device_model="Watch Model A"),
    )
    raw = {
        "activityId": 1,
        "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-10-30 21:14:00",
        "duration": 3600.0,
        "manufacturer": "GARMIN",
    }
    record, _ = workouts._normalize_workout(raw, uuid4())
    assert record.device_model == "Watch Model A"
    record, _ = workouts._normalize_workout({**raw, "deviceName": "Watch Model B"}, uuid4())
    assert record.device_model == "Watch Model B"
