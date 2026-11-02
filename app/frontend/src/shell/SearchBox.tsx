import { Search, X } from 'lucide-react';
import { useEffect, useId, useRef, useState, type SubmitEvent } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { HOME_PATH } from './screens';
import { serializeUrlState } from './urlState';

const shortcutLabel = navigator.userAgent.includes('Mac') ? '⌘K' : 'Ctrl K';

/** Searches the Transactions ledger: submitting navigates to /transactions?q=… (Ctrl/⌘+K focuses it). */
export function SearchBox({ q, month }: { q: string; month: string }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(q);
  const [syncedQ, setSyncedQ] = useState(q);

  // Follow the URL when it changes (back/forward, ledger search box, links).
  if (q !== syncedQ) {
    setSyncedQ(q);
    setDraft(q);
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  function search(text: string) {
    void navigate({ pathname: HOME_PATH, search: serializeUrlState({ month, q: text, view: 'ledger', year: null }) });
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    search(draft);
  }

  function handleClear() {
    setDraft('');
    if (pathname === HOME_PATH && q) search('');
    inputRef.current?.focus();
  }

  return (
    <form role="search" onSubmit={handleSubmit} className="relative w-full">
      <label htmlFor={id} className="sr-only">
        Search transactions
      </label>
      <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
      <input
        ref={inputRef}
        id={id}
        type="search"
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
        }}
        placeholder="Search transactions"
        autoComplete="off"
        enterKeyHint="search"
        className="h-9 w-full rounded-lg border border-line-strong bg-surface pl-9 pr-16 text-sm text-fg placeholder:text-fg-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-focus/40"
        data-testid="global-search"
      />
      {draft ? (
        <button
          type="button"
          onClick={handleClear}
          className="absolute right-1.5 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus"
          aria-label="Clear search"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      ) : (
        <kbd className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 rounded border border-line bg-surface-2 px-1.5 py-0.5 font-sans text-xs text-fg-muted sm:block">
          {shortcutLabel}
        </kbd>
      )}
    </form>
  );
}
