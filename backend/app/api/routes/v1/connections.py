import contextlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.database import DbSession
from app.models import ProviderSetting
from app.repositories.provider_settings_repository import ProviderSettingsRepository
from app.schemas.enums import ProviderName
from app.schemas.model_crud.user_management import UserConnectionWithCapabilities
from app.services import ApiKeyDep, user_connection_service
from app.services.providers.factory import ProviderFactory

router = APIRouter()
factory = ProviderFactory()
provider_settings_repo = ProviderSettingsRepository()


def _with_capabilities(
    conn: object,
    settings_map: dict[str, ProviderSetting],
) -> UserConnectionWithCapabilities:
    enriched = UserConnectionWithCapabilities.model_validate(conn)
    with contextlib.suppress(ValueError):
        strategy = factory.get_provider(enriched.provider)
        caps = strategy.capabilities
        enriched.max_historical_days = caps.max_historical_days
        enriched.rest_pull = caps.rest_pull
        enriched.webhook_stream = caps.webhook_stream
        enriched.webhook_ping = caps.webhook_ping
        enriched.webhook_callback = caps.webhook_callback
        setting = settings_map.get(enriched.provider)
        enriched.live_sync_mode = (
            setting.live_sync_mode
            if (setting and setting.live_sync_mode is not None)
            else strategy.default_live_sync_mode
        )
    return enriched


@router.get("/users/{user_id}/connections", response_model=list[UserConnectionWithCapabilities])
def get_connections_endpoint(
    user_id: UUID,
    db: DbSession,
    _api_key: ApiKeyDep,
):
    """Get all connections for a user, enriched with provider capability metadata."""
    settings_map = provider_settings_repo.get_all(db)
    return [
        _with_capabilities(conn, settings_map) for conn in user_connection_service.get_connections_by_user(db, user_id)
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
    strategy = factory.get_provider(provider.value)
    if strategy.oauth is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{provider.value}' uses OAuth. Use GET /oauth/{provider.value}/authorize to connect.",
        )

    connection = user_connection_service.ensure_credential_connection(db, user_id, provider.value)
    settings_map = provider_settings_repo.get_all(db)
    return _with_capabilities(connection, settings_map)


@router.delete("/users/{user_id}/connections/{provider}")
def disconnect_provider_endpoint(
    user_id: UUID,
    provider: ProviderName,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> Response:
    """Disconnect a user from a provider, revoking the connection and clearing tokens."""
    strategy = ProviderFactory().get_provider(provider.value)
    user_connection_service.disconnect(db, user_id, provider.value, oauth=strategy.oauth)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
