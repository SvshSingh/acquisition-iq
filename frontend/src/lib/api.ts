import type { Meta, ScoredCompany, SearchResponse, Weights } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    // Surface the server's own message where it gave one — "no company with id
    // 'cslb:123'" tells a user far more than "Request failed".
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body; the status line is what we have */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export interface SearchParams {
  q?: string;
  industry?: string;
  city?: string;
  business_type?: string;
  has_employees?: boolean;
  min_age?: number;
  min_score?: number;
  limit?: number;
}

async function post<T>(path: string): Promise<T> {
  const response = await fetch(new URL(`${BASE}${path}`, window.location.origin), {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  meta: () => get<Meta>("/meta"),
  search: (params: SearchParams) => get<SearchResponse>("/companies", { ...params }),
  company: (id: string) => get<ScoredCompany>(`/companies/${encodeURIComponent(id)}`),

  /** Re-fetch one company from source, re-validate and re-score.
   *
   *  Slow by nature — it crawls the company's own site and hits DNS — which is
   *  exactly why the backend is a long-running container rather than a
   *  serverless function. */
  refresh: (id: string) =>
    post<ScoredCompany>(`/companies/${encodeURIComponent(id)}/refresh`),

  /** Build the export URL rather than fetching it: the browser's own download
   *  handling gives a real filename and a progress indicator, which a blob
   *  round-trip through fetch would throw away. */
  exportUrl(ids: string[], preset: string, weights: Weights): string {
    const url = new URL(`${BASE}/export`, window.location.origin);
    if (ids.length) url.searchParams.set("ids", ids.join(","));
    url.searchParams.set("preset", preset);
    url.searchParams.set("weights", JSON.stringify(weights));
    return url.toString();
  },
};
