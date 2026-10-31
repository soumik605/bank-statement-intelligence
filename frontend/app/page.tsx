"use client";

import { useEffect, useRef, useState } from "react";
import { analyze, ApiError, fetchSample, getAnalysis, listModels, retryFailure } from "@/lib/api";
import type { AnalyzeResponse, FailureItem, ModelOption } from "@/lib/types";
import FailuresList from "./components/FailuresList";
import HistoryList from "./components/HistoryList";
import LoadingState from "./components/LoadingState";
import PasswordPrompt from "./components/PasswordPrompt";
import Results from "./components/Results";
import UploadPanel from "./components/UploadPanel";

const MODEL_KEY = "bsi.model";

type State =
  | { status: "idle"; error: string | null; failureId?: number | null }
  | { status: "loading"; fileName: string; reopening: boolean }
  // A protected PDF: `submit` re-runs whatever needed the password (an upload or a
  // retry), so the user only has to type it.
  | {
      status: "password";
      fileName: string;
      incorrect: boolean;
      attempt: number;
      submit: (password: string, attempt: number) => void;
    }
  | { status: "done"; result: AnalyzeResponse };

export default function Home() {
  const [state, setState] = useState<State>({ status: "idle", error: null });
  const controller = useRef<AbortController | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState<string | null>(null);

  useEffect(() => {
    listModels().then(
      (list) => {
        setModels(list.models);
        // The viewer's last choice, if it's still offered; otherwise the server's default.
        let saved: string | null = null;
        try {
          saved = window.localStorage.getItem(MODEL_KEY);
        } catch {
          // storage unavailable (private window, blocked); use the default
        }
        // A model without an API key is never picked for you.
        const usable = (key: string | null) => list.models.some((m) => m.key === key && m.available);
        setModel(usable(saved) ? saved : usable(list.default) ? list.default : (list.models.find((m) => m.available)?.key ?? list.default));
      },
      () => {}, // no list: the server's default model is used, and errors show on upload
    );
  }, []);

  function chooseModel(key: string) {
    setModel(key);
    try {
      window.localStorage.setItem(MODEL_KEY, key);
    } catch {
      // not remembered; it still applies to this visit
    }
  }

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
      setState({
        status: "idle",
        error: e instanceof Error ? e.message : "Something went wrong.",
        failureId: e instanceof ApiError ? e.failureId : null,
      });
    }
  }

  // The server checks for a password before doing any work, so a locked PDF
  // comes back within a second and we ask for the password.
  function askForPassword(e: unknown, fileName: string, attempt: number, submit: (password: string, attempt: number) => void) {
    const locked = e instanceof ApiError && (e.code === "password_required" || e.code === "incorrect_password");
    if (locked) {
      setState({ status: "password", fileName, incorrect: e.code === "incorrect_password", attempt: attempt + 1, submit });
    }
    return locked;
  }

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
        return analyze(file, signal, password, model ?? undefined);
      },
      {
        onError: (e) => {
          const kept = file;
          return !!kept && askForPassword(e, kept.name, attempt, (pw, n) => analyzeFile(kept.name, async () => kept, pw, n));
        },
      },
    );
  }

  function retry(item: FailureItem, password?: string, attempt = 0) {
    run(item.source_file, (signal) => retryFailure(item.id, signal, password, model ?? undefined), {
      onError: (e) => {
        if (askForPassword(e, item.source_file, attempt, (pw, n) => retry(item, pw, n))) return true;
        setState({
          status: "idle",
          error: `Retry failed: ${e instanceof Error ? e.message : "something went wrong."}`,
          failureId: e instanceof ApiError ? e.failureId : null,
        });
        return true;
      },
    });
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
        <Results
          result={state.result}
          models={models}
          onReset={() => setState({ status: "idle", error: null })}
        />
      ) : (
        <div className="mx-auto w-full max-w-xl rounded-xl border border-border bg-surface p-6 sm:p-8">
          {state.status === "loading" && (
            <LoadingState fileName={state.fileName} reopening={state.reopening} onCancel={cancel} />
          )}
          {state.status === "password" && (
            <PasswordPrompt
              // A fresh prompt (empty field) after each wrong attempt.
              key={state.attempt}
              fileName={state.fileName}
              incorrect={state.incorrect}
              onSubmit={(password) => state.submit(password, state.attempt)}
              onCancel={() => setState({ status: "idle", error: null })}
            />
          )}
          {state.status === "idle" && (
            <UploadPanel
              error={state.error}
              failureId={state.failureId ?? null}
              models={models}
              model={model}
              onModelChange={chooseModel}
              onAnalyze={(file) => analyzeFile(file.name, async () => file)}
              onSample={(name) => analyzeFile(name, (signal) => fetchSample(name, signal))}
            />
          )}
        </div>
      )}

      {state.status === "idle" && (
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
          <HistoryList
            onOpen={(item) => run(item.source_file, (signal) => getAnalysis(item.id, signal), { reopening: true })}
          />
          <FailuresList
            onRetry={(item) => retry(item)}
            onOpenResult={(analysisId, fileName) =>
              run(fileName, (signal) => getAnalysis(analysisId, signal), { reopening: true })
            }
          />
        </div>
      )}
    </main>
  );
}
