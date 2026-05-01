"""Garmin Connect provider strategy (credential-based, no OAuth)."""

from app.services.providers.base_strategy import BaseProviderStrategy, ProviderCapabilities
from app.services.providers.garmin_connect.client import GarminConnectClient
from app.services.providers.garmin_connect.data_247 import GarminConnect247Data
from app.services.providers.garmin_connect.workouts import GarminConnectWorkouts


class GarminConnectStrategy(BaseProviderStrategy):
    """Garmin Connect provider — pulls data via python-garminconnect.

    Authentication uses Garmin Connect email/password (set via GARMIN_CONNECT_EMAIL
    and GARMIN_CONNECT_PASSWORD environment variables). No OAuth dance is needed.
    The provider stores session tokens at GARMIN_CONNECT_TOKEN_STORE so repeated
    logins are avoided.
    """

    def __init__(self) -> None:
        super().__init__()

        # Shared client used by both workouts and 247 handlers
        client = GarminConnectClient()

        # oauth stays None — no cloud OAuth for this provider
        self.oauth = None

        self.workouts = GarminConnectWorkouts(
            workout_repo=self.workout_repo,
            connection_repo=self.connection_repo,
            provider_name=self.name,
            api_base_url=self.api_base_url,
            client=client,
        )

        self.data_247 = GarminConnect247Data(
            provider_name=self.name,
            api_base_url=self.api_base_url,
            client=client,
        )

    @property
    def name(self) -> str:
        return "garmin_connect"

    @property
    def display_name(self) -> str:
        return "Garmin Connect"

    @property
    def api_base_url(self) -> str:
        # garminconnect manages URLs internally; this is informational only
        return "https://connect.garmin.com"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(rest_pull=True)
