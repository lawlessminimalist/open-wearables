import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiPublicPost = vi.fn();
const fetchAllProviders = vi.fn();
vi.mock('./api', () => ({ apiPublicPost: (...args: unknown[]) => apiPublicPost(...args) }));
vi.mock('./providers', () => ({ fetchAllProviders: () => fetchAllProviders() }));

const { fetchKeyProviders, linkGarminKey } = await import('./garminKey');

const provider = (slug: string, is_enabled: boolean, has_cloud_api = false) => ({
	provider: slug,
	name: slug,
	is_enabled,
	has_cloud_api,
	icon_url: ''
});

describe('garminKey', () => {
	beforeEach(() => vi.clearAllMocks());

	it('offers garmin_connect only while it is enabled', async () => {
		fetchAllProviders.mockResolvedValue([
			provider('garmin_connect', true),
			provider('oura', true, true)
		]);
		expect((await fetchKeyProviders()).map((p) => p.provider)).toEqual(['garmin_connect']);

		fetchAllProviders.mockResolvedValue([provider('garmin_connect', false)]);
		expect(await fetchKeyProviders()).toEqual([]);
	});

	it('posts the key to the public link route for that user', async () => {
		apiPublicPost.mockResolvedValue({});
		await linkGarminKey('user-1', 'key-123');
		expect(apiPublicPost).toHaveBeenCalledWith(
			'/api/v1/users/user-1/connections/garmin_connect/api-key',
			{
				api_key: 'key-123'
			}
		);
	});
});
