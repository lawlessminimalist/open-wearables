/**
 * Timezone-aware datetime helpers for the dashboard.
 *
 * The codebase has two distinct timezones — see ow-patches/PATCHES.md and
 * the README "Fork Patches" section for details:
 *
 *   - User Timezone (User.timezone IANA, stored on the backend)
 *     Controls how the backend buckets daily activity / sleep summaries.
 *
 *   - Display Timezone (frontend-only, ephemeral)
 *     Controls how this client renders UTC ISO timestamps as wall-clock text.
 *     Defaults to "UTC". Bound to a context (see contexts/display-timezone.tsx);
 *     persisted in localStorage per user_id; user-changeable via the picker
 *     in components/common/timezone-selector.tsx. Does NOT affect the data.
 *
 * The two are intentionally decoupled: changing the display tz never touches
 * the bucketing the backend sends, so the "Sun, May 3" card label stays
 * stable as developers toggle their own viewing preference.
 */

import { format } from 'date-fns';
import { formatInTimeZone, toZonedTime } from 'date-fns-tz';

/** Canonical "no timezone known yet" fallback. */
export const DEFAULT_DISPLAY_TZ = 'UTC';

/**
 * Format a UTC ISO timestamp (or Date) as wall-clock text in the given IANA tz.
 *
 * Returns an empty string for null/undefined inputs so call sites don't have
 * to repeat the guard. If the IANA name is invalid we fall back to UTC and
 * log once — failing closed beats throwing inside a render path.
 */
export function formatInTz(
  input: string | Date | null | undefined,
  tz: string | null | undefined,
  fmt: string
): string {
  if (input === null || input === undefined || input === '') {
    return '';
  }
  const zone = tz && tz.length > 0 ? tz : DEFAULT_DISPLAY_TZ;
  try {
    return formatInTimeZone(input, zone, fmt);
  } catch (err) {
    if (zone !== DEFAULT_DISPLAY_TZ) {
      // eslint-disable-next-line no-console
      console.warn(
        `formatInTz: invalid timezone ${JSON.stringify(zone)}, falling back to UTC`,
        err
      );
    }
    try {
      return formatInTimeZone(input, DEFAULT_DISPLAY_TZ, fmt);
    } catch {
      return typeof input === 'string' ? input : input.toISOString();
    }
  }
}

/**
 * Convert a UTC ISO timestamp to a Date object whose wall-clock fields
 * reflect the given timezone. Useful when feeding a charting library that
 * formats Dates with the local-zone formatters and expects "naive" wall time.
 */
export function toLocalWallTime(
  input: string | Date,
  tz: string | null | undefined
): Date {
  const zone = tz && tz.length > 0 ? tz : DEFAULT_DISPLAY_TZ;
  return toZonedTime(input, zone);
}

/**
 * Curated list shown in the display-timezone picker. Keep this short — users
 * who need an esoteric zone can extend it; this list covers the common cases
 * and avoids overwhelming the dropdown with the full IANA database.
 */
export const COMMON_TIMEZONES: Array<{ value: string; label: string }> = [
  { value: 'UTC', label: 'UTC' },
  { value: 'America/Los_Angeles', label: 'Pacific (Los Angeles)' },
  { value: 'America/Denver', label: 'Mountain (Denver)' },
  { value: 'America/Chicago', label: 'Central (Chicago)' },
  { value: 'America/New_York', label: 'Eastern (New York)' },
  { value: 'America/Sao_Paulo', label: 'Brazil (São Paulo)' },
  { value: 'Europe/London', label: 'London' },
  { value: 'Europe/Berlin', label: 'Berlin / Paris / Madrid' },
  { value: 'Europe/Athens', label: 'Athens / Helsinki' },
  { value: 'Africa/Johannesburg', label: 'Johannesburg' },
  { value: 'Asia/Dubai', label: 'Dubai' },
  { value: 'Asia/Kolkata', label: 'India (Kolkata)' },
  { value: 'Asia/Singapore', label: 'Singapore' },
  { value: 'Asia/Tokyo', label: 'Tokyo / Seoul' },
  { value: 'Australia/Perth', label: 'Perth' },
  { value: 'Australia/Brisbane', label: 'Brisbane' },
  { value: 'Australia/Sydney', label: 'Sydney / Melbourne' },
  { value: 'Pacific/Auckland', label: 'Auckland' },
];

/** Convenience for the (rare) places that need a non-tz-aware format. */
export function formatLocal(
  input: string | Date | null | undefined,
  fmt: string
): string {
  if (input === null || input === undefined || input === '') return '';
  return format(typeof input === 'string' ? new Date(input) : input, fmt);
}
