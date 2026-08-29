/**
 * Display-timezone picker.
 *
 * Renders a dropdown of common IANA zones (plus the user's stored timezone if
 * provided) and writes the selection into DisplayTimezoneContext.
 *
 * This is a pure VIEW control — selecting a timezone never causes a request
 * or modifies the user's stored timezone. See the README "Fork Patches"
 * section for the rationale.
 */

import { Globe, Check } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { useDisplayTimezone } from '@/contexts/display-timezone';
import { COMMON_TIMEZONES, DEFAULT_DISPLAY_TZ } from '@/lib/dates';

interface TimezoneSelectorProps {
  /**
   * Optional — the user's stored timezone (User.timezone). When provided we
   * surface a "Use this user's timezone" shortcut at the top of the menu so
   * the developer doesn't have to hunt for "Australia/Brisbane" in the list.
   */
  userTimezone?: string | null;
  /** Optional render override for narrow layouts. */
  className?: string;
}

export function TimezoneSelector({
  userTimezone,
  className,
}: TimezoneSelectorProps) {
  const { displayTz, setDisplayTz, resetDisplayTz } = useDisplayTimezone();

  const hasUserTz = !!userTimezone && userTimezone.length > 0;
  const userTzInCommon =
    hasUserTz && COMMON_TIMEZONES.some((t) => t.value === userTimezone);

  const matchedCommon = COMMON_TIMEZONES.find((t) => t.value === displayTz);
  const buttonLabel =
    displayTz === userTimezone && hasUserTz
      ? `${userTimezone} (user)`
      : (matchedCommon?.label ?? displayTz);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={className}>
          <Globe className="h-3.5 w-3.5" />
          <span className="ml-2 truncate">{buttonLabel}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-64 bg-zinc-900/95 backdrop-blur-sm border-zinc-700/50"
      >
        <DropdownMenuLabel>Display timezone</DropdownMenuLabel>
        <DropdownMenuSeparator />

        {hasUserTz && (
          <>
            <DropdownMenuItem
              onClick={() => setDisplayTz(userTimezone)}
              className="flex items-center justify-between"
            >
              <span className="flex flex-col">
                <span className="text-[10px] uppercase tracking-wide text-zinc-500">
                  User's timezone
                </span>
                <span>{userTimezone}</span>
              </span>
              {displayTz === userTimezone && <Check className="h-4 w-4" />}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}

        <DropdownMenuLabel className="text-[10px] uppercase tracking-wide text-zinc-500 font-normal">
          Common
        </DropdownMenuLabel>
        {COMMON_TIMEZONES.map((tz) => {
          const isUserTz = hasUserTz && tz.value === userTimezone;
          return (
            <DropdownMenuItem
              key={tz.value}
              onClick={() => setDisplayTz(tz.value)}
              className="flex items-center justify-between"
            >
              <span>
                {tz.label}
                {isUserTz && (
                  <span className="ml-1 text-[10px] text-zinc-500">· user</span>
                )}
              </span>
              {displayTz === tz.value && <Check className="h-4 w-4" />}
            </DropdownMenuItem>
          );
        })}

        {/* resetDisplayTz clears the stored override, so the zone falls back to
            the user's own timezone — only UTC when that is unset. Offer it
            whenever the current zone differs from that fallback. */}
        {displayTz !== (userTimezone || DEFAULT_DISPLAY_TZ) && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={resetDisplayTz}>
              {hasUserTz ? `Reset to ${userTimezone}` : 'Reset to UTC'}
            </DropdownMenuItem>
          </>
        )}

        {/* userTzInCommon retained for completeness; left intentionally unused. */}
        {userTzInCommon && null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
