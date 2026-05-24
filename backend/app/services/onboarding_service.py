"""
Patient onboarding service.
Manages the state machine:
  invited → consent_pending → language_confirmed → medication_capture
          → confirm → preferences → complete

Entry paths:
  - Coordinator-initiated: generate_invite_token() → QR code → patient scans → /start TOKEN
  - Patient self-initiated: /start (no token) → identity_verification → NRIC lookup
"""
import base64
import io
import logging
import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.core.config import settings, hash_sha256
from app.core.timezone import now_sgt
from app.models.models import EscalationCase, OnboardingToken, Patient, PatientMedication
from app.services import telegram_service, escalation_service, sms_service

logger = logging.getLogger(__name__)

TOKEN_TTL_HOURS = 72

ONBOARDING_STATES = {
    # Coordinator-initiated path
    "identity_verification",
    "invited",
    "consent_pending",
    "language_confirmed",
    # Self-registration path (fully bot-driven, no coordinator needed)
    "self_lang",
    "self_consent",
    "self_name",
    "self_nric",
    "self_condition",
    # Shared states (both paths converge here)
    "medication_capture",
    "patient_pending_ocr_confirmation",
    "confirm",
    "preferences",
    "voice_preference",
    "voice_selection",
    "self_registering",
    "drop_off_recovery",
    "medication_confirm_pending",
}

LANG_PROMPT_TEXT = (
    "Please select your preferred language / 请选择语言 / Sila pilih bahasa / மொழியைத் தேர்ந்தெடுக்கவும்:"
)
LANG_BUTTONS = [[
    {"text": "English", "callback_data": "1"},
    {"text": "中文", "callback_data": "2"},
    {"text": "Melayu", "callback_data": "3"},
    {"text": "தமிழ்", "callback_data": "4"},
]]

SELF_WELCOME = (
    "Welcome to Medi-Nudge! 👋\n\n"
    "Please select your preferred language / 请选择语言 / Sila pilih bahasa / மொழியைத் தேர்ந்தெடுக்கவும்:"
)

SELF_CONSENT_MESSAGES = {
    "en": (
        "Medi-Nudge helps you manage your medications with personalised reminders.\n\n"
        "Your information is stored securely in accordance with PDPA and will only be used "
        "to support your medication care.\n\n"
        "Do you agree to join? Reply *YES* to continue or *NO* to cancel."
    ),
    "zh": (
        "Medi-Nudge 通过个性化提醒帮助您管理用药。\n\n"
        "您的信息将根据个人数据保护法（PDPA）安全存储，仅用于支持您的用药护理。\n\n"
        "您同意加入吗？回复 *同意* 继续，或 *拒绝* 取消。"
    ),
    "ms": (
        "Medi-Nudge membantu anda mengurus ubat dengan peringatan yang diperibadikan.\n\n"
        "Maklumat anda disimpan dengan selamat mengikut PDPA dan hanya digunakan untuk "
        "menyokong penjagaan ubat anda.\n\n"
        "Adakah anda bersetuju untuk menyertai? Balas *YA* untuk teruskan atau *TIDAK* untuk batal."
    ),
    "ta": (
        "Medi-Nudge தனிப்பயன் நினைவூட்டல்கள் மூலம் உங்கள் மருந்துகளை நிர்வகிக்க உதவுகிறது.\n\n"
        "உங்கள் தகவல் PDPA படி பாதுகாப்பாக சேமிக்கப்படும்.\n\n"
        "சேர ஒப்புக்கொள்கிறீர்களா? தொடர *YES* என்று பதிலளிக்கவும் அல்லது ரத்து செய்ய *NO* என்று பதிலளிக்கவும்."
    ),
}

SELF_NAME_PROMPTS = {
    "en": "What is your full name? (as shown on your ID)",
    "zh": "请输入您的全名（与身份证相同）：",
    "ms": "Apakah nama penuh anda? (seperti pada IC anda)",
    "ta": "உங்கள் முழு பெயர் என்ன? (அடையாள அட்டையில் உள்ளபடி)",
}

SELF_NRIC_PROMPTS = {
    "en": (
        "Please enter your NRIC or FIN number (e.g. S1234567A).\n\n"
        "This is stored securely as a hash — we never see or store the actual number."
    ),
    "zh": "请输入您的身份证或FIN号码（例如 S1234567A）。\n\n此信息将以加密形式存储，保障您的隐私。",
    "ms": "Sila masukkan nombor NRIC atau FIN anda (cth. S1234567A).\n\nMaklumat ini disimpan secara selamat.",
    "ta": "உங்கள் NRIC அல்லது FIN எண்ணை உள்ளிடவும் (எ.கா. S1234567A).\n\nதகவல் பாதுகாப்பாக சேமிக்கப்படும்.",
}

SELF_CONDITION_PROMPTS = {
    "en": "Which health condition(s) are you managing? Tap all that apply, then tap Done.",
    "zh": "您正在管理哪些健康状况？请选择所有适用项，然后点击「完成」。",
    "ms": "Apakah keadaan kesihatan yang anda urus? Pilih semua yang berkenaan, kemudian ketik Selesai.",
    "ta": "நீங்கள் எந்த உடல்நல நிலைமைகளை நிர்வகிக்கிறீர்கள்? பொருந்தும் அனைத்தையும் தேர்ந்தெடுக்கவும்.",
}

