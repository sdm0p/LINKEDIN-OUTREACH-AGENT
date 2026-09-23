import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { SettingsInfo } from "../types";
import { useToast } from "../toast";
import { ChevronDown, ChevronRight, Play } from "../icons";

interface TraceEntry {
  stage: string;
  detail: string;
  ts: number;
}

interface RunPayload {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  recency: string;
  trace: TraceEntry[];
  summary: {
    keywords_used: string[];
    raw_hits: number;
    per_keyword: Record<string, number>;
    dedup_skipped?: number;
    yoe_dropped?: number;
    location_dropped?: number;
    noise?: number;
    no_contact?: number;
    capped?: number;
    drafts_created?: number;
  } | null;
  error: string | null;
}

interface ValidationResult {
  keyword: string;
  recency: string;
  raw: string;
  raw_length: number;
  parsed_count: number;
  degraded: boolean;
  sample_post: Record<string, unknown> | null;
}

const STAGE_LABELS: Record<string, string> = {
  "resume-parse": "Resume parse",
  "role-expansion": "Role expansion",
  "keyword-generation": "Keywords",
  search: "Search (per keyword)",
  dedup: "Dedup",
  "yoe-filter": "YoE filter",
  "location-filter": "Location filter",
  extraction: "Extraction & classification",
  draft: "Drafts",
  run: "Run",
};

