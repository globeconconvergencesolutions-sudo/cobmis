"use client";

import Link from "next/link";
import { FormEvent, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AnswerBody } from "@/components/AnswerBody";
import { AskProgress, type ProgressStep } from "@/components/AskProgress";
import { EvidenceRail } from "@/components/EvidenceRail";
import { Sheet } from "@/components/ui/Sheet";
import {
  api,
  askStream,
  formatScopeLabel,
  isRefuseText,
  PROGRESS_LABELS,
  ROUTE_LABELS,
  ROUTE_TIPS,
  type AskResult,
  type Citation,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

function routeLabel(r: string | null | undefined) {
  if (!r) return "";
  return ROUTE_LABELS[r] || r;
}

function progressLabel(step: string, fallback: string) {
  // Prefer server label when it already carries scope detail.
  if (fallback && fallback !== PROGRESS_LABELS[step]) return fallback;
  return PROGRESS_LABELS[step] || fallback;
}

function pushProgress(job: FlightJob, step: string, label: string) {
  const mapped = progressLabel(step, label);
  if (step === "heartbeat") {
    const without = job.progress.filter((p) => p.step !== "heartbeat");
    job.progress = [...without, { step, label: mapped }];
    return;
  }
  const last = job.progress[job.progress.length - 1];
  if (last && last.step === step && last.label === mapped) return;
  job.progress = [...job.progress.filter((p) => p.step !== "heartbeat"), { step, label: mapped }];
}

function looksLikeChallenge(q: string) {
  return /\b(challenges?|issues?|problems?|recommendations?|observations?)\b/i.test(q);
}

function friendlyError(detail: string) {
  const d = detail.trim();
  if (!d || d === "cancelled") return null;
  if (/timed out|timeout|busy|cancelled/i.test(d)) {
    return d;
  }
  return d;
}

type Conv = { conversation_id: number; title: string; updated_at: string };

type Msg = {
  id: string;
  role: "user" | "assistant";
  content: string;
  route?: string;
  citations?: Citation[];
  sql_citations?: Citation[];
  sql?: string | null;
  columns?: string[];
  rows?: unknown[][];
  unsupported?: string[];
  message_id?: number;
  cancelled?: boolean;
  scopeLabel?: string | null;
  isError?: boolean;
};

type FlightJob = {
  jobKey: string;
  conversationId: number | null;
  asstId: string;
  question: string;
  text: string;
  progress: ProgressStep[];
  route: string | null;
  gotToken: boolean;
  controller: AbortController;
  cancelled: boolean;
  finished: boolean;
  citations: Citation[];
  sqlCitations: Citation[];
  sql: { sql: string | null; columns: string[]; rows: unknown[][] } | null;
  priorQuestion?: string;
  priorAnswer?: string;
  scopeLabel?: string | null;
  notices: string[];
};

type ReadyNote = { conversationId: number; preview: string };

const FALLBACK_STARTERS = [
  "What data do you have?",
  "Top 5 counties by pending bills in FY 2025/26",
  "What challenges did Kericho County face in the first nine months of FY 2025/26?",
  "What challenges did Kericho face and what was their OSR performance in 9M FY 2025/26?",
];

function mapApiMessages(
  rows: Array<{
    message_id: number;
    role: "user" | "assistant";
    content: string;
    route: string | null;
    citations: Citation[];
    sql: {
      sql?: string;
      columns?: string[];
      rows?: unknown[][];
      citations?: Citation[];
    } | null;
    unsupported_numbers: string[];
  }>,
): Msg[] {
  return rows.map((m) => ({
    id: String(m.message_id),
    role: m.role,
    content: m.content,
    route: m.route || undefined,
    citations: m.citations || [],
    sql_citations: m.sql?.citations || [],
    sql: m.sql?.sql ?? null,
    columns: m.sql?.columns || [],
    rows: m.sql?.rows || [],
    unsupported: m.unsupported_numbers,
    message_id: m.message_id,
  }));
}

export default function ChatPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-[var(--ink-muted)]">Loading…</div>}>
      <ChatInner />
    </Suspense>
  );
}