SELF_CONDITION_BUTTONS = {
    "en": [
        [{"text": "🩸 Diabetes", "callback_data": "sc:Diabetes"},
         {"text": "💉 Hypertension", "callback_data": "sc:Hypertension"}],
        [{"text": "🫀 High Cholesterol", "callback_data": "sc:Hyperlipidemia"},
         {"text": "✏️ Other (type below)", "callback_data": "sc:other"}],
        [{"text": "➡️ None / Skip", "callback_data": "sc:none"}],
    ],
    "zh": [
        [{"text": "🩸 糖尿病", "callback_data": "sc:Diabetes"},
         {"text": "💉 高血压", "callback_data": "sc:Hypertension"}],
        [{"text": "🫀 高胆固醇", "callback_data": "sc:Hyperlipidemia"},
         {"text": "✏️ 其他（请输入）", "callback_data": "sc:other"}],
        [{"text": "➡️ 无 / 跳过", "callback_data": "sc:none"}],
    ],
    "ms": [
        [{"text": "🩸 Diabetes", "callback_data": "sc:Diabetes"},
         {"text": "💉 Hipertensi", "callback_data": "sc:Hypertension"}],
        [{"text": "🫀 Kolesterol Tinggi", "callback_data": "sc:Hyperlipidemia"},
         {"text": "✏️ Lain-lain (taip di bawah)", "callback_data": "sc:other"}],
        [{"text": "➡️ Tiada / Langkau", "callback_data": "sc:none"}],
    ],
    "ta": [
        [{"text": "🩸 நீரிழிவு", "callback_data": "sc:Diabetes"},
         {"text": "💉 உயர் இரத்த அழுத்தம்", "callback_data": "sc:Hypertension"}],
        [{"text": "🫀 அதிக கொலஸ்ட்ரால்", "callback_data": "sc:Hyperlipidemia"},
         {"text": "✏️ மற்றவை (கீழே தட்டச்சு)", "callback_data": "sc:other"}],
        [{"text": "➡️ எதுவுமில்லை / தவிர்", "callback_data": "sc:none"}],
    ],
}

LANG_MAP = {
    "1": "en", "english": "en",
    "2": "zh", "中文": "zh", "chinese": "zh",
    "3": "ms", "melayu": "ms", "malay": "ms",
    "4": "ta", "தமிழ்": "ta", "tamil": "ta",
}

CONSENT_MESSAGES = {
    "en": (
        "Hi {name} 👋 This is Medi-Nudge from your clinic.\n\n"
        "We'd like to support you in managing your medications. "
        "Reply *YES* to join or *NO* to decline.\n\n"
        "Your information will be handled in accordance with PDPA."
    ),
    "zh": (
        "您好 {name} 👋 这是来自您诊所的 Medi-Nudge 服务。\n\n"
        "我们希望帮助您管理用药。回复 *同意* 加入，或 *拒绝* 取消。"
    ),
    "ms": (
        "Hai {name} 👋 Ini adalah Medi-Nudge dari klinik anda.\n\n"
        "Kami ingin membantu anda menguruskan ubat anda. Balas *YA* untuk menyertai atau *TIDAK* untuk menolak."
    ),
    "ta": (
        "வணக்கம் {name} 👋 இது உங்கள் கிளினிக்கிலிருந்து Medi-Nudge.\n\n"
        "உங்கள் மருந்துகளை நிர்வகிக்க உதவ விரும்புகிறோம். சேர *YES* என பதிலளிக்கவும் அல்லது *NO* என மறுக்கவும்."
    ),
}

MEDICATION_PROMPT = {
    "en": "Let's set up your medications. How would you like to add them?",
    "zh": "让我们设置您的药物，请选择方式：",
    "ms": "Mari kita sediakan ubat anda. Pilih cara:",
    "ta": "மருந்துகளை அமைப்போம். எவ்வாறு சேர்க்க விரும்புகிறீர்கள்?",
}
MEDICATION_BUTTONS = {
    "en": [
        [{"text": "✅ Confirm on file", "callback_data": "1"}],
        [{"text": "📷 Send a photo", "callback_data": "2"}],
        [{"text": "✏️ Enter manually", "callback_data": "3"}],
    ],
    "zh": [
        [{"text": "✅ 确认已有记录", "callback_data": "1"}],
        [{"text": "📷 发送处方照片", "callback_data": "2"}],
        [{"text": "✏️ 手动输入", "callback_data": "3"}],
    ],
    "ms": [
        [{"text": "✅ Sahkan rekod sedia ada", "callback_data": "1"}],
        [{"text": "📷 Hantar foto preskripsi", "callback_data": "2"}],
        [{"text": "✏️ Masukkan manual", "callback_data": "3"}],
    ],
    "ta": [
        [{"text": "✅ ரெக்கார்டை உறுதிப்படுத்தவும்", "callback_data": "1"}],
        [{"text": "📷 புகைப்படம் அனுப்பவும்", "callback_data": "2"}],
        [{"text": "✏️ கைமுறையாக உள்ளிடவும்", "callback_data": "3"}],
    ],
}

PREFERENCES_PROMPT = {
    "en": "Almost done! When would you like to receive reminders?",
    "zh": "快完成了！您希望何时收到提醒？",
    "ms": "Hampir selesai! Bilakah anda ingin menerima peringatan?",
    "ta": "கிட்டத்தட்ட முடிந்தது! எப்போது நினைவூட்டல்கள் வேண்டும்?",
}
PREFERENCES_BUTTONS = {
    "en": [
        [{"text": "☀️ Morning", "callback_data": "1"}, {"text": "🌤 Afternoon", "callback_data": "2"}],
        [{"text": "🌆 Evening", "callback_data": "3"}, {"text": "🔕 No preference", "callback_data": "4"}],
    ],
    "zh": [
        [{"text": "☀️ 早上", "callback_data": "1"}, {"text": "🌤 下午", "callback_data": "2"}],
        [{"text": "🌆 傍晚", "callback_data": "3"}, {"text": "🔕 无偏好", "callback_data": "4"}],
    ],
    "ms": [
        [{"text": "☀️ Pagi", "callback_data": "1"}, {"text": "🌤 Tengahari", "callback_data": "2"}],
        [{"text": "🌆 Petang", "callback_data": "3"}, {"text": "🔕 Tiada pilihan", "callback_data": "4"}],
    ],
    "ta": [
        [{"text": "☀️ காலை", "callback_data": "1"}, {"text": "🌤 மதியம்", "callback_data": "2"}],
        [{"text": "🌆 மாலை", "callback_data": "3"}, {"text": "🔕 விருப்பமில்லை", "callback_data": "4"}],
    ],
}

