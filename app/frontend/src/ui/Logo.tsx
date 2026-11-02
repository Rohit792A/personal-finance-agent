/** Brand mark: a rising line chart in a rounded square. Decorative; pair it with the product name. */
export function Logo({ className = 'h-8 w-8' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={className}>
      <rect width="32" height="32" rx="8" className="fill-accent" />
      <path
        d="M8 21.5l5.2-5.4 4 3.6L24 12"
        fill="none"
        stroke="white"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24" cy="12" r="1.9" fill="white" />
    </svg>
  );
}
