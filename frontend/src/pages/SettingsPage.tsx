import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { SettingsInfo } from "../types";
import { useToast } from "../toast";

function PurgeControls() {
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);

  const purge = useCallback(
    async (status: string | null) => {
      try {
        const res = await api<{ removed: number }>("/api/queue/purge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status }),
        });
        toast(`Purged ${res.removed} entries`);
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Purge failed", "error");
      } finally {
        setConfirming(false);
        window.location.reload();
      }
    },
    [toast],
  );

  if (!confirming) {
    return (
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn small" onClick={() => setConfirming(true)}>
          Purge data...
        </button>
      </div>
    );
  }
  return (
    <div className="row" style={{ marginTop: 12 }}>
      <span className="muted">Purge what, exactly?</span>
      <button className="btn small" onClick={() => void purge("skipped")}>
        Skipped drafts
      </button>
      <button className="btn small" onClick={() => void purge("sent")}>
        Sent drafts
      </button>
      <button
        className="btn small"
        onClick={() => void purge(null)}
        title="All drafts and dedup entries — posts can be reprocessed on the next run"
      >
        Everything
      </button>
      <button className="btn small" onClick={() => setConfirming(false)}>
        Cancel
      </button>
    </div>
  );
}

export default function SettingsPage() {
  const [info, setInfo] = useState<SettingsInfo | null>(null);

  useEffect(() => {
    api<SettingsInfo>("/api/settings")
      .then(setInfo)
      .catch(() => undefined);
  }, []);

  if (!info) return null;

  const li = info.linkedin;

  return (
    <div>
      <h1 className="page-title">Settings</h1>
      <p className="page-subtitle">Provider status and data controls.</p>

      <div className="card">
        <h2 className="section-heading">LLM provider</h2>
        <div className="meta-row">
          <div className="meta-item">
            <span className="meta-label">Provider</span>
            <span className="meta-value">{info.llm.provider}</span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Model</span>
            <span className="meta-value">{info.llm.model}</span>
          </div>
          <div className="meta-item">
            <span className="meta-label">API key</span>
            <span className={`badge ${info.llm.configured ? "accent" : ""}`}>
              {info.llm.configured ? "configured" : "missing"}
            </span>
          </div>
        </div>
        {!info.llm.configured && (
          <p className="muted" style={{ marginTop: 12 }}>
            Set GEMINI_API_KEY in backend/.env, then restart the backend.
          </p>
        )}
      </div>

      <div className="card">
        <h2 className="section-heading">LinkedIn search source</h2>
        <div className="meta-row">
          <div className="meta-item">
            <span className="meta-label">Source</span>
            <span className="meta-value">{info.search_source?.name ?? "linkedin"}</span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Session status</span>
            <span className={`badge ${li.status === "unknown" || li.status === "valid" ? "accent" : "muted"}`}>
              {li.status.replace("_", " ")}
            </span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Docker installed</span>
            <span className="badge muted">{li.docker_installed ? "yes" : "no"}</span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Session volume</span>
            <span className="badge muted">{li.session_dir_present ? "present" : "missing"}</span>
          </div>
        </div>
        <p className="muted" style={{ marginTop: 12 }}>
          {li.detail}
        </p>
        <p className="muted" style={{ marginTop: 8 }}>
          One-time login (opens a browser viewer on port 6080):
          <code> docker run -it --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp -p 127.0.0.1:6080:6080 stickerdaniel/linkedin-mcp-server:latest --login --login-viewer</code>
        </p>
      </div>

      <div className="card">
        <h2 className="section-heading">Draft cap</h2>
        <div className="meta-row">
          <div className="meta-item">
            <span className="meta-label">Max drafts per day</span>
            <span className="meta-value">{info.drafts.max_per_day}</span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Enforced from</span>
            <span className="muted">the draft-generation stage (upcoming slice)</span>
          </div>
        </div>
      </div>

      <div className="card">
        <h2 className="section-heading">Data retention</h2>
        <div className="meta-row">
          <div className="meta-item">
            <span className="meta-label">Dedup entries</span>
            <span className="meta-value">{info.retention.dedup_entries}</span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Drafts</span>
            <span className="meta-value">
              {info.retention.drafts_new ?? 0} new / {info.retention.drafts_total ?? 0} total
            </span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Oldest entry</span>
            <span className="meta-value">
              {info.retention.oldest_entry_age_days === 0
                ? "—"
                : `${info.retention.oldest_entry_age_days} days old`}
            </span>
          </div>
          <div className="meta-item">
            <span className="meta-label">Resume cached</span>
            <span className="badge muted">{info.retention.resume_cached ? "yes" : "no"}</span>
          </div>
        </div>
        <p className="muted" style={{ marginTop: 12 }}>
          Nothing is ever purged automatically — purging is manual only.
        </p>
        <PurgeControls />
      </div>
    </div>
  );
}