VOICE_PREFERENCE_PROMPT = {
    "en": "How would you like to receive medication reminders?",
    "zh": "您希望如何收到用药提醒？",
    "ms": "Bagaimana anda mahu menerima peringatan ubat?",
    "ta": "மருந்து நினைவூட்டல்களை எவ்வாறு பெற விரும்புகிறீர்கள்?",
}
VOICE_PREFERENCE_BUTTONS = {
    "en": [[
        {"text": "💬 Text only", "callback_data": "1"},
        {"text": "🔊 Voice only", "callback_data": "2"},
        {"text": "💬🔊 Both", "callback_data": "3"},
    ]],
    "zh": [[
        {"text": "💬 仅文字", "callback_data": "1"},
        {"text": "🔊 仅语音", "callback_data": "2"},
        {"text": "💬🔊 两者", "callback_data": "3"},
    ]],
    "ms": [[
        {"text": "💬 Teks sahaja", "callback_data": "1"},
        {"text": "🔊 Suara sahaja", "callback_data": "2"},
        {"text": "💬🔊 Kedua-dua", "callback_data": "3"},
    ]],
    "ta": [[
        {"text": "💬 உரை மட்டும்", "callback_data": "1"},
        {"text": "🔊 குரல் மட்டும்", "callback_data": "2"},
        {"text": "💬🔊 இரண்டும்", "callback_data": "3"},
    ]],
}

VOICE_SELECTION_PROMPT = {
    "en": "Choose a voice for your reminders:",
    "zh": "选择提醒语音：",
    "ms": "Pilih suara untuk peringatan anda:",
    "ta": "நினைவூட்டலுக்கு குரல் தேர்ந்தெடுக்கவும்:",
}
VOICE_SELECTION_BUTTONS = {
    "en": [[
        {"text": "👩 Female", "callback_data": "1"},
        {"text": "👨 Male", "callback_data": "2"},
        {"text": "🎙 Record my own", "callback_data": "3"},
    ]],
    "zh": [[
        {"text": "👩 女声", "callback_data": "1"},
        {"text": "👨 男声", "callback_data": "2"},
        {"text": "🎙 录制自己的声音", "callback_data": "3"},
    ]],
    "ms": [[
        {"text": "👩 Wanita", "callback_data": "1"},
        {"text": "👨 Lelaki", "callback_data": "2"},
        {"text": "🎙 Rakam sendiri", "callback_data": "3"},
    ]],
    "ta": [[
        {"text": "👩 பெண் குரல்", "callback_data": "1"},
        {"text": "👨 ஆண் குரல்", "callback_data": "2"},
        {"text": "🎙 சொந்த குரல்", "callback_data": "3"},
    ]],
}

VOICE_RECORD_PROMPT = {
    "en": "Please send a voice message (60-90 seconds) reading the following:\n\n\"Hello, this is a reminder to take your medication. Staying consistent helps manage your health. Remember to collect your refill when it's due.\"\n\nYour voice will be used for your reminders. You can also do this later — a default voice will be used in the meantime.",
    "zh": "请发送一条语音消息（60-90秒），朗读以下内容：\n\n「您好，提醒您按时服药。坚持服药有助于控制您的健康。」\n\n您也可以稍后再录制——目前将使用默认语音。",
    "ms": "Sila hantar mesej suara (60-90 saat) membaca teks berikut:\n\n\"Hai, ini peringatan untuk mengambil ubat anda. Konsisten membantu menjaga kesihatan.\"\n\nAnda juga boleh lakukan nanti — suara lalai akan digunakan buat sementara.",
    "ta": "தயவுசெய்து குரல் செய்தி அனுப்பவும் (60-90 விநாடிகள்):\n\n\"வணக்கம், மருந்து எடுக்க நினைவூட்டல். தொடர்ச்சியாக எடுப்பது ஆரோக்கியத்திற்கு உதவும்.\"\n\nநீங்கள் பின்னர் செய்யலாம் — இப்போதைக்கு இயல்பு குரல் பயன்படுத்தப்படும்.",
}

DELIVERY_MODE_MAP = {"1": "text", "2": "voice", "3": "both"}

CONTACT_WINDOWS = {
    "1": ("08:00", "12:00"),
    "2": ("12:00", "17:00"),
    "3": ("17:00", "21:00"),
    "4": (None, None),
}

WELCOME_MESSAGES = {
    "en": (
        "Welcome to Medi-Nudge! 🎉\n\n"
        "We'll remind you when your medications are due for refill.\n\n"
        "How to use this service:\n"
        "• Reply *YES* when you've collected your medication\n"
        "• Reply *HELP* if you have questions\n"
        "• Reply *SIDE EFFECT* if you feel unwell after taking your medicine\n"
        "• Reply *STOP* at any time to opt out"
    ),
    "zh": (
        "欢迎使用 Medi-Nudge！🎉\n\n"
        "• 领取药品后请回复 *已领*\n• 有问题请回复 *帮助*\n• 服药后不舒服请回复 *副作用*\n• 随时回复 *停止* 取消服务"
    ),
    "ms": (
        "Selamat datang ke Medi-Nudge! 🎉\n\n"
        "• Balas *YA* apabila anda mengambil ubat\n• Balas *BANTUAN* untuk soalan\n"
        "• Balas *KESAN SAMPINGAN* jika tidak sihat\n• Balas *BERHENTI* untuk berhenti"
    ),
    "ta": (
        "Medi-Nudge-க்கு வரவேற்கிறோம்! 🎉\n\n"
        "• *YES* — மருந்து எடுத்தவுடன்\n• *HELP* — கேள்விகள்\n"
        "• *SIDE EFFECT* — உடல்நலக்குறைவு\n• *STOP* — விலக"
    ),
}


