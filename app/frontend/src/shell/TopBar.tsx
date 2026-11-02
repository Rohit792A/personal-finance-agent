import { Menu, Sparkles } from 'lucide-react';
import type { Ref } from 'react';
import { ThemeToggle } from '../ui/ThemeToggle';
import { PeriodPicker } from './PeriodPicker';
import { SearchBox } from './SearchBox';
import type { MonthOption } from './useMonths';
import { UserMenu } from './UserMenu';

interface TopBarProps {
  title: string;
  month: string;
  q: string;
  months: MonthOption[];
  onMonthChange: (month: string) => void;
  onOpenNav: () => void;
  menuButtonRef: Ref<HTMLButtonElement>;
  jarvisOpen?: boolean;
  onToggleJarvis?: () => void;
}

export function TopBar({
  title,
  month,
  q,
  months,
  onMonthChange,
  onOpenNav,
  menuButtonRef,
  jarvisOpen,
  onToggleJarvis,
}: TopBarProps) {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-surface">
      <div className="flex min-h-14 flex-wrap items-center gap-2 px-4 py-2 sm:px-6 lg:px-8">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onOpenNav}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus lg:hidden"
          aria-label="Open navigation"
          data-testid="open-navigation"
        >
          <Menu aria-hidden="true" className="h-5 w-5" />
        </button>
        <p className="hidden truncate text-base font-semibold text-fg md:block">{title}</p>

        <div className="ml-auto">
          <PeriodPicker month={month} months={months} onChange={onMonthChange} />
        </div>
        {/* Wraps onto its own full-width row on phones. */}
        <div className="order-last w-full sm:order-none sm:w-64 xl:w-80">
          <SearchBox q={q} month={month} />
        </div>
        {onToggleJarvis ? (
          <button
            type="button"
            onClick={onToggleJarvis}
            className={`inline-flex h-9 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus ${
              jarvisOpen
                ? 'border-accent bg-accent text-accent-fg shadow-sm'
                : 'border-accent/40 bg-accent-soft/50 text-accent hover:bg-accent-soft'
            }`}
            aria-label="Toggle Jarvis Master Orchestrator Copilot"
            title="Jarvis · Master CFO Orchestrator (Voice & Text Copilot across all screens)"
            data-testid="topbar-jarvis-toggle"
          >
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Jarvis AI</span>
          </button>
        ) : null}
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
