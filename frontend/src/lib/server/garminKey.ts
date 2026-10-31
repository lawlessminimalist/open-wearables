// FORK: Garmin Connect links with the user's own Garmin Personal API key, not OAuth,
// so the pairing page offers it beside the OAuth providers. See
// backend/app/services/providers/garmin_connect/credentials.py.
import { apiPublicPost } from './api';
import { fetchAllProviders, type Provider } from './providers';

export const GARMIN_KEY_PROVIDER = 'garmin_connect';

/** garmin_connect when it is enabled; it has no cloud OAuth, so fetchCloudProviders skips it. */
export const fetchKeyProviders = async (): Promise<Provider[]> =>
	(await fetchAllProviders()).filter(
		(provider) => provider.is_enabled && provider.provider === GARMIN_KEY_PROVIDER
	);

/** Public, like OAuth authorize: the backend checks the key with Garmin before storing it. */
export const linkGarminKey = (userId: string, apiKey: string) =>
	apiPublicPost<unknown>(`/api/v1/users/${userId}/connections/garmin_connect/api-key`, {
		api_key: apiKey
	});
