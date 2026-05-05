/**
 * DisplayTimezone — ephemeral, view-only IANA timezone for rendering UTC
 * timestamps in any user dashboard view.
 *
 *   Default:           "UTC".
 *   Persistence:       localStorage, keyed per user_id (so switching between
 *                      users in the dashboard doesn't drag the previous user's
 *                      preference along).
 *   Decoupled from:    User.timezone on the backend, which controls how the
 *                      backend buckets daily aggregates. Changing the display
 *                      tz never modifies data.
 *
 * Usage:
 *
 *   <DisplayTimezoneProvider userId={user.id}>
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

const DisplayTimezoneContext = createContext<DisplayTimezoneContextValue | null>(null);

const STORAGE_PREFIX = 'ow:display-tz:';

function storageKey(userId: string | null | undefined): string {
  return `${STORAGE_PREFIX}${userId ?? 'global'}`;
}

function readStoredTz(userId: string | null | undefined): string {
  if (typeof window === 'undefined') return DEFAULT_DISPLAY_TZ;
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    return raw && raw.length > 0 ? raw : DEFAULT_DISPLAY_TZ;
  } catch {
    return DEFAULT_DISPLAY_TZ;
  }
}

interface DisplayTimezoneProviderProps {
  children: ReactNode;
  /** Scopes the persisted preference. Pass the user being viewed. */
  userId?: string | null;
}

export function DisplayTimezoneProvider({ children, userId }: DisplayTimezoneProviderProps) {
  const [displayTz, setDisplayTzState] = useState<string>(() => readStoredTz(userId));

  // If the userId changes (navigated to a different user), reload the
  // preference for the new scope.
  useEffect(() => {
    setDisplayTzState(readStoredTz(userId));
  }, [userId]);

  const setDisplayTz = useCallback(
    (tz: string) => {
      const next = tz && tz.length > 0 ? tz : DEFAULT_DISPLAY_TZ;
      setDisplayTzState(next);
      try {
        window.localStorage.setItem(storageKey(userId), next);
      } catch {
        // localStorage write can fail in privacy modes — ignore.
      }
    },
    [userId],
  );

  const resetDisplayTz = useCallback(() => {
    setDisplayTzState(DEFAULT_DISPLAY_TZ);
    try {
      window.localStorage.removeItem(storageKey(userId));
    } catch {
      // ignore
    }
  }, [userId]);

  const value = useMemo<DisplayTimezoneContextValue>(
    () => ({ displayTz, setDisplayTz, resetDisplayTz }),
    [displayTz, setDisplayTz, resetDisplayTz],
  );

  return (
    <DisplayTimezoneContext.Provider value={value}>{children}</DisplayTimezoneContext.Provider>
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