function stageHeadline(stage: string, entries: TraceEntry[]): string {
  const details = entries.map((e) => e.detail);
  if (stage === "search") {
    const done = details.filter((d) => /': \d+ posts found/.test(d)).length;
    const hits = details
      .map((d) => /': (\d+) posts found/.exec(d)?.[1])
      .filter(Boolean)
      .reduce((a, b) => a + Number(b), 0);
    const total = /Selected (\d+) of/.exec(details.join(" | "))?.[1];
    if (done > 0 && total) return `${done}/${total} searched · ${hits} posts found`;
    if (details.some((d) => d.startsWith("Pausing"))) return "in progress...";
    return details[details.length - 1] ?? "";
  }
  return details[details.length - 1] ?? "";
}

interface StageGroup {
  stage: string;
  entries: TraceEntry[];
}

export default function RunPage() {
  const toast = useToast();
  const [run, setRun] = useState<RunPayload | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [recency, setRecency] = useState("24h");
  const [allKeywords, setAllKeywords] = useState(false);
  const [starting, setStarting] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [valKeyword, setValKeyword] = useState("hiring developer");
  const [settings, setSettings] = useState<SettingsInfo | null>(null);
  const [searchSourceSaving, setSearchSourceSaving] = useState(false);
  const [localSource, setLocalSource] = useState<string | null>(null);
  const autoExpandedFor = useRef<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const latest = await api<RunPayload | null>("/api/runs/latest");
      setRun(latest);
    } catch {
      // backend briefly unavailable mid-run — keep polling
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void poll();
    const interval = window.setInterval(() => void poll(), 1500);
    return () => window.clearInterval(interval);
  }, [poll]);

  useEffect(() => {
    api<SettingsInfo>("/api/settings").then(setSettings).catch(() => undefined);
  }, []);

  const running = run?.status === "running";
  const searchSource = settings?.search_source?.name ?? "mcp";
  const availableSources = settings?.search_source?.available_sources ?? ["mcp"];
  const activeSource = localSource ?? searchSource;

  // Optimistic switch: flip the select immediately, roll back on failure.
  const setSearchSource = useCallback(
    async (value: string) => {
      const prev = activeSource;
      setSearchSourceSaving(true);
      setLocalSource(value);
      try {
        await api("/api/settings/search-source", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: value }),
        });
        toast(
          value === "playwright"
            ? "Search source: Playwright (direct browser automation)"
            : "Search source: MCP container",
        );
      } catch (err) {
        setLocalSource(prev);
        toast(err instanceof ApiError ? err.message : "Could not switch source", "error");
      } finally {
        setSearchSourceSaving(false);
      }
    },
    [activeSource, toast],
  );
  const availableCountries = settings?.location_targets?.available ?? [];
  const targetCountries = settings?.location_targets?.countries ?? [];

  // The Run-page location filter writes through to the same setting the
  // Settings page edits, so both stay in sync. Single-pick here (the
  // common case: one country covers all its cities/states); multi-target
  // remains possible on Settings.
  const setTargetCountry = useCallback(
    async (value: string) => {
      const next = value ? [value] : [];
      try {
        const res = await api<{ countries: string[] }>(
          "/api/settings/location-targets",
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ countries: next }),
          },
        );
        setSettings((prev) =>
          prev
            ? {
                ...prev,
                location_targets: {
                  available: prev.location_targets?.available ?? [],
                  countries: res.countries,
                },
              }
            : prev,
        );
        toast(
          value
            ? `Runs will keep only ${value} posts`
            : "Location filter off — posts from anywhere qualify",
        );
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Could not save", "error");
      }
    },
    [toast],
  );

  // Auto-expand the currently-running stage only.
  useEffect(() => {
    if (!run || run.trace.length === 0) return;
    if (autoExpandedFor.current === run.id) return;
    autoExpandedFor.current = run.id;
    const groups = new Map<string, TraceEntry[]>();
    for (const e of run.trace) {
      const list = groups.get(e.stage) ?? [];
      list.push(e);
      groups.set(e.stage, list);
    }
    const lastStage = [...groups.keys()].pop();
    if (lastStage) setExpanded({ [lastStage]: true });
  }, [run]);

  const stages: StageGroup[] = useMemo(() => {
    if (!run) return [];
    const groups = new Map<string, TraceEntry[]>();
    for (const e of run.trace) {
      const list = groups.get(e.stage) ?? [];
      list.push(e);
      groups.set(e.stage, list);
    }
    return [...groups.entries()].map(([stage, entries]) => ({ stage, entries }));
  }, [run]);

  const start = useCallback(async () => {
    setStarting(true);
    setExpanded({});
    autoExpandedFor.current = null;
    try {
      await api("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          recency,
          keyword_limit: allKeywords ? "all" : "default",
        }),
      });
      await poll();
      toast("Run started");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Could not start run", "error");
    } finally {
      setStarting(false);
    }
  }, [recency, allKeywords, poll, toast]);

  const validate = useCallback(async () => {
    setValidating(true);
    try {
      const res = await api<ValidationResult>("/api/runs/validate-search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword: valKeyword, recency }),
      });
      setValidation(res);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Validation call failed", "error");
    } finally {
      setValidating(false);
    }
  }, [valKeyword, recency, toast]);

  if (!loaded) return null;

  return (
    <div>
      <h1 className="page-title">Run</h1>
      <p className="page-subtitle">
        Manually triggered pipeline run — nothing runs on a schedule.
      </p>

      <div className="section card">
        <div className="row between">
          <div className="row">
            <label className="muted" htmlFor="recency">Recency</label>
            <select
              id="recency"
              className="input"
              value={recency}
              onChange={(e) => setRecency(e.target.value)}
            >
              <option value="24h">Past 24 hours</option>
              <option value="week">Past week</option>
              <option value="month">Past month</option>
            </select>
          </div>
          <div className="row">
            <label
              className="muted"
              htmlFor="location-target"
              title="Keep only hiring posts in this country — every city and state matches. Posts naming no location still come through."
            >
              Location
            </label>
            <select
              id="location-target"
              className="input"
              value={
                targetCountries.length === 1
                  ? targetCountries[0]
                  : targetCountries.length > 1
                    ? "__multi__"
                    : ""
              }
              onChange={(e) => {
                if (e.target.value !== "__multi__") void setTargetCountry(e.target.value);
              }}
            >
              <option value="">All locations</option>
              {targetCountries.length > 1 && (
                <option value="__multi__">
                  Multiple ({targetCountries.length}) — pick one to replace
                </option>
              )}
              {availableCountries.map((country) => (
                <option key={country} value={country}>
                  {country}
                </option>
              ))}
            </select>
          </div>
          <label
              className="row"
              style={{ gap: 8, cursor: "pointer" }}
              title="Search every active keyword in this run, instead of the usual 5-keyword rotation"
            >
              <input
                type="checkbox"
                checked={allKeywords}
                onChange={(e) => setAllKeywords(e.target.checked)}
              />
              <span className="muted">Search all keywords (full pool this run)</span>
            </label>
          <div className="row">
            <label
              className="muted"
              htmlFor="search-source"
              title="Which implementation searches LinkedIn: the MCP container (mcp) or direct browser automation (playwright)."
            >
              Source
            </label>
            <select
              id="search-source"
              className="input"
              value={activeSource}
              disabled={running || searchSourceSaving}
              onChange={(e) => void setSearchSource(e.target.value)}
            >
              {availableSources.map((s) => (
                <option key={s} value={s}>
                  {s === "playwright"
                    ? "Playwright (browser)"
                    : s === "mcp"
                      ? "MCP container"
                      : s}
                </option>
              ))}
            </select>
          </div>
          <button
            className="btn primary"
            disabled={starting || running}
            onClick={() => void start()}
          >
            {running ? <span className="spinner" /> : <Play size={16} strokeWidth={1.5} />}
            {running ? "Run in progress..." : "Run now"}
          </button>
        </div>
        {!settings?.linkedin.session_dir_present && (
          <p className="muted" style={{ marginTop: 12 }}>
            LinkedIn session not set up yet — runs will fail at the search
            stage until the one-time login is done (see Settings).
          </p>
        )}
      </div>

      {run && (
        <div className="section">
          <div className="row between" style={{ marginBottom: 12 }}>
            <h2 className="section-heading" style={{ margin: 0 }}>
              Trace · {run.status}
              {run.recency ? ` · past-${run.recency === "24h" ? "24h" : run.recency}` : ""}
            </h2>
            {run.summary && (
              <p className="muted">
                {run.summary.raw_hits} raw hits · {run.summary.keywords_used.length} keywords
                {run.summary.yoe_dropped != null
                  ? ` · ${run.summary.yoe_dropped} dropped by YoE`
                  : ""}
                {run.summary.location_dropped
                  ? ` · ${run.summary.location_dropped} dropped by location`
                  : ""}
                {run.summary.drafts_created != null
                  ? ` · ${run.summary.drafts_created} drafts`
                  : ""}
              </p>
            )}
          </div>

          {run.error && (
            <div className="card" style={{ borderColor: "var(--border-strong)", marginBottom: 12 }}>
              <span className="badge filled">failed</span>
              <p style={{ marginTop: 8 }}>{run.error}</p>
            </div>
          )}

          {stages.length === 0 && !running && (
            <p className="muted">No trace entries recorded for this run.</p>
          )}

          <div className="stepper">
            {stages.map(({ stage, entries }, idx) => {
              const isLast = idx === stages.length - 1;
              const isRunningStage = running && isLast;
              const open = expanded[stage] ?? false;
              const label = STAGE_LABELS[stage] ?? stage;
              return (
                <div key={stage} className={`stage${isRunningStage ? " active" : ""}`}>
                  <button
                    className="stage-header"
                    onClick={() => setExpanded((prev) => ({ ...prev, [stage]: !open }))}
                  >
                    <span className="stage-dot" />
                    <span className="stage-name">{label}</span>
                    <span className="stage-headline">{stageHeadline(stage, entries)}</span>
                    {open ? (
                      <ChevronDown size={14} strokeWidth={1.5} />
                    ) : (
                      <ChevronRight size={14} strokeWidth={1.5} />
                    )}
                  </button>
                  {open && (
                    <div className="stage-body">
                      {entries.map((e, i) => (
                        <p key={i} className="stage-entry">
                          {e.detail}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="section card">
        <h2 className="section-heading">Search validation (schema check)</h2>
        <p className="muted" style={{ marginBottom: 12 }}>
          One real search_posts call against the live session. Compare the raw
          response against the parsed preview before trusting downstream
          parsing — the plan flags the response schema as unverified.
        </p>
        <div className="row">
          <input
            className="input"
            style={{ flex: 1 }}
            value={valKeyword}
            onChange={(e) => setValKeyword(e.target.value)}
            placeholder="keyword for the validation search"
          />
          <button className="btn" disabled={validating} onClick={() => void validate()}>
            {validating ? <span className="spinner" /> : null}
            {validating ? "Calling search_posts..." : "Run validation call"}
          </button>
        </div>

        {validation && (
          <div style={{ marginTop: 16 }}>
            <div className="meta-row" style={{ marginBottom: 12 }}>
              <div className="meta-item">
                <span className="meta-label">Raw response size</span>
                <span className="meta-value">{validation.raw_length.toLocaleString()} chars</span>
              </div>
              <div className="meta-item">
                <span className="meta-label">Parsed posts</span>
                <span className="meta-value">{validation.parsed_count}</span>
              </div>
              <div className="meta-item">
                <span className="meta-label">Response quality</span>
                <span className={`badge ${validation.degraded ? "" : "accent"}`}>
                  {validation.degraded ? "degraded" : "parsed"}
                </span>
              </div>
            </div>

            {validation.sample_post && (
              <div className="card" style={{ marginBottom: 12 }}>
                <h3 className="section-heading">Sample parsed post (normalized fields)</h3>
                <table className="data-table">
                  <tbody>
                    {Object.entries(validation.sample_post).map(([k, v]) => (
                      <tr key={k}>
                        <td style={{ width: 180 }}>
                          <code>{k}</code>
                        </td>
                        <td className="muted" style={{ whiteSpace: "pre-wrap" }}>
                          {String(v) || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <details>
              <summary className="section-heading" style={{ cursor: "pointer" }}>
                Raw response (first 8,000 chars)
              </summary>
              <pre className="raw-pre">{validation.raw || "(empty)"}</pre>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}
