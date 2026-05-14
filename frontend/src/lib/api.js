import axios from "axios";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const api = axios.create({ baseURL: BASE_URL });

// Attach JWT from sessionStorage (survives refresh, clears on tab close — never localStorage)
let _token = sessionStorage.getItem("token");
export const setToken = (t) => { _token = t; sessionStorage.setItem("token", t); };
export const clearToken = () => { _token = null; sessionStorage.removeItem("token"); };

api.interceptors.request.use((config) => {
  if (_token) config.headers.Authorization = `Bearer ${_token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && !window.location.pathname.includes("/login")) {
      clearToken();
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// Auth
export const login = (email, password) =>
  api.post("/api/auth/login", { email, password });

// Patients
export const getPatients = (params) => api.get("/api/patients", { params });
export const getPatient = (id) => api.get(`/api/patients/${id}`);
export const createPatient = (data) => api.post("/api/patients", data);
export const updatePatient = (id, data) => api.patch(`/api/patients/${id}`, data);
export const deletePatient = (id) => api.delete(`/api/patients/${id}`);
export const regenerateInviteLink = (id) => api.post(`/api/patients/${id}/invite-link`);
export const generateCaregiverInviteLink = (id) => api.post(`/api/patients/${id}/caregiver-invite-link`);

// Medication catalog
export const getMedications = () => api.get("/api/medications");
export const createMedication = (data) => api.post("/api/medications", data);

// Patient medications
export const getPatientMedications = (patientId) =>
  api.get(`/api/patients/${patientId}/medications`);
export const assignMedication = (patientId, data) =>
  api.post(`/api/patients/${patientId}/medications`, data);

// Dispensing records
export const getDispensingRecords = (patientId) =>
  api.get(`/api/patients/${patientId}/dispensing-records`);
export const createDispensingRecord = (data) =>
  api.post("/api/dispensing-records", data);

// Conditions (with medication mappings)
export const getConditions = () => api.get("/api/conditions");

// Nudge campaigns
export const getNudgeCampaigns = (params) => api.get("/api/nudge-campaigns", { params });
export const triggerNudgeCampaigns = () => api.post("/api/nudge-campaigns/trigger");
export const triggerDailyReminders = () => api.post("/api/reminders/trigger");
export const triggerPatientNudge = (patientId) => api.post(`/api/patients/${patientId}/nudge/trigger`);
export const triggerPatientReminder = (patientId) => api.post(`/api/patients/${patientId}/reminder/trigger`);

// Escalations
export const getEscalations = (params) => api.get("/api/escalations", { params });
export const updateEscalation = (id, data) => api.patch(`/api/escalations/${id}`, data);

// Prescriptions
export const getPrescriptions = (params) => api.get("/api/prescriptions", { params });
export const getPrescription = (id) => api.get(`/api/prescriptions/${id}`);
export const confirmPrescription = (id, data) =>
  api.patch(`/api/prescriptions/${id}/confirm`, data);
export const rejectPrescription = (id, data) =>
  api.patch(`/api/prescriptions/${id}/reject`, data);
export const uploadPrescription = (patientId, formData) =>
  api.post(`/api/prescriptions?patient_id=${patientId}`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });

// Analytics
export const getAdherenceAnalytics = (params) =>
  api.get("/api/analytics/adherence", { params });
export const getEscalationAnalytics = (params) =>
  api.get("/api/analytics/escalations", { params });
export const getDoseHistory = (patientId, params) =>
  api.get(`/api/patients/${patientId}/dose-history`, { params });
export const getDoseAdherence = (params) =>
  api.get("/api/analytics/dose-adherence", { params });
export const getDashboardSummary = () => api.get("/api/dashboard/summary");

// AI Summary
export const getPatientAiSummary = (patientId, params) =>
  api.get(`/api/patients/${patientId}/ai-summary`, { params });

// Analytics: critical adherence + missed dose heatmap
export const getCriticalAdherence = (params) =>
  api.get("/api/analytics/critical-adherence", { params });
export const getMissedDoseHeatmap = (params) =>
  api.get("/api/analytics/missed-dose-heatmap", { params });

export default api;
