"""Per-user Garmin Personal API keys: encrypted storage and the per-user client.

A key gives read access to one person's Garmin data for up to a year, so it is never
stored in plain text. Both the key (in `UserConnection.refresh_token`) and its 24-hour
bearer token (in `access_token`) are Fernet-encrypted with the deployment's MASTER_KEY.
Without MASTER_KEY the app's own decryptor silently becomes a no-op (`FakeFernet`); this
module refuses to store or read a key instead, so a missing secret fails loudly rather
than writing usable keys into the database.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func

from app.database import DbSession
from app.models import DataSource, EventRecord, UserConnection
from app.repositories.user_connection_repository import UserConnectionRepository
from app.schemas.auth.connection_status import ConnectionStatus
from app.services.providers.garmin_connect.client import (
    GarminConnectClient,
    GarminConnectClientError,
    GarminConnectRateLimitError,
    exchange_api_key,
)

PROVIDER = "garmin_connect"
_connections = UserConnectionRepository()

# Per-user cooldown after a 429. Garmin sends no Retry-After, so back off by doubling: 1h,
# 2h, 4h ... capped at a day. Strikes are forgotten 48 hours after the last one.
COOLDOWN_BASE_SECONDS = 3600
COOLDOWN_MAX_SECONDS = 86400
_STRIKE_MEMORY_SECONDS = 2 * 86400


def _redis():  # noqa: ANN202
    """Redis, or None when it is unreachable: pacing still applies, only the cooldown is lost."""
    try:
        from app.integrations.redis_client import get_redis_client  # noqa: PLC0415

        client = get_redis_client()
        client.ping()
        return client
    except Exception:  # noqa: BLE001
        return None


def cooldown_remaining(user_id: UUID) -> int:
    """Seconds left in the user's Garmin cooldown, 0 when none."""
    redis = _redis()
    if redis is None:
        return 0
    ttl = redis.ttl(f"garmin_connect:cooldown:{user_id}")
    return max(int(ttl), 0) if ttl is not None else 0


def record_rate_limit(user_id: UUID) -> int:
    """Start (or extend) the user's cooldown after a 429 and return its length in seconds."""
    redis = _redis()
    if redis is None:
        return COOLDOWN_BASE_SECONDS
    strikes_key = f"garmin_connect:strikes:{user_id}"
    strikes = int(redis.incr(strikes_key))
    redis.expire(strikes_key, _STRIKE_MEMORY_SECONDS)
    seconds = min(COOLDOWN_BASE_SECONDS * 2 ** (strikes - 1), COOLDOWN_MAX_SECONDS)
    redis.set(f"garmin_connect:cooldown:{user_id}", strikes, ex=seconds)
    return seconds


class MasterKeyMissingError(GarminConnectClientError):
    """MASTER_KEY is not set, so a Garmin key can be neither stored nor read."""


def _fernet() -> Fernet:
    key = os.environ.get("MASTER_KEY")
    if not key:
        raise MasterKeyMissingError(
            "MASTER_KEY is not set; Garmin Personal API keys are stored encrypted and cannot be handled without it"
        )
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise GarminConnectClientError("The stored Garmin key could not be decrypted with this MASTER_KEY") from exc


def store_api_key(db: DbSession, user_id: UUID, api_key: str) -> UserConnection:
    """Validate a key with Garmin, then store it encrypted on the user's connection.

    The exchange runs first, so a mistyped or revoked key is rejected before anything is
    written, and the fresh bearer is kept so the first sync needs no second exchange.
    """
    from app.services.user_connection_service import user_connection_service  # noqa: PLC0415

    api_key = api_key.strip()
    encrypt("")  # fail on a missing MASTER_KEY before calling Garmin
    bearer, expires_at = exchange_api_key(api_key)
    connection = user_connection_service.ensure_credential_connection(db, user_id, PROVIDER)
    connection.refresh_token = encrypt(api_key)
    connection.access_token = encrypt(bearer)
    connection.token_expires_at = expires_at
    connection.status = ConnectionStatus.ACTIVE
    connection.updated_at = datetime.now(timezone.utc)
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def _known_device_model(db: DbSession, user_id: UUID) -> str | None:
    """The device on the user's most recently active garmin_connect source, so 24/7 rows join it."""
    last_event = func.max(EventRecord.end_datetime)
    row = (
        db.query(DataSource.device_model, last_event)
        .outerjoin(EventRecord, EventRecord.data_source_id == DataSource.id)
        .filter(
            DataSource.user_id == user_id,
            DataSource.provider == PROVIDER,
            DataSource.device_model.is_not(None),
        )
        .group_by(DataSource.device_model)
        .order_by(last_event.desc().nulls_last())
        .first()
    )
    return row[0] if row else None


def client_for(db: DbSession, user_id: UUID) -> GarminConnectClient:
    """The Personal API client for one user, from their active garmin_connect connection.

    Refuses while the user is cooling down from a 429, before any request reaches Garmin.
    """
    if (remaining := cooldown_remaining(user_id)) > 0:
        raise GarminConnectRateLimitError(
            f"Garmin is cooling down for this user after a rate limit; {remaining}s left",
            retry_after_seconds=remaining,
        )
    connection = _connections.get_active_connection(db, user_id, PROVIDER)
    if connection is None or not connection.refresh_token:
        raise GarminConnectClientError(
            "No Garmin Personal API key is linked for this user; paste one on the pairing page"
        )

    def _save_bearer(bearer: str, expires_at: datetime) -> None:
        connection.access_token = encrypt(bearer)
        connection.token_expires_at = expires_at
        db.add(connection)
        db.commit()

    def _expire() -> None:
        # Garmin rejected the key itself (revoked or past its 30-day to one-year life).
        connection.status = ConnectionStatus.EXPIRED
        db.add(connection)
        db.commit()

    return GarminConnectClient(
        api_key=decrypt(connection.refresh_token),
        bearer=decrypt(connection.access_token) if connection.access_token else None,
        bearer_expires_at=connection.token_expires_at,
        device_model=_known_device_model(db, user_id),
        on_new_bearer=_save_bearer,
        on_key_rejected=_expire,
        on_rate_limited=lambda: record_rate_limit(user_id),
    )
