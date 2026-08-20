import { formatInTz, DEFAULT_DISPLAY_TZ } from '@/lib/dates';
import type { TimeSeriesSample } from '@/lib/api/types';

/**
 * Prepared heart rate chart data point
 */
export interface HrChartDataPoint {
  time: string;
  hr: number;
}

/**
 * Prepare heart rate time series data for chart display.
 * Filters to heart_rate type, sorts by timestamp, and formats time labels
 * in the caller's display timezone so the x-axis matches the user's expected
 * wall-clock view.
 */
export function prepareHrChartData(
  data: TimeSeriesSample[] | undefined,
  tz: string = DEFAULT_DISPLAY_TZ
): HrChartDataPoint[] {
  if (!data?.length) return [];

  return data
    .filter((d) => d.type === 'heart_rate')
    .sort(
      (a, b) =>
        new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
    )
    .map((d) => ({
      time: formatInTz(d.timestamp, tz, 'HH:mm'),
      hr: d.value,
    }));
}
