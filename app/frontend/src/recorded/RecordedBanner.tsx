import { CirclePlay, CodeXml, History } from 'lucide-react';
import { recordedManifest } from './manifest';
import { REPO_URL, VIDEO_URL } from './mode';

/** '2026-11-02' -> '2 November 2026' (the date as recorded, whatever the visitor's time zone). */
function longDate(day: string): string {
  const parsed = new Date(`${day}T00:00:00Z`);
  if (!day || Number.isNaN(parsed.getTime())) return day;
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    parsed,
  );
}

const linkClass =
  'inline-flex items-center gap-1 font-medium underline decoration-accent-text/40 underline-offset-2 ' +
  'hover:decoration-accent-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus';

/** Shown instead of the demo banner in the recorded demo build. */
export function RecordedBanner() {
  const recordedOn = recordedManifest()?.recordedOn ?? '';
  return (
    <div
      className="border-b border-accent/30 bg-accent-soft px-4 py-2 text-sm text-accent-text sm:px-6 lg:px-8"
      data-testid="recorded-demo-banner"
    >
      <p className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-2">
          <History aria-hidden="true" className="h-4 w-4 shrink-0" />
          <span>
            <strong className="font-semibold">Recorded demo</strong> · responses captured from the real backend
            {recordedOn ? ` on ${longDate(recordedOn)}` : ''}
          </span>
        </span>
        {REPO_URL ? (
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className={linkClass}>
            <CodeXml aria-hidden="true" className="h-4 w-4" />
            Source on GitHub
          </a>
        ) : null}
        {VIDEO_URL ? (
          <a href={VIDEO_URL} target="_blank" rel="noopener noreferrer" className={linkClass}>
            <CirclePlay aria-hidden="true" className="h-4 w-4" />
            Demo video
          </a>
        ) : null}
      </p>
    </div>
  );
}
