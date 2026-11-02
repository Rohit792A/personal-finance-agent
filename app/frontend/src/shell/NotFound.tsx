import { FileQuestion } from 'lucide-react';
import { Link } from 'react-router';
import { buttonClass } from '../ui/classes';
import { HOME_PATH } from './screens';

export function NotFound() {
  return (
    <section className="mx-auto mt-6 max-w-xl rounded-xl border border-line bg-surface p-8 text-center shadow-sm">
      <FileQuestion aria-hidden="true" className="mx-auto h-10 w-10 text-fg-muted" />
      <h2 className="mt-4 text-lg font-semibold text-fg">Page not found</h2>
      <p className="mt-2 text-sm text-fg-muted">This address doesn't match a page in the app.</p>
      <Link to={HOME_PATH} className={`${buttonClass('primary', 'sm')} mt-6`}>
        Go to Transactions
      </Link>
    </section>
  );
}
