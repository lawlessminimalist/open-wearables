import { useState, useEffect, useMemo } from 'react';
import {
  useOAuthProviders,
  useUpdateOAuthProviders,
} from '@/hooks/api/use-oauth-providers';
import { Loader2, CheckCircle2 } from 'lucide-react';
import { ProviderItem } from '@/components/settings/providers/provider-item';
import { Button } from '@/components/ui/button';
import type { Provider } from '@/lib/api/types';

export function ProvidersTab() {
  // Fetch all providers (cloud_only=false) so credential-based providers are included
  const {
    data: allProviders,
    isLoading,
    error,
    refetch,
  } = useOAuthProviders(false);
  const updateMutation = useUpdateOAuthProviders();

  const oauthProviders: Provider[] = useMemo(
    () => allProviders?.filter((p) => p.has_cloud_api) ?? [],
    [allProviders]
  );

  const credentialProviders: Provider[] = useMemo(
    () => allProviders?.filter((p) => !p.has_cloud_api) ?? [],
    [allProviders]
  );

  const [localToggleStates, setLocalToggleStates] = useState<
    Record<string, boolean>
  >({});
  const [hasInitialized, setHasInitialized] = useState(false);

  useEffect(() => {
    if (allProviders && allProviders.length > 0 && !hasInitialized) {
      const initial: Record<string, boolean> = {};
      allProviders.forEach((provider) => {
        initial[provider.provider] = provider.is_enabled;
      });
      setLocalToggleStates(initial);
      setHasInitialized(true);
    }
  }, [allProviders, hasInitialized]);

  const hasChanges = useMemo(() => {
    if (!allProviders || !hasInitialized) return false;
    return allProviders.some(
      (provider) => localToggleStates[provider.provider] !== provider.is_enabled
    );
  }, [allProviders, localToggleStates, hasInitialized]);

  const handleToggleProvider = (providerId: string) => {
    setLocalToggleStates((prev) => ({
      ...prev,
      [providerId]: !prev[providerId],
    }));
  };

  const handleSave = async () => {
    if (!allProviders) return;
    await updateMutation.mutateAsync({ providers: localToggleStates });
  };

  if (isLoading) {
    return (
      <div className="rounded-2xl border border-border/60 bg-gradient-to-br from-card/80 to-card/40 backdrop-blur-xl p-12">
        <div className="flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-border/60 bg-gradient-to-br from-card/80 to-card/40 backdrop-blur-xl p-12 text-center">
        <p className="text-muted-foreground mb-4">Failed to load providers</p>
        <Button variant="outline" onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  if (!allProviders || allProviders.length === 0) {
    return (
      <div className="rounded-2xl border border-border/60 bg-gradient-to-br from-card/80 to-card/40 backdrop-blur-xl p-12 text-center">
        <p className="text-muted-foreground">No providers available</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-medium text-foreground">Providers</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Configure which providers are available to your end users
        </p>
      </div>

      {oauthProviders.length > 0 && (
        <div className="rounded-2xl border border-border/60 bg-gradient-to-br from-card/80 to-card/40 backdrop-blur-xl overflow-hidden">
          <div className="px-6 py-4 border-b border-border/60">
            <h3 className="text-sm font-medium text-foreground">
              OAuth Providers
            </h3>
            <p className="text-xs text-muted-foreground mt-1">
              Providers that connect via OAuth — users authorise in their
              browser
            </p>
          </div>
          <div className="divide-y divide-border/40">
            {oauthProviders.map((provider) => (
              <ProviderItem
                key={provider.provider}
                provider={provider}
                localToggleState={
                  localToggleStates[provider.provider] ?? provider.is_enabled
                }
                onToggle={() => handleToggleProvider(provider.provider)}
              />
            ))}
          </div>
        </div>
      )}

      {credentialProviders.length > 0 && (
        <div className="rounded-2xl border border-border/60 bg-gradient-to-br from-card/80 to-card/40 backdrop-blur-xl overflow-hidden">
          <div className="px-6 py-4 border-b border-border/60">
            <h3 className="text-sm font-medium text-foreground">
              Credential Providers
            </h3>
            <p className="text-xs text-muted-foreground mt-1">
              Providers that authenticate with stored credentials — configure
              via environment variables
            </p>
          </div>
          <div className="divide-y divide-border/40">
            {credentialProviders.map((provider) => (
              <ProviderItem
                key={provider.provider}
                provider={provider}
                localToggleState={
                  localToggleStates[provider.provider] ?? provider.is_enabled
                }
                onToggle={() => handleToggleProvider(provider.provider)}
              />
            ))}
          </div>
        </div>
      )}

      {hasChanges && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-4 rounded-lg border border-border bg-card px-6 py-3 shadow-lg shadow-black/50">
          <p className="text-sm text-foreground/90">You have unsaved changes</p>
          <Button onClick={handleSave} disabled={updateMutation.isPending}>
            {updateMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <CheckCircle2 className="h-4 w-4" />
                Save Changes
              </>
            )}
          </Button>
        </div>
      )}
    </div>
  );
}
