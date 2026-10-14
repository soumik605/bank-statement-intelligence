"use client";

import { useRef, useState } from "react";
import { analyze, ApiError, fetchSample, getAnalysis } from "@/lib/api";
import type { AnalyzeResponse } from "@/lib/types";
import HistoryList from "./components/HistoryList";
import LoadingState from "./components/LoadingState";
import PasswordPrompt from "./components/PasswordPrompt";
import Results from "./components/Results";
import UploadPanel from "./components/UploadPanel";

type State =
  | { status: "idle"; error: string | null }
  | { status: "loading"; fileName: string; reopening: boolean }
  // A protected PDF: keep the file so the user only has to type the password.
  | { status: "password"; file: File; incorrect: boolean; attempt: number }
  | { status: "done"; result: AnalyzeResponse };

export default function Home() {
  const [state, setState] = useState<State>({ status: "idle", error: null });
  const controller = useRef<AbortController | null>(null);

  async function run(
    fileName: string,
    getResult: (signal: AbortSignal) => Promise<AnalyzeResponse>,
    { reopening = false, onError }: { reopening?: boolean; onError?: (e: unknown) => boolean } = {},
  ) {
    const abort = new AbortController();
    controller.current = abort;
    setState({ status: "loading", fileName, reopening });
    try {
      const result = await getResult(abort.signal);
      setState({ status: "done", result });
    } catch (e) {
      if (abort.signal.aborted) return; // cancelled; the cancel handler already reset the page
      if (onError?.(e)) return;
      setState({ status: "idle", error: e instanceof Error ? e.message : "Something went wrong." });
    }
  }

  // The server checks for a password before doing any work, so a locked PDF
  // comes back within a second and we ask for the password.
  function analyzeFile(
    fileName: string,
    getFile: (signal: AbortSignal) => Promise<File>,
    password?: string,
    attempt = 0,
  ) {
    let file: File | null = null;
    run(
      fileName,
      async (signal) => {
        file = await getFile(signal);
        return analyze(file, signal, password);
      },
      {
        onError: (e) => {
          const locked = e instanceof ApiError && (e.code === "password_required" || e.code === "incorrect_password");
          if (!locked || !file) return false;
          setState({ status: "password", file, incorrect: e.code === "incorrect_password", attempt: attempt + 1 });
          return true;
        },
      },
    );
  }

  function cancel() {
    controller.current?.abort();
    setState({ status: "idle", error: null });
  }

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-4 py-10 sm:px-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Bank Statement Intelligence</h1>
        <p className="mt-1 text-ink-2">
          Upload a bank statement to get categorized transactions, totals, unusual items and a plain-English summary.
        </p>
      </header>

      {state.status === "done" ? (
        <Results result={state.result} onReset={() => setState({ status: "idle", error: null })} />
      ) : (
        <div className="mx-auto w-full max-w-xl rounded-xl border border-border bg-surface p-6 sm:p-8">
          {state.status === "loading" && (
            <LoadingState fileName={state.fileName} reopening={state.reopening} onCancel={cancel} />
          )}
          {state.status === "password" && (
            <PasswordPrompt
              // A fresh prompt (empty field) after each wrong attempt.
              key={state.attempt}
              fileName={state.file.name}
              incorrect={state.incorrect}
              onSubmit={(password) => {
                const { file, attempt } = state;
                analyzeFile(file.name, async () => file, password, attempt);
              }}
              onCancel={() => setState({ status: "idle", error: null })}
            />
          )}
          {state.status === "idle" && (
            <UploadPanel
              error={state.error}
              onAnalyze={(file) => analyzeFile(file.name, async () => file)}
              onSample={(name) => analyzeFile(name, (signal) => fetchSample(name, signal))}
            />
          )}
        </div>
      )}

      {state.status === "idle" && (
        <div className="mx-auto w-full max-w-3xl">
          <HistoryList
            onOpen={(item) => run(item.source_file, (signal) => getAnalysis(item.id, signal), { reopening: true })}
          />
        </div>
      )}
    </main>
  );
}
