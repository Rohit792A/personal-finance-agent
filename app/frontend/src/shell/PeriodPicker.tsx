import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useId, useMemo } from 'react';
import type { MonthOption } from './useMonths';
import { ALL_TIME, formatMonthLabel } from './urlState';

interface PeriodPickerProps {
  month: string;
  months: MonthOption[];
  onChange: (month: string) => void;
}

const arrowClass =
  'inline-flex h-9 w-9 items-center justify-center text-fg-muted hover:bg-surface-2 hover:text-fg ' +
  'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus';

/** "All time" or one month; the arrows step to the previous (older) and next (newer) month. */
export function PeriodPicker({ month, months, onChange }: PeriodPickerProps) {
  const id = useId();

  // A deep link can name a month the list doesn't have (yet); keep it selectable.
  const options = useMemo(() => {
    if (month === ALL_TIME || months.some((option) => option.month === month)) return months;
    return [...months, { month, label: formatMonthLabel(month) }].sort((a, b) => b.month.localeCompare(a.month));
  }, [month, months]);

  const index = options.findIndex((option) => option.month === month);
  const older = month === ALL_TIME ? options[0] : options[index + 1];
  const newer = month === ALL_TIME || index <= 0 ? undefined : options[index - 1];

  return (
    <div className="flex h-9 items-stretch overflow-hidden rounded-lg border border-line-strong bg-surface">
      <button
        type="button"
        className={`${arrowClass} rounded-l-lg`}
        onClick={() => {
          if (older) onChange(older.month);
        }}
        disabled={!older}
        aria-label={older ? `Previous month: ${older.label}` : 'Previous month'}
        title="Previous month"
      >
        <ChevronLeft aria-hidden="true" className="h-4 w-4" />
      </button>
      <label htmlFor={id} className="sr-only">
        Period
      </label>
      <select
        id={id}
        value={month}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        className="min-w-0 max-w-[11rem] cursor-pointer border-x border-line-strong bg-surface px-2 text-sm font-medium text-fg focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus"
        data-testid="period-picker"
      >
        <option value={ALL_TIME}>All time</option>
        {options.map((option) => (
          <option key={option.month} value={option.month}>
            {option.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        className={`${arrowClass} rounded-r-lg`}
        onClick={() => {
          if (newer) onChange(newer.month);
        }}
        disabled={!newer}
        aria-label={newer ? `Next month: ${newer.label}` : 'Next month'}
        title="Next month"
      >
        <ChevronRight aria-hidden="true" className="h-4 w-4" />
      </button>
    </div>
  );
}
