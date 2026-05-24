import { useState, useEffect, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  getDashboardSummary, updateEscalation, getPatients, createPatient,
  getConditions,
} from "../lib/api";
import { useAuth } from "../hooks/useAuth";
import TableSortHeader from "../components/ui/TableSortHeader";

/* ============================================================
   Greeting helpers — derive a friendly first name from the
   user object (fallback to "Care Team" if no name/email).
   ============================================================ */
function friendlyName(user) {
  if (!user) return "there";
  if (user.name) {
    // "Sarah Tan" → "Sarah", "nurse.sarah" → "Sarah"
    const first = user.name.split(/[\s_.]+/)[0];
    return first.charAt(0).toUpperCase() + first.slice(1);
  }
  if (user.email) {
    // "nurse.sarah@medinudge.sg" → "Sarah"
    const handle = user.email.split("@")[0];
    const first = handle.split(/[._-]+/).filter(Boolean).pop() || handle;
    return first.charAt(0).toUpperCase() + first.slice(1);
  }
  return "there";
}

function todayLabel() {
  // "Sat 24 May" style
  try {
    return new Intl.DateTimeFormat("en-SG", {
      weekday: "short",
      day: "numeric",
      month: "short",
    }).format(new Date());
  } catch {
    return new Date().toDateString();
  }
}

const PRIORITY_STYLE = {
  urgent: "bg-error-container/30 border-l-4 border-error",
  high: "bg-error-container/20 border-l-4 border-error/60",
  normal: "bg-surface-container-low",
  low: "bg-surface-container-low",
};

