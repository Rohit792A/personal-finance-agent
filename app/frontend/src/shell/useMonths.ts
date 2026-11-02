import { useEffect, useState } from 'react';
import { getTransactionMonths, type MonthsResponse } from '../api/transactions';
import { formatMonthLabel, isMonthKey } from './urlState';

export interface MonthOption {
  month: string;
  label: string;
}

function toOptions(response: MonthsResponse): MonthOption[] {
  const seen = new Set<string>();
  const options: MonthOption[] = [];
  for (const item of response.months) {
    const month = typeof item.month === 'string' ? item.month : '';
    if (!isMonthKey(month) || seen.has(month)) continue;
    seen.add(month);
    const label = typeof item.label === 'string' && item.label.trim() ? item.label.trim() : formatMonthLabel(month);
    options.push({ month, label });
  }
  return options.sort((a, b) => b.month.localeCompare(a.month));
}

/** Months that have transactions (newest first); reloads when `version` changes. */
export function useMonths(version: number): MonthOption[] {
  const [months, setMonths] = useState<MonthOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    getTransactionMonths().then(
      (response) => {
        if (!cancelled) setMonths(toOptions(response));
      },
      () => {
        // Keep the previous list; the picker still offers "All time" and the URL's month.
      },
    );
    return () => {
      cancelled = true;
    };
  }, [version]);

  return months;
}
