import type { ReactNode } from 'react';
import { Logo } from './Logo';

/** Full-page layout for sign-in adjacent states: loading, no access, errors. */
export function CenteredMessage({
  title,
  icon,
  children,
  actions,
  busy = false,
}: {
  title: string;
  icon?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  busy?: boolean;
}) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-4 py-10 text-fg">
      <main className="w-full max-w-md text-center" aria-busy={busy}>
        <div className="mb-6 flex items-center justify-center gap-2">
          <Logo className="h-7 w-7" />
          <span className="text-base font-semibold">Personal Finance AI</span>
        </div>
        <div className="rounded-xl border border-line bg-surface p-6 shadow-sm" role={busy ? 'status' : undefined}>
          {icon ? <div className="mb-3 flex justify-center text-fg-muted">{icon}</div> : null}
          <h1 className="text-lg font-semibold">{title}</h1>
          {children ? <div className="mt-2 space-y-2 text-sm text-fg-muted">{children}</div> : null}
          {actions ? <div className="mt-5 flex flex-wrap justify-center gap-2">{actions}</div> : null}
        </div>
      </main>
    </div>
  );
}
