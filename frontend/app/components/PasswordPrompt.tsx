"use client";

import { useState } from "react";

function LockIcon({ className = "size-8" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} aria-hidden>
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 1 1 8 0v3" strokeLinecap="round" />
    </svg>
  );
}

export default function PasswordPrompt({
  fileName,
  incorrect,
  onSubmit,
  onCancel,
}: {
  fileName: string;
  incorrect: boolean;
  onSubmit: (password: string) => void;
  onCancel: () => void;
}) {
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (password) onSubmit(password);
      }}
      className="flex flex-col gap-5"
    >
      <div className="flex flex-col items-center gap-3 text-center">
        <LockIcon className="size-9 text-muted" />
        <div>
          <p className="text-lg font-medium">This PDF is password protected</p>
          <p className="mt-1 break-all text-sm text-ink-2">{fileName}</p>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor="pdf-password" className="text-sm font-medium">
          Statement password
        </label>
        <div className="flex gap-2">
          <input
            id="pdf-password"
            type={visible ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="off"
            spellCheck={false}
            aria-invalid={incorrect}
            aria-describedby="pdf-password-help"
            className="h-11 min-w-0 flex-1 rounded-lg border border-border bg-plane px-3 aria-invalid:border-critical"
          />
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            aria-pressed={visible}
            className="h-11 rounded-lg border border-border px-3 text-sm text-ink-2 hover:bg-wash"
          >
            {visible ? "Hide" : "Show"}
          </button>
        </div>
        {incorrect && (
          <p role="alert" className="text-sm text-critical">
            That password didn&apos;t open the file. Check it and try again.
          </p>
        )}
        <p id="pdf-password-help" className="text-xs text-muted">
          Banks often build it from your name and date of birth; the email the statement came with usually says how.
          It&apos;s only used to open the file and is never saved.
        </p>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row-reverse">
        <button
          type="submit"
          disabled={!password}
          className="h-11 flex-1 rounded-lg bg-ink px-5 font-medium text-plane transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
        >
          Unlock and analyze
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="h-11 rounded-lg border border-border px-5 font-medium transition-colors hover:bg-wash"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