# ---------------------------------------------------------------------------
# Token + QR generation
# ---------------------------------------------------------------------------

def generate_invite_token(db: Session, patient: Patient) -> dict:
    """
    Create a one-time OnboardingToken and return the deep-link + base64 QR PNG.
    Existing unused tokens for this patient are invalidated first.
    """
    now = now_sgt()
    existing = (
        db.query(OnboardingToken)
        .filter(OnboardingToken.patient_id == patient.id, OnboardingToken.used_at.is_(None))
        .all()
    )
    for t in existing:
        t.used_at = now

    raw = secrets.token_hex(32)
    token_row = OnboardingToken(
        patient_id=patient.id,
        token=raw,
        expires_at=now + timedelta(hours=TOKEN_TTL_HOURS),
    )
    db.add(token_row)
    db.commit()

    bot_username = settings.TELEGRAM_BOT_USERNAME or "MediNudgeBot"
    invite_link = f"https://t.me/{bot_username}?start={raw}"
    qr_b64 = _generate_qr_b64(invite_link)
    return {"invite_link": invite_link, "qr_code_png_b64": qr_b64}


def _generate_qr_b64(data: str) -> str:
    try:
        import qrcode  # type: ignore
        img = qrcode.make(data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except ImportError:
        logger.warning("qrcode library not installed — QR generation skipped")
        return ""


def generate_caregiver_invite_token(db: Session, patient: Patient) -> str:
    """
    Create a one-time caregiver OnboardingToken and return the deep-link URL.
    Old unused caregiver tokens for this patient are invalidated first.
    """
    now = now_sgt()
    existing = (
        db.query(OnboardingToken)
        .filter(
            OnboardingToken.patient_id == patient.id,
            OnboardingToken.is_caregiver == True,  # noqa: E712
            OnboardingToken.used_at.is_(None),
        )
        .all()
    )
    for t in existing:
        t.used_at = now

    raw = secrets.token_hex(32)
    token_row = OnboardingToken(
        patient_id=patient.id,
        token=raw,
        expires_at=now + timedelta(hours=TOKEN_TTL_HOURS),
        is_caregiver=True,
    )
    db.add(token_row)
    db.commit()

    bot_username = settings.TELEGRAM_BOT_USERNAME or "MediNudgeBot"
    return f"https://t.me/{bot_username}?start={raw}"


def send_caregiver_invite(db: Session, patient: Patient) -> bool:
    """
    Send the caregiver a WhatsApp/SMS invite link if they haven't linked yet.
    Returns True if sent (or stubbed in dev), False if no phone or already linked.
    """
    if not patient.caregiver_phone_number:
        return False
    if patient.caregiver_telegram_id:
        return False  # already linked

    invite_link = generate_caregiver_invite_token(db, patient)
    caregiver_name = patient.caregiver_name or "Caregiver"
    patient_first = patient.full_name.split()[0]

    body = (
        f"Hi {caregiver_name}, you have been listed as a caregiver for {patient_first} "
        f"on Medi-Nudge.\n\n"
        f"Please tap the link below to connect your Telegram account so we can reach you "
        f"if {patient_first} needs help:\n\n"
        f"{invite_link}\n\n"
        f"The link expires in {TOKEN_TTL_HOURS} hours."
    )
    return sms_service.send_whatsapp(patient.caregiver_phone_number, body)


def validate_and_consume_token(db: Session, raw_token: str) -> "Patient | None":
    row = db.query(OnboardingToken).filter(OnboardingToken.token == raw_token).first()
    if not row or row.used_at is not None or now_sgt() > row.expires_at:
        return None
    row.used_at = now_sgt()
    db.commit()
    db.refresh(row)
    return row.patient


# ---------------------------------------------------------------------------
# /start entry point (called from webhook)
# ---------------------------------------------------------------------------

def handle_start_command(db: Session, chat_id: str, token_arg: "str | None") -> None:
    if token_arg:
        patient = validate_and_consume_token(db, token_arg)
        if patient is None:
            row = db.query(OnboardingToken).filter(OnboardingToken.token == token_arg).first()
            if row and row.used_at:
                _send_raw(chat_id, "This invite link has already been used. If you need help, reply HELP.")
            else:
                _send_raw(chat_id, "This invite link has expired. Please ask your clinic for a new one.")
            return

        # ── Caregiver invite token ──────────────────────────────────────────
        row = db.query(OnboardingToken).filter(OnboardingToken.token == token_arg).first()
        if row and row.is_caregiver:
            if patient.caregiver_telegram_id and patient.caregiver_telegram_id != chat_id:
                _send_raw(chat_id, "This caregiver slot is already linked. Please contact the clinic if this is an error.")
                return
            patient.caregiver_telegram_id = chat_id
            db.commit()
            _send_raw(
                chat_id,
                f"✅ You are now registered as a caregiver for {patient.full_name.split()[0]} on Medi-Nudge.\n\n"
                "You will be notified here if they miss medications or need assistance.",
            )
            return

        # ── Patient invite token ────────────────────────────────────────────
        if patient.telegram_chat_id and patient.telegram_chat_id != chat_id:
            _send_raw(chat_id, "This patient record is already linked to another account. Please contact your clinic.")
            return
        patient.telegram_chat_id = chat_id
        patient.onboarding_state = "invited"
        db.commit()
        db.refresh(patient)
        _send_consent(db, patient)
        return

    existing = db.query(Patient).filter(Patient.telegram_chat_id == chat_id).first()
    if existing:
        if existing.onboarding_state == "complete":
            _send_raw(chat_id, "You're already enrolled in Medi-Nudge. Reply HELP for assistance.")
        else:
            handle_onboarding_reply(db, existing, "")
        return

    # Self-registration: create stub and start with language selection
    stub = Patient(
        full_name="",
        phone_number=f"tg_{chat_id}",
        telegram_chat_id=chat_id,
        onboarding_state="self_lang",
        is_active=False,
    )
    db.add(stub)
    db.commit()
    _send_raw_keyboard(chat_id, SELF_WELCOME, LANG_BUTTONS)


# ---------------------------------------------------------------------------
# State machine dispatcher
# ---------------------------------------------------------------------------

def handle_onboarding_reply(db: Session, patient: Patient, text: str) -> None:
    state = patient.onboarding_state
    text_lower = text.strip().lower()

    dispatch = {
        # Self-registration path
        "self_lang":      _handle_self_lang,
        "self_consent":   _handle_self_consent,
        "self_name":      _handle_self_name,
        "self_nric":      _handle_self_nric,
        "self_condition": _handle_self_condition,
        # Coordinator-initiated path
        "identity_verification": handle_identity_verification,
        "invited": _handle_invite_reply,
        "consent_pending": _handle_consent_reply,
        "language_confirmed": _handle_language_reply,
        # Shared states
        "medication_capture": _handle_medication_capture,
        "confirm": _handle_confirm_reply,
        "preferences": _handle_preferences_reply,
        "voice_preference": _handle_voice_preference_reply,
        "voice_selection": _handle_voice_selection_reply,
    }

    handler = dispatch.get(state)
    if handler:
        handler(db, patient, text_lower)
    else:
        _send_patient(db, patient, "Reply HELP for assistance or wait for your care team to contact you.")
        escalation_service.create_escalation(db=db, patient_id=patient.id, reason="patient_question")


# ---------------------------------------------------------------------------
# Self-registration handlers
# ---------------------------------------------------------------------------

def _handle_self_lang(db: Session, patient: Patient, text: str) -> None:
    """Language selection — first step of self-registration."""
    # text arrives lowercased; callback_data is "1"/"2"/"3"/"4"
    lang = LANG_MAP.get(text.strip())
    if not lang:
        _send_raw_keyboard(patient.telegram_chat_id, SELF_WELCOME, LANG_BUTTONS)
        return
    patient.language_preference = lang
    patient.onboarding_state = "self_consent"
    db.commit()
    _send_patient(db, patient, SELF_CONSENT_MESSAGES.get(lang, SELF_CONSENT_MESSAGES["en"]))


def _handle_self_consent(db: Session, patient: Patient, text: str) -> None:
    lang = patient.language_preference or "en"
    if any(k in text for k in ("yes", "ya", "好", "同意", "setuju")):
        patient.consent_obtained_at = now_sgt()
        patient.consent_channel = "telegram"
        patient.onboarding_state = "self_name"
        db.commit()
        _send_patient(db, patient, SELF_NAME_PROMPTS.get(lang, SELF_NAME_PROMPTS["en"]))
    elif any(k in text for k in ("no", "tidak", "不要", "拒绝", "nope")):
        patient.is_active = False
        db.commit()
    else:
        _send_patient(db, patient, SELF_CONSENT_MESSAGES.get(lang, SELF_CONSENT_MESSAGES["en"]))


def _handle_self_name(db: Session, patient: Patient, text: str) -> None:
    lang = patient.language_preference or "en"
    name = text.strip().title()
    if len(name) < 2:
        _send_patient(db, patient, SELF_NAME_PROMPTS.get(lang, SELF_NAME_PROMPTS["en"]))
        return
    patient.full_name = name
    patient.onboarding_state = "self_nric"
    db.commit()
    _send_patient(db, patient, SELF_NRIC_PROMPTS.get(lang, SELF_NRIC_PROMPTS["en"]))


def _handle_self_nric(db: Session, patient: Patient, text: str) -> None:
    lang = patient.language_preference or "en"
    nric_input = text.strip().upper()
    if len(nric_input) < 7:
        _send_patient(db, patient, SELF_NRIC_PROMPTS.get(lang, SELF_NRIC_PROMPTS["en"]))
        return
    patient.nric_hash = hash_sha256(nric_input)
    patient.onboarding_state = "self_condition"
    db.commit()
    _send_patient_keyboard(
        db, patient,
        SELF_CONDITION_PROMPTS.get(lang, SELF_CONDITION_PROMPTS["en"]),
        SELF_CONDITION_BUTTONS.get(lang, SELF_CONDITION_BUTTONS["en"]),
    )


def _handle_self_condition(db: Session, patient: Patient, text: str) -> None:
    """Handle condition selection — supports callback (sc:X) and free-text input."""
    lang = patient.language_preference or "en"

    # Parse callback_data (e.g. "sc:diabetes", "sc:none", "sc:other")
    raw = text.strip()
    if raw.startswith("sc:"):
        key = raw[3:]
        if key == "none":
            patient.conditions = []
        elif key == "other":
            # Ask patient to type their condition
            OTHER_PROMPT = {
                "en": "Please type your health condition(s) (e.g. Asthma, Kidney disease):",
                "zh": "请输入您的健康状况（例如：哮喘、肾脏疾病）：",
                "ms": "Sila taip keadaan kesihatan anda (cth. Asma, Penyakit buah pinggang):",
                "ta": "உங்கள் உடல்நல நிலைமைகளை தட்டச்சு செய்யவும்:",
            }
            _send_patient(db, patient, OTHER_PROMPT.get(lang, OTHER_PROMPT["en"]))
            # Stay in self_condition to receive the typed text
            return
        else:
            # Map normalised key back to proper name
            _COND_MAP = {
                "diabetes": "Diabetes",
                "hypertension": "Hypertension",
                "hyperlipidemia": "Hyperlipidemia",
            }
            condition = _COND_MAP.get(key.lower(), key.title())
            existing = list(patient.conditions or [])
            if condition not in existing:
                existing.append(condition)
            patient.conditions = existing
    else:
        # Free-text entry (e.g. from "Other" prompt or direct typing)
        typed = raw.strip().title()
        if typed:
            existing = list(patient.conditions or [])
            for part in [c.strip() for c in typed.split(",") if c.strip()]:
                if part and part not in existing:
                    existing.append(part)
            patient.conditions = existing

    # Activate patient and advance to medication capture
    patient.is_active = True
    patient.onboarding_state = "medication_capture"
    db.commit()
    _send_patient_keyboard(
        db, patient,
        MEDICATION_PROMPT.get(lang, MEDICATION_PROMPT["en"]),
        MEDICATION_BUTTONS.get(lang, MEDICATION_BUTTONS["en"]),
    )


def handle_identity_verification(db: Session, patient: Patient, text: str) -> None:
    chat_id = patient.telegram_chat_id
    nric_input = text.strip().upper()
    if not nric_input:
        _send_raw(chat_id, "Please enter your NRIC/FIN number to continue.")
        return

    nric_hash = hash_sha256(nric_input)
    matched = (
        db.query(Patient)
        .filter(Patient.nric_hash == nric_hash, Patient.telegram_chat_id.is_(None))
        .first()
    )

    if matched:
        patient.telegram_chat_id = None  # release unique value before assigning to matched
        db.flush()
        matched.telegram_chat_id = chat_id
        matched.onboarding_state = "invited"
        db.delete(patient)
        db.commit()
        _send_consent(db, matched)
        return

    patient.onboarding_state = "self_registering"
    db.commit()
    escalation_service.create_escalation(db=db, patient_id=patient.id, reason="self_registration_review")
    _send_raw(
        chat_id,
        "Your registration is under review. "
        "A care coordinator will be in touch within 1 business day.",
    )


def _send_consent(db: Session, patient: Patient) -> None:
    lang = patient.language_preference or "en"
    msg = CONSENT_MESSAGES.get(lang, CONSENT_MESSAGES["en"]).format(name=patient.full_name)
    _send_patient(db, patient, msg)


def _handle_invite_reply(db: Session, patient: Patient, text: str) -> None:
    if any(k in text for k in ("yes", "ya", "好", "同意", "setuju")):
        patient.onboarding_state = "consent_pending"
        patient.consent_obtained_at = now_sgt()
        patient.consent_channel = "telegram"
        db.commit()
        _send_patient_keyboard(db, patient, LANG_PROMPT_TEXT, LANG_BUTTONS)
    elif any(k in text for k in ("no", "tidak", "不要", "nope", "拒绝")):
        patient.is_active = False
        db.commit()


def _handle_consent_reply(db: Session, patient: Patient, text: str) -> None:
    lang = LANG_MAP.get(text.strip())
    if lang:
        patient.language_preference = lang
        patient.onboarding_state = "language_confirmed"
        db.commit()
        _send_patient_keyboard(
            db, patient,
            MEDICATION_PROMPT.get(lang, MEDICATION_PROMPT["en"]),
            MEDICATION_BUTTONS.get(lang, MEDICATION_BUTTONS["en"]),
        )
    else:
        _send_patient_keyboard(db, patient, LANG_PROMPT_TEXT, LANG_BUTTONS)


def _handle_language_reply(db: Session, patient: Patient, text: str) -> None:
    _handle_medication_capture(db, patient, text)


def _handle_medication_capture(db: Session, patient: Patient, text: str) -> None:
    lang = patient.language_preference or "en"
    choice = text.strip()

    if choice == "1":
        meds = db.query(PatientMedication).filter(PatientMedication.patient_id == patient.id).all()
        if meds:
            from app.models.models import Medication as MedModel
            lines = []
            for pm in meds:
                med = db.query(MedModel).filter(MedModel.id == pm.medication_id).first()
                if med:
                    lines.append(f"• {med.name}" + (f" ({pm.dosage})" if pm.dosage else ""))
            _send_patient(
                db, patient,
                "Your medications on file:\n\n" + "\n".join(lines) + "\n\nIs this correct? Reply *YES* to confirm or *NO* to re-enter."
            )
            patient.onboarding_state = "confirm"
            db.commit()
        else:
            _send_patient_keyboard(
                db, patient,
                "No medications on file yet. How would you like to add them?",
                MEDICATION_BUTTONS.get(lang, MEDICATION_BUTTONS["en"])[1:],  # only photo + manual options
            )
    elif choice == "2":
        _send_patient(db, patient, "Please send a photo of your prescription or medicine label.")
    elif choice == "3":
        patient.onboarding_state = "confirm"
        db.commit()
        _send_patient(db, patient, "Please type the name of your first medication (e.g. Metformin 500mg). Reply *DONE* when finished.")
    else:
        _send_patient_keyboard(
            db, patient,
            MEDICATION_PROMPT.get(lang, MEDICATION_PROMPT["en"]),
            MEDICATION_BUTTONS.get(lang, MEDICATION_BUTTONS["en"]),
        )


def _handle_confirm_reply(db: Session, patient: Patient, text: str) -> None:
    if any(k in text for k in ("yes", "ya", "confirm", "好", "是", "setuju")):
        db.query(PatientMedication).filter(
            PatientMedication.patient_id == patient.id,
            PatientMedication.is_active == False,  # noqa: E712
        ).update({"is_active": True})
        db.commit()
        # Send info card for newly activated medications (manual-entry path)
        _send_info_cards_for_new_medications(db, patient)
        patient.onboarding_state = "preferences"
        db.commit()
        lang = patient.language_preference or "en"
        _send_patient_keyboard(
            db, patient,
            PREFERENCES_PROMPT.get(lang, PREFERENCES_PROMPT["en"]),
            PREFERENCES_BUTTONS.get(lang, PREFERENCES_BUTTONS["en"]),
        )
    elif text.strip() == "done":
        _send_patient(db, patient, "Thank you! Reply *YES* to confirm your medication list.")
    elif any(k in text for k in ("no", "tidak", "不", "nope")):
        lang = patient.language_preference or "en"
        patient.onboarding_state = "medication_capture"
        db.commit()
        _send_patient_keyboard(
            db, patient,
            MEDICATION_PROMPT.get(lang, MEDICATION_PROMPT["en"]),
            MEDICATION_BUTTONS.get(lang, MEDICATION_BUTTONS["en"]),
        )
    else:
        # Treat as manual entry — run through medicine verification gate
        from app.services import agent_service
        agent_service.verify_and_confirm_medication(patient, text, db)


def _handle_preferences_reply(db: Session, patient: Patient, text: str) -> None:
    choice = text.strip()
    window = CONTACT_WINDOWS.get(choice)
    if window is None:
        lang = patient.language_preference or "en"
        _send_patient_keyboard(
            db, patient,
            PREFERENCES_PROMPT.get(lang, PREFERENCES_PROMPT["en"]),
            PREFERENCES_BUTTONS.get(lang, PREFERENCES_BUTTONS["en"]),
        )
        return
    start, end = window
    patient.contact_window_start = start
    patient.contact_window_end = end
    patient.onboarding_state = "voice_preference"
    db.commit()
    lang = patient.language_preference or "en"
    _send_patient_keyboard(
        db, patient,
        VOICE_PREFERENCE_PROMPT.get(lang, VOICE_PREFERENCE_PROMPT["en"]),
        VOICE_PREFERENCE_BUTTONS.get(lang, VOICE_PREFERENCE_BUTTONS["en"]),
    )


def _handle_voice_preference_reply(db: Session, patient: Patient, text: str) -> None:
    choice = text.strip()
    mode = DELIVERY_MODE_MAP.get(choice)
    if mode is None:
        lang = patient.language_preference or "en"
        _send_patient_keyboard(
            db, patient,
            VOICE_PREFERENCE_PROMPT.get(lang, VOICE_PREFERENCE_PROMPT["en"]),
            VOICE_PREFERENCE_BUTTONS.get(lang, VOICE_PREFERENCE_BUTTONS["en"]),
        )
        return

    patient.nudge_delivery_mode = mode
    if mode == "text":
        _complete_onboarding(db, patient)
    else:
        patient.onboarding_state = "voice_selection"
        db.commit()
        lang = patient.language_preference or "en"
        _send_patient_keyboard(
            db, patient,
            VOICE_SELECTION_PROMPT.get(lang, VOICE_SELECTION_PROMPT["en"]),
            VOICE_SELECTION_BUTTONS.get(lang, VOICE_SELECTION_BUTTONS["en"]),
        )


def _handle_voice_selection_reply(db: Session, patient: Patient, text: str) -> None:
    choice = text.strip()
    if choice == "1":
        patient.selected_voice_id = settings.ELEVENLABS_DEFAULT_VOICE_FEMALE or None
    elif choice == "2":
        patient.selected_voice_id = settings.ELEVENLABS_DEFAULT_VOICE_MALE or None
    elif choice == "3":
        # Use default voice now; patient will send a voice message post-onboarding to clone
        patient.selected_voice_id = settings.ELEVENLABS_DEFAULT_VOICE_FEMALE or None
        db.commit()
        lang = patient.language_preference or "en"
        _send_patient(db, patient, VOICE_RECORD_PROMPT.get(lang, VOICE_RECORD_PROMPT["en"]))
        _complete_onboarding(db, patient)
        return
    else:
        lang = patient.language_preference or "en"
        _send_patient_keyboard(
            db, patient,
            VOICE_SELECTION_PROMPT.get(lang, VOICE_SELECTION_PROMPT["en"]),
            VOICE_SELECTION_BUTTONS.get(lang, VOICE_SELECTION_BUTTONS["en"]),
        )
        return
    _complete_onboarding(db, patient)


def _complete_onboarding(db: Session, patient: Patient) -> None:
    """Finalize onboarding: set state to complete, send welcome, invite caregiver."""
    patient.onboarding_state = "complete"
    patient.is_active = True
    db.commit()
    lang = patient.language_preference or "en"
    _send_patient(db, patient, WELCOME_MESSAGES.get(lang, WELCOME_MESSAGES["en"]))

    # Send caregiver invite via WhatsApp/SMS if phone is set and not yet linked
    try:
        send_caregiver_invite(db, patient)
    except Exception as exc:
        logger.warning("Caregiver invite send failed for patient %s: %s", patient.id, exc)

    # Immediately create side-effect check-in campaigns for all active medications
    try:
        from app.services.side_effect_checkin_service import run_side_effect_checkin_check
        run_side_effect_checkin_check(db, patient_id=patient.id)
    except Exception as exc:
        logger.warning("Side-effect check-in trigger failed for patient %s: %s", patient.id, exc)


# ---------------------------------------------------------------------------
# Drop-off recovery
# ---------------------------------------------------------------------------

def handle_drop_off(db: Session, patient: Patient, retry_count: int) -> None:
    # Voice preference or voice selection timeout — default to text, complete onboarding
    if patient.onboarding_state in ("voice_preference", "voice_selection") and retry_count >= 1:
        patient.nudge_delivery_mode = "text"
        _complete_onboarding(db, patient)
        return

    if patient.onboarding_state == "preferences" and retry_count >= 1:
        patient.contact_window_start = None
        patient.contact_window_end = None
        _complete_onboarding(db, patient)
        return

    if retry_count < 2:
        if patient.telegram_chat_id:
            _send_consent(db, patient)
    else:
        escalation_service.create_escalation(db=db, patient_id=patient.id, reason="onboarding_drop_off")
        patient.onboarding_state = "drop_off_recovery"
        db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OCR_CONFIRM_PROMPTS = {
    "en": (
        "I've read your prescription label. Does this look correct?\n\n"
        "{details}\n\n"
        "Reply CONFIRM to accept or EDIT to send to your care team for review."
    ),
    "zh": (
        "我已读取您的处方标签。这些信息是否正确？\n\n"
        "{details}\n\n"
        "回复「确认」接受，或「修改」交由护理团队审核。"
    ),
    "ms": (
        "Saya telah membaca label preskripsi anda. Adakah ini betul?\n\n"
        "{details}\n\n"
        "Balas CONFIRM untuk terima atau EDIT untuk hantar kepada pasukan penjagaan anda."
    ),
    "ta": (
        "உங்கள் மருந்து லேபிளை படித்தேன். இது சரியா?\n\n"
        "{details}\n\n"
        "CONFIRM என ஒப்புக்கொள்ள பதிலளிக்கவும் அல்லது EDIT என்று பராமரிப்பு குழுவிற்கு அனுப்பவும்."
    ),
}

_OCR_CONFIRM_BUTTONS = [
    [{"text": "✅ CONFIRM", "callback_data": "CONFIRM"}],
    [{"text": "✏️ EDIT", "callback_data": "EDIT"}],
]


def send_ocr_confirmation_prompt(db: "Session", patient: "Patient", scan: object) -> None:
    """Send the patient a summary of OCR-extracted fields for self-confirmation."""
    lang = patient.language_preference or "en"
    field_map = {f.field_name: f.extracted_value for f in scan.fields if f.extracted_value}

    lines = []
    if field_map.get("medication_name"):
        lines.append(f"💊 Medication: {field_map['medication_name']}")
    if field_map.get("dosage"):
        lines.append(f"📋 Dosage: {field_map['dosage']}")
    if field_map.get("frequency"):
        lines.append(f"🔁 Frequency: {field_map['frequency']}")
    if field_map.get("dispense_date"):
        lines.append(f"📅 Dispense date: {field_map['dispense_date']}")
    details = "\n".join(lines) if lines else "(no fields extracted)"

    tmpl = _OCR_CONFIRM_PROMPTS.get(lang, _OCR_CONFIRM_PROMPTS["en"])
    body = tmpl.format(details=details)
    _send_patient_keyboard(db, patient, body, _OCR_CONFIRM_BUTTONS)


def _send_patient(db: Session, patient: Patient, body: str) -> None:
    if not patient.telegram_chat_id:
        logger.warning("Cannot send to patient %s — telegram_chat_id not set", patient.id)
        return
    telegram_service.send_text(db=db, patient_id=patient.id, to_phone=patient.telegram_chat_id, body=body)


def _send_info_cards_for_new_medications(db: Session, patient: Patient) -> None:
    """Send medication info cards for any newly activated medications that haven't received one yet."""
    from datetime import datetime as _dt
    from app.services.medication_info_service import generate_info_card
    from app.models.models import Medication as MedModel

    lang = patient.language_preference or "en"
    condition = patient.conditions[0] if patient.conditions else None

    pms = (
        db.query(PatientMedication)
        .filter(
            PatientMedication.patient_id == patient.id,
            PatientMedication.is_active == True,  # noqa: E712
            PatientMedication.med_info_card_sent_at.is_(None),
        )
        .all()
    )
    for pm in pms:
        med = db.query(MedModel).filter(MedModel.id == pm.medication_id).first()
        if not med:
            continue
        try:
            card = generate_info_card(med.name, lang, condition=condition)
            telegram_service.send_text(
                db=db,
                patient_id=patient.id,
                to_phone=patient.telegram_chat_id or patient.phone_number,
                body=card,
            )
            pm.med_info_card_sent_at = now_sgt()
        except Exception as exc:
            logger.warning("Failed to send info card for medication %s: %s", med.name, exc)
    db.commit()


def _send_patient_keyboard(db: Session, patient: Patient, body: str, buttons: list) -> None:
    if not patient.telegram_chat_id:
        logger.warning("Cannot send keyboard to patient %s — telegram_chat_id not set", patient.id)
        return
    telegram_service.send_keyboard(
        db=db, patient_id=patient.id, to_phone=patient.telegram_chat_id, body=body, buttons=buttons
    )


def _send_raw_keyboard(chat_id: str, body: str, buttons: list) -> None:
    """Send a keyboard message to an unregistered chat (no OutboundMessage record)."""
    import httpx
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.info("Simulated keyboard to %s: %s", chat_id, body)
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": body,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": buttons},
            },
            timeout=10,
        )
    except Exception as exc:
        logger.error("Failed to send raw keyboard to %s: %s", chat_id, exc)


def _send_raw(chat_id: str, body: str) -> None:
    import httpx
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.info("Simulated reply to %s: %s", chat_id, body)
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": body, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logger.error("Failed to send raw message to %s: %s", chat_id, exc)


def send_invite(db: Session, patient: Patient) -> None:
    """Legacy shim — now generates invite token instead of direct message."""
    generate_invite_token(db, patient)
