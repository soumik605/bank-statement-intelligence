"use client";

import { useRef, useState } from "react";
import { analyze, fetchSample, getAnalysis } from "@/lib/api";
import type { AnalyzeResponse } from "@/lib/types";
import HistoryList from "./components/HistoryList";
import LoadingState from "./components/LoadingState";
import Results from "./components/Results";
import UploadPanel from "./components/UploadPanel";

type State =
  | { status: "idle"; error: string | null }
  | { status: "loading"; fileName: string; reopening: boolean }
  | { status: "done"; result: AnalyzeResponse };

export default function Home() {
  const [state, setState] = useState<State>({ status: "idle", error: null });
  const controller = useRef<AbortController | null>(null);

  async function run(fileName: string, getResult: (signal: AbortSignal) => Promise<AnalyzeResponse>, reopening = false) {
    const abort = new AbortController();
    controller.current = abort;
    setState({ status: "loading", fileName, reopening });
    try {
      const result = await getResult(abort.signal);
      setState({ status: "done", result });
    } catch (e) {
      if (abort.signal.aborted) return; // cancelled; the cancel handler already reset the page
      setState({ status: "idle", error: e instanceof Error ? e.message : "Something went wrong." });
    }
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
          {state.status === "loading" ? (
            <LoadingState fileName={state.fileName} reopening={state.reopening} onCancel={cancel} />
          ) : (
            <UploadPanel
              error={state.error}
              onAnalyze={(file) => run(file.name, (signal) => analyze(file, signal))}
              onSample={(name) =>
                run(name, async (signal) => analyze(await fetchSample(name, signal), signal))
              }
            />
          )}
        </div>
      )}

      {state.status === "idle" && (
        <div className="mx-auto w-full max-w-3xl">
          <HistoryList onOpen={(item) => run(item.source_file, (signal) => getAnalysis(item.id, signal), true)} />
        </div>
      )}
    </main>
  );
}
