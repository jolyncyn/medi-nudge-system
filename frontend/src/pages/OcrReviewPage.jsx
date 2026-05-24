import { useState, useEffect } from "react";
import { getPrescriptions, confirmPrescription, rejectPrescription } from "../lib/api";
import { formatSgtDate } from "../lib/time";

const STATUS_CHIP = {
  pending_review: "bg-yellow-100 text-yellow-800",
  confirmed: "bg-tertiary-container text-on-tertiary-container",
  rejected: "bg-error-container text-on-error-container",
  processing: "bg-secondary-container text-secondary",
};

export default function OcrReviewPage() {
  const [scans, setScans] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [editedFields, setEditedFields] = useState({});
  const [saving, setSaving] = useState(false);
  const [statusFilter, setStatusFilter] = useState("pending_review");

  const fetchScans = async () => {
    setLoading(true);
    try {
      const { data } = await getPrescriptions({
        status: statusFilter || undefined,
        page: 1,
        page_size: 50,
      });
      setScans(data.items || data);
    } catch {
      // interceptor handles
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchScans();
  }, [statusFilter]);

  const openScan = (scan) => {
    setSelected(scan);
    const fieldMap = {};
    scan.fields?.forEach((f) => {
      fieldMap[f.id] = f.value;
    });
    setEditedFields(fieldMap);
  };

  const handleConfirm = async () => {
    setSaving(true);
    try {
      const field_overrides = Object.entries(editedFields).map(([id, value]) => ({
        field_id: parseInt(id),
        value,
      }));
      await confirmPrescription(selected.id, { field_overrides });
      setSelected(null);
      fetchScans();
    } catch {
      // leave open
    } finally {
      setSaving(false);
    }
  };

  const handleReject = async (reason) => {
    setSaving(true);
    try {
      await rejectPrescription(selected.id, { reason });
      setSelected(null);
      fetchScans();
    } catch {
      // leave open
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="font-display text-2xl font-bold text-on-surface tracking-tight">OCR Review Queue</h1>
        <p className="font-body text-sm text-on-surface/50">Review and confirm prescription scans</p>
      </div>

      <div className="flex gap-3 mb-5">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-surface-container-highest rounded-xl px-3.5 py-2 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed transition-shadow"
        >
          <option value="pending_review">Pending Review</option>
          <option value="">All</option>
          <option value="confirmed">Confirmed</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>

      <div className="flex gap-4">
        {/* Scan list */}
        <div className="flex-1 bg-surface-container-low rounded-2xl shadow-ambient overflow-hidden">
          {loading ? (
            <div className="p-8 text-center font-body text-on-surface/30">Loading…</div>
          ) : scans.length === 0 ? (
            <div className="p-8 text-center font-body text-on-surface/30">No scans found</div>
          ) : (
            <div>
              {scans.map((s) => (
                <button
                  key={s.id}
                  onClick={() => openScan(s)}
                  className={`w-full text-left px-5 py-4 transition-colors ${
                    selected?.id === s.id
                      ? "bg-primary/10"
                      : "bg-surface-container-lowest hover:bg-surface-container-highest/40"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-body text-sm font-medium text-on-surface">
                      Scan #{s.id} · Patient {s.patient_id}
                    </span>
                    <span className={`font-body text-xs px-2.5 py-0.5 rounded-pill font-semibold ${STATUS_CHIP[s.status] || "bg-surface-container-highest text-on-surface/60"}`}>
                      {s.status}
                    </span>
                  </div>
                  <p className="font-body text-xs text-on-surface/40 mt-1.5">
                    {s.ocr_engine} · {formatSgtDate(s.uploaded_at)}
                  </p>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Review panel */}
        {selected && (
          <div className="w-96 bg-surface-container-low rounded-2xl shadow-ambient p-5 flex flex-col gap-5">
            {/* Image */}
            {selected.image_url && (
              <div className="rounded-xl overflow-hidden bg-surface-container-highest">
                <img
                  src={selected.image_url}
                  alt="Prescription scan"
                  className="w-full object-contain max-h-48"
                />
              </div>
            )}

            {/* Extracted fields */}
            <div>
              <h3 className="font-display text-sm font-bold text-on-surface mb-3">Extracted Fields</h3>
              {selected.fields?.length === 0 ? (
                <p className="font-body text-sm text-on-surface/30">No fields extracted</p>
              ) : (
                <div className="space-y-3">
                  {selected.fields?.map((f) => (
                    <div key={f.id}>
                      <label className="flex items-center gap-1.5 font-body text-xs text-on-surface/60 mb-1">
                        {f.field_name}
                        {f.confidence < 0.75 && (
                          <span className="text-error text-xs font-semibold">
                            ⚠ low confidence ({(f.confidence * 100).toFixed(0)}%)
                          </span>
                        )}
                      </label>
                      <input
                        type="text"
                        value={editedFields[f.id] ?? f.value}
                        onChange={(e) =>
                          setEditedFields((prev) => ({ ...prev, [f.id]: e.target.value }))
                        }
                        className={`w-full rounded-xl px-3 py-2 font-body text-sm text-on-surface outline-none focus:ring-2 transition-shadow ${
                          f.confidence < 0.75
                            ? "bg-error-container focus:ring-error/40"
                            : "bg-surface-container-highest focus:ring-primary-fixed"
                        }`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Actions */}
            {selected.status === "pending_review" && (
              <div className="flex gap-2 mt-auto">
                <button
                  onClick={handleConfirm}
                  disabled={saving}
                  className="flex-1 bg-gradient-to-br from-primary to-primary-container text-white rounded-pill py-2.5 font-body text-sm font-semibold disabled:opacity-60"
                >
                  {saving ? "…" : "Confirm"}
                </button>
                <button
                  onClick={() => handleReject("coordinator_rejected")}
                  disabled={saving}
                  className="flex-1 bg-error-container text-on-error-container rounded-pill py-2.5 font-body text-sm font-semibold hover:opacity-90 disabled:opacity-60"
                >
                  Reject
                </button>
                <button
                  onClick={() => setSelected(null)}
                  className="px-3 bg-surface-container-highest rounded-pill font-body text-sm text-on-surface/60 hover:bg-outline-variant/30"
                >
                  ✕
                </button>
              </div>
            )}
            {selected.status !== "pending_review" && (
              <button
                onClick={() => setSelected(null)}
                className="bg-surface-container-highest rounded-pill py-2.5 font-body text-sm text-on-surface/70 hover:bg-outline-variant/30"
              >
                Close
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
