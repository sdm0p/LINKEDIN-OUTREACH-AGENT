import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { ResumeState } from "../types";
import { useToast } from "../toast";
import { RefreshCw, Upload } from "../icons";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function EmptyResume({ onUploaded }: { onUploaded: (s: ResumeState) => void }) {
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const upload = useCallback(
    async (file: File) => {
      setBusy(true);
      try {
        const body = new FormData();
        body.append("file", file);
        const state = await api<ResumeState>("/api/resume/upload", {
          method: "POST",
          body,
        });
        onUploaded(state);
        toast(
          state.cached
            ? "Resume already parsed — loaded from cache"
            : "Resume parsed and roles expanded",
        );
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Upload failed", "error");
      } finally {
        setBusy(false);
      }
    },
    [onUploaded, toast],
  );

  return (
    <div className="empty-state">
      <p>Upload a resume PDF to build the skill, role, and experience profile.</p>
      <p className="muted">
        Parsing runs once per file version; identical uploads reuse the cache.
      </p>
      <button
        className="btn primary"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? <span className="spinner" /> : <Upload size={16} strokeWidth={1.5} />}
        {busy ? "Parsing..." : "Upload resume PDF"}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export default function ResumePage() {
  const toast = useToast();
  const [state, setState] = useState<ResumeState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [reparsing, setReparsing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputBusy = useRef(false);

  useEffect(() => {
    api<ResumeState>("/api/resume")
      .then(setState)
      .catch(() => undefined)
      .finally(() => setLoaded(true));
  }, []);

  const handleUpload = useCallback((s: ResumeState) => setState(s), []);

  const replaceFile = useCallback(
    async (file: File) => {
      if (fileInputBusy.current) return;
      fileInputBusy.current = true;
      try {
        const body = new FormData();
        body.append("file", file);
        const s = await api<ResumeState>("/api/resume/upload", {
          method: "POST",
          body,
        });
        setState(s);
        toast(
          s.cached ? "Identical resume — loaded from cache" : "Resume replaced and re-parsed",
        );
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Upload failed", "error");
      } finally {
        fileInputBusy.current = false;
      }
    },
    [toast],
  );

  const reparse = useCallback(async () => {
    setReparsing(true);
    try {
      const s = await api<ResumeState>("/api/resume/reparse", { method: "POST" });
      setState(s);
      toast("Fresh parse and role expansion complete");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Re-parse failed", "error");
    } finally {
      setReparsing(false);
    }
  }, [toast]);

  if (!loaded) return null;

  return (
    <div>
      <h1 className="page-title">Resume</h1>
      <p className="page-subtitle">
        Source of truth for skills, roles, and years of experience.
      </p>

      {!state?.has_resume ? (
        <EmptyResume onUploaded={handleUpload} />
      ) : (
        <>
          <div className="section">
            <div className="row between">
              <div className="meta-row">
                <div className="meta-item">
                  <span className="meta-label">File</span>
                  <span className="meta-value">{state.file_name}</span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">Size</span>
                  <span className="meta-value">
                    {state.file_size != null ? formatBytes(state.file_size) : "—"}
                  </span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">Parsed</span>
                  <span className="meta-value">{formatTimestamp(state.parsed_at)}</span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">Cache</span>
                  <span className={`badge ${state.cached ? "" : "accent"}`}>
                    {state.cached ? "from cache" : "fresh"}
                  </span>
                </div>
              </div>
              <div className="row">
                <button
                  className="btn small"
                  disabled={reparsing}
                  onClick={() => inputRef.current?.click()}
                >
                  <Upload size={14} strokeWidth={1.5} />
                  Replace
                </button>
                <button
                  className="btn small"
                  disabled={reparsing}
                  onClick={() => void reparse()}
                >
                  {reparsing ? <span className="spinner" /> : <RefreshCw size={14} strokeWidth={1.5} />}
                  {reparsing ? "Re-parsing..." : "Re-parse"}
                </button>
              </div>
            </div>
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              style={{ display: "none" }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void replaceFile(file);
                e.target.value = "";
              }}
            />
          </div>

          <div className="card">
            <div className="meta-row">
              <div className="meta-item">
                <span className="meta-label">Years of experience (YoE)</span>
                <span className="meta-value">{state.my_yoe}</span>
              </div>
              <div className="meta-item">
                <span className="meta-label">Exact estimate (pre-ceil)</span>
                <span className="meta-value">{state.total_experience_years}</span>
              </div>
            </div>
          </div>

          <div className="card">
            <h2 className="section-heading">Skills ({state.skills.length})</h2>
            <div className="chip-list">
              {state.skills.map((s) => (
                <span key={s} className="chip">
                  {s}
                </span>
              ))}
            </div>
          </div>

          <div className="card">
            <h2 className="section-heading">Roles held ({state.roles.length})</h2>
            <div className="chip-list">
              {state.roles.map((r) => (
                <span key={r} className="chip">
                  {r}
                </span>
              ))}
            </div>
          </div>

          {state.roles_expanded && state.roles_expanded.length > 0 && (
            <div className="card">
              <h2 className="section-heading">
                Expanded role pool ({state.roles_expanded.length})
              </h2>
              <p className="muted" style={{ marginBottom: 12 }}>
                Generated alongside the parse — re-runs only when the resume changes.
                Editable on the Keywords &amp; roles page.
              </p>
              <div className="chip-list">
                {state.roles_expanded.map((r) => (
                  <span key={r} className="chip">
                    {r}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="card">
            <h2 className="section-heading">Experience ({state.experience.length})</h2>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Company</th>
                  <th>Start</th>
                  <th>End</th>
                </tr>
              </thead>
              <tbody>
                {state.experience.map((e, i) => (
                  <tr key={`${e.title}-${e.company}-${i}`}>
                    <td>{e.title}</td>
                    <td>{e.company}</td>
                    <td>{e.start_date || "—"}</td>
                    <td>{e.end_date || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
