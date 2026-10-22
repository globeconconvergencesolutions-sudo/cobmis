const API = ""; // same-origin via Next rewrite → 127.0.0.1:8000

export type User = {
  user_id: number;
  email: string;
  display_name: string;
  role: string;
};

function csrfHeaders(csrf: string | null): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) h["X-CSRF-Token"] = csrf;
  return h;
}

export async function api<T>(
  path: string,
  opts: RequestInit & { csrf?: string | null } = {},
): Promise<T> {
  const { csrf, ...rest } = opts;
  const res = await fetch(`${API}${path}`, {
    ...rest,
    credentials: "include",
    headers: {
      ...csrfHeaders(csrf ?? null),
      ...(rest.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res as unknown as T;
}

export async function login(email: string, password: string) {
  return api<{ user: User; csrf_token: string }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(csrf: string | null) {
  return api("/api/auth/logout", { method: "POST", csrf });
}

export async function me() {
  return api<{ user: User; csrf_token: string }>("/api/auth/me");
}

export type AskResult = {
  question: string;
  route: string;
  text: string;
  citations: Citation[];
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  sql_citations: Citation[];
  unsupported_numbers: string[];
  error?: string | null;
  conversation_id?: number;
  message_id?: number;
};

export type Citation = {
  tag?: string;
  document?: string;
  pages?: [number | null, number | null] | number[];
  table?: string;
  snippet?: string;
  source_page?: number;
  source_title?: string;
  doc_id?: number;
};

export type StreamHandlers = {
  onRoute?: (route: string) => void;
  onProgress?: (step: string, label: string) => void;
  onScope?: (label: string) => void;
  onNotice?: (notices: string[]) => void;
  onToken?: (text: string) => void;
  onRewritten?: (q: string) => void;
  onSources?: (data: { citations: Citation[]; sql_citations: Citation[] }) => void;
  onSql?: (data: { sql: string | null; columns: string[]; rows: unknown[][] }) => void;
  onWarning?: (nums: string[]) => void;
  onDone?: (data: AskResult) => void;
  onError?: (detail: string) => void;
};

export async function askStream(
  body: Record<string, unknown>,
  csrf: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
) {
  const res = await fetch(`${API}/api/ask/stream`, {
    method: "POST",
    credentials: "include",
    headers: csrfHeaders(csrf),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* ignore */
    }
    handlers.onError?.(String(detail));
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() || "";
      for (const block of parts) {
        const lines = block.split("\n");
        let event = "message";
        let data = "";
        for (const line of lines) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        const payload = JSON.parse(data);
        if (event === "route") handlers.onRoute?.(payload.route);
        else if (event === "progress") handlers.onProgress?.(payload.step, payload.label);
        else if (event === "scope") handlers.onScope?.(payload.label || "");
        else if (event === "notice") handlers.onNotice?.(payload.notices || []);
        else if (event === "token") handlers.onToken?.(payload.text);
        else if (event === "rewritten") handlers.onRewritten?.(payload.question);
        else if (event === "sources") handlers.onSources?.(payload);
        else if (event === "sql") handlers.onSql?.(payload);
        else if (event === "warning") handlers.onWarning?.(payload.unsupported_numbers || []);
        else if (event === "done") handlers.onDone?.(payload);
        else if (event === "error") handlers.onError?.(payload.detail || "error");
      }
    }
  } catch (err) {
    if (signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) {
      handlers.onError?.("cancelled");
      return;
    }
    throw err;
  }
}

/** Short human labels (never show raw route codes as primary UI). */
export const ROUTE_LABELS: Record<string, string> = {
  catalog: "Coverage",
  sql: "From the tables",
  rag: "From the report",
  both: "Tables + report",
};

export const ROUTE_TIPS: Record<string, string> = {
  catalog: "Answered from what is loaded on this machine — years, documents, metrics.",
  sql: "Figures from validated county tables published in the COB reports.",
  rag: "Explanation drawn from cited pages in the PDF reports.",
  both: "Money and rates from the tables; story and challenges from the report pages.",
};

export const PROGRESS_LABELS: Record<string, string> = {
  start: "Starting…",
  accepted: "Request received…",
  scope: "Applying your filters…",
  notice: "Note about your filters…",
  heartbeat: "Still working…",
  rewrite: "Understanding your follow-up…",
  classify: "Choosing the best way to answer…",
  catalog: "Checking what data is loaded…",
  embed: "Understanding your question…",
  sql_generate: "Looking up county figures…",
  sql_repair: "Trying another lookup…",
  sql_execute: "Reading the tables…",
  narrate: "Writing the answer…",
  retrieve: "Searching the reports…",
  rag: "Writing the answer from cited pages…",
  merge: "Combining figures with the explanation…",
};

export function formatScopeLabel(parts: {
  county?: string;
  fy?: string;
  period?: string;
}): string {
  const bits: string[] = [];
  if (parts.county?.trim()) bits.push(parts.county.trim());
  if (parts.fy?.trim()) bits.push(`FY ${parts.fy.trim()}`);
  if (parts.period?.trim()) bits.push(parts.period.trim());
  return bits.join(" · ");
}

export function isRefuseText(text: string) {
  const t = text.toLowerCase();
  return (
    t.includes("do not contain this information") ||
    t.includes("do not contain this information.") ||
    t.includes("nothing relevant found")
  );
}
