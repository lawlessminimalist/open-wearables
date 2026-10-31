import { fail, redirect } from '@sveltejs/kit';
import { ApiError } from '$lib/server/api';
import { authorizeProvider } from '$lib/server/oauth';
import { fetchCloudProviders } from '$lib/server/providers';
import { GARMIN_KEY_PROVIDER, fetchKeyProviders, linkGarminKey } from '$lib/server/garminKey';
import { pairingSuccessUrl, safeReturnUrl } from '$lib/pairing/links';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url }) => ({
	providers: await fetchCloudProviders(),
	// FORK: providers that pair with a pasted key instead of OAuth (garmin_connect).
	keyProviders: await fetchKeyProviders(),
	returnUrl: safeReturnUrl(url.searchParams.get('redirect_url'))
});

export const actions: Actions = {
	connect: async ({ params, request, url }) => {
		// `?/connect` replaces the query string, so the return link travels as a field.
		const form = await request.formData();
		const provider = String(form.get('provider') ?? '');
		const returnUrl = String(form.get('return_url') ?? '') || null;

		let authorizationUrl: string;
		try {
			const target = pairingSuccessUrl(url.origin, params.id, provider, returnUrl);
			({ authorization_url: authorizationUrl } = await authorizeProvider(
				provider,
				params.id,
				target
			));
		} catch (error) {
			const detail = error instanceof ApiError ? error.message : 'Try again in a moment.';
			return fail(400, { provider, message: `Could not reach ${provider}. ${detail}` });
		}

		redirect(303, authorizationUrl);
	},

	// FORK: Garmin Connect pairs with the user's own Personal API key, checked by the backend.
	garminKey: async ({ params, request, url }) => {
		const form = await request.formData();
		const apiKey = String(form.get('api_key') ?? '').trim();
		const returnUrl = String(form.get('return_url') ?? '') || null;
		if (!apiKey) {
			return fail(400, {
				provider: GARMIN_KEY_PROVIDER,
				message: 'Paste your Garmin Personal API key.'
			});
		}

		try {
			await linkGarminKey(params.id, apiKey);
		} catch (error) {
			const detail = error instanceof ApiError ? error.message : 'Try again in a moment.';
			return fail(400, {
				provider: GARMIN_KEY_PROVIDER,
				message: `Could not connect Garmin. ${detail}`
			});
		}

		redirect(303, pairingSuccessUrl(url.origin, params.id, GARMIN_KEY_PROVIDER, returnUrl));
	}
};
