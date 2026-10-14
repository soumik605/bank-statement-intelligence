// Small inline icons; stroke uses currentColor so they follow the text colour.
type Props = { className?: string };

export function UploadIcon({ className = "size-6" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} aria-hidden>
      <path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function WarningIcon({ className = "size-4" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
      <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function CheckIcon({ className = "size-4" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
      <path d="m5 12 5 5L20 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Spinner({ className = "size-8" }: Props) {
  return (
    <svg className={`${className} animate-spin`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity={0.2} strokeWidth={3} />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth={3} strokeLinecap="round" />
    </svg>
  );
}
