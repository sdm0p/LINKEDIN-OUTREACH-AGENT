import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useToast } from "../toast";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Mail,
  MessageSquare,
} from "../icons";

export interface Draft {
  id: number;
  post_id: string;
  run_id: string;
  keyword: string;
  company: string | null;
  role: string | null;
  author_name: string | null;
  author_headline: string | null;
  author_profile_url: string | null;
  post_url: string | null;
  contact_method: "email" | "dm";
  contact_value: string | null;
  yoe_required: number | null;
  classification: string;
  location: string | null;
  country: string | null;
  posted_at: string | null;
  job_id: string | null;
  job_url: string | null;
  job_details_json: string | null;
  post_text: string;
  draft_text: string | null;
  drafted_at: string | null;
  status: "pending" | "new" | "reviewed" | "sent" | "skipped";
  created_at: string;
  updated_at: string;
}

type SortKey = "company" | "role" | "yoe_required" | "status" | "posted_at" | "created_at";

const STATUS_FILTERS = ["all", "pending", "new", "reviewed", "sent", "skipped"] as const;

const FALLBACK_COUNTRIES = [
  "Remote", "India", "United States", "United Kingdom", "Canada", "Germany",
  "France", "Netherlands", "Singapore", "United Arab Emirates", "Australia",
];

function statusBadgeClass(status: Draft["status"]): string {
  if (status === "pending") return "badge muted";
  if (status === "new") return "badge accent";
  if (status === "sent") return "badge filled";
  return "badge";
}

function formatPosted(iso: string | null): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function jobDetails(draft: Draft): Record<string, unknown> | null {
  if (!draft.job_details_json) return null;
  try {
    const parsed = JSON.parse(draft.job_details_json);
    return typeof parsed === "object" && parsed !== null ? parsed : null;
  } catch {
    return null;
  }
}

