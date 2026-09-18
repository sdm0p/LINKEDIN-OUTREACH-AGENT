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

interface LinkedInHealth {
  status: string;
  detail: string;
}

function LlmKeyControls() {
  const toast = useToast();
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await api("/api/settings/llm/key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key.trim() }),
      });
      toast("Key verified and saved");
      setKey("");
      window.location.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Could not save key", "error");
    } finally {
      setSaving(false);
    }
  }, [key, toast]);

  const remove = useCallback(async () => {
    try {
      await api("/api/settings/llm/key", { method: "DELETE" });
      toast("Key removed");
      window.location.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Could not remove key", "error");
    } finally {
      setConfirming(false);
    }
  }, [toast]);

  return (
    <div style={{ marginTop: 12 }}>
      <div className="row">
        <input
          className="input"
          style={{ flex: 1 }}
          type="password"
          autoComplete="off"
          placeholder="Paste your Gemini API key (AIza...)"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button
          className="btn primary"
          disabled={saving || key.trim().length < 30}
          onClick={() => void save()}
        >
          {saving ? <span className="spinner" /> : null}
          {saving ? "Verifying..." : "Save & verify"}
        </button>
      </div>
      <p className="muted" style={{ marginTop: 8 }}>
        Free key from{" "}
        <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">
          aistudio.google.com/apikey
        </a>
        . It's verified with a live call before being saved, stored only in
        this app's local data (never sent anywhere else), and never displayed
        again.
      </p>
      {confirming ? (
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted">Remove the stored key?</span>
          <button className="btn small" onClick={() => void remove()}>
            Yes, remove
          </button>
          <button className="btn small" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn small" onClick={() => setConfirming(true)}>
            Remove key
          </button>
        </div>
      )}
    </div>
  );
}

function LocationTargetsCard() {
  const toast = useToast();
  const [selected, setSelected] = useState<string[]>([]);
  const [available, setAvailable] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<SettingsInfo>("/api/settings")
      .then((info) => {
        const lt = info.location_targets;
        if (lt) {
          setSelected(lt.countries);
          setAvailable(lt.available);
        }
      })
      .catch(() => undefined)
      .finally(() => setLoaded(true));
  }, []);

  const toggle = (country: string) => {
    setSelected((prev) =>
      prev.includes(country)
        ? prev.filter((c) => c !== country)
        : [...prev, country],
    );
  };

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await api("/api/settings/location-targets", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ countries: selected }),
      });
      toast(
        selected.length
          ? `Target countries saved: ${selected.join(", ")}`
          : "Location filter cleared — posts from anywhere qualify",
      );
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Could not save", "error");
    } finally {
      setSaving(false);
    }
  }, [selected, toast]);

  return (
    <div className="card">
      <h2 className="section-heading">Target countries</h2>
      <p className="muted" style={{ marginBottom: 12 }}>
        Runs search and keep only hiring posts in these countries — e.g.
        India matches Bangalore, Delhi, Gurgaon, every city and state.
        Posts that mention no location still come through; posts provably
        elsewhere are dropped. Empty = no location filter.
      </p>
      {!loaded ? (
        <span className="spinner" />
      ) : (
        <>
          <div className="chip-row" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {available.map((country) => {
              const on = selected.includes(country);
              return (
                <button
                  key={country}
                  className={`btn small${on ? " primary" : ""}`}
                  onClick={() => toggle(country)}
                >
                  {country}
                  {on ? " ✓" : ""}
                </button>
              );
            })}
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button
              className="btn primary"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? <span className="spinner" /> : null}
              {saving ? "Saving..." : "Save target countries"}
            </button>
            {selected.length > 0 && (
              <span className="muted">{selected.length} selected</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const [info, setInfo] = useState<SettingsInfo | null>(null);
  const [checking, setChecking] = useState(false);
  const [health, setHealth] = useState<LinkedInHealth | null>(null);

  useEffect(() => {
    api<SettingsInfo>("/api/settings")
      .then(setInfo)
      .catch(() => undefined);
  }, []);

  const runHealthCheck = useCallback(async () => {
    setChecking(true);
    setHealth(null);
    try {
      const res = await api<LinkedInHealth>("/api/settings/linkedin/check", {
        method: "POST",
      });
      setHealth(res);
    } catch (err) {
      setHealth({
        status: "unavailable",
        detail: err instanceof ApiError ? err.message : "Health check request failed",
      });
    } finally {
      setChecking(false);
    }
  }, []);

  if (!info) return null;

  const li = info.linkedin;

  return (
    <div>
      <h1 className="page-title">Settings</h1>
      <p className="page-subtitle">Provider status and data controls.</p>

      <LocationTargetsCard />

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
            No key configured yet — paste one below to enable resume parsing,
            keyword generation, and drafts.
          </p>
        )}
        {info.llm.source === "env" && (
          <p className="muted" style={{ marginTop: 12 }}>
            Using GEMINI_API_KEY from the environment. You can replace it here
            without restarting.
          </p>
        )}
        <LlmKeyControls />
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
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn small"
            disabled={checking || !li.docker_installed || !li.session_dir_present}
            onClick={() => void runHealthCheck()}
            title="Spawns the MCP container and makes one read-only call — can take up to a minute"
          >
            {checking ? <span className="spinner" /> : null}
            {checking ? "Checking session..." : "Check session now"}
          </button>
          {health && (
            <span className={`badge ${health.status === "valid" ? "accent" : ""}`}>
              {health.status}
            </span>
          )}
        </div>
        {health && <p className="muted" style={{ marginTop: 8 }}>{health.detail}</p>}
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
