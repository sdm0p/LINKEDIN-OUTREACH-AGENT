import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useToast } from "../toast";
import { Pin, PinOff, Plus, Sparkles, Trash2 } from "../icons";

interface Keyword {
  id: number;
  text: string;
  tier: "skill" | "title";
  pinned: boolean;
  active: boolean;
  last_used_at: string | null;
  times_used: number;
  created_at: string;
}

function formatLastUsed(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days}d ago`;
}

export default function KeywordsPage() {
  const toast = useToast();
  const [keywords, setKeywords] = useState<Keyword[] | null>(null);
  const [generating, setGenerating] = useState(false);
  const [newText, setNewText] = useState("");
  const [newTier, setNewTier] = useState<"skill" | "title">("skill");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setKeywords(await api<Keyword[]>("/api/keywords"));
    } catch {
      setKeywords([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const generate = useCallback(async () => {
    setGenerating(true);
    try {
      const next = await api<Keyword[]>("/api/keywords/generate", { method: "POST" });
      setKeywords(next);
      toast(`Pool regenerated — ${next.length} keywords (pinned kept)`);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Generation failed", "error");
    } finally {
      setGenerating(false);
    }
  }, [toast]);

  const add = useCallback(async () => {
    if (!newText.trim()) return;
    try {
      const kw = await api<Keyword>("/api/keywords", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newText, tier: newTier }),
      });
      setKeywords((prev) =>
        prev ? [...prev, kw].sort((a, b) => a.tier.localeCompare(b.tier)) : [kw],
      );
      setNewText("");
      toast("Keyword added");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Could not add keyword", "error");
    }
  }, [newText, newTier, toast]);

  const patch = useCallback(
    async (id: number, fields: Record<string, unknown>) => {
      try {
        await api(`/api/keywords/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(fields),
        });
        setKeywords((prev) =>
          prev ? prev.map((k) => (k.id === id ? { ...k, ...fields } as Keyword : k)) : prev,
        );
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Update failed", "error");
      }
    },
    [toast],
  );

  const remove = useCallback(
    async (id: number) => {
      try {
        await api(`/api/keywords/${id}`, { method: "DELETE" });
        setKeywords((prev) => (prev ? prev.filter((k) => k.id !== id) : prev));
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Delete failed", "error");
      } finally {
        setConfirmDeleteId(null);
      }
    },
    [toast],
  );

  if (!keywords) return null;

  return (
    <div>
      <h1 className="page-title">Keywords &amp; roles</h1>
      <p className="page-subtitle">
        Search pool used by runs. Each run rotates a subset (5) so search
        volume stays low; pins are always included.
      </p>

      <div className="section row between">
        <p className="muted">
          {keywords.length} keywords in pool ·{" "}
          {keywords.filter((k) => k.pinned).length} pinned
        </p>
        <button className="btn primary" disabled={generating} onClick={() => void generate()}>
          {generating ? <span className="spinner" /> : <Sparkles size={16} strokeWidth={1.5} />}
          {generating ? "Generating..." : "Generate pool from resume"}
        </button>
      </div>

      {keywords.length === 0 ? (
        <div className="empty-state">
          <p>
            No keywords yet. Generate a pool from the parsed resume, or add
            individual keywords below.
          </p>
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: "44%" }}>Keyword</th>
              <th>Tier</th>
              <th>Last used</th>
              <th>Uses</th>
              <th style={{ width: "220px" }}></th>
            </tr>
          </thead>
          <tbody>
            {keywords.map((k) => (
              <tr key={k.id}>
                <td>
                  {editingId === k.id ? (
                    <input
                      className="input"
                      value={editText}
                      autoFocus
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && editText.trim()) {
                          void patch(k.id, { text: editText.trim() });
                          setEditingId(null);
                        }
                        if (e.key === "Escape") setEditingId(null);
                      }}
                    />
                  ) : (
                    k.text
                  )}
                </td>
                <td>
                  <span className={`badge ${k.tier === "skill" ? "accent" : ""}`}>
                    {k.tier}
                  </span>
                </td>
                <td className="muted">{formatLastUsed(k.last_used_at)}</td>
                <td className="muted">{k.times_used}</td>
                <td>
                  <div className="row" style={{ justifyContent: "flex-end" }}>
                    {editingId === k.id ? (
                      <button
                        className="btn small"
                        onClick={() => {
                          if (editText.trim()) void patch(k.id, { text: editText.trim() });
                          setEditingId(null);
                        }}
                      >
                        Save
                      </button>
                    ) : (
                      <button
                        className="btn small"
                        onClick={() => {
                          setEditingId(k.id);
                          setEditText(k.text);
                        }}
                      >
                        Edit
                      </button>
                    )}
                    <button
                      className="btn small"
                      title={k.pinned ? "Unpin" : "Pin (always included in runs)"}
                      onClick={() => void patch(k.id, { pinned: !k.pinned })}
                    >
                      {k.pinned ? <PinOff size={14} strokeWidth={1.5} /> : <Pin size={14} strokeWidth={1.5} />}
                    </button>
                    {confirmDeleteId === k.id ? (
                      <span className="inline-confirm">
                        <button className="btn small primary" onClick={() => void remove(k.id)}>
                          Confirm
                        </button>
                        <button className="btn small" onClick={() => setConfirmDeleteId(null)}>
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        className="btn small"
                        title="Remove"
                        onClick={() => setConfirmDeleteId(k.id)}
                      >
                        <Trash2 size={14} strokeWidth={1.5} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h2 className="section-heading">Add keyword manually</h2>
        <div className="row">
          <input
            className="input"
            style={{ flex: 1 }}
            placeholder='e.g. "hiring LangChain"'
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void add()}
          />
          <select
            className="input"
            value={newTier}
            onChange={(e) => setNewTier(e.target.value as "skill" | "title")}
          >
            <option value="skill">skill-anchored</option>
            <option value="title">title-anchored</option>
          </select>
          <button className="btn" onClick={() => void add()}>
            <Plus size={16} strokeWidth={1.5} />
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
