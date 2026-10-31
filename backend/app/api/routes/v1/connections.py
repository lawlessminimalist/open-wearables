import contextlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.database import DbSession
from app.models import ProviderSetting
from app.schemas.auth import ConnectionStatus, LiveSyncMode, SDKAuthContext
from app.schemas.enums import ProviderName
from app.schemas.model_crud.user_management import UserConnectionWithCapabilities
from app.services import ApiKeyDep, user_connection_service
from app.services.provider_settings_service import provider_settings_service
from app.services.providers.base_strategy import BaseProviderStrategy
from app.services.providers.factory import ProviderFactory
from app.utils.auth import CombinedAuthDep

router = APIRouter()
factory = ProviderFactory()


def _with_capabilities(
    conn: object,
    settings_map: dict[str, ProviderSetting],
    linked_user_ids: list | None = None,
) -> UserConnectionWithCapabilities:
    enriched = UserConnectionWithCapabilities.model_validate(conn)
    with contextlib.suppress(ValueError):
        strategy = factory.get_provider(enriched.provider)
        caps = strategy.capabilities
        enriched.icon_url = strategy.icon_url
        enriched.max_historical_days = caps.max_historical_days
        enriched.rest_pull = caps.rest_pull
        enriched.webhook_stream = caps.webhook_stream
        enriched.webhook_ping = caps.webhook_ping
        enriched.webhook_callback = caps.webhook_callback
        setting = settings_map.get(enriched.provider)
        mode = (
            setting.live_sync_mode
            if (setting and setting.live_sync_mode is not None)
            else strategy.default_live_sync_mode
        )
        # ORM yields a plain str and attribute assignment skips validation; coerce to the enum
        enriched.live_sync_mode = LiveSyncMode(mode) if mode is not None else None
    if linked_user_ids:
        enriched.linked_user_ids = linked_user_ids
    return enriched


@router.get("/users/{user_id}/connections", response_model=list[UserConnectionWithCapabilities])
def get_connections_endpoint(
    user_id: UUID,
    db: DbSession,
    _api_key: ApiKeyDep,
):
    """Get all connections for a user, enriched with provider capability metadata."""
    settings_map = provider_settings_service.get_settings_map(db)
    connections = user_connection_service.get_connections_by_user(db, user_id)
    provider_pairs = [
        (c.provider, c.provider_user_id)
        for c in connections
        if c.provider_user_id and c.status == ConnectionStatus.ACTIVE
    ]
    linked_map = user_connection_service.get_linked_user_ids(db, user_id, provider_pairs)
    return [
        _with_capabilities(
            conn,
            settings_map,
            linked_map.get((conn.provider, conn.provider_user_id)) if conn.provider_user_id else None,
        )
        for conn in connections
    ]


@router.post("/users/{user_id}/connections/{provider}", response_model=UserConnectionWithCapabilities)
def connect_credential_provider_endpoint(
    user_id: UUID,
    provider: ProviderName,
    db: DbSession,
    _api_key: ApiKeyDep,
):
    """Create or reactivate a connection for a credential-based (non-OAuth) provider.

    Use this for providers like `garmin_connect` that authenticate via environment
    variables rather than OAuth. The connection record is created immediately; credentials
    are read from the server environment when a sync is triggered.

    Returns 400 if the provider uses OAuth (those connect via the OAuth authorize flow).
    """
    if provider == ProviderName.GARMIN_CONNECT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="garmin_connect needs the user's Garmin Personal API key: POST it to "
            f"/api/v1/users/{user_id}/connections/garmin_connect/api-key.",
        )
    strategy = factory.get_provider(provider.value)
    if strategy.oauth is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{provider.value}' uses OAuth. Use GET /oauth/{provider.value}/authorize to connect.",
        )

    connection = user_connection_service.ensure_credential_connection(db, user_id, provider.value)
    settings_map = provider_settings_service.get_settings_map(db)
    return _with_capabilities(connection, settings_map)


class GarminApiKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=1024, description="A Garmin Connect Personal API key")


@router.post(
    "/users/{user_id}/connections/garmin_connect/api-key",
    response_model=UserConnectionWithCapabilities,
    tags=["System: OAuth"],
)
def link_garmin_personal_api_key(user_id: UUID, body: GarminApiKeyIn, db: DbSession):
    """Link a user's Garmin account with their own Garmin Personal API key (fork).

    Public, like `GET /oauth/{provider}/authorize`: the pairing link is the credential, and
    this is what its Garmin key field posts. The key is exchanged with Garmin before
    anything is stored, so a wrong or revoked key is rejected, and it is kept encrypted
    with MASTER_KEY (see garmin_connect/credentials.py).
    """
    from app.services.providers.garmin_connect.client import (  # noqa: PLC0415
        GarminConnectClientError,
        GarminConnectRateLimitError,
    )
    from app.services.providers.garmin_connect.credentials import (  # noqa: PLC0415
        MasterKeyMissingError,
        store_api_key,
    )
    from app.services.user_service import user_service  # noqa: PLC0415

    user_service.get(db, user_id, raise_404=True)
    try:
        connection = store_api_key(db, user_id, body.api_key)
    except MasterKeyMissingError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GarminConnectRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except GarminConnectClientError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _with_capabilities(connection, provider_settings_service.get_settings_map(db))


def _assert_sdk_token_may_disconnect(
    db: DbSession,
    auth: SDKAuthContext,
    user_id: UUID,
    strategy: BaseProviderStrategy,
) -> None:
    """Confine an SDK-token caller to its own user's SDK-fed connections.

    The token carries no provider claim, so ``client_sdk`` gates which providers are
    reachable at all. The token check behind it is defence in depth: no SDK provider
    holds OAuth tokens today, and an SDK sign-out must never force a re-authorization.
    """
    if auth.user_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token does not match user_id")

    if not strategy.capabilities.client_sdk:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "SDK tokens cannot disconnect this provider")

    connection = user_connection_service.get_connection(db, user_id, strategy.name)
    if connection and (connection.access_token or connection.refresh_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "SDK tokens cannot disconnect an OAuth connection")


@router.delete("/users/{user_id}/connections/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_provider_endpoint(
    user_id: UUID,
    provider: ProviderName,
    db: DbSession,
    auth: CombinedAuthDep,
) -> Response:
    """Disconnect a user from a provider, revoking the connection and clearing tokens.

    Also takes an SDK user token, so the mobile SDK can report a sign-out its local-only
    ``signOut()`` would otherwise hide. That path skips provider deregistration: leaving an
    app is no reason to unregister the user from the provider's API.
    """
    strategy = ProviderFactory().get_provider(provider.value)

    if auth.auth_type == "sdk_token":
        _assert_sdk_token_may_disconnect(db, auth, user_id, strategy)
        user_connection_service.disconnect(db, user_id, provider.value, oauth=None, reason="sdk_sign_out")
    else:
        user_connection_service.disconnect(db, user_id, provider.value, oauth=strategy.oauth)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/users/{user_id}/connections/{provider}/data", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider_data_endpoint(
    user_id: UUID,
    provider: ProviderName,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> Response:
    """Delete all of a user's data for a provider and revoke the connection."""
    strategy = ProviderFactory().get_provider(provider.value)
    user_connection_service.purge_provider_data(db, user_id, provider.value, oauth=strategy.oauth)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