function ChatInner() {
  const { csrf } = useAuth();
  const params = useSearchParams();
  const [convs, setConvs] = useState<Conv[]>([]);
  const [convId, setConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState(params.get("q") || "");
  const [route, setRoute] = useState<string | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [starters, setStarters] = useState<string[]>(FALLBACK_STARTERS);
  const [fy, setFy] = useState("");
  const [period, setPeriod] = useState("");
  const [county, setCounty] = useState("");
  const [feedbackSent, setFeedbackSent] = useState<Record<number, number>>({});
  const [activeEvidence, setActiveEvidence] = useState<{
    citations: Citation[];
    sqlCitations: Citation[];
    sql: { sql: string | null; columns: string[]; rows: unknown[][] } | null;
  }>({ citations: [], sqlCitations: [], sql: null });
  const [isNarrow, setIsNarrow] = useState(false);
  const [readyNotes, setReadyNotes] = useState<ReadyNote[]>([]);
  const [jobsTick, setJobsTick] = useState(0);

  const bottomRef = useRef<HTMLDivElement>(null);
  const jobsRef = useRef<Map<string, FlightJob>>(new Map());
  const viewingRef = useRef<number | null>(null);

  const bumpJobs = () => setJobsTick((t) => t + 1);

  useEffect(() => {
    viewingRef.current = convId;
  }, [convId]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const apply = () => setIsNarrow(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  const loadConvs = useCallback(async () => {
    try {
      const r = await api<{ conversations: Conv[] }>("/api/conversations");
      setConvs(r.conversations);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadConvs();
    api<{ examples?: string[] }>("/api/catalog")
      .then((c) => {
        if (c.examples?.length) setStarters(c.examples);
      })
      .catch(() => undefined);
  }, [loadConvs]);

  const activeJobs = Array.from(jobsRef.current.values()).filter((j) => !j.finished);
  const viewingJob =
    activeJobs.find((j) =>
      j.conversationId != null
        ? j.conversationId === convId
        : convId === null && j.jobKey.startsWith("pending:"),
    ) || null;
  const viewStreaming = !!(viewingJob && !viewingJob.cancelled && !viewingJob.finished);
  // jobsTick forces recompute when jobs mutate
  void jobsTick;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, viewStreaming, viewingJob?.progress.length, viewingJob?.text]);

  function isViewing(job: FlightJob) {
    const v = viewingRef.current;
    if (job.conversationId != null) return v === job.conversationId;
    return v === null;
  }

  function patchJob(jobKey: string, patch: Partial<FlightJob>) {
    const job = jobsRef.current.get(jobKey);
    if (!job) return;
    Object.assign(job, patch);
    bumpJobs();
  }

  async function openConv(id: number) {
    // Switching chats does NOT cancel in-flight work.
    setEvidenceOpen(false);
    setActiveTag(null);
    const r = await api<{
      messages: Array<{
        message_id: number;
        role: "user" | "assistant";
        content: string;
        route: string | null;
        citations: Citation[];
        sql: {
          sql?: string;
          columns?: string[];
          rows?: unknown[][];
          citations?: Citation[];
        } | null;
        unsupported_numbers: string[];
      }>;
    }>(`/api/conversations/${id}`);
    setConvId(id);
    setHistoryOpen(false);
    setReadyNotes((notes) => notes.filter((n) => n.conversationId !== id));

    let msgs = mapApiMessages(r.messages);
    const job = Array.from(jobsRef.current.values()).find(
      (j) => j.conversationId === id && !j.finished,
    );
    if (job) {
      // Overlay live stream onto loaded history if the answer is not saved yet.
      const hasSavedAsst = msgs.some(
        (m) => m.role === "assistant" && m.content && m.message_id,
      );
      if (!hasSavedAsst || job.text) {
        const withoutTrailingEmptyAsst = msgs.filter(
          (m, i) =>
            !(
              i === msgs.length - 1 &&
              m.role === "assistant" &&
              !m.content &&
              !m.message_id
            ),
        );
        const lastUser = [...withoutTrailingEmptyAsst].reverse().find((m) => m.role === "user");
        if (!lastUser || lastUser.content !== job.question) {
          withoutTrailingEmptyAsst.push({
            id: `u-live-${job.asstId}`,
            role: "user",
            content: job.question,
          });
        }
        withoutTrailingEmptyAsst.push({
          id: job.asstId,
          role: "assistant",
          content: job.text,
          route: job.route || undefined,
          citations: job.citations,
          sql_citations: job.sqlCitations,
          sql: job.sql?.sql ?? null,
          columns: job.sql?.columns || [],
          rows: job.sql?.rows || [],
        });
        msgs = withoutTrailingEmptyAsst;
      }
      setRoute(job.route);
      setActiveEvidence({
        citations: job.citations,
        sqlCitations: job.sqlCitations,
        sql: job.sql,
      });
    } else {
      const last = [...msgs].reverse().find((m) => m.role === "assistant");
      if (last) {
        setRoute(last.route || null);
        setActiveEvidence({
          citations: last.citations || [],
          sqlCitations: last.sql_citations || [],
          sql: { sql: last.sql ?? null, columns: last.columns || [], rows: last.rows || [] },
        });
      } else {
        setRoute(null);
        setActiveEvidence({ citations: [], sqlCitations: [], sql: null });
      }
    }
    setMessages(msgs);
  }

  function newChat() {
    // Do not cancel background jobs — only change what is on screen.
    setConvId(null);
    setMessages([]);
    setRoute(null);
    setEvidenceOpen(false);
    setActiveEvidence({ citations: [], sqlCitations: [], sql: null });
    setActiveTag(null);
    setHistoryOpen(false);
  }

  function cancelViewingAsk() {
    if (!viewingJob) return;
    viewingJob.cancelled = true;
    viewingJob.controller.abort();
    bumpJobs();
    if (isViewing(viewingJob)) {
      setMessages((ms) =>
        ms.map((msg) =>
          msg.id === viewingJob.asstId
            ? {
                ...msg,
                content: "Cancelled — ask again when you are ready.",
                cancelled: true,
              }
            : msg,
        ),
      );
    }
  }

  function chatBusy(targetConvId: number | null) {
    return Array.from(jobsRef.current.values()).some((j) => {
      if (j.finished || j.cancelled) return false;
      if (targetConvId == null) return j.conversationId == null && j.jobKey.startsWith("pending:");
      return j.conversationId === targetConvId;
    });
  }

  async function send(question: string) {
    if (!csrf || !question.trim()) return;
    if (chatBusy(convId)) return;

    const q = question.trim();
    const asstId = `a-${Date.now()}`;
    const userMsgId = `u-${Date.now()}`;
    const jobKey = convId != null ? `conv:${convId}` : `pending:${asstId}`;
    const controller = new AbortController();

    const priorUser = [...messages].reverse().find((m) => m.role === "user");
    const priorAsst = [...messages].reverse().find((m) => m.role === "assistant");

    const job: FlightJob = {
      jobKey,
      conversationId: convId,
      asstId,
      question: q,
      text: "",
      progress: [{ step: "start", label: progressLabel("start", "Starting…") }],
      route: null,
      gotToken: false,
      controller,
      cancelled: false,
      finished: false,
      citations: [],
      sqlCitations: [],
      sql: null,
      priorQuestion: priorUser && messages.length ? priorUser.content : undefined,
      priorAnswer: priorAsst && messages.length ? priorAsst.content : undefined,
      scopeLabel: formatScopeLabel({ county, fy, period }) || null,
      notices: [],
    };
    jobsRef.current.set(jobKey, job);
    bumpJobs();

    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setRoute(null);
    setMessages((m) => [
      ...m,
      {
        id: userMsgId,
        role: "user",
        content: q,
        scopeLabel: job.scopeLabel,
      },
      { id: asstId, role: "assistant", content: "", scopeLabel: job.scopeLabel },
    ]);

    try {
      await askStream(
        {
          question: q,
          conversation_id: convId,
          fy: fy || undefined,
          period: period || undefined,
          county: county || undefined,
          prior_question: job.priorQuestion,
          prior_answer: job.priorAnswer,
        },
        csrf,
        {
          onRoute: (r) => {
            patchJob(jobKey, { route: r });
            if (isViewing(job)) setRoute(r);
          },
          onProgress: (step, label) => {
            const j = jobsRef.current.get(jobKey);
            if (!j || j.cancelled) return;
            pushProgress(j, step, label);
            if (step === "scope" && label.startsWith("Using filters:")) {
              j.scopeLabel = label.replace(/^Using filters:\s*/, "");
            }
            bumpJobs();
          },
          onScope: (label) => {
            const j = jobsRef.current.get(jobKey);
            if (!j || j.cancelled || !label) return;
            j.scopeLabel = label;
            bumpJobs();
            if (isViewing(j)) {
              setMessages((ms) =>
                ms.map((msg) =>
                  msg.id === asstId || msg.id === userMsgId
                    ? { ...msg, scopeLabel: label }
                    : msg,
                ),
              );
            }
          },
          onNotice: (notices) => {
            const j = jobsRef.current.get(jobKey);
            if (!j || j.cancelled) return;
            j.notices = notices;
            bumpJobs();
          },
          onToken: (t) => {
            const j = jobsRef.current.get(jobKey);
            if (!j || j.cancelled) return;
            j.gotToken = true;
            j.text += t;
            bumpJobs();
            if (isViewing(j)) {
              const snapshot = j.text;
              setMessages((ms) =>
                ms.map((msg) => (msg.id === asstId ? { ...msg, content: snapshot } : msg)),
              );
            }
          },
          onSources: (d) => {
            const j = jobsRef.current.get(jobKey);
            if (!j || j.cancelled) return;
            j.citations = d.citations || [];
            j.sqlCitations = d.sql_citations || [];
            bumpJobs();
            if (isViewing(j)) {
              setActiveEvidence((e) => ({
                ...e,
                citations: j.citations,
                sqlCitations: j.sqlCitations,
              }));
            }
          },
          onSql: (d) => {
            const j = jobsRef.current.get(jobKey);
            if (!j || j.cancelled) return;
            j.sql = { sql: d.sql, columns: d.columns || [], rows: d.rows || [] };
            bumpJobs();
            if (isViewing(j)) {
              setActiveEvidence((e) => ({ ...e, sql: j.sql }));
            }
          },
          onDone: (data: AskResult) => {
            const j = jobsRef.current.get(jobKey);
            if (!j) return;
            j.finished = true;
            j.text = data.text || j.text;
            j.route = data.route;
            j.citations = data.citations || [];
            j.sqlCitations = data.sql_citations || [];
            j.sql = {
              sql: data.sql,
              columns: data.columns || [],
              rows: data.rows || [],
            };
            const cid = data.conversation_id ?? j.conversationId;
            if (cid != null) j.conversationId = cid;

            // Re-key pending → conv
            if (jobKey.startsWith("pending:") && cid != null) {
              jobsRef.current.delete(jobKey);
              j.jobKey = `conv:${cid}`;
              jobsRef.current.set(j.jobKey, j);
            }
            bumpJobs();
            loadConvs();

            if (j.cancelled) {
              // Server may still have saved; user asked to stop display.
              jobsRef.current.delete(j.jobKey);
              bumpJobs();
              return;
            }

            if (isViewing(j)) {
              if (cid != null) setConvId(cid);
              setMessages((ms) =>
                ms.map((msg) =>
                  msg.id === asstId
                    ? {
                        ...msg,
                        content: data.text || j.text,
                        route: data.route,
                        citations: data.citations,
                        sql_citations: data.sql_citations,
                        sql: data.sql,
                        columns: data.columns,
                        rows: data.rows,
                        unsupported: data.unsupported_numbers,
                        message_id: data.message_id,
                      }
                    : msg,
                ),
              );
              setRoute(data.route);
              setActiveEvidence({
                citations: data.citations || [],
                sqlCitations: data.sql_citations || [],
                sql: {
                  sql: data.sql,
                  columns: data.columns || [],
                  rows: data.rows || [],
                },
              });
            } else if (cid != null) {
              const preview = (data.question || j.question).slice(0, 60);
              setReadyNotes((notes) => [
                ...notes.filter((n) => n.conversationId !== cid),
                { conversationId: cid, preview },
              ]);
            }
            jobsRef.current.delete(j.jobKey);
            bumpJobs();
          },
          onError: (detail) => {
            const j = jobsRef.current.get(jobKey);
            if (!j) return;
            j.finished = true;
            const cancelled = detail === "cancelled" || j.cancelled;
            const msgText = cancelled
              ? "Cancelled — the local model was released. Ask again when you are ready."
              : friendlyError(detail) ||
                "Something went wrong answering that. Cancel any stuck ask, then retry.";
            bumpJobs();
            if (isViewing(j)) {
              setMessages((ms) =>
                ms.map((msg) =>
                  msg.id === asstId
                    ? {
                        ...msg,
                        content: msgText,
                        cancelled,
                        isError: !cancelled,
                        scopeLabel: j.scopeLabel,
                      }
                    : msg,
                ),
              );
            }
            jobsRef.current.delete(j.jobKey);
            bumpJobs();
          },
        },
        controller.signal,
      );
    } catch {
      const j = jobsRef.current.get(jobKey);
      if (j && !j.finished) {
        j.finished = true;
        jobsRef.current.delete(jobKey);
        bumpJobs();
      }
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  async function feedback(messageId: number, rating: 1 | -1) {
    if (!csrf) return;
    await api("/api/conversations/feedback", {
      method: "POST",
      csrf,
      body: JSON.stringify({ message_id: messageId, rating }),
    });
    setFeedbackSent((f) => ({ ...f, [messageId]: rating }));
  }

  function workingConvIds() {
    const ids = new Set<number>();
    for (const j of jobsRef.current.values()) {
      if (!j.finished && !j.cancelled && j.conversationId != null) ids.add(j.conversationId);
    }
    return ids;
  }
  const working = workingConvIds();
  const readyIds = new Set(readyNotes.map((n) => n.conversationId));

  const historyList = (
    <ul className="min-h-0 flex-1 overflow-y-auto p-1">
      {convs.map((c) => (
        <li key={c.conversation_id}>
          <button
            type="button"
            onClick={() => openConv(c.conversation_id)}
            className={`flex w-full items-center gap-1 truncate rounded px-2 py-1.5 text-left text-xs focus-ring ${
              convId === c.conversation_id
                ? "bg-[var(--accent-wash)] text-[var(--accent)]"
                : "text-[var(--ink)] hover:bg-[var(--surface-2)]"
            }`}
          >
            <span className="min-w-0 flex-1 truncate">{c.title}</span>
            {working.has(c.conversation_id) && (
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)] progress-pulse"
                title="Working…"
              />
            )}
            {!working.has(c.conversation_id) && readyIds.has(c.conversation_id) && (
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--sql)]"
                title="Answer ready"
              />
            )}
          </button>
        </li>
      ))}
      {!convs.length && (
        <li className="px-2 py-3 text-xs text-[var(--ink-muted)]">No chats yet</li>
      )}
    </ul>
  );

  const activeRoute = viewingJob?.route || route;
  const scopeActive = Boolean(county || fy || period);

  function openProof(m: Msg) {
    setEvidenceOpen(true);
    setActiveEvidence({
      citations: m.citations || [],
      sqlCitations: m.sql_citations || [],
      sql: {
        sql: m.sql ?? null,
        columns: m.columns || [],
        rows: m.rows || [],
      },
    });
  }

  function resizeComposer() {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  return (
    <div className="relative flex h-full min-h-0 overflow-hidden">
      <aside className="hidden w-[200px] shrink-0 flex-col border-r border-[var(--hairline)] bg-[var(--surface)] xl:flex">
        <div className="border-b border-[var(--hairline)] p-2">
          <button
            type="button"
            onClick={newChat}
            className="w-full rounded-[var(--radius)] border border-[var(--hairline)] px-2 py-1.5 text-left text-sm hover:bg-[var(--surface-2)] focus-ring"
          >
            New chat
          </button>
        </div>
        {historyList}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-center gap-2 border-b border-[var(--hairline)] px-3 py-2 sm:px-4">
          <button
            type="button"
            className="rounded border border-[var(--hairline)] px-2 py-1 text-xs xl:hidden focus-ring"
            onClick={() => setHistoryOpen(true)}
          >
            Chats
          </button>
          <h1 className="text-sm font-semibold">Ask</h1>
          {activeRoute && (
            <span
              className={`route-chip route-tip ${activeRoute}`}
              tabIndex={0}
              data-tip={ROUTE_TIPS[activeRoute] || ""}
            >
              {routeLabel(activeRoute)}
            </span>
          )}
          <button
            type="button"
            className="ml-auto text-xs text-[var(--ink-muted)] hover:text-[var(--accent)] focus-ring"
            onClick={() => setEvidenceOpen((v) => !v)}
          >
            {evidenceOpen ? "Hide proof" : "Proof"}
          </button>
        </header>

        {readyNotes.length > 0 && (
          <div className="shrink-0 border-b border-[var(--hairline)] bg-[var(--accent-wash)] px-3 py-2 text-xs text-[var(--accent)]">
            {readyNotes.map((n) => (
              <button
                key={n.conversationId}
                type="button"
                className="mr-3 underline focus-ring"
                onClick={() => openConv(n.conversationId)}
              >
                Answer ready: {n.preview}
                {n.preview.length >= 60 ? "…" : ""}
              </button>
            ))}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-5 sm:py-6">
          {messages.length === 0 && (
            <div className="mx-auto max-w-xl pt-6 text-center sm:pt-10">
              <h2 className="font-display text-3xl text-[var(--ink)] sm:text-4xl">COBMIS</h2>
              <p className="mt-2 text-sm text-[var(--ink-muted)]">
                Ask about county budgets. Get figures and report citations you can trust.
              </p>
              <ul className="mt-8 space-y-2 text-left">
                {starters.map((s) => (
                  <li key={s}>
                    <button
                      type="button"
                      onClick={() => send(s)}
                      disabled={chatBusy(convId)}
                      className="w-full rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--surface)] px-3 py-2.5 text-left text-sm leading-snug hover:border-[var(--accent)] hover:bg-[var(--accent-wash)] focus-ring disabled:opacity-50"
                    >
                      {s}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mx-auto max-w-2xl space-y-6">
            {messages.map((m) => {
              const isStreamingMsg =
                viewStreaming &&
                m.role === "assistant" &&
                viewingJob &&
                m.id === viewingJob.asstId;
              const msgRoute = m.route || (isStreamingMsg ? viewingJob?.route : undefined);
              return (
                <div key={m.id} className={m.role === "user" ? "text-right" : ""}>
                  {m.role === "user" ? (
                    <div className="inline-block max-w-[min(100%,36rem)] text-left">
                      {m.scopeLabel && (
                        <div className="mb-1 text-[11px] text-[var(--ink-muted)]">
                          Filters: {m.scopeLabel}
                        </div>
                      )}
                      <div className="whitespace-pre-wrap break-words rounded-[var(--radius)] bg-[var(--surface-2)] px-3 py-2 text-sm leading-relaxed">
                        {m.content}
                      </div>
                    </div>
                  ) : (
                    <div
                      className={`msg-assistant route-${msgRoute || "none"}`}
                    >
                      {msgRoute && (
                        <span
                          className={`route-chip route-tip ${msgRoute} mb-2`}
                          tabIndex={0}
                          data-tip={ROUTE_TIPS[msgRoute] || ""}
                        >
                          {routeLabel(msgRoute)}
                        </span>
                      )}
                      {(m.scopeLabel || (isStreamingMsg && viewingJob?.scopeLabel)) && (
                        <div className="mb-2 text-[11px] text-[var(--accent)]">
                          Scope: {m.scopeLabel || viewingJob?.scopeLabel}
                        </div>
                      )}
                      {isStreamingMsg && viewingJob && !viewingJob.gotToken && (
                        <AskProgress
                          steps={viewingJob.progress}
                          active={viewStreaming}
                          scopeLabel={viewingJob.scopeLabel}
                          onCancel={cancelViewingAsk}
                        />
                      )}
                      {m.isError || m.cancelled ? (
                        <div className={m.isError ? "callout-warn" : "text-sm text-[var(--ink-muted)]"}>
                          <p className="whitespace-pre-wrap">{m.content}</p>
                        </div>
                      ) : m.content && isRefuseText(m.content) ? (
                        <div className="callout-refuse">
                          <p>{m.content}</p>
                          <Link href="/about" className="mt-2 inline-block text-[var(--accent)]">
                            See what is loaded →
                          </Link>
                        </div>
                      ) : m.content ? (
                        <AnswerBody
                          text={m.content}
                          activeTag={activeTag}
                          onCite={(tag) => {
                            setActiveTag(tag);
                            openProof(m);
                          }}
                        />
                      ) : null}
                      {m.unsupported && m.unsupported.length > 0 && (
                        <div className="callout-warn mt-2">
                          Some figures could not be verified against the tables:{" "}
                          {m.unsupported.join(", ")}
                        </div>
                      )}
                      {(m.message_id ||
                        (m.citations && m.citations.length > 0) ||
                        (m.rows && m.rows.length > 0)) && (
                        <div className="mt-2 flex flex-wrap gap-3 text-xs text-[var(--ink-muted)]">
                          {m.message_id && (
                            <>
                              <button
                                type="button"
                                className={`focus-ring ${feedbackSent[m.message_id] === 1 ? "font-medium text-[var(--accent)]" : ""}`}
                                onClick={() => feedback(m.message_id!, 1)}
                              >
                                Useful
                              </button>
                              <button
                                type="button"
                                className={`focus-ring ${feedbackSent[m.message_id] === -1 ? "font-medium text-[var(--warn)]" : ""}`}
                                onClick={() => feedback(m.message_id!, -1)}
                              >
                                Not useful
                              </button>
                            </>
                          )}
                          <button
                            type="button"
                            className="text-[var(--accent)] focus-ring"
                            onClick={() => openProof(m)}
                          >
                            How we checked
                          </button>
                        </div>
                      )}
                      {isStreamingMsg && viewingJob?.gotToken && (
                        <div className="mt-2">
                          <button
                            type="button"
                            onClick={cancelViewingAsk}
                            className="text-xs text-[var(--ink-muted)] hover:text-[var(--warn)] focus-ring"
                          >
                            Cancel
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          onSubmit={onSubmit}
          className="shrink-0 border-t border-[var(--hairline)] bg-[var(--surface)] px-3 py-3 sm:px-5"
        >
          <div className="mx-auto max-w-2xl">
            <div className="mb-2 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => setFiltersOpen((v) => !v)}
                className="text-[11px] text-[var(--ink-muted)] hover:text-[var(--accent)] focus-ring"
              >
                {filtersOpen ? "Hide filters" : "Optional filters"}
                {scopeActive && !filtersOpen ? " · on" : ""}
              </button>
              {scopeActive && (
                <button
                  type="button"
                  className="text-[11px] text-[var(--ink-muted)] hover:text-[var(--warn)] focus-ring"
                  onClick={() => {
                    setCounty("");
                    setFy("");
                    setPeriod("");
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            {filtersOpen && (
              <div className="mb-2 space-y-1.5">
                <div className="flex flex-wrap gap-2">
                  <input
                    value={county}
                    onChange={(e) => setCounty(e.target.value)}
                    placeholder="County"
                    className="w-[7rem] rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1 text-xs focus-ring"
                  />
                  <input
                    value={fy}
                    onChange={(e) => setFy(e.target.value)}
                    placeholder="FY e.g. 2025/26"
                    className="w-[7.5rem] rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1 text-xs focus-ring"
                  />
                  <select
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                    className="rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1 text-xs focus-ring"
                  >
                    <option value="">Period</option>
                    <option value="Q1">Q1</option>
                    <option value="H1">H1</option>
                    <option value="9M">9M</option>
                    <option value="FY">FY</option>
                  </select>
                </div>
                {scopeActive && (
                  <p className="text-[11px] text-[var(--accent)]">
                    Will search: {formatScopeLabel({ county, fy, period })}
                  </p>
                )}
                {looksLikeChallenge(input) && period === "FY" && (
                  <p className="text-[11px] text-[var(--ink-muted)]">
                    Tip: challenge write-ups are often richer in <strong>9M</strong> or{" "}
                    <strong>H1</strong> than in FY.
                  </p>
                )}
                {looksLikeChallenge(input) && !county.trim() && (
                  <p className="text-[11px] text-[var(--warn)]">
                    Challenge questions need a county — set the County filter or name it in the
                    question.
                  </p>
                )}
              </div>
            )}
            {!filtersOpen && scopeActive && (
              <p className="mb-2 text-[11px] text-[var(--ink-muted)]">
                Filters on: {formatScopeLabel({ county, fy, period })}
              </p>
            )}
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                rows={1}
                onChange={(e) => {
                  setInput(e.target.value);
                  requestAnimationFrame(resizeComposer);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (input.trim() && !chatBusy(convId) && !viewStreaming) {
                      onSubmit(e as unknown as FormEvent);
                    }
                  }
                }}
                placeholder="Ask about county budgets…"
                disabled={chatBusy(convId)}
                className="ask-composer min-w-0 flex-1 rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--bg)] px-3 py-2.5 text-sm leading-relaxed outline-none focus:border-[var(--accent)] focus-ring disabled:opacity-70"
              />
              {viewStreaming ? (
                <button
                  type="button"
                  onClick={cancelViewingAsk}
                  className="shrink-0 rounded-[var(--radius)] border border-[var(--hairline)] px-4 py-2.5 text-sm font-medium text-[var(--warn)] focus-ring"
                >
                  Stop
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim() || chatBusy(convId)}
                  className="shrink-0 rounded-[var(--radius)] bg-[var(--accent)] px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50 focus-ring"
                >
                  Ask
                </button>
              )}
            </div>
            {viewStreaming && (
              <p className="mt-1.5 text-[11px] text-[var(--ink-muted)]">
                Working — you can switch chats; this answer stays with this thread.
              </p>
            )}
            {chatBusy(convId) && !viewStreaming && (
              <p className="mt-1.5 text-[11px] text-[var(--ink-muted)]">
                This chat already has a question running.
              </p>
            )}
          </div>
        </form>
      </div>

      {!isNarrow && (
        <EvidenceRail
          open={evidenceOpen}
          citations={activeEvidence.citations}
          sqlCitations={activeEvidence.sqlCitations}
          sql={activeEvidence.sql}
          activeTag={activeTag}
          route={activeRoute}
          waiting={viewStreaming && !viewingJob?.gotToken}
          onClose={() => setEvidenceOpen(false)}
        />
      )}
      {isNarrow && (
        <EvidenceRail
          open={evidenceOpen}
          asOverlay
          citations={activeEvidence.citations}
          sqlCitations={activeEvidence.sqlCitations}
          sql={activeEvidence.sql}
          activeTag={activeTag}
          route={activeRoute}
          waiting={viewStreaming && !viewingJob?.gotToken}
          onClose={() => setEvidenceOpen(false)}
        />
      )}

      <Sheet open={historyOpen} title="Chats" onClose={() => setHistoryOpen(false)} side="left">
        <div className="border-b border-[var(--hairline)] p-2">
          <button
            type="button"
            onClick={newChat}
            className="w-full rounded border border-[var(--hairline)] px-2 py-1.5 text-left text-sm"
          >
            New chat
          </button>
        </div>
        {historyList}
      </Sheet>
    </div>
  );
}
