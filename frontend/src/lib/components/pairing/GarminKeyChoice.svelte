<script lang="ts">
	// FORK: Garmin Connect pairs with the user's own Personal API key (see lib/server/garminKey.ts).
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { enhance } from '$app/forms';
	import type { Provider } from '$lib/server/providers';
	import { createSubmitFlag } from '$lib/utils/forms.svelte';
	import ProviderLogo from '$lib/components/providers/ProviderLogo.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import PasswordField from '$lib/components/ui/PasswordField.svelte';

	let { provider, returnUrl }: { provider: Provider; returnUrl: string | null } = $props();

	const submit = createSubmitFlag();
	const KEY_PAGE = 'https://connect.garmin.com/app/settings/personalApiToken';
</script>

<form
	method="POST"
	action="?/garminKey"
	use:enhance={submit.enhance}
	class="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4"
>
	{#if returnUrl}
		<input type="hidden" name="return_url" value={returnUrl} />
	{/if}

	<div class="flex items-center gap-4">
		<ProviderLogo {provider} />
		<span class="min-w-0 flex-1">
			<span class="block truncate font-medium text-foreground">{provider.name}</span>
			<span class="block text-xs text-muted-foreground">Paste a Personal API key to connect</span>
		</span>
	</div>

	<p class="text-sm text-muted-foreground">
		In Garmin Connect, open
		<a
			href={KEY_PAGE}
			target="_blank"
			rel="noopener noreferrer"
			class="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
		>
			Settings, Personal API <ExternalLink size={12} aria-hidden="true" />
		</a>
		and create a key. It is shown once; paste it here. A longer expiry means fewer reconnects.
	</p>

	<PasswordField name="api_key" label="Garmin Personal API key" autocomplete="new-password" />

	<Button type="submit" variant="primary" disabled={submit.submitting}>
		{#if submit.submitting}
			<LoaderCircle size={16} aria-hidden="true" class="animate-spin" />
			Checking the key…
		{:else}
			Connect Garmin
		{/if}
	</Button>
</form>
