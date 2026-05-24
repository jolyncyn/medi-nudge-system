import { useState, useEffect } from "react";
import { getEscalations, updateEscalation } from "../lib/api";
import { formatSgtDate } from "../lib/time";

const PRIORITY_CHIP = {
  urgent: "bg-error-container text-on-error-container",
  high: "bg-accent-container text-accent",
  medium: "bg-gold-container text-gold",
  low: "bg-surface-container-highest text-on-surface/60",
};

const STATUS_OPTIONS = ["open", "in_progress", "resolved", "escalated_external"];

export default function EscalationsPage() {
  const [cases, setCases] = useState([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState("open");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [selected, setSelected] = useState(null);
  const [notes, setNotes] = useState("");
  const [newStatus, setNewStatus] = useState("");
  const [saving, setSaving] = useState(false);

  const fetchCases = async () => {
    setLoading(true);
    try {
      const { data } = await getEscalations({
        status: statusFilter || undefined,
        priority: priorityFilter || undefined,
        page: 1,
        page_size: 50,
      });
      setCases(data.items || data);
    } catch {
      // interceptor handles
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchCases();
  }, [statusFilter, priorityFilter]);

  const openCase = (c) => {
    setSelected(c);
    setNotes(c.coordinator_notes || "");
    setNewStatus(c.status);
  };

  const saveAction = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await updateEscalation(selected.id, {
        status: newStatus,
        coordinator_notes: notes,
      });
      setSelected(null);
      fetchCases();
    } catch {
      // leave panel open
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="font-display text-[26px] font-medium text-on-surface tracking-[-0.02em]">Escalation Queue</h1>
        <p className="font-body text-sm text-on-surface/50">{cases.length} cases</p>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-5">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-surface-container-highest rounded-xl px-3.5 py-2 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed transition-shadow"
        >
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="resolved">Resolved</option>
          <option value="escalated_external">Escalated External</option>
        </select>
        <select
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value)}
          className="bg-surface-container-highest rounded-xl px-3.5 py-2 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed transition-shadow"
        >
          <option value="">All priorities</option>
          <option value="urgent">Urgent</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      <div className="flex gap-4">
        {/* Case list */}
        <div className="flex-1 bg-surface-container-low rounded-2xl shadow-ambient overflow-hidden">
          {loading ? (
            <div className="p-8 text-center font-body text-on-surface/30">Loading…</div>
          ) : cases.length === 0 ? (
            <div className="p-8 text-center font-body text-on-surface/30">No cases found</div>
          ) : (
            <div className="space-y-0">
              {cases.map((c) => (
                <button
                  key={c.id}
                  onClick={() => openCase(c)}
                  className={`w-full text-left px-5 py-4 transition-colors ${
                    selected?.id === c.id
                      ? "bg-primary/10"
                      : "bg-surface-container-lowest hover:bg-surface-container-highest/40"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <span className={`px-2.5 py-0.5 rounded-pill text-xs font-semibold ${PRIORITY_CHIP[c.priority] || "bg-surface-container-highest text-on-surface/60"}` }>
                        {c.priority}
                      </span>
                      <span className="font-body text-sm font-medium text-on-surface">
                        {c.patient?.full_name ?? `Patient #${c.patient_id}`}
                      </span>
                    </div>
                    <span className="font-body text-xs text-on-surface/40">
                      {formatSgtDate(c.opened_at)}
                    </span>
                  </div>
                  <p className="font-body text-xs text-on-surface/50 mt-1.5 truncate">{c.reason}</p>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Action panel */}
        {selected && (
          <div className="w-80 bg-surface-container-low rounded-2xl shadow-ambient p-5 flex flex-col">
            <h2 className="font-display text-base font-bold text-on-surface mb-1">
              {selected.patient?.full_name ?? `Patient #${selected.patient_id}`}
            </h2>
            <p className="font-body text-xs text-on-surface/50 mb-5">{selected.reason}</p>

            <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Status</label>
            <select
              value={newStatus}
              onChange={(e) => setNewStatus(e.target.value)}
              className="bg-surface-container-highest rounded-xl px-3.5 py-2 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed mb-4 transition-shadow"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s.replace("_", " ")}</option>
              ))}
            </select>

            <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={5}
              className="bg-surface-container-highest rounded-xl px-3.5 py-2.5 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed flex-1 resize-none mb-4 transition-shadow"
              placeholder="Add coordinator notes…"
            />

            <div className="flex gap-2">
              <button
                onClick={saveAction}
                disabled={saving}
                className="flex-1 bg-gradient-to-br from-primary to-primary-container text-white rounded-pill py-2.5 font-body text-sm font-semibold disabled:opacity-60"
              >
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                onClick={() => setSelected(null)}
                className="flex-1 bg-surface-container-highest rounded-pill py-2.5 font-body text-sm text-on-surface/70 hover:bg-outline-variant/30"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
