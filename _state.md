## LANDING PAGE WARNING
- The root `index.html` in S3 (`s3://medi-nudge-frontend-staging/`) is the Adheris landing page — NOT the React app.
- The React app lives under `s3://medi-nudge-frontend-staging/portal/`.
- ALWAYS sync frontend build to `/portal/` prefix: `aws s3 sync dist s3://medi-nudge-frontend-staging/portal/`
- NEVER sync to the S3 root — it overwrites the landing page.

## DEPLOYMENT FACTS (DO NOT DEVIATE)
- AWS profile: agency_admin-354918370110
- ECS cluster: medi-nudge-staging
- No NAT gateways. Public subnets with public IP.
- No HashiCorp tools. No Vault. No Consul. None of that.
- No new IP allowlisting needed. Ever.
- CloudFront URL: https://d2osdk2gdq7n3i.cloudfront.net
- Deploy steps are in CLAUDE.md under "Deploy". Follow those EXACTLY.

# Project State

Current phase: FINAL SPRINT — pitch day is 25 May 2026 at 9am SGT. Today is 24 May.

## What is built and working (deployed on AWS)

### Web Portal (Adheris design system — merged 24 May)
- Fully restyled to Adheris design: warm cream palette, Fraunces/Inter Tight fonts, coral/teal/green tokens
- Layout: hover-expand sidebar with pin, top utility bar with user dropdown + sign out
- LoginPage: split-screen layout
- Dashboard: "Welcome back" greeting, sortable columns, URL search sync
- MedicationsPage: sortable columns
- EscalationsPage + PatientsPage: color-patched to Adheris tokens
- All MediNudge references renamed to Adheris
- TableSortHeader.jsx added as shared component
- Dashboard with "Doses Taken" column (colour-coded %) + ⚠️ icon for critical med misses
- High Risk Patients + Pending Refills cards in sidebar below Pending Escalations
- Patient detail page: AI Insights (Summary + "If You Miss" tabs), Adherence Trend chart, "What's Impacting Adherence" cards, Care Notes section
- Active Medications section with CRITICAL badges
- Pharmacy Refill Timeline (left col) + Doses by Medication + Recent Activity (right col)

