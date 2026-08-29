/**
 * DisplayTimezone — ephemeral, view-only IANA timezone for rendering UTC
 * timestamps in any user dashboard view.
 *
 *   Default:           the viewed user's `User.timezone`, falling back to "UTC"
 *                      when that is unset. It used to default to "UTC"
 *                      unconditionally, which meant a Brisbane user's dashboard
 *                      opened showing every timestamp 10 hours out — a 23:58
 *                      bedtime rendered as 13:58 — until they touched the
 *                      picker. The zone the data was recorded in is the only
 *                      sensible opening view.
 *   Persistence:       localStorage, keyed per user_id (so switching between
 *                      users in the dashboard doesn't drag the previous user's
 *                      preference along). Only an explicit pick is stored, so
 *                      the default keeps following User.timezone if it changes.
 *   Decoupled from:    User.timezone on the backend, which controls how the
 *                      backend buckets daily aggregates. It only SEEDS this;
 *                      changing the display tz never modifies data.
 *
 * Usage:
 *
 *   <DisplayTimezoneProvider userId={user.id} userTimezone={user.timezone}>
 *     ...
 *     <TimezoneSelector />
 *     ...
 *     {/* anywhere inside, formatInTz(iso, displayTz, fmt) renders local *\/}
 *   </DisplayTimezoneProvider>
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { DEFAULT_DISPLAY_TZ } from '@/lib/dates';

interface DisplayTimezoneContextValue {
  /** Current IANA tz used to render UTC datetimes. Never null — defaults to "UTC". */
  displayTz: string;
  /** Override the display tz (persisted to localStorage). */
  setDisplayTz: (tz: string) => void;
  /** Reset to the default. */
  resetDisplayTz: () => void;
}

const DisplayTimezoneContext =
  createContext<DisplayTimezoneContextValue | null>(null);

const STORAGE_PREFIX = 'ow:display-tz:';

function storageKey(userId: string | null | undefined): string {
  return `${STORAGE_PREFIX}${userId ?? 'global'}`;
}

/** The stored override, or null when the viewer has never picked one. */
function readStoredTz(userId: string | null | undefined): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    return raw && raw.length > 0 ? raw : null;
  } catch {
    return null;
  }
}

interface DisplayTimezoneProviderProps {
  children: ReactNode;
  /** Scopes the persisted preference. Pass the user being viewed. */
  userId?: string | null;
  /**
   * The viewed user's `User.timezone`. Seeds the display tz when the viewer has
   * not picked one, so the dashboard opens in the timezone the data was
   * recorded in rather than UTC. May arrive after first render (the user query
   * resolves async) — `displayTz` is derived, not stored, so it just re-renders.
   */
  userTimezone?: string | null;
}

export function DisplayTimezoneProvider({
  children,
  userId,
  userTimezone,
}: DisplayTimezoneProviderProps) {
  // Only the explicit override is state. The effective zone is derived below,
  // so a null override transparently follows `userTimezone` as it loads or
  // changes — and an explicit choice of "UTC" is still distinguishable from
  // "never chose", which a plain string default could not express.
  const [overrideTz, setOverrideTz] = useState<string | null>(() =>
    readStoredTz(userId)
  );

  // If the userId changes (navigated to a different user), reload the
  // preference for the new scope.
  useEffect(() => {
    setOverrideTz(readStoredTz(userId));
  }, [userId]);

  const displayTz = overrideTz ?? userTimezone ?? DEFAULT_DISPLAY_TZ;

  const setDisplayTz = useCallback(
    (tz: string) => {
      const next = tz && tz.length > 0 ? tz : DEFAULT_DISPLAY_TZ;
      setOverrideTz(next);
      try {
        window.localStorage.setItem(storageKey(userId), next);
      } catch {
        // localStorage write can fail in privacy modes — ignore.
      }
    },
    [userId]
  );

  const resetDisplayTz = useCallback(() => {
    // Drop the override so the zone falls back to the user's own timezone.
    setOverrideTz(null);
    try {
      window.localStorage.removeItem(storageKey(userId));
    } catch {
      // ignore
    }
  }, [userId]);

  const value = useMemo<DisplayTimezoneContextValue>(
    () => ({ displayTz, setDisplayTz, resetDisplayTz }),
    [displayTz, setDisplayTz, resetDisplayTz]
  );

  return (
    <DisplayTimezoneContext.Provider value={value}>
      {children}
    </DisplayTimezoneContext.Provider>
  );
}

/**
 * Read-only access to the current display tz. Components inside a
 * DisplayTimezoneProvider should call this for any datetime they render.
 *
 * Outside a provider this returns the DEFAULT_DISPLAY_TZ (UTC) so charts and
 * formatters degrade gracefully rather than throwing.
 */
export function useDisplayTimezone(): DisplayTimezoneContextValue {
  const ctx = useContext(DisplayTimezoneContext);
  if (ctx) return ctx;
  return {
    displayTz: DEFAULT_DISPLAY_TZ,
    setDisplayTz: () => {},
    resetDisplayTz: () => {},
  };
}
