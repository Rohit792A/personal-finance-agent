import { CloudUpload, MailX, Sparkles, type LucideIcon } from 'lucide-react';
import { Link } from 'react-router';
import type { Environment } from '../api/me';
import { buttonClass } from '../ui/classes';
import { HOME_PATH, screenById } from './screens';
import { screenSearch } from './urlState';

export type UnavailableFeature = 'gmail' | 'uploads' | 'ai';

interface Copy {
  icon: LucideIcon;
  title: (environment: Environment) => string;
  body: (environment: Environment) => string;
}

const COPY: Record<UnavailableFeature, Copy> = {
  gmail: {
    icon: MailX,
    title: (environment) => (environment === 'demo' ? 'Gmail sync is turned off in the demo' : 'Gmail sync is turned off'),
    body: (environment) =>
      environment === 'demo'
        ? "The demo runs on sample data, so it isn't connected to a Gmail inbox. In the personal app, bank alert emails and monthly statements are imported from Gmail automatically."
        : "Gmail sync isn't enabled on this server.",
  },
  uploads: {
    icon: CloudUpload,
    title: () => 'Uploads are turned off',
    body: (environment) =>
      environment === 'demo' ? "Uploading documents isn't available in this demo." : "Uploads aren't enabled on this server.",
  },
  ai: {
    icon: Sparkles,
    title: () => 'AI features are turned off',
    body: (environment) =>
      environment === 'demo' ? "The AI CFO isn't available in this demo." : "AI features aren't enabled on this server.",
  },
};

/** Shown instead of a legacy screen whose feature this backend has switched off. */
export function FeatureUnavailable({
  feature,
  environment,
  month,
  uploadsEnabled,
}: {
  feature: UnavailableFeature;
  environment: Environment;
  month: string;
  uploadsEnabled: boolean;
}) {
  const copy = COPY[feature];
  const Icon = copy.icon;
  const search = screenSearch(month);
  return (
    <section
      className="mx-auto mt-6 max-w-xl rounded-xl border border-line bg-surface p-8 text-center shadow-sm"
      data-testid={`feature-unavailable-${feature}`}
    >
      <Icon aria-hidden="true" className="mx-auto h-10 w-10 text-fg-muted" />
      <h2 className="mt-4 text-lg font-semibold text-fg">{copy.title(environment)}</h2>
      <p className="mt-2 text-sm text-fg-muted">{copy.body(environment)}</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {feature === 'gmail' && uploadsEnabled ? (
          <Link to={{ pathname: screenById('upload').path, search }} className={buttonClass('primary', 'sm')}>
            Upload a statement instead
          </Link>
        ) : null}
        <Link to={{ pathname: HOME_PATH, search }} className={buttonClass('secondary', 'sm')}>
          View transactions
        </Link>
      </div>
    </section>
  );
}
