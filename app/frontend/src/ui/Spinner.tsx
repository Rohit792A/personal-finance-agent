import { LoaderCircle } from 'lucide-react';

export function Spinner({ label, className = 'h-4 w-4' }: { label?: string; className?: string }) {
  return (
    <>
      <LoaderCircle aria-hidden="true" className={`${className} animate-spin`} />
      {label ? <span className="sr-only">{label}</span> : null}
    </>
  );
}
