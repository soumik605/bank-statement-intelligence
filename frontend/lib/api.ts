import type { AnalyzeResponse, FailurePage, HistoryPage } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  // The backend's machine-readable reason, e.g. "password_required"; null for network errors.
  // failureId: the failed upload was kept for review under this id.
  constructor(
    message: string,
    public code: string | null = null,
    public failureId: number | null = null,
  ) {
    super(message);
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    // fetch only rejects like this when the server is down, or CORS blocked the response.
    throw new ApiError(
      `Couldn't reach the analysis server at ${API_URL}. Check that the backend is running ` +
        "(uv run uvicorn app.main:app) and that this site's address is in CORS_ORIGINS.",
    );
  }
  if (!response.ok) {
    let message = `The server returned an error (HTTP ${response.status}).`;
    let code: string | null = null;
    let failureId: number | null = null;
    try {
      const body = await response.json();
      if (typeof body?.detail?.message === "string") message = body.detail.message;
      if (typeof body?.detail?.error === "string") code = body.detail.error;
      if (typeof body?.detail?.failure_id === "number") failureId = body.detail.failure_id;
    } catch {
      // not JSON; keep the generic message
    }
    throw new ApiError(message, code, failureId);
  }
  return response;
}

// `password` is only for protected PDFs; it's sent once to open the file and never stored.
export async function analyze(file: File, signal?: AbortSignal, password?: string): Promise<AnalyzeResponse> {
  const form = new FormData();
  form.append("file", file);
  if (password) form.append("password", password);
  const response = await request("/analyze", { method: "POST", body: form, signal });
  return response.json();
}

// Downloads a synthetic statement from the backend, so "Try a sample" goes
// through exactly the same upload path as a real file.
export async function fetchSample(name: string, signal?: AbortSignal): Promise<File> {
  const response = await request(`/samples/${encodeURIComponent(name)}`, { signal });
  const blob = await response.blob();
  return new File([blob], name, { type: blob.type });
}

export async function listHistory(offset = 0, limit = 20): Promise<HistoryPage> {
  const response = await request(`/history?limit=${limit}&offset=${offset}`);
  return response.json();
}

// A saved analysis exactly as it was first returned; nothing is re-run.
export async function getAnalysis(id: number, signal?: AbortSignal): Promise<AnalyzeResponse> {
  const response = await request(`/history/${id}`, { signal });
  return response.json();
}

export async function deleteAnalysis(id: number): Promise<void> {
  await request(`/history/${id}`, { method: "DELETE" });
}

// The statement exactly as it was uploaded, for previewing.
export const originalFileUrl = (analysisId: number) => `${API_URL}/history/${analysisId}/file`;

export async function fetchOriginalFile(analysisId: number, signal?: AbortSignal): Promise<Blob> {
  const response = await request(`/history/${analysisId}/file`, { signal });
  return response.blob();
}

export async function listFailures(offset = 0, limit = 20): Promise<FailurePage> {
  const response = await request(`/failures?limit=${limit}&offset=${offset}`);
  return response.json();
}

export const failureFileUrl = (failureId: number) => `${API_URL}/failures/${failureId}/file`;

export async function deleteFailure(id: number): Promise<void> {
  await request(`/failures/${id}`, { method: "DELETE" });
}

// Run the analysis again on a failed upload's kept file. Success is saved to
// history and marks the failure resolved; another failure updates the same record.
export async function retryFailure(failureId: number, signal?: AbortSignal, password?: string): Promise<AnalyzeResponse> {
  const form = new FormData();
  if (password) form.append("password", password);
  const response = await request(`/failures/${failureId}/retry`, { method: "POST", body: form, signal });
  return response.json();
}
