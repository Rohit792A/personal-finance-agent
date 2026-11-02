import {
  ArrowLeftRight,
  Bot,
  CalendarClock,
  ChartColumn,
  Landmark,
  Sparkles,
  Target,
  Upload,
  type LucideIcon,
} from 'lucide-react';

export type ScreenId =
  | 'transactions'
  | 'analytics'
  | 'budgets'
  | 'forecast'
  | 'upload'
  | 'ai-cfo'
  | 'agents'
  | 'bank-sync';

export interface ScreenDef {
  id: ScreenId;
  path: string;
  label: string;
  icon: LucideIcon;
}

export const SCREENS: readonly ScreenDef[] = [
  { id: 'transactions', path: '/transactions', label: 'Transactions', icon: ArrowLeftRight },
  { id: 'analytics', path: '/analytics', label: 'Analytics', icon: ChartColumn },
  { id: 'budgets', path: '/budgets', label: 'Budgets', icon: Target },
  { id: 'forecast', path: '/forecast', label: 'Recurring & Forecast', icon: CalendarClock },
  { id: 'upload', path: '/upload', label: 'Upload', icon: Upload },
  { id: 'ai-cfo', path: '/ai-cfo', label: 'AI CFO', icon: Sparkles },
  { id: 'agents', path: '/agents', label: 'Agent Registry', icon: Bot },
  { id: 'bank-sync', path: '/bank-sync', label: 'Bank Sync', icon: Landmark },
];

export const HOME_PATH = '/transactions';

export function screenForPath(pathname: string): ScreenDef | null {
  const path = pathname.replace(/\/+$/, '') || '/';
  return SCREENS.find((screen) => screen.path === path) ?? null;
}

export function screenById(id: ScreenId): ScreenDef {
  const screen = SCREENS.find((item) => item.id === id);
  if (!screen) throw new Error(`Unknown screen: ${id}`);
  return screen;
}

/** Page ids the legacy screens still use for cross-links (ledger home tiles). */
const SCREEN_BY_LEGACY_PAGE: Record<string, ScreenId> = {
  'page-transactions': 'transactions',
  'page-summary': 'analytics',
  'page-budgets': 'budgets',
  'page-recurring': 'forecast',
  'page-advisor': 'ai-cfo',
  'page-bank': 'bank-sync',
};

export function screenForLegacyPage(pageId: string): ScreenId | null {
  return Object.hasOwn(SCREEN_BY_LEGACY_PAGE, pageId) ? (SCREEN_BY_LEGACY_PAGE[pageId] ?? null) : null;
}