export default function DashboardPage() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [_loading, setLoading] = useState(true);

  // ============ URL <-> search box sync ============
  // The global search bar in the top utility bar (Layout) navigates here
  // with `?search=…`. We read that query string and keep it in sync with
  // the page-level search input below.
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSearch = searchParams.get("search") || "";

  // Patient list state
  const [patients, setPatients] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState(urlSearch);
  const [patientsLoading, setPatientsLoading] = useState(false);
  const PAGE_SIZE = 15;

  // When the URL `?search=` changes (e.g. user types in the top-bar search
  // and submits), pull it into local state and reset to page 1.
  useEffect(() => {
    if (urlSearch !== search) {
      setSearch(urlSearch);
      setPage(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlSearch]);

  // Enrol modal
  const [showEnrol, setShowEnrol] = useState(false);
  const [newPatient, setNewPatient] = useState({
    full_name: "", phone_number: "", nric: "", language_preference: "en", conditions: [], caregiver_name: "", caregiver_phone_number: "",
  });
  const [enrolling, setEnrolling] = useState(false);
  const [conditionsList, setConditionsList] = useState([]);

  // ============ Sort state (client-side, applies to the current page) ============
  const [sort, setSort] = useState({ key: "name", dir: "asc" });
  const onSort = (key) =>
    setSort((s) =>
      s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }
    );

  const sortedPatients = useMemo(() => {
    const copy = [...patients];
    const dirMul = sort.dir === "asc" ? 1 : -1;
    copy.sort((a, b) => {
      let va, vb;
      switch (sort.key) {
        case "name":
          va = (a.full_name || "").toLowerCase(); vb = (b.full_name || "").toLowerCase(); break;
        case "doses":
          va = data?.patient_compliance?.[a.id]?.compliance_score ?? -1;
          vb = data?.patient_compliance?.[b.id]?.compliance_score ?? -1;
          break;
        case "language":
          va = (a.language_preference || "").toLowerCase();
          vb = (b.language_preference || "").toLowerCase();
          break;
        case "onboarding":
          va = (a.onboarding_state || "").toLowerCase();
          vb = (b.onboarding_state || "").toLowerCase();
          break;
        case "active":
          va = a.is_active ? 0 : 1; vb = b.is_active ? 0 : 1; break;
        default:
          va = 0; vb = 0;
      }
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dirMul;
      if (va < vb) return -1 * dirMul;
      if (va > vb) return  1 * dirMul;
      return 0;
    });
    return copy;
  }, [patients, sort, data]);


  // Load dashboard summary
  const loadDashboard = async () => {
    try {
      const { data: d } = await getDashboardSummary();
      setData(d);
    } catch { /* no-op */ } finally { setLoading(false); }
  };

  // Load patient list
  const fetchPatients = async () => {
    setPatientsLoading(true);
    try {
      const { data } = await getPatients({ page, page_size: PAGE_SIZE, search: search || undefined });
      setPatients(data.items);
      setTotal(data.total);
    } catch { /* no-op */ } finally { setPatientsLoading(false); }
  };

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadDashboard(); getConditions().then(({ data }) => setConditionsList(data)).catch(() => {}); }, []);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { fetchPatients(); }, [page]);
  useEffect(() => { const t = setTimeout(fetchPatients, 350); return () => clearTimeout(t); }, [search]);

  const handleEnrol = async (e) => {
    e.preventDefault();
    setEnrolling(true);
    try {
      await createPatient(newPatient);
      setShowEnrol(false);
      setNewPatient({ full_name: "", phone_number: "", nric: "", language_preference: "en", conditions: [], caregiver_name: "", caregiver_phone_number: "" });
      fetchPatients();
      loadDashboard();
    } catch { /* no-op */ } finally { setEnrolling(false); }
  };

  const handleDismissEscalation = async (id) => {
    try { await updateEscalation(id, { status: "resolved" }); loadDashboard(); } catch { /* no-op */ }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="p-8 max-w-7xl mx-auto">
      {/* ============ Welcome greeting ============ */}
      <div className="mb-8">
        <div className="font-body text-[11.5px] uppercase tracking-[0.18em] font-semibold text-accent inline-flex items-center gap-2 mb-2">
          <span className="w-[18px] h-px bg-accent" />
          Today · {todayLabel()}
        </div>
        <h1 className="font-display text-[34px] font-normal tracking-[-0.025em] leading-tight text-on-surface">
          Welcome back, <em className="not-italic-fallback italic text-accent font-light">{friendlyName(user)}.</em>
        </h1>
        {data && (
          <p className="font-body text-sm text-ink-soft mt-1.5">
            {total} {total === 1 ? "patient" : "patients"} under your care
            {data.pending_escalations && data.pending_escalations.length > 0 && (
              <> · {data.pending_escalations.length} need{data.pending_escalations.length === 1 ? "s" : ""} attention today</>
            )}
            {data.pending_escalations && data.pending_escalations.length === 0 && (
              <> · all caught up today</>
            )}
          </p>
        )}
      </div>

      {/* Hero Metrics Bento Grid — hidden for mid-review, uncomment for final pitch
      {data && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          <div className="md:col-span-2 bg-gradient-to-br from-primary to-primary-container p-8 rounded-3xl text-white shadow-xl relative overflow-hidden">
            <div className="relative z-10">
              <p className="text-primary-fixed-dim font-body font-medium mb-1">Overall Adherence</p>
              <h2 className="text-5xl font-display font-extrabold mb-4">{data.overall_adherence}%</h2>
              <div className="flex items-center gap-2 bg-white/10 w-fit px-3 py-1 rounded-full text-xs font-body">
                <span>{data.adherence_trend >= 0 ? "+" : ""}{data.adherence_trend}% from last month</span>
              </div>
            </div>
            <div className="absolute -right-12 -bottom-12 w-48 h-48 bg-white/10 rounded-full blur-3xl" />
          </div>
          <div className="bg-surface-container-lowest p-6 rounded-3xl shadow-ambient flex flex-col justify-between">
            <div>
              <div className="w-10 h-10 rounded-full bg-error-container text-error flex items-center justify-center mb-4 text-lg font-bold">!!</div>
              <p className="text-on-surface/50 font-body text-sm font-medium">Needs Attention</p>
            </div>
            <div>
              <h3 className="text-3xl font-display font-bold text-on-surface">{data.high_risk_count}</h3>
              <p className="text-xs text-error font-semibold mt-1">Requires Immediate Action</p>
            </div>
          </div>
          <div className="bg-surface-container-lowest p-6 rounded-3xl shadow-ambient flex flex-col justify-between">
            <div>
              <div className="w-10 h-10 rounded-full bg-secondary-container text-secondary flex items-center justify-center mb-4 text-lg font-bold">Rx</div>
              <p className="text-on-surface/50 font-body text-sm font-medium">Pending Refills</p>
            </div>
            <div>
              <h3 className="text-3xl font-display font-bold text-on-surface">{data.pending_refills}</h3>
              <p className="text-xs text-on-surface/40 mt-1">Awaiting patient response</p>
            </div>
          </div>
        </div>
      )}
      */}

      {/* Content: Patient Table + Escalations Sidebar */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
        {/* Patient Registry with search, filters, pagination */}
        <div className="lg:col-span-3 bg-surface-container-lowest rounded-3xl shadow-ambient overflow-hidden">
          <div className="p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-lg font-display font-bold text-on-surface">Patient Registry</h3>
                <p className="text-xs text-on-surface/40 font-body">{total} patients total</p>
              </div>
              <button onClick={() => setShowEnrol(true)} className="bg-gradient-to-br from-primary to-primary-container text-white text-xs font-bold px-4 py-2 rounded-full hover:opacity-90">
                + Enrol Patient
              </button>
            </div>
            {/* Search */}
            <div className="flex gap-3 mb-4">
              <input
                type="text"
                placeholder="Search by name or phone..."
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                  // Keep the URL in sync so the top-bar search reflects this
                  if (e.target.value) {
                    setSearchParams({ search: e.target.value }, { replace: true });
                  } else {
                    setSearchParams({}, { replace: true });
                  }
                }}
                className="bg-surface-container-highest rounded-full px-4 py-2 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed w-64 transition-shadow"
              />
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="bg-surface-container-low text-muted text-[11px] font-bold uppercase tracking-[0.13em] font-body">
                  <TableSortHeader label="Patient"     sortKey="name"       active={sort.key} dir={sort.dir} onSort={onSort} />
                  <TableSortHeader label="Doses Taken" sortKey="doses"      active={sort.key} dir={sort.dir} onSort={onSort} />
                  <TableSortHeader label="Language"    sortKey="language"   active={sort.key} dir={sort.dir} onSort={onSort} />
                  <TableSortHeader label="Onboarding"  sortKey="onboarding" active={sort.key} dir={sort.dir} onSort={onSort} />
                  <TableSortHeader label="Active"      sortKey="active"     active={sort.key} dir={sort.dir} onSort={onSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-outline-variant/30">
                {patientsLoading ? (
                  <tr><td colSpan={5} className="px-6 py-10 text-center text-on-surface/30 font-body text-sm">Loading...</td></tr>
                ) : sortedPatients.length === 0 ? (
                  <tr><td colSpan={5} className="px-6 py-10 text-center text-on-surface/30 font-body text-sm">No patients found</td></tr>
                ) : (
                  sortedPatients.map((p, i) => (
                    <tr key={p.id} className={`hover:bg-surface-container-low/50 transition-colors ${i % 2 === 0 ? "bg-surface-container-lowest" : ""}`}>
                      <td className="px-4 py-3">
                        <Link to={`/patients/${p.id}`} className="text-sm font-bold text-on-surface hover:text-primary">{p.full_name}</Link>
                        <p className="text-xs text-on-surface/40">{p.phone_number}</p>
                      </td>
                      <td className="px-4 py-3">
                        {(() => {
                          const c = data?.patient_compliance?.[p.id];
                          if (!c) return <span className="text-on-surface/30 text-xs">--</span>;
                          const score = c.compliance_score;
                          const color = score >= 80 ? "text-green" : score >= 50 ? "text-gold" : "text-error";
                          return (
                            <div className="flex items-center gap-1.5">
                              <span className={`text-sm font-bold ${color}`}>{score}%</span>
                              {c.critical_missed_count > 0 && (
                                <span title={`${c.critical_missed_count} critical medication doses missed`}>⚠️</span>
                              )}
                            </div>
                          );
                        })()}
                      </td>
                      <td className="px-4 py-3 text-xs text-on-surface/60 uppercase">{p.language_preference}</td>
                      <td className="px-4 py-3 text-xs text-on-surface/50">{p.onboarding_state}</td>
                      <td className="px-4 py-3">
                        <span className={`inline-block w-2.5 h-2.5 rounded-full ${p.is_active ? "bg-tertiary-container" : "bg-surface-container-highest"}`} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
            {/* Pagination */}
            {totalPages > 1 && (
              <div className="px-6 py-3.5 bg-surface-container-lowest flex items-center justify-between font-body text-sm text-on-surface/50 border-t border-outline-variant/20">
                <span>Page {page} of {totalPages}</span>
                <div className="flex gap-2">
                  <button disabled={page === 1} onClick={() => setPage((p) => p - 1)} className="px-3 py-1 rounded-full bg-surface-container-highest disabled:opacity-40 hover:bg-outline-variant/30 text-xs">&larr; Prev</button>
                  <button disabled={page === totalPages} onClick={() => setPage((p) => p + 1)} className="px-3 py-1 rounded-full bg-surface-container-highest disabled:opacity-40 hover:bg-outline-variant/30 text-xs">Next &rarr;</button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Sidebar: Escalations */}
        <div className="space-y-6">
          {data && (
            <div className="bg-surface-container-lowest p-6 rounded-3xl shadow-ambient">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-md font-display font-bold text-on-surface">Pending Escalations</h3>
                {data.pending_escalations.length > 0 && (
                  <span className="bg-error text-white text-[10px] px-2 py-0.5 rounded-full font-bold">{data.pending_escalations.length} NEW</span>
                )}
              </div>
              <div className="space-y-4">
                {data.pending_escalations.map((e) => (
                  <div key={e.id} className={`p-4 rounded-2xl ${PRIORITY_STYLE[e.priority] || PRIORITY_STYLE.normal}`}>
                    <p className="text-xs font-bold text-on-surface capitalize">{e.reason.replace(/_/g, " ")}</p>
                    <p className="text-[11px] text-on-surface/60 mt-1">{e.patient_name} — {e.priority} priority</p>
                    <div className="mt-3 flex gap-2">
                      <Link to={`/patients/${e.patient_id}`} className="text-[10px] bg-primary text-white px-3 py-1 rounded-full font-bold hover:opacity-90">View Patient</Link>
                      <button onClick={() => handleDismissEscalation(e.id)} className="text-[10px] bg-white text-on-surface px-3 py-1 rounded-full border border-outline-variant hover:bg-surface-container-low">Dismiss</button>
                    </div>
                  </div>
                ))}
                {data.pending_escalations.length === 0 && (
                  <p className="text-sm text-on-surface/30 font-body text-center py-4">No pending escalations</p>
                )}
              </div>
            </div>
          )}
          {data && (
            <div className="bg-surface-container-lowest p-6 rounded-3xl shadow-ambient">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-8 h-8 rounded-full bg-error-container text-error flex items-center justify-center text-sm font-bold">!!</div>
                <div>
                  <p className="text-on-surface/50 font-body text-xs font-medium">Needs Attention</p>
                  <h3 className="text-2xl font-display font-bold text-on-surface">{data.high_risk_count}</h3>
                </div>
              </div>
            </div>
          )}
          {data && (
            <div className="bg-surface-container-lowest p-6 rounded-3xl shadow-ambient">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-8 h-8 rounded-full bg-secondary-container text-secondary flex items-center justify-center text-sm font-bold">Rx</div>
                <div>
                  <p className="text-on-surface/50 font-body text-xs font-medium">Pending Refills</p>
                  <h3 className="text-2xl font-display font-bold text-on-surface">{data.pending_refills}</h3>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Enrol Patient Modal */}
      {showEnrol && (
        <div className="fixed inset-0 bg-on-surface/40 flex items-center justify-center z-50">
          <div className="bg-surface-container-lowest/90 backdrop-blur-[20px] rounded-2xl shadow-float p-6 w-full max-w-md max-h-[90vh] overflow-y-auto">
            <h2 className="font-display text-xl font-bold text-on-surface mb-5">Enrol New Patient</h2>
            <form onSubmit={handleEnrol} className="space-y-4">
              <div>
                <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Full Name</label>
                <input type="text" required value={newPatient.full_name} onChange={(e) => setNewPatient((prev) => ({ ...prev, full_name: e.target.value }))} className="w-full bg-surface-container-highest rounded-xl px-3.5 py-2.5 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed" />
              </div>
              <div>
                <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">NRIC / FIN</label>
                <input type="text" required placeholder="S1234567A" value={newPatient.nric} onChange={(e) => setNewPatient((prev) => ({ ...prev, nric: e.target.value.toUpperCase() }))} className="w-full bg-surface-container-highest rounded-xl px-3.5 py-2.5 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed" />
              </div>
              <div>
                <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Phone (E.164)</label>
                <input type="text" required placeholder="+6598765432" value={newPatient.phone_number} onChange={(e) => setNewPatient((prev) => ({ ...prev, phone_number: e.target.value }))} className="w-full bg-surface-container-highest rounded-xl px-3.5 py-2.5 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed" />
              </div>
              <div>
                <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Language</label>
                <select value={newPatient.language_preference} onChange={(e) => setNewPatient((prev) => ({ ...prev, language_preference: e.target.value }))} className="w-full bg-surface-container-highest rounded-xl px-3.5 py-2.5 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed">
                  <option value="en">English</option>
                  <option value="zh">Chinese</option>
                  <option value="ms">Malay</option>
                  <option value="ta">Tamil</option>
                </select>
              </div>
              <div>
                <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Caregiver Name <span className="text-on-surface/40 font-normal">(optional)</span></label>
                <input type="text" placeholder="e.g. Jane Doe" value={newPatient.caregiver_name} onChange={(e) => setNewPatient((prev) => ({ ...prev, caregiver_name: e.target.value }))} className="w-full bg-surface-container-highest rounded-xl px-3.5 py-2.5 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed" />
              </div>
              <div>
                <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Caregiver Phone <span className="text-on-surface/40 font-normal">(optional)</span></label>
                <input type="text" placeholder="+6591234567" value={newPatient.caregiver_phone_number} onChange={(e) => setNewPatient((prev) => ({ ...prev, caregiver_phone_number: e.target.value }))} className="w-full bg-surface-container-highest rounded-xl px-3.5 py-2.5 font-body text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary-fixed" />
              </div>
              <div>
                <label className="block font-body text-xs font-medium text-on-surface/70 mb-1.5">Conditions</label>
                <div className="grid grid-cols-2 gap-1 max-h-40 overflow-y-auto rounded-xl bg-surface-container-highest p-2.5">
                  {conditionsList.map((c) => (
                    <label key={c.id} className="flex items-center gap-2 cursor-pointer py-0.5 px-1 rounded-lg hover:bg-surface-container-low">
                      <input type="checkbox" checked={newPatient.conditions.includes(c.name)} onChange={() => setNewPatient((prev) => ({ ...prev, conditions: prev.conditions.includes(c.name) ? prev.conditions.filter((x) => x !== c.name) : [...prev.conditions, c.name] }))} className="accent-primary w-3.5 h-3.5" />
                      <span className="font-body text-xs text-on-surface">{c.name}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="flex gap-3 pt-2">
                <button type="submit" disabled={enrolling} className="flex-1 bg-gradient-to-br from-primary to-primary-container text-white rounded-full py-2.5 font-body text-sm font-semibold disabled:opacity-60">{enrolling ? "Enrolling..." : "Enrol"}</button>
                <button type="button" onClick={() => setShowEnrol(false)} className="flex-1 bg-surface-container-highest rounded-full py-2.5 font-body text-sm text-on-surface/70 hover:bg-outline-variant/30">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
