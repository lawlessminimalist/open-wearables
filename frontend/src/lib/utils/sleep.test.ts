import { describe, expect, it } from 'vitest';
import { calculateSleepStats } from './sleep';
import { formatBedtime } from './format';
import type { SleepSummary } from '@/lib/api/types';

/**
 * Regression tests for the timezone handling in calculateSleepStats.
 *
 * The bug these exist for: `tz` used to default to DEFAULT_DISPLAY_TZ ("UTC"),
 * and the only call site (sleep-section.tsx) omitted the argument entirely. So
 * avgBedtime was computed from UTC wall-clock minutes while every session row
 * rendered beside it used the display timezone. For a Brisbane user a 23:58
 * bedtime was reported as 13:58 — a plausible-looking number, ten hours out,
 * with nothing to flag it.
 *
 * `tz` is now required, so the omission is a type error. These tests pin the
 * behaviour itself: the same instants must yield different wall-clock averages
 * in different zones.
 */

/** Minimal SleepSummary; only start_time drives avgBedtime. */
function night(startTimeUtc: string): SleepSummary {
  return {
    date: startTimeUtc.slice(0, 10),
    start_time: startTimeUtc,
    end_time: null,
    duration_minutes: 480,
    efficiency_percent: null,
    stages: null,
    source: {
      provider: 'ultrahuman',
      source: 'ultrahuman',
      device: null,
      device_type: null,
      device_name: null,
    },
  } as unknown as SleepSummary;
}

describe('calculateSleepStats — avgBedtime timezone handling', () => {
  // 13:58Z is 23:58 the same evening in Brisbane (UTC+10).
  const nights = [
    night('2026-08-25T13:58:00Z'),
    night('2026-08-26T13:58:00Z'),
    night('2026-08-27T13:58:00Z'),
  ];

  it('reports a Brisbane evening bedtime for a Brisbane user', () => {
    const stats = calculateSleepStats(nights, 'Australia/Brisbane');
    // 23:58 -> 23 * 60 + 58
    expect(stats?.avgBedtime).toBe(23 * 60 + 58);
    expect(formatBedtime(stats?.avgBedtime ?? null)).toContain('11:58');
  });

  it('reports the UTC wall-clock time when UTC is the display zone', () => {
    const stats = calculateSleepStats(nights, 'UTC');
    // 13:58 is after 06:00, so it is NOT shifted into the previous evening.
    expect(stats?.avgBedtime).toBe(13 * 60 + 58);
  });

  it('gives a different answer per zone — the bug was these agreeing', () => {
    const brisbane = calculateSleepStats(nights, 'Australia/Brisbane');
    const utc = calculateSleepStats(nights, 'UTC');
    expect(brisbane?.avgBedtime).not.toBe(utc?.avgBedtime);
    // Brisbane is UTC+10, so exactly 600 minutes apart here.
    expect((brisbane?.avgBedtime ?? 0) - (utc?.avgBedtime ?? 0)).toBe(600);
  });

  it('treats a past-midnight bedtime as the previous evening', () => {
    // 16:30Z = 02:30 Brisbane next day. Should wrap to 26:30 (1590) so that
    // averaging a 23:00 and a 01:00 night does not land at midday.
    const stats = calculateSleepStats(
      [night('2026-08-25T16:30:00Z')],
      'Australia/Brisbane'
    );
    expect(stats?.avgBedtime).toBe(2 * 60 + 30 + 1440);
  });

  it('returns null for no summaries', () => {
    expect(calculateSleepStats([], 'Australia/Brisbane')).toBeNull();
  });
});
