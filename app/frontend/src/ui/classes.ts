/** Shared class strings for the shell's controls (no UI kit until Phase 3). */

const base =
  'inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus ' +
  'disabled:cursor-not-allowed disabled:opacity-60';

const variants = {
  primary: 'bg-accent text-accent-fg hover:bg-accent-hover',
  secondary: 'border border-line-strong bg-surface text-fg hover:bg-surface-2',
  ghost: 'text-fg-muted hover:bg-surface-2 hover:text-fg',
} as const;

const sizes = {
  md: 'h-10 px-4',
  sm: 'h-9 px-3',
  icon: 'h-9 w-9',
} as const;

export function buttonClass(variant: keyof typeof variants = 'secondary', size: keyof typeof sizes = 'md'): string {
  return `${base} ${variants[variant]} ${sizes[size]}`;
}

export const inputClass =
  'block w-full rounded-lg border border-line-strong bg-surface px-3 text-sm text-fg placeholder:text-fg-muted ' +
  'focus:border-accent focus:outline-none focus:ring-2 focus:ring-focus/40 disabled:opacity-60';
