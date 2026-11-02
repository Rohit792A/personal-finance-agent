import { FlaskConical } from 'lucide-react';
import type { Me } from '../api/me';

export function DemoBanner({ me }: { me: Me }) {
  const hasLimits = me.ai_limits !== null && Object.keys(me.ai_limits).length > 0;
  return (
    <div
      className="border-b border-warning/30 bg-warning-soft px-4 py-2 text-sm text-warning sm:px-6 lg:px-8"
      data-testid="demo-banner"
    >
      <p className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-2 gap-y-1">
        <FlaskConical aria-hidden="true" className="h-4 w-4 shrink-0" />
        <span>
          <strong className="font-semibold">Demo</strong> · sample data{me.data_resets_daily ? ', resets nightly' : ''}
        </span>
        {hasLimits ? <span className="text-warning/90">· AI features have a daily limit</span> : null}
      </p>
    </div>
  );
}
