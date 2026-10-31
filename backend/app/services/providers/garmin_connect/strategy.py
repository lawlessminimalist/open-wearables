"""Garmin Connect provider strategy (credential-based, no OAuth)."""

from app.services.providers.base_strategy import BaseProviderStrategy, ProviderCapabilities, ProviderCoverage
from app.services.providers.garmin_connect.coverage import SLEEP_FIELDS, TIMESERIES, WORKOUT_FIELDS
from app.services.providers.garmin_connect.data_247 import GarminConnect247Data
from app.services.providers.garmin_connect.workouts import GarminConnectWorkouts


class GarminConnectStrategy(BaseProviderStrategy):
    """Garmin Connect provider through Garmin's Personal API.

    Each user links their own Personal API key from the pairing page; the key is stored
    encrypted on their connection and exchanged for a bearer token per sync (see
    client.py and credentials.py). No OAuth dance and no shared login.
    """

    def __init__(self) -> None:
        super().__init__()

        # Shared client used by both workouts and 247 handlers

        # oauth stays None — no cloud OAuth for this provider
        self.oauth = None

        self.workouts = GarminConnectWorkouts(
            workout_repo=self.workout_repo,
            connection_repo=self.connection_repo,
            provider_name=self.name,
            api_base_url=self.api_base_url,
        )

        self.data_247 = GarminConnect247Data(
            provider_name=self.name,
            api_base_url=self.api_base_url,
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
    def coverage(self) -> ProviderCoverage:
        return ProviderCoverage(
            timeseries=TIMESERIES,
            workout_fields=WORKOUT_FIELDS,
            sleep_fields=SLEEP_FIELDS,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(rest_pull=True)