export default function QueuePage() {
  const toast = useToast();
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [filter, setFilter] = useState<(typeof STATUS_FILTERS)[number]>("all");
  const [country, setCountry] = useState<string>("all");
  const [countries, setCountries] = useState<string[]>([]);
  const [orderBy, setOrderBy] = useState<"added" | "posted">("posted");
  const [sortKey, setSortKey] = useState<SortKey>("posted_at");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [confirmSendId, setConfirmSendId] = useState<number | null>(null);
  const [generatingIds, setGeneratingIds] = useState<number[]>([]);
  const [generatingAll, setGeneratingAll] = useState(false);
  const [fetchingJobIds, setFetchingJobIds] = useState<number[]>([]);

  const refresh = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filter !== "all") params.set("status", filter);
      // "unknown" is a client-side bucket (rows with no country at all),
      // so only real country names go to the API.
      if (country !== "all" && country !== "unknown") params.set("country", country);
      params.set("order", orderBy);
      const qs = params.toString();
      setDrafts(await api<Draft[]>(`/api/queue${qs ? `?${qs}` : ""}`));
    } catch {
      setDrafts([]);
    }
    // Keep the country dropdown fresh: the backend backfills missing
    // locations on load, so new options can appear after the first poll.
    api<{ countries: string[] }>("/api/queue/countries")
      .then((res) => setCountries(res.countries))
      .catch(() => undefined);
  }, [filter, country, orderBy]);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const setStatus = useCallback(
    async (id: number, status: Draft["status"]) => {
      try {
        await api(`/api/queue/${id}/status`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status }),
        });
        setDrafts((prev) =>
          prev ? prev.map((d) => (d.id === id ? { ...d, status } : d)) : prev,
        );
        if (status === "sent") toast("Marked as sent — send it yourself from your mail/LinkedIn client");
        setConfirmSendId(null);
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Update failed", "error");
      }
    },
    [toast],
  );

  const generate = useCallback(
    async (id: number) => {
      setGeneratingIds((prev) => [...prev, id]);
      try {
        await api(`/api/queue/${id}/generate`, { method: "POST" });
        toast("Draft generated — review it below");
        await refresh();
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Draft generation failed", "error");
      } finally {
        setGeneratingIds((prev) => prev.filter((x) => x !== id));
      }
    },
    [toast, refresh],
  );

  const generateAll = useCallback(async () => {
    setGeneratingAll(true);
    try {
      const res = await api<{ generated: number; failed: number; capped: number }>(
        "/api/queue/generate-all",
        { method: "POST" },
      );
      const bits = [`${res.generated} drafted`];
      if (res.capped) bits.push(`${res.capped} left for tomorrow (daily cap)`);
      if (res.failed) bits.push(`${res.failed} failed`);
      toast(bits.join(" · "), res.failed ? "error" : "success");
      await refresh();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Generate-all failed", "error");
    } finally {
      setGeneratingAll(false);
    }
  }, [toast, refresh]);

  const fetchJob = useCallback(
    async (id: number) => {
      setFetchingJobIds((prev) => [...prev, id]);
      try {
        await api(`/api/queue/${id}/fetch-job`, { method: "POST" });
        toast("Job details fetched");
        await refresh();
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Job fetch failed", "error");
      } finally {
        setFetchingJobIds((prev) => prev.filter((x) => x !== id));
      }
    },
    [toast, refresh],
  );

  const visible = useMemo(() => {
    let rows = drafts ?? [];
    if (filter !== "all") rows = rows.filter((d) => d.status === filter);
    if (country === "unknown") rows = rows.filter((d) => !d.country);
    else if (country !== "all") rows = rows.filter((d) => d.country === country);
    const dir = sortAsc ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = (a[sortKey] ?? "").toString().toLowerCase();
      const vb = (b[sortKey] ?? "").toString().toLowerCase();
      return va < vb ? -dir : va > vb ? dir : 0;
    });
  }, [drafts, filter, country, sortKey, sortAsc]);

  const toggleSort = useCallback(
    (key: SortKey) => {
      if (sortKey === key) setSortAsc((prev) => !prev);
      else {
        setSortKey(key);
        setSortAsc(true);
      }
    },
    [sortKey],
  );

  if (!drafts) return null;

  const counts = {
    pending: drafts.filter((d) => d.status === "pending").length,
    new: drafts.filter((d) => d.status === "new").length,
    total: drafts.length,
  };

  return (
    <div>
      <h1 className="page-title">Review queue</h1>
      <p className="page-subtitle">
        {counts.pending} pending · {counts.new} new · {counts.total} total. Nothing
        sends automatically — and drafts are only generated when you ask for them.
      </p>

      <div className="section row">
        {STATUS_FILTERS.map((s) => (
          <button
            key={s}
            className={`btn small${filter === s ? " primary" : ""}`}
            onClick={() => setFilter(s)}
          >
            {s}
          </button>
        ))}
        <select
          className="input"
          style={{ maxWidth: 180 }}
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          title="Filter by country"
        >
          <option value="all">All locations</option>
          <option value="unknown">Location unknown</option>
          {(countries.length > 0 ? countries : FALLBACK_COUNTRIES)
            .filter((c) => c !== country && c !== "unknown")
            .map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
        </select>
        <select
          className="input"
          style={{ maxWidth: 160 }}
          value={orderBy}
          onChange={(e) => setOrderBy(e.target.value as "added" | "posted")}
          title="Default queue order"
        >
          <option value="posted">Sort: post time</option>
          <option value="added">Sort: added time</option>
        </select>
        {counts.pending > 0 && (
          <button
            className="btn small primary"
            style={{ marginLeft: "auto" }}
            disabled={generatingAll}
            onClick={() => void generateAll()}
          >
            {generatingAll
              ? "Generating…"
              : `Generate all drafts (${counts.pending})`}
          </button>
        )}
      </div>

      {visible.length === 0 ? (
        <div className="empty-state">
          {drafts.length === 0 ? (
            <p>
              No leads yet. Trigger a run — kept posts that pass the YoE filter
              and hiring-intent check land here as pending leads.
            </p>
          ) : (
            <p>No leads with status "{filter}".</p>
          )}
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 28 }}></th>
              <th onClick={() => toggleSort("company")} style={{ cursor: "pointer" }}>
                Company {sortKey === "company" && (sortAsc ? "▲" : "▼")}
              </th>
              <th onClick={() => toggleSort("role")} style={{ cursor: "pointer" }}>
                Role {sortKey === "role" && (sortAsc ? "▲" : "▼")}
              </th>
              <th>Contact</th>
              <th>Location</th>
              <th onClick={() => toggleSort("yoe_required")} style={{ cursor: "pointer" }}>
                YoE {sortKey === "yoe_required" && (sortAsc ? "▲" : "▼")}
              </th>
              <th onClick={() => toggleSort("status")} style={{ cursor: "pointer" }}>
                Status {sortKey === "status" && (sortAsc ? "▲" : "▼")}
              </th>
              <th onClick={() => toggleSort("posted_at")} style={{ cursor: "pointer" }}>
                Posted {sortKey === "posted_at" && (sortAsc ? "▲" : "▼")}
              </th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((d) => {
              const open = expandedId === d.id;
              return (
                <RowGroup
                  key={d.id}
                  draft={d}
                  open={open}
                  generating={generatingIds.includes(d.id)}
                  fetchingJob={fetchingJobIds.includes(d.id)}
                  onToggle={() => setExpandedId(open ? null : d.id)}
                  onSetStatus={setStatus}
                  onGenerate={generate}
                  onFetchJob={fetchJob}
                  confirmSend={confirmSendId === d.id}
                  onConfirmSend={() => setConfirmSendId(d.id)}
                  onCancelConfirm={() => setConfirmSendId(null)}
                />
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RowGroup({
  draft,
  open,
  generating,
  fetchingJob,
  onToggle,
  onSetStatus,
  onGenerate,
  onFetchJob,
  confirmSend,
  onConfirmSend,
  onCancelConfirm,
}: {
  draft: Draft;
  open: boolean;
  generating: boolean;
  fetchingJob: boolean;
  onToggle: () => void;
  onSetStatus: (id: number, status: Draft["status"]) => Promise<void>;
  onGenerate: (id: number) => Promise<void>;
  onFetchJob: (id: number) => Promise<void>;
  confirmSend: boolean;
  onConfirmSend: () => void;
  onCancelConfirm: () => void;
}) {
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }}>
        <td>
          {open ? (
            <ChevronDown size={14} strokeWidth={1.5} />
          ) : (
            <ChevronRight size={14} strokeWidth={1.5} />
          )}
        </td>
        <td>
          <div>{draft.company || "—"}</div>
          <div className="muted">{draft.author_name}</div>
        </td>
        <td>
          <div>{draft.role || "—"}</div>
          <div className="muted">{draft.keyword}</div>
        </td>
        <td>
          <span className="row">
            {draft.contact_method === "email" ? (
              <Mail size={14} strokeWidth={1.5} />
            ) : (
              <MessageSquare size={14} strokeWidth={1.5} />
            )}
            <span className="muted">{draft.contact_value || "—"}</span>
          </span>
        </td>
        <td>
          <div>{draft.country || "—"}</div>
          <div className="muted">{draft.location || ""}</div>
        </td>
        <td className="muted">
          {draft.yoe_required != null && draft.yoe_required > 0
            ? `${draft.yoe_required}y req`
            : "n/s"}
        </td>
        <td>
          <span className={statusBadgeClass(draft.status)}>{draft.status}</span>
        </td>
        <td
          className="muted"
          title={
            draft.posted_at
              ? `Posted ${new Date(draft.posted_at).toLocaleString()} · added ${new Date(draft.created_at).toLocaleString()}`
              : `Post time unknown · added ${new Date(draft.created_at).toLocaleString()}`
          }
        >
          {formatPosted(draft.posted_at)}
        </td>
        <td>
          {draft.post_url ? (
            <a
              href={draft.post_url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              title="Open the post"
            >
              <span className="row">
                <ExternalLink size={14} strokeWidth={1.5} />
                <span className="muted">post</span>
              </span>
            </a>
          ) : draft.author_profile_url ? (
            <a
              href={draft.author_profile_url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              title="Post permalink unavailable — open the author's profile"
            >
              <span className="row">
                <ExternalLink size={14} strokeWidth={1.5} />
                <span className="muted">profile</span>
              </span>
            </a>
          ) : null}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={9} style={{ background: "var(--bg)" }}>
            <div className="draft-detail">
              {draft.author_headline && (
                <p className="muted" style={{ marginBottom: 8 }}>
                  {draft.author_headline}
                </p>
              )}
              {jobDetails(draft) && (
                <details style={{ marginBottom: 8 }}>
                  <summary className="muted" style={{ cursor: "pointer" }}>
                    Job details{draft.job_url ? " (open job posting)" : ""}
                  </summary>
                  {draft.job_url && (
                    <p style={{ marginBottom: 4 }}>
                      <a href={draft.job_url} target="_blank" rel="noreferrer">
                        Open job posting on LinkedIn
                      </a>
                    </p>
                  )}
                  <pre className="draft-text muted">
                    {String(jobDetails(draft)?.text || "(no details captured)")}
                  </pre>
                </details>
              )}
              {draft.draft_text ? (
                <pre className="draft-text">{draft.draft_text}</pre>
              ) : (
                <details>
                  <summary className="muted" style={{ cursor: "pointer" }}>
                    Source post
                  </summary>
                  <pre className="draft-text muted">{draft.post_text || "(post text unavailable)"}</pre>
                </details>
              )}
              <div className="row" style={{ marginTop: 12 }}>
                {draft.status === "pending" && (
                  <button
                    className="btn small primary"
                    disabled={generating}
                    onClick={(e) => {
                      e.stopPropagation();
                      void onGenerate(draft.id);
                    }}
                  >
                    {generating ? "Generating draft…" : "Generate draft"}
                  </button>
                )}
                {(draft.job_id || draft.keyword) && !draft.job_details_json && (
                  <button
                    className="btn small"
                    disabled={fetchingJob}
                    onClick={(e) => {
                      e.stopPropagation();
                      void onFetchJob(draft.id);
                    }}
                    title={
                      draft.job_id
                        ? "Fetch the attached job's details from LinkedIn"
                        : "Search LinkedIn jobs for this keyword and fetch the top match's details"
                    }
                  >
                    {fetchingJob ? "Fetching job…" : "Fetch job details"}
                  </button>
                )}
                {draft.status !== "pending" && draft.status !== "sent" &&
                  (confirmSend ? (
                    <span className="inline-confirm">
                      <button
                        className="btn small primary"
                        onClick={(e) => {
                          e.stopPropagation();
                          void onSetStatus(draft.id, "sent");
                        }}
                      >
                        Confirm sent
                      </button>
                      <button className="btn small" onClick={onCancelConfirm}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      className="btn small primary"
                      onClick={(e) => {
                        e.stopPropagation();
                        onConfirmSend();
                      }}
                    >
                      Mark as sent
                    </button>
                  ))}
                {draft.status !== "pending" && draft.status !== "reviewed" && (
                  <button
                    className="btn small"
                    onClick={(e) => {
                      e.stopPropagation();
                      void onSetStatus(draft.id, "reviewed");
                    }}
                  >
                    Mark reviewed
                  </button>
                )}
                {draft.status !== "pending" && draft.status !== "skipped" && (
                  <button
                    className="btn small"
                    onClick={(e) => {
                      e.stopPropagation();
                      void onSetStatus(draft.id, "skipped");
                    }}
                  >
                    Skip
                  </button>
                )}
                {draft.status !== "pending" && draft.status !== "new" && (
                  <button
                    className="btn small"
                    onClick={(e) => {
                      e.stopPropagation();
                      void onSetStatus(draft.id, "new");
                    }}
                  >
                    Reset to new
                  </button>
                )}
                <span className="muted">via {draft.contact_method === "email" ? "email" : "LinkedIn DM"}</span>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