### Backend + Integrations
- iOS app (connected to same backend via CloudFront)
- Telegram bot (@MediNudgeBot) with medication reminders and nudge campaigns
- Scheduler service for automated reminders (api-service + scheduler-service on ECS)
- OpenAI integration for nudge message generation + AI patient summaries (cause-and-effect messaging with medication education)
- ElevenLabs integration for voice nudges (keys configured, untested end-to-end)
- 10 seeded patients (Danny's seed_patients.py) with medications assigned + 90 days of dose history + caregivers on 5 patients
- Warfarin + Gliclazide flagged as critical medications
- Full medication catalog (41 meds) + missed_dose_info for all 41 (sourced from HealthHub)
- Condition mappings (30 conditions)
- Danny's upstream features merged (inline buttons, scheduled nudges, side-effect check-ins, OCR self-confirmation, medication info cards)
- Authentication with session persistence (sessionStorage) + role-based login (admin/patient/caregiver)
- User accounts: nurse/doctor (web portal), patient accounts (iOS), caregiver accounts (iOS) — all use Demo1234!
- Telegram webhook registered and receiving messages
- Analytics page: Dose Adherence Rate (fixed), Critical vs Non-Critical trend chart, Missed Dose Heatmap, Adherence by Medication table, Escalation Volume
- Care Notes: POST/GET API, web portal UI with Add Note form, 4 demo notes seeded
- Caregiver self-profile (own_patient_id): Tan Mei Ling has her own patient record + accessible_patients in login response
- iOS app sync APIs: dose event write, phone number update, caregiver link, device token registration
- APNs push notification service: ES256 JWT signing, HTTP/2 delivery, JWT caching, bad-token deactivation
- POST /api/users/me/test-push endpoint (authenticated, safe APNs test delivery)
- APNs secrets (APNS_PRIVATE_KEY, APNS_KEY_ID, APNS_TEAM_ID, APNS_TOPIC, APNS_ENVIRONMENT) injected into ECS task definitions :2 via Secrets Manager
- All domain timestamps normalised to SGT (Asia/Singapore) — backend now_sgt() + frontend time.js formatSgtDate/DateTime
- Nurse/doctor email domains changed to @medinudge.sg (was @sgh.com.sg) — live DB updated
- tg_51789857 and tg_1746763759 Telegram stubs hidden from patient registry list

## What is commented out / hidden

| File | Section | Reason |
|------|---------|--------|
| DashboardPage.jsx | Hero Metrics Bento Grid | Hidden for mid-review |
| AnalyticsPage.jsx | Refill Adherence Rate chart | Hidden for mid-review |
| PatientDetailPage.jsx | Voice Nudge section | Hidden for mid-review |
| PatientDetailPage.jsx | Nudge Campaigns section | Hidden for mid-review |
| PatientDetailPage.jsx | Trigger Nudge / Trigger Reminder buttons | Not demo-ready (Telegram) |
| PatientDetailPage.jsx | Telegram QR / Caregiver Invite section | Not demo-ready (Telegram) |
| App.jsx + Layout.jsx | OCR Review tab and route | Killed for final pitch |

## What was changed on 24 May (final sprint — deployed to AWS)

### APNs Push Notifications (feat/push-notification — merged)
- `apns_service.py`: ES256 JWT signing, HTTP/2 APNs calls, JWT caching (50-min TTL), automatic bad-token deactivation
- `POST /api/users/me/test-push`: authenticated endpoint for iOS PM to test APNs delivery end-to-end
- `PushSendResponse` schema: attempted / sent / failed / deactivated / errors
- `h2>=4.1` and `tzdata>=2024.1` added to requirements.txt
- ECS task definitions updated to `:2` by Codex (AWS CLI, no Terraform): 5 APNs secrets injected from Secrets Manager into both api-service and scheduler-service
- `apns_is_configured()` guard: if secrets not present, push silently fails without crashing the service
- Unit tests: payload shape, missing-config handling, bad-token deactivation, test-push endpoint

### Email Domain Update
- Nurse and doctor accounts changed from `@sgh.com.sg` → `@medinudge.sg`
- `seed_user_accounts.py` updated; live DB updated by Codex (old rows deactivated, new rows active)
- Accounts: `nurse.sarah@medinudge.sg`, `dr.lim@medinudge.sg` — password `Demo1234!`

### Demo Data Fix — 7-Day Dose Logs (fix_recent_dose_logs)
- Tan Wei Liang, Chen Mei Fong, Tan Mei Ling all had all-missed past 7 days (random seed produced bad data)
- `fix_recent_dose_logs()` added to `seed_user_accounts.py`: sets 5 taken / 2 missed per medication for days 1–7 (missed on days 3 and 6)
- Ran via ECS task override on live DB — iOS 7-day charts now show non-zero graphs

### SGT Timestamp Normalisation (fix/sgt-timestamps — merged)
- `backend/app/core/timezone.py`: `now_sgt()`, `as_sgt_naive()`, `sgt_isoformat()` helpers
- All `func.now()` (DB UTC) replaced with `now_sgt` across all models and services
- Incoming dose/dispensing timestamps normalised to SGT in routers
- `frontend/src/lib/time.js`: `formatSgtDate()`, `formatSgtDateTime()`, `nowSgtDatetimeLocal()`, `datetimeLocalToSgtIso()`
- PatientDetailPage, DashboardPage, EscalationsPage: all `new Date().toLocaleString()` replaced with explicit SGT formatting

### Patient Registry Cleanup
- `tg_51789857` and `tg_1746763759` (Telegram onboarding stubs) hidden from `/api/patients` list via `notin_` filter
- Records not deleted — just excluded from registry. Count is also correct (filtered from total).
- Unit test added: `test_patient_registry.py`

---

## What was changed on 22 May (today's sprint)

### Medication Education Feature
- Added `missed_dose_info` text field to Medication model + Alembic migration
- Curated missed-dose consequences for ALL 41 medications from HealthHub Singapore
- Seed script: `seed_missed_dose_info.py` populates all 41 medications
- "If You Miss" tab added to AI Insights card — shows per-medication education
- AI summary prompt updated: references medication consequences for critical meds, original 2-3 sentence style preserved

### Patient Detail Page Redesign
- Adherence Score + "What's Impacting Adherence" cards (worst med, pattern detection, streak alert)
- Adherence Trend (30 Days) line chart with 80% goal line + red dots for days below 50%
- AI Insights moved higher (above trend chart) for doctor quick-scan
- Removed separate Risk Level card (integrated into adherence label)
- Layout: Pharmacy Refill Timeline (left col-7) + Doses by Medication & Recent Activity (right col-5)

### Analytics Page
- Fixed Dose Adherence Rate chart: removed confusing Taken/Missed raw count lines, kept only adherence %
- Added Critical vs Non-Critical Medication Adherence trend chart (red vs teal lines)
- Added Missed Dose Heatmap (7 days × 4 time slots, red intensity = missed count)
- Backend endpoints: /api/analytics/critical-adherence, /api/analytics/missed-dose-heatmap

### User Accounts + Role-Based Access
- Added `role` (admin/patient/caregiver) and `patient_id` (FK) to User model
- Login response now returns `role`, `patient_id`, `full_name` — iOS app uses this for persona routing
- Created 10 demo accounts: 2 nurse/doctor (web portal), 5 patient (iOS), 3 caregiver (iOS)
- All accounts use password: Demo1234!
- iOS app developer (PM) informed of API contract for persona routing

### Caregiver Notes Feature
- New `CaregiverNote` model: patient_id, author_name, author_role, category, content, created_at
- Categories: missed_dose, wrong_dose, side_effect, behavior, general
- API: POST + GET at /api/patients/{id}/caregiver-notes
- Web portal: Caregiver Notes section with "+ Add Note" form (category dropdown + free text)
- Seeded 4 realistic demo notes for Tan Wei Liang (from interview data)

### UI Cleanup
- Commented out OCR Review tab (nav + route) — killed for final pitch
- Commented out Trigger Nudge / Trigger Reminder buttons — Telegram not demo-ready
- Commented out Telegram QR / Caregiver Invite section — not demo-ready

### Infrastructure
- JWT expiry extended to 6 hours (for demo day)
- CLAUDE.md updated with ABSOLUTE RULES for post-compact behavior
- _state.md updated with DEPLOYMENT FACTS section

---

## What was changed on 14 May

### Merged Danny's upstream (post-fork features)
- Inline Telegram buttons, scheduled nudge campaigns (fire_at), side-effect check-ins, medication info cards, OCR self-confirmation, smart self-onboarding
- 2 new Alembic migrations run: add_fire_at_to_nudge_campaigns + enhance_smart_self_onboarding
- Alembic merge migration created to resolve multiple heads (our is_critical + Danny's migrations)

### Database reset to Danny's data
- Cleared our 5 manually-created patients
- Ran Danny's seed_patients.py: 10 patients, 90 days dose history, caregivers, escalations
- Ran seed_data.py: full 40 medication catalog + 30 conditions
- Fixed seed_patients.py: get_password_hash → hash_password, removed start_date, fixed patient.last_taken_at → pm.last_taken_at

### Dashboard UI changes
- Removed hero metrics bento grid (commented out, not deleted)
- Moved High Risk Patients + Pending Refills into right sidebar below Pending Escalations
- Renamed "Compliance" column to "Doses Taken"
- Replaced "6 critical" text badge with ⚠️ icon (hover shows detail)
- Widened patient table (3/4 page instead of 2/3)
- Increased text sizes for readability
- Fixed scrollbar from middle of page to browser edge (#root width fix in index.css)
- Sidebar made sticky (stays visible while scrolling)

### Patient Detail UI changes
- AI Insights now auto-generates on page load (no manual click needed)
- AI timestamp fixed (appended Z suffix so JavaScript converts UTC → local SGT time)
- Replaced raw dose history list with per-medication breakdown (progress bars + adherence % per med)
- Recent Activity section: scrollable box, shows 50 records
- CRITICAL badge on medications
- Hidden: Voice Nudge section (commented out)
- Hidden: Nudge Campaigns section (commented out)
- Moved Active Medications above Pharmacy Refill Timeline

### Analytics UI changes
- Centered analytics page layout (w-full mx-auto)
- Hidden: Refill Adherence Rate chart (commented out)

### Medications UI changes
- Centered medications page layout (w-full mx-auto)
- Medication names cleaned: removed dosage from names (e.g. "Warfarin 5mg" → "Warfarin")

### New charts built but NOT deployed (branch: feat/analytics-critical-meds-trend-and-missed-dose-heatmap)
- Chart 1: Critical vs Non-Critical medication adherence trend (weekly line chart)
- Chart 2: Missed Doses heatmap by day of week × time of day
- Backend endpoints: /api/analytics/critical-adherence, /api/analytics/missed-dose-heatmap
- Frontend: API functions + chart components in AnalyticsPage.jsx
- Deploy for final pitch, not mid-review

### Documentation
- bazaar-prep.md updated with analytics chart explanations (what each chart shows, data source, what it tells a nurse)
- _state.md kept up to date
- defects.md updated: added rule about verifying tables/lists before presenting

## What is in progress / to build next (23-24 May)

### HIGH PRIORITY
1. HTML landing page updates — color scheme to match Jordan's design, button routing (TestFlight + web portal), remove dash from tagline
2. Patient detail page layout polish — current layout still needs visual improvement
3. Focus demo on Tan Wei Liang as main patient profile (most complete dataset + caregiver notes)
4. Practice end-to-end demo flow following caregiver journey workflow
5. iOS app: PM to implement persona routing using role/patient_id from login response
6. iOS app: PM to build caregiver notes UI (POST/GET /api/patients/{id}/caregiver-notes)

### MEDIUM PRIORITY
- Uncomment hidden sections — decide which to show for pitch (Voice Nudge? Nudge Campaigns? Hero grid?)
- Mobile-responsive fixes for phone demo
- CSS tooltip for ⚠️ icon (instant hover)
- Success metrics — integrate into presentation

### LOW PRIORITY (skip if no time)
- Telegram bot testing (QR link bug, onboarding flow)
- Self-adjustment detection alert
- Medication interaction flagging

## Key decisions made

- Prioritise 2-3 features at 100% polish over many features half-done
- Demo impact matters more than code perfection
- Danny's upstream features merged — our baseline matches his latest code
- Critical meds list: using Warfarin + Gliclazide as placeholders until Sabrina provides list
- ECS in public subnets with public IP, no NAT gateways (cost saving)
- CloudFront serves both frontend and proxies /api/* to ALB (single URL)
- No Twilio credentials — WhatsApp/SMS features silently fall back to logging
- Comment out (don't delete) features hidden — easy to restore
- OCR Review killed for final pitch (commented out, not deleted)
- Risk Level removed as separate card — integrated into adherence score label
- Web portal = healthcare professionals only. iOS app = patients + caregivers.
- Caregiver notes: predefined categories + optional free text (demo-friendly)
- Demo focus: Tan Wei Liang (patient 9) — has caregiver, notes, Warfarin, low adherence

## User accounts (all password: Demo1234!)

| Email | Role | For |
|-------|------|-----|
| nurse.sarah@medinudge.sg | admin | Web portal demo |
| dr.lim@medinudge.sg | admin | Web portal demo |
| tanweiliang@patient.medinudge.sg | patient | iOS app (patient_id linked to Tan Wei Liang) |
| tanmeiling@caregiver.medinudge.sg | caregiver | iOS app (linked to Tan Wei Liang + Chen Mei Fong) |
| admin@medinudge.sg | admin | Original admin (web + iOS) |

## Unmerged branches on GitHub

| Branch | Purpose | Merge? |
|--------|---------|--------|
| Old feature branches | Already merged PRs | Can delete |

---

## Feedback from Mid-Review Bazaar (16 May)

### Questions raised by judges/visitors:
1. **How do we measure success?** — Need success metrics framework (engagement, outcome, behavioral)
2. **How do we tackle behavioural issues ("playing doctor")?** — Reminders alone won't fix conscious non-compliance. Need education on consequences, escalation, pattern flagging.
3. **Caregiver notes feeding into healthcare reports** — Allow caregivers to submit notes (e.g. "Mum seemed confused"), compiled into summary for doctors.
4. **Medication dependency/interactions** — If Medication A conflicts with Medication B, does the system flag it? (Team response: good feedback, but need interaction data source)
5. **QR code testing at bazaar** — Visitors can't experience the flow without iOS app installed. Web portal only has admin login.

### Team meeting decisions (post-bazaar):
- **AI Insights too lengthy** — need shorter, more actionable summaries upfront. Fitbit-style encouragement messaging.
- **Cause-and-effect messaging** — focus on consequences of missing doses. Example: "Missing Crestor and Lipitor could lead to future complications." AI-generated, accuracy secondary for POC.
- **Personalized reminders** — family member names/relationships in messages. Voice cloning explored but trust concerns.
- **Positioning** — reframe as HealthHub gap-filler, not standalone. Leverage existing HealthHub medication lists. Caregiver consent process.
- **Two-user access model** — patient and caregiver both monitor same medication list. End-of-day compliance summaries for caregivers.
- **Success metrics for pitch**: (1) engagement metrics (DAU/MAU), (2) outcome metrics (adherence improvement %), (3) behavioral metrics (caregiver-patient interaction effectiveness)
- **Scaled back**: remove direct doctor calling, keep caregiver alerts for missed doses/symptoms
- **Mobile interface**: current desktop view too small for phone demo. Animation capabilities available.

---

## What Jolyn is building for final pitch (assigned from meeting)

1. **AI Insights improvements** — shorter summaries, cause-and-effect messaging, medication education (what happens if you miss a dose)
2. **Medication education data** — enrich medication catalog with missed_dose_info (what happens if missed, from HealthHub). Manual curation for the 16 meds our demo patients use.
3. **Feed medication education into AI summary prompt** — so AI generates context-aware summaries like "Missing Warfarin can cause blood to become too thick. Do not double dose."

---

## Priority for final 2 days (23-24 May)

### DONE (completed 22 May)
- ✅ AI Insights improvements — cause-and-effect messaging with medication education
- ✅ Add missed_dose_info to medication model — curated from HealthHub for ALL 41 meds
- ✅ Feed medication education into AI summary prompt
- ✅ Deploy analytics charts (critical meds trend + missed dose heatmap)
- ✅ Fix Dose Adherence Rate chart
- ✅ Caregiver notes feature (API + web portal UI + demo data)
- ✅ User accounts (patient/caregiver/nurse roles + login response with role/patient_id)
- ✅ Remove non-functional buttons (OCR, nudge triggers, Telegram QR)

### HIGH PRIORITY (must do before 25 May)
1. HTML landing page — color scheme, button routing, remove dash from tagline
2. End-to-end demo rehearsal with Tan Wei Liang as main patient
3. iOS: PM to register device token + call POST /api/users/me/test-push to verify APNs delivery

### MEDIUM PRIORITY (nice to have)
- Uncomment hidden sections — decide which to show
- Mobile-responsive fixes for phone demo
- Success metrics integration into presentation/portal
- CSS tooltip for ⚠️ icon

### LOW PRIORITY (skip)
- Telegram bot testing
- Medication interaction flagging
- Self-adjustment detection
