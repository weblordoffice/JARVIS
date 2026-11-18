import hashlib
import json
import os
import re
import secrets
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

load_dotenv(Path(__file__).parent / ".env")

app = FastAPI(title="JARVIS AI Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE = Path(__file__).parent
LAYOUT_PATH = BASE / "static" / "layout.html"
PAGES_DIR = BASE / "pages"
LOGIN_ART = BASE / "static" / "login-art.jpg"
STATE_PATH = BASE / "data" / "state.json"
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "jarvis")
STARTED = datetime.now()
LOG_LIMIT = 100
INBOX_LIMIT = 100
CHANNELS = {"gmail", "whatsapp", "notification"}
ROLES = {"business", "operational", "blocked"}
REPLY_MODES = {"approval", "auto", "both"}
LEAD_STATUSES = ("new", "interested", "followup", "negotiation", "converted", "lost")
_SENSITIVE = re.compile(
    r"\b(otp|password|aadhaar|aadhar|cvv|pan card|account number|payment|discount|"
    r"refund|invoice|legal|lawyer|court|contract|gst|upi|price|quote|kitna|cost|fees)\b",
    re.IGNORECASE,
)
_ENQUIRY = re.compile(
    r"\b(enquiry|inquiry|interested|website|service|product|quote|price|demo|"
    r"meeting|project|business|client|app|software|pricing|invoice|quotation|proposal)\b",
    re.IGNORECASE,
)

DEFAULT_SETTINGS = {
    "name": "JARVIS",
    "model": "gpt-4o-mini",
    "reply_mode": "approval",
    "emergency_stop": False,
    "system_prompt": (
        "You are JARVIS, a calm assistant for business messages. "
        "Reply only to people on the approved list, and only after the active reply mode allows it. "
        "Never reply to operational, blocked, unknown, or spam contacts. "
        "Answer in the language the user writes in."
    ),
}

_lock = Lock()
_log: deque[dict] = deque(maxlen=LOG_LIMIT)
_contacts: list[dict] = []
_inbox: deque[dict] = deque(maxlen=INBOX_LIMIT)
_audit: deque[dict] = deque(maxlen=50)
_next_id = 1
_next_contact_id = 1
_next_inbox_id = 1
_handled = 0
_settings = dict(DEFAULT_SETTINGS)
_account: dict | None = None
_projects: list[dict] = []
_tasks: list[dict] = []
_meetings: list[dict] = []
_documents: list[dict] = []
_next_work_id = 1


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class SettingsIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=80)
    system_prompt: str = Field(min_length=1, max_length=2000)


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    handle: str = Field(min_length=1, max_length=120)
    channel: str
    role: str
    auto_reply: bool = False
    vip: bool = False
    reply_days: int | None = Field(default=None, ge=0, le=30)


class ControlIn(BaseModel):
    reply_mode: str
    emergency_stop: bool


class InboxIn(BaseModel):
    channel: str
    sender: str = Field(min_length=1, max_length=80)
    handle: str = Field(default="", max_length=120)
    body: str = Field(min_length=1, max_length=4000)


class AllowIn(BaseModel):
    role: str = "business"
    auto_reply: bool = False


class LeadIn(BaseModel):
    status: str


class StopIn(BaseModel):
    on: bool = True


class ProjectIn(BaseModel):
    client: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    budget: str = Field(default="", max_length=40)
    deadline: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=500)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    client: str = Field(default="", max_length=80)
    due: str = Field(default="", max_length=40)


class MeetingIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    client: str = Field(default="", max_length=80)
    when: str = Field(min_length=1, max_length=40)


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="note", max_length=20)
    text: str = Field(min_length=1, max_length=8000)


class AccountIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=5, max_length=80)
    name: str = Field(default="", max_length=80)


def _mongo_host() -> str:
    parsed = urlparse(MONGODB_URI)
    if not parsed.hostname:
        return "mongodb"
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname


def _database_status() -> dict:
    return {"ok": True, "engine": "mongodb", "name": MONGODB_DB, "host": _mongo_host()}


def _snapshot() -> dict:
    return {
        "next_id": _next_id,
        "next_contact_id": _next_contact_id,
        "next_inbox_id": _next_inbox_id,
        "handled": _handled,
        "next_work_id": _next_work_id,
        "settings": _settings,
        "projects": list(_projects),
        "tasks": list(_tasks),
        "meetings": list(_meetings),
        "documents": list(_documents),
        "messages": list(_log),
        "contacts": list(_contacts),
        "inbox": list(_inbox),
        "audit": list(_audit),
        "account": _account,
    }


def _apply_saved(saved: dict) -> None:
    global _next_id, _next_contact_id, _next_inbox_id, _handled, _settings, _account, _next_work_id
    _settings = {**DEFAULT_SETTINGS, **saved.get("settings", {})}
    _next_id = int(saved.get("next_id", 1))
    _next_contact_id = int(saved.get("next_contact_id", 1))
    _next_inbox_id = int(saved.get("next_inbox_id", 1))
    _handled = int(saved.get("handled", 0))
    _next_work_id = int(saved.get("next_work_id", 1))
    _contacts[:] = list(saved.get("contacts", []))
    _projects[:] = list(saved.get("projects", []))
    _tasks[:] = list(saved.get("tasks", []))
    _meetings[:] = list(saved.get("meetings", []))
    _documents[:] = list(saved.get("documents", []))
    _log.clear()
    for entry in reversed(saved.get("messages", [])[:LOG_LIMIT]):
        _log.appendleft(entry)
    _inbox.clear()
    for entry in reversed(saved.get("inbox", [])[:INBOX_LIMIT]):
        _inbox.appendleft(entry)
    _audit.clear()
    for entry in reversed(saved.get("audit", [])[:50]):
        _audit.appendleft(entry)
    saved_account = saved.get("account")
    _account = dict(saved_account) if isinstance(saved_account, dict) else None


def _connect_mongo():
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        raise SystemExit(
            f"MongoDB is not reachable at {_mongo_host()}. Start MongoDB, or set MONGODB_URI."
        ) from exc
    return client[MONGODB_DB]["state"]


_state_col = _connect_mongo()


def _load_state() -> None:
    saved = _state_col.find_one({"_id": "app"})
    if saved is None and STATE_PATH.exists():
        try:
            saved = json.loads(STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            saved = None
        if saved:
            _apply_saved(saved)
            _save_state()
            return
    if saved:
        _apply_saved(saved)


def _save_state() -> None:
    payload = _snapshot()
    payload["_id"] = "app"
    _state_col.replace_one({"_id": "app"}, payload, upsert=True)


def _clean_channel(channel: str) -> str:
    value = channel.strip().lower()
    if value not in CHANNELS:
        raise HTTPException(status_code=400, detail="Channel must be gmail, whatsapp, or notification.")
    return value


def _clean_role(role: str) -> str:
    value = role.strip().lower()
    if value not in ROLES:
        raise HTTPException(status_code=400, detail="Role must be business or operational.")
    return value


def _normalize(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\u0900-\u097F\s']", "", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _local_command(text: str) -> str | None:
    asked = _normalize(text)
    hindi = bool(re.search(r"[\u0900-\u097F]", text)) or any(
        word in asked.split() for word in ("samay", "baja", "tarikh", "madad", "namaste", "haal")
    )
    now = datetime.now()

    if asked in {"hi", "hello", "hey", "namaste", "good morning", "good evening"}:
        name = _settings["name"]
        return f"Namaste. {name} online hai." if hindi else f"Hello. {name} is online."

    if asked in {"time", "what time", "what time is it", "whats the time", "samay", "kitna baja", "kitna baja hai", "time kya hai"}:
        clock = now.strftime("%I:%M %p")
        return f"Abhi samay {clock} hai." if hindi else f"The time is {clock}."

    if asked in {"date", "what is the date", "whats the date", "tarikh", "aaj ki tarikh"}:
        today = now.strftime("%A, %d %B %Y")
        return f"Aaj {today} hai." if hindi else f"Today is {today}."

    if asked in {"help", "madad", "what can you do"}:
        if hindi:
            return "Main sirf unhi ko reply karta hoon jinhe aap business list mein rakhte ho. Operational contacts ko chhod deta hoon."
        return "I reply only to people on your business list. Operational contacts are skipped."

    if asked in {"status", "uptime", "haal"}:
        elapsed = int((now - STARTED).total_seconds())
        hours, rest = divmod(elapsed, 3600)
        minutes, seconds = divmod(rest, 60)
        clock = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if hindi:
            return f"Online hoon. Uptime {clock}. Messages handled: {_handled}."
        return f"Online. Uptime {clock}. Messages handled: {_handled}."

    return None


def _complete(text: str, *, with_history: bool) -> str:
    if OpenAI is None:
        raise RuntimeError("OpenAI package is not installed")
    with _lock:
        history = list(_log)[:8] if with_history else []
        settings = dict(_settings)
    history.reverse()
    messages = [{"role": "system", "content": settings["system_prompt"]}]
    for item in history:
        messages.append({"role": "user", "content": item["text"]})
        messages.append({"role": "assistant", "content": item["reply"]})
    messages.append({"role": "user", "content": text})
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=settings["model"],
        messages=messages,
        max_completion_tokens=300,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("empty reply")
    return content


def _local_draft(sender: str, body: str) -> str:
    first = sender.strip().split()[0] if sender.strip() else "there"
    unsure = _SENSITIVE.search(body) or re.search(r"\b(kab|when|date|time)\b", body.lower())
    if re.search(r"[\u0900-\u097F]", body):
        if unsure:
            return f"Namaste {first}, message mil gaya. Exact detail confirm karke hi jawab dunga. Main andaza nahi lagaunga."
        return f"Namaste {first}, aapka message mil gaya. Main isko dekh kar jaldi aapko update karunga."
    if unsure:
        return f"Hello {first}, I have your message. I will confirm the exact detail before answering. I will not guess."
    return f"Hello {first}, thank you for writing. I have your message and will come back with the next step shortly."


def _draft_reply(sender: str, body: str, channel: str) -> tuple[str, str]:
    prompt = (
        f"Write a short professional reply to this {channel} message from {sender}. "
        "Use the same language as the message. Do not mention operational staff.\n\n"
        f"{body}"
    )
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _complete(prompt, with_history=False), "ai"
        except Exception:
            return _local_draft(sender, body), "local"
    return _local_draft(sender, body), "local"


def _reply(text: str) -> tuple[str, str]:
    command = _local_command(text)
    if command:
        return command, "local"
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _complete(text, with_history=True), "ai"
        except Exception:
            return (
                "I could not reach the AI service. Ask for the time, date, status, or help.",
                "local",
            )
    return (
        "Local mode is on. I reply only to people on your business list and skip operational contacts.",
        "local",
    )


def _record_unlocked(text: str, reply: str, source: str) -> None:
    global _next_id, _handled
    _log.appendleft(
        {
            "id": _next_id,
            "text": text,
            "reply": reply,
            "source": source,
            "time": datetime.now().isoformat(),
        }
    )
    _next_id += 1
    _handled += 1


def _record(text: str, reply: str, source: str) -> None:
    with _lock:
        _record_unlocked(text, reply, source)
        _save_state()


def _find_contact(sender: str, handle: str, channel: str) -> dict | None:
    handle_key = handle.strip().lower()
    name_key = sender.strip().lower()
    with _lock:
        contacts = [dict(item) for item in _contacts]
    for contact in contacts:
        if contact["channel"] == channel and handle_key and contact["handle"].lower() == handle_key:
            return contact
    for contact in contacts:
        if contact["channel"] == channel and name_key and contact["name"].lower() == name_key:
            return contact
    return None


def _duplicate(channel: str, handle: str, body: str) -> dict | None:
    handle_key = handle.strip().lower()
    for item in _inbox:
        if (
            item["channel"] == channel
            and item["handle"].lower() == handle_key
            and item["body"] == body
        ):
            return dict(item)
    return None


def _is_enquiry(body: str) -> bool:
    return bool(_ENQUIRY.search(body or ""))


def _summary_text(body: str) -> str:
    line = " ".join((body or "").split())
    return line if len(line) <= 160 else line[:157] + "..."


def _followup_draft(sender: str) -> str:
    first = sender.strip().split()[0] if sender.strip() else "there"
    return (
        f"Hello {first}, I am following up on your message. "
        "I will confirm the details before sharing a price, date, or commitment."
    )


def _priority(body: str, contact: dict | None) -> str:
    text = body.lower()
    if any(word in text for word in ("lottery", "you won", "crypto giveaway", "claim your prize")):
        return "spam"
    if _SENSITIVE.search(text):
        return "sensitive"
    if any(word in text for word in ("urgent", "asap", "jaldi", "turant")):
        return "urgent"
    if contact and contact.get("role") == "business":
        return "business"
    return "personal"


def _in_reply_window(contact: dict | None) -> bool:
    if not contact or contact.get("role") != "business" or not contact.get("auto_reply"):
        return False
    until = contact.get("reply_until")
    if not isinstance(until, str) or not until:
        return False
    try:
        end = datetime.fromisoformat(until)
    except ValueError:
        return False
    return datetime.now() < end


def _channel_spoken(channel: str) -> str:
    if channel == "whatsapp":
        return "WhatsApp"
    if channel == "gmail":
        return "Gmail"
    return "Alert"


def _speak_for(item: dict, contact: dict | None) -> str:
    channel = _channel_spoken(item.get("channel", ""))
    sender = item.get("sender") or "Koi"
    if contact is None and channel == "WhatsApp":
        return (
            f"Sir, naya WhatsApp message. {sender}. "
            "Sir, you have received a new WhatsApp message. "
            "Sir, a new business client has contacted you."
        )
    if contact is None and channel == "Gmail":
        return (
            f"Sir, naya Gmail enquiry. {sender}. "
            "Sir, you have received a new Gmail enquiry."
        )
    if contact is None:
        return f"Sir, naya alert. {sender}. Approval required."
    if item.get("status") == "skipped":
        return f"{sender} operational hai. Reply nahi diya. No reply was sent."
    if _phone_can_send(item, contact):
        return (
            f"Sir, {sender} ko {channel} reply bhej raha hoon. "
            f"Sir, I am replying to {sender} on {channel}."
        )
    if item.get("status") == "pending":
        return (
            f"Sir, {sender} ke {channel} message ka reply taiyar hai. "
            "Sir, I have prepared a reply. Please approve it."
        )
    return f"Sir, {channel} message. {sender}."


def _phone_can_send(item: dict, contact: dict | None) -> bool:
    return bool(
        contact
        and _in_reply_window(contact)
        and item.get("status") == "replied"
        and item.get("draft")
        and item.get("priority") != "sensitive"
        and not _settings.get("emergency_stop")
    )


def _queue_allowed(contact: dict) -> bool:
    if _settings.get("emergency_stop"):
        return False
    return _in_reply_window(contact)


def _decide(item: dict, contact: dict | None, draft: str, source: str) -> None:
    if item.get("priority") == "spam":
        item.update(
            decision="skip",
            status="skipped",
            draft="",
            draft_source="local",
            reason="Spam. No reply.",
        )
        return
    if contact is None:
        enquiry = _is_enquiry(item.get("body") or "")
        item.update(
            decision="hold",
            status="held",
            draft="",
            draft_source="local",
            reason=(
                "New business enquiry. Potential lead. Approval required."
                if enquiry
                else "Naya contact. List mein nahi, isliye reply nahi diya."
            ),
            lead_status="new" if enquiry else "",
        )
        return
    if contact["role"] in {"operational", "blocked"}:
        item.update(
            decision="skip",
            status="skipped",
            draft="",
            draft_source="local",
            reason="Operational contact. No reply." if contact["role"] == "operational" else "Blocked contact. No reply.",
        )
        return
    if _settings.get("emergency_stop"):
        item.update(
            decision="hold",
            status="held",
            draft=draft,
            draft_source=source,
            reason="Emergency stop. No reply will be sent.",
        )
        return
    if item.get("priority") == "sensitive":
        if not item.get("lead_status") and _is_enquiry(item.get("body") or ""):
            item["lead_status"] = "negotiation"
        item.update(
            decision="reply",
            status="pending",
            draft=draft,
            draft_source=source,
            reason="Payment, price, discount, or legal detail. Approval required. No promise was sent.",
        )
        return
    lang = _language_name(item.get("body") or "")
    if lang not in {"hindi", "english"}:
        item.update(
            decision="reply",
            status="pending",
            draft=draft,
            draft_source=source,
            reason=f"{lang.capitalize()} message. Translation is not guessed. Approval required.",
        )
        return
    if not item.get("lead_status") and _is_enquiry(item.get("body") or ""):
        item["lead_status"] = "interested"
    queued = _queue_allowed(contact)
    item.update(
        decision="reply",
        status="replied" if queued else "pending",
        draft=draft,
        draft_source=source,
        reason=(
            "Aapke chune hue dinon tak reply queue hua."
            if queued and _in_reply_window(contact)
            else "Approved contact. Reply queued."
            if queued
            else "Approved contact. Waiting for your approval."
        ),
    )


def _mode() -> str:
    return "openai" if os.getenv("OPENAI_API_KEY") else "local"


def _other_reply(sender: str, body: str) -> str:
    first = sender.strip().split()[0] if sender.strip() else "there"
    hindi = bool(re.search(r"[\u0900-\u097F]", body))
    if hindi:
        return f"English meaning of the reply: Hello {first}, I have your message. I will confirm the detail before answering."
    return f"Jawab ka Hindi matlab: Namaste {first}, message mil gaya. Detail confirm karke hi jawab dunga."


def _language_name(body: str) -> str:
    scripts = (
        (r"[\u0900-\u097F]", "hindi"),
        (r"[\u0980-\u09FF]", "bengali"),
        (r"[\u0A00-\u0A7F]", "punjabi"),
        (r"[\u0A80-\u0AFF]", "gujarati"),
        (r"[\u0B80-\u0BFF]", "tamil"),
        (r"[\u0C00-\u0C7F]", "telugu"),
        (r"[\u0C80-\u0CFF]", "kannada"),
        (r"[\u0D00-\u0D7F]", "malayalam"),
        (r"[\u0600-\u06FF]", "arabic"),
        (r"[\u4E00-\u9FFF]", "chinese"),
        (r"[\u3040-\u30FF]", "japanese"),
        (r"[\u0400-\u04FF]", "cyrillic"),
    )
    for pattern, name in scripts:
        if re.search(pattern, body or ""):
            return name
    return "english"


def _with_explain(item: dict) -> dict:
    copy = dict(item)
    body = copy.get("body") or ""
    lang = _language_name(body)
    copy["lang"] = lang
    copy["summary"] = _summary_text(body)
    if not copy.get("lead_status") and copy.get("status") == "held" and _is_enquiry(body):
        copy["lead_status"] = "new"
    if lang not in {"hindi", "english"}:
        copy["explain"] = f"{lang.capitalize()} message. Translation is not guessed."
    elif copy.get("draft"):
        copy["explain"] = _other_reply(copy.get("sender") or "", body)
    elif lang == "hindi":
        copy["explain"] = "Hindi message. English meaning is not guessed."
    else:
        copy["explain"] = "English message. Hindi meaning appears when a draft exists."
    return copy


def _overview() -> dict:
    with _lock:
        return {
            "assistant": _settings["name"],
            "status": "online",
            "mode": _mode(),
            "started_at": STARTED.isoformat(),
            "uptime_seconds": int((datetime.now() - STARTED).total_seconds()),
            "handled": _handled,
            "settings": dict(_settings),
            "messages": list(_log),
            "contacts": list(_contacts),
            "inbox": [_with_explain(item) for item in _inbox],
            "audit": list(_audit),
            "projects": list(_projects),
            "tasks": list(_tasks),
            "meetings": list(_meetings),
            "documents": list(_documents),
            "followups": _followups(),
            "report": _daily_report(),
            "synced_at": datetime.now().isoformat(),
            "database": _database_status(),
        }


_load_state()


@app.get("/")
def home():
    with _lock:
        name = _settings["name"]
    return {
        "assistant": name,
        "status": "online",
        "mode": _mode(),
        "message": f"Hello. {name} is ready.",
        "time": datetime.now().isoformat(),
    }


def _set_control(reply_mode: str | None = None, emergency_stop: bool | None = None) -> None:
    with _lock:
        if reply_mode is not None:
            if reply_mode not in REPLY_MODES:
                raise HTTPException(status_code=400, detail="Reply mode must be approval, auto, or both.")
            _settings["reply_mode"] = reply_mode
        if emergency_stop is not None:
            _settings["emergency_stop"] = emergency_stop
        _save_state()


def _summary() -> str:
    with _lock:
        waiting = [
            item for item in _inbox
            if item.get("status") == "pending" and item.get("priority", "business") in {"business", "urgent"}
        ]
    if not waiting:
        return "Koi naya business message approval ka wait nahi kar raha."
    lines = [f"{item['channel']}: {item['sender']} — {item['body'][:90]}" for item in waiting[:6]]
    return "Approval mein business messages:\n" + "\n".join(lines)


def _apply_order(text: str) -> str | None:
    asked = _normalize(text)
    if any(phrase in asked for phrase in ("emergency stop", "stop all replies", "sab band", "sab reply band")):
        _set_control(emergency_stop=True)
        return "Emergency stop on. Kisi ko reply nahi jayega."
    if any(phrase in asked for phrase in ("resume replies", "emergency off", "replies chalu", "stop hatao")):
        _set_control(emergency_stop=False)
        return "Emergency stop off. Approved replies phir se approval mode par hain."
    if any(phrase in asked for phrase in ("dono mode", "both modes", "manual aur automatic")):
        _set_control(reply_mode="both")
        return "Dono modes on. VIP ya auto contacts queue honge. Baaki approval ka wait karenge."
    if any(phrase in asked for phrase in ("automatic replies band", "auto replies band", "automatic reply band", "auto band", "automatic replies off", "auto replies off")):
        _set_control(reply_mode="approval")
        return "Automatic replies band. Har reply pehle aapki approval ka wait karega."
    if any(phrase in asked for phrase in ("sirf approved", "automatic reply", "auto reply")):
        _set_control(reply_mode="auto")
        return "Sirf approved business contacts ko automatic reply hoga. Baaki ko kuch nahi jayega."
    if any(phrase in asked for phrase in ("pehle meri approval", "meri approval", "approval mode")):
        _set_control(reply_mode="approval")
        return "Ab har reply pehle aapki approval ka wait karega."
    if any(phrase in asked for phrase in ("pending email", "pending emails", "pending gmail")):
        return _pending_emails()
    if any(phrase in asked for phrase in ("approved contacts", "approved contact", "reply list", "contact list")):
        return _contact_list()
    named = _named_reply(asked)
    if named:
        return named
    if any(phrase in asked for phrase in ("aaj ki report", "daily report", "business report", "aaj ka report")):
        return _report_text()
    if any(phrase in asked for phrase in ("tasks dikhao", "task list", "mere tasks")):
        return _task_text()
    if any(phrase in asked for phrase in ("meetings dikhao", "meeting list", "aaj ki meeting")):
        return _meeting_text()
    if any(phrase in asked for phrase in ("follow up", "followup", "purane clients")):
        return _followup_text()
    if any(phrase in asked for phrase in ("summary", "naye business", "business emails", "business messages", "daily briefing", "briefing")):
        return _briefing()
    return None


def _read_document(text: str) -> dict:
    amounts = re.findall(
        r"(?:₹|rs\.?|inr|\$|€)\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*\s?(?:rupees|rs)\b",
        text,
        flags=re.IGNORECASE,
    )
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text)
    return {
        "summary": _summary_text(text),
        "amounts": amounts[:8],
        "dates": dates[:8],
        "note": "Sirf woh amount aur date jo text mein likhe hain. Jo likha nahi, woh guess nahi kiya.",
    }


def _stamp_work(item: dict) -> dict:
    global _next_work_id
    item["id"] = _next_work_id
    item["time"] = datetime.now().isoformat(timespec="seconds")
    _next_work_id += 1
    return item


def _followups() -> list[dict]:
    now = datetime.now()
    rows = []
    for contact in _contacts:
        if contact.get("role") != "business":
            continue
        handle = (contact.get("handle") or "").lower()
        channel = contact.get("channel")
        latest = next(
            (
                item for item in _inbox
                if item.get("channel") == channel and (item.get("handle") or "").lower() == handle
            ),
            None,
        )
        if latest is None:
            continue
        try:
            when = datetime.fromisoformat(latest.get("time") or "")
        except ValueError:
            continue
        days = (now - when).days
        if days < 2:
            continue
        rows.append(
            {
                "client": contact.get("name") or "",
                "channel": channel,
                "days": days,
                "draft": _followup_draft(contact.get("name") or ""),
            }
        )
    return rows[:12]


def _daily_report() -> dict:
    today = datetime.now().date().isoformat()
    new_clients = [item for item in _inbox if item.get("status") == "held" and str(item.get("time") or "").startswith(today)]
    pending = [item for item in _inbox if item.get("status") == "pending"]
    completed = [item for item in _inbox if item.get("status") == "replied" and str(item.get("time") or "").startswith(today)]
    open_tasks = [task for task in _tasks if not task.get("done")]
    soon = []
    now = datetime.now()
    for meeting in _meetings:
        try:
            when = datetime.fromisoformat(meeting.get("when") or "")
        except ValueError:
            continue
        if now <= when <= now + timedelta(days=7):
            soon.append(meeting)
    lines = [
        f"Naye clients aaj: {len(new_clients)}.",
        f"Pending replies: {len(pending)}.",
        f"Aaj complete hue replies: {len(completed)}.",
        f"Open tasks: {len(open_tasks)}.",
        f"Aane wali meetings, 7 din: {len(soon)}.",
        f"Projects: {len(_projects)}. Documents: {len(_documents)}.",
    ]
    return {
        "new_clients": len(new_clients),
        "pending_replies": len(pending),
        "completed": len(completed),
        "open_tasks": len(open_tasks),
        "meetings_soon": len(soon),
        "projects": len(_projects),
        "documents": len(_documents),
        "lines": lines,
    }


def _report_text() -> str:
    with _lock:
        lines = _daily_report()["lines"]
    return "Daily business report:\n" + "\n".join(lines)


def _task_text() -> str:
    with _lock:
        open_tasks = [task for task in _tasks if not task.get("done")]
    if not open_tasks:
        return "Koi open task nahi. Task tab banta hai jab aap use likhte ho."
    return "Open tasks:\n" + "\n".join(
        f"- {task.get('title')} ({task.get('client') or 'no client'})" for task in open_tasks[:8]
    )


def _meeting_text() -> str:
    with _lock:
        rows = list(_meetings)
    if not rows:
        return "Koi meeting save nahi hai."
    return "Meetings:\n" + "\n".join(
        f"- {item.get('title')} · {item.get('when')}" for item in rows[:8]
    )


def _followup_text() -> str:
    with _lock:
        rows = _followups()
    if not rows:
        return "Koi purana client follow-up nahi. Do din se chup contact par suggestion aayega. Abhi kuch bheja nahi gaya."
    lines = [f"- {item['client']}: {item['days']} din. {item['draft']}" for item in rows[:6]]
    return "Follow-up suggestions. Abhi kuch send nahi hua:\n" + "\n".join(lines)


def _briefing() -> str:
    with _lock:
        items = list(_inbox)
    business = [item for item in items if item.get("priority") in {"business", "urgent", "sensitive"}]
    pending = [item for item in items if item.get("status") == "pending"]
    spam = [item for item in items if item.get("priority") == "spam"]
    lines = [
        f"Aaj desk par {len(items)} messages hain.",
        f"Business {len(business)}. Pending approval {len(pending)}. Spam {len(spam)}.",
    ]
    lines.extend(f"- {item['sender']}: {item['body'][:80]}" for item in pending[:5])
    if not pending:
        lines.append("Koi pending reply nahi.")
    return "\n".join(lines)


def _pending_emails() -> str:
    with _lock:
        items = [item for item in _inbox if item.get("channel") == "gmail" and item.get("status") == "pending"]
    if not items:
        return "Koi pending email nahi."
    lines = [f"{item['sender']}: {item['body'][:80]}" for item in items[:6]]
    return "Pending emails:\n" + "\n".join(lines)


def _contact_list() -> str:
    with _lock:
        names = [contact["name"] for contact in _contacts if contact.get("role") == "business"]
    if not names:
        return "Approved contact list khali hai."
    return "Approved contacts: " + ", ".join(names)


def _named_reply(asked: str) -> str | None:
    if " ko " not in asked or ("reply" not in asked and "jawab" not in asked):
        return None
    name = asked.split(" ko ", 1)[0].replace("jarvis", "").replace("please", "").strip()
    if not name or len(name) > 40:
        return None
    with _lock:
        matches = [item for item in _inbox if name in item.get("sender", "").lower()]
    if not matches:
        return f"{name.title()} ka koi message desk par nahi hai. Main andaza laga kar jawab nahi likhunga."
    item = matches[0]
    if not item.get("draft"):
        return f"{item['sender']} approved list par nahi. Unhe reply nahi likha jayega."
    return f"{item['sender']} ke liye draft, abhi bheja nahi:\n{item['draft']}"


def _remember(item: dict) -> None:
    _audit.appendleft(
        {
            "sender": item.get("sender", ""),
            "channel": item.get("channel", ""),
            "body": (item.get("body") or "")[:160],
            "draft": (item.get("draft") or "")[:200],
            "time": datetime.now().isoformat(),
        }
    )


@app.post("/message")
def receive_message(data: Message):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message is empty.")
    order = _apply_order(text)
    if order:
        _record(text, order, "local")
        return {"received": text, "reply": order, "source": "local"}
    reply, source = _reply(text)
    _record(text, reply, source)
    return {"received": text, "reply": reply, "source": source}


@app.post("/api/control")
def update_control(body: ControlIn):
    _set_control(reply_mode=body.reply_mode, emergency_stop=body.emergency_stop)
    with _lock:
        return {
            "reply_mode": _settings["reply_mode"],
            "emergency_stop": _settings["emergency_stop"],
        }


ADMIN_PAGES = {
    "inbox",
    "approval",
    "channels",
    "rules",
    "brain",
    "security",
    "devices",
    "health",
    "people",
    "leads",
    "work",
    "desk",
    "profile",
    "reports",
    "stop",
    "gmail",
    "whatsapp",
    "alerts",
}


def _admin_html(page: str) -> HTMLResponse:
    layout = LAYOUT_PATH.read_text()
    fragment = (PAGES_DIR / page / "page.html").read_text()
    return HTMLResponse(layout.replace("<!--PAGE-->", fragment))


@app.get("/admin")
def admin_panel():
    return _admin_html("home")


@app.get("/admin/{page}")
def admin_section(page: str):
    if page not in ADMIN_PAGES:
        raise HTTPException(status_code=404, detail="Page not found.")
    return _admin_html(page)


app.mount("/assets", StaticFiles(directory=BASE / "static"), name="assets")


@app.get("/login-art.jpg")
def login_art():
    return FileResponse(LOGIN_ART)


@app.get("/admin.")
def admin_panel_with_dot():
    return RedirectResponse("/admin")


@app.get("/api/admin/overview")
def admin_overview():
    return _overview()


@app.put("/api/admin/settings")
def update_settings(body: SettingsIn):
    name = body.name.strip()
    model = body.model.strip()
    prompt = body.system_prompt.strip()
    if not name or not model or not prompt:
        raise HTTPException(status_code=400, detail="Name, model, and system prompt are required.")
    with _lock:
        _settings.update({"name": name, "model": model, "system_prompt": prompt})
        _save_state()
        saved = dict(_settings)
    return saved


@app.delete("/api/admin/log")
def clear_log():
    with _lock:
        _log.clear()
        _save_state()
    return {"cleared": True}


@app.delete("/api/admin/audit")
def clear_audit():
    with _lock:
        _audit.clear()
        _save_state()
    return {"cleared": True}


@app.post("/api/contacts")
def add_contact(body: ContactIn):
    global _next_contact_id
    channel = _clean_channel(body.channel)
    role = _clean_role(body.role)
    name = body.name.strip()
    handle = body.handle.strip()
    auto_reply = bool(body.auto_reply) and role == "business"
    vip = bool(body.vip) and role == "business"
    reply_until = None
    if body.reply_days is not None:
        if body.reply_days > 0 and role == "business":
            auto_reply = True
            reply_until = (datetime.now() + timedelta(days=body.reply_days)).isoformat(timespec="seconds")
        else:
            auto_reply = False
            reply_until = None
    with _lock:
        for contact in _contacts:
            if contact["channel"] == channel and contact["handle"].lower() == handle.lower():
                contact["name"] = name
                contact["role"] = role
                contact["auto_reply"] = auto_reply
                contact["vip"] = vip
                if body.reply_days is not None:
                    contact["reply_until"] = reply_until
                _save_state()
                return dict(contact)
        contact = {
            "id": _next_contact_id,
            "name": name,
            "handle": handle,
            "channel": channel,
            "role": role,
            "auto_reply": auto_reply,
            "vip": vip,
            "reply_until": reply_until,
        }
        _next_contact_id += 1
        _contacts.append(contact)
        _save_state()
        return dict(contact)


@app.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: int):
    with _lock:
        kept = [contact for contact in _contacts if contact["id"] != contact_id]
        if len(kept) == len(_contacts):
            raise HTTPException(status_code=404, detail="Contact not found.")
        _contacts[:] = kept
        _save_state()
    return {"deleted": True}


@app.post("/api/inbox")
def ingest_message(body: InboxIn):
    global _next_inbox_id
    channel = _clean_channel(body.channel)
    sender = body.sender.strip()
    handle = body.handle.strip() or sender
    text = body.body.strip()
    contact = _find_contact(sender, handle, channel)
    draft, source = ("", "local")
    if contact and contact["role"] == "business":
        draft, source = _draft_reply(sender, text, channel)
    with _lock:
        existing = _duplicate(channel, handle, text)
        if existing:
            return existing
        item = {
            "id": _next_inbox_id,
            "channel": channel,
            "sender": sender,
            "handle": handle,
            "body": text,
            "priority": _priority(text, contact),
            "time": datetime.now().isoformat(),
        }
        _next_inbox_id += 1
        _decide(item, contact, draft, source)
        if item["status"] == "replied":
            _record_unlocked(
                f"[{channel}] {sender}: {text}",
                item["draft"],
                item["draft_source"],
            )
            _remember(item)
        _inbox.appendleft(item)
        _save_state()
        result = dict(item)
        result["speak"] = _speak_for(item, contact)
        result["send_reply"] = item.get("draft") or "" if _phone_can_send(item, contact) else ""
        return result


def _inbox_item(item_id: int) -> dict | None:
    for item in _inbox:
        if item["id"] == item_id:
            return item
    return None


@app.post("/api/inbox/{item_id}/send")
async def send_inbox_reply(item_id: int, request: Request):
    raw = (await request.body()).decode()
    edited = ""
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            edited = str(payload.get("draft") or "").strip()
    if len(edited) > 2000:
        raise HTTPException(status_code=400, detail="Reply is too long.")
    with _lock:
        item = _inbox_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        if _settings.get("emergency_stop"):
            raise HTTPException(status_code=409, detail="Emergency stop is on. No reply will be sent.")
        if item["decision"] != "reply":
            raise HTTPException(
                status_code=409,
                detail="JARVIS will not message this person. They are not on your reply list.",
            )
        if edited:
            item["draft"] = edited
            item["draft_source"] = "local"
        if not item.get("draft"):
            raise HTTPException(status_code=409, detail="Write a reply before approving.")
        if item["status"] != "replied":
            item["status"] = "replied"
            item["reason"] = "Approved by you. Reply queued."
            _record_unlocked(
                f"[{item['channel']}] {item['sender']}: {item['body']}",
                item["draft"],
                item.get("draft_source") or "local",
            )
            _remember(item)
        _save_state()
        return dict(item)


@app.post("/api/inbox/{item_id}/skip")
def skip_inbox(item_id: int):
    with _lock:
        item = _inbox_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        if item["status"] == "replied":
            raise HTTPException(status_code=409, detail="This reply is already queued.")
        item["decision"] = "skip"
        item["status"] = "skipped"
        item["draft"] = ""
        item["reason"] = "Held. JARVIS will not reply."
        _save_state()
        return dict(item)


@app.post("/api/inbox/{item_id}/allow")
def allow_sender(item_id: int, body: AllowIn):
    role = _clean_role(body.role)
    with _lock:
        item = _inbox_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        snapshot = dict(item)
    contact = add_contact(
        ContactIn(
            name=snapshot["sender"],
            handle=snapshot["handle"],
            channel=snapshot["channel"],
            role=role,
            auto_reply=body.auto_reply,
        )
    )
    draft, source = ("", "local")
    if contact["role"] == "business":
        draft, source = _draft_reply(snapshot["sender"], snapshot["body"], snapshot["channel"])
    with _lock:
        item = _inbox_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        _decide(item, contact, draft, source)
        if item["status"] == "replied":
            _record_unlocked(
                f"[{item['channel']}] {item['sender']}: {item['body']}",
                item["draft"],
                item["draft_source"],
            )
            _remember(item)
        _save_state()
        return dict(item)


@app.get("/api/leads")
def list_leads():
    with _lock:
        shown = [_with_explain(item) for item in _inbox]
    return {"leads": [item for item in shown if item.get("lead_status")]}


@app.post("/api/inbox/{item_id}/lead")
def set_lead(item_id: int, body: LeadIn):
    status = body.status.strip().lower()
    if status not in LEAD_STATUSES:
        raise HTTPException(status_code=400, detail="Lead status is not one of the six pipeline stages.")
    with _lock:
        item = _inbox_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        item["lead_status"] = status
        if status == "followup":
            item["followup"] = _followup_draft(item.get("sender") or "")
        _save_state()
        return _with_explain(item)


@app.post("/api/emergency-stop")
def emergency_stop(body: StopIn):
    _set_control(emergency_stop=body.on)
    return {"emergency_stop": body.on}


@app.post("/api/projects")
def add_project(body: ProjectIn):
    with _lock:
        project = _stamp_work(
            {
                "client": body.client.strip(),
                "name": body.name.strip(),
                "budget": body.budget.strip(),
                "deadline": body.deadline.strip(),
                "note": body.note.strip(),
            }
        )
        _projects.append(project)
        _save_state()
        return dict(project)


@app.delete("/api/projects/{item_id}")
def delete_project(item_id: int):
    return _delete_work(_projects, item_id)


@app.post("/api/tasks")
def add_task(body: TaskIn):
    with _lock:
        task = _stamp_work(
            {
                "title": body.title.strip(),
                "client": body.client.strip(),
                "due": body.due.strip(),
                "done": False,
            }
        )
        _tasks.append(task)
        _save_state()
        return dict(task)


@app.post("/api/tasks/{item_id}/done")
def finish_task(item_id: int):
    with _lock:
        for task in _tasks:
            if task["id"] == item_id:
                task["done"] = True
                _save_state()
                return dict(task)
    raise HTTPException(status_code=404, detail="Task not found.")


@app.delete("/api/tasks/{item_id}")
def delete_task(item_id: int):
    return _delete_work(_tasks, item_id)


@app.post("/api/meetings")
def add_meeting(body: MeetingIn):
    when = body.when.strip()
    try:
        datetime.fromisoformat(when)
    except ValueError:
        raise HTTPException(status_code=400, detail="Meeting time must be a real date and time.")
    with _lock:
        meeting = _stamp_work(
            {
                "title": body.title.strip(),
                "client": body.client.strip(),
                "when": when,
            }
        )
        _meetings.append(meeting)
        _save_state()
        return dict(meeting)


@app.delete("/api/meetings/{item_id}")
def delete_meeting(item_id: int):
    return _delete_work(_meetings, item_id)


@app.post("/api/documents")
def add_document(body: DocumentIn):
    kind = body.kind.strip().lower()
    if kind not in {"note", "invoice", "project", "pdf"}:
        kind = "note"
    found = _read_document(body.text.strip())
    with _lock:
        document = _stamp_work(
            {
                "title": body.title.strip(),
                "kind": kind,
                "text": body.text.strip(),
                "summary": found["summary"],
                "amounts": found["amounts"],
                "dates": found["dates"],
                "note": found["note"],
            }
        )
        _documents.append(document)
        _save_state()
        return dict(document)


@app.delete("/api/documents/{item_id}")
def delete_document(item_id: int):
    return _delete_work(_documents, item_id)


def _delete_work(bucket: list[dict], item_id: int) -> dict:
    with _lock:
        kept = [item for item in bucket if item.get("id") != item_id]
        if len(kept) == len(bucket):
            raise HTTPException(status_code=404, detail="Item not found.")
        bucket[:] = kept
        _save_state()
    return {"deleted": True}


def _page(title: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ margin: 0; background: #06101c; color: #e7f7ff; font: 16px/1.5 -apple-system, BlinkMacSystemFont, sans-serif; }}
    main {{ max-width: 720px; margin: 0 auto; padding: 32px 20px 64px; }}
    a {{ color: #3ce7ff; }}
    h1 {{ font-size: 28px; margin-bottom: 8px; }}
    h2 {{ font-size: 18px; margin-top: 28px; }}
    p, li {{ color: #d5e8f5; }}
    .muted {{ color: #7e9bb3; }}
    form {{ margin-top: 20px; display: grid; gap: 12px; max-width: 360px; }}
    input, button {{ font: inherit; border-radius: 10px; padding: 12px 14px; }}
    input {{ border: 1px solid #1a3a56; background: #0c1a2e; color: #e7f7ff; }}
    button {{ border: 0; background: #ff6b8a; color: #1a0610; font-weight: 700; }}
  </style>
</head>
<body><main>{body}</main></body>
</html>"""
    return HTMLResponse(html)


def _wipe_account() -> None:
    global _next_id, _next_contact_id, _next_inbox_id, _handled, _settings, _account, _next_work_id
    _log.clear()
    _contacts.clear()
    _inbox.clear()
    _audit.clear()
    _projects.clear()
    _tasks.clear()
    _meetings.clear()
    _documents.clear()
    _next_work_id = 1
    _next_id = 1
    _next_contact_id = 1
    _next_inbox_id = 1
    _handled = 0
    _settings = dict(DEFAULT_SETTINGS)
    _account = None
    _save_state()


def _clean_email(email: str) -> str:
    value = email.strip().lower()
    domain = value.split("@")[-1] if "@" in value else ""
    if "@" not in value or "." not in domain or " " in value:
        raise HTTPException(status_code=400, detail="Email sahi nahi hai.")
    return value


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()


def _public_account() -> dict:
    if _account is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return {"email": _account["email"], "name": _account["name"]}


@app.post("/api/account/register")
def register_account(body: AccountIn):
    global _account
    email = _clean_email(body.email)
    name = body.name.strip() or email.split("@")[0]
    with _lock:
        if _account is not None:
            raise HTTPException(status_code=409, detail="Account pehle se hai. Sign in karo.")
        salt = secrets.token_hex(16)
        _account = {
            "email": email,
            "name": name,
            "salt": salt,
            "password_hash": _hash_password(body.password, salt),
            "token": secrets.token_urlsafe(32),
        }
        _save_state()
        return {"token": _account["token"], "email": email, "name": name}


@app.post("/api/account/login")
def login_account(body: AccountIn):
    email = _clean_email(body.email)
    with _lock:
        if _account is None or _account["email"] != email:
            raise HTTPException(status_code=401, detail="Email ya password galat hai.")
        digest = _hash_password(body.password, _account["salt"])
        if not secrets.compare_digest(digest, _account["password_hash"]):
            raise HTTPException(status_code=401, detail="Email ya password galat hai.")
        _account["token"] = secrets.token_urlsafe(32)
        _save_state()
        return {"token": _account["token"], "email": email, "name": _account["name"]}


@app.get("/api/account/me")
def account_me(request: Request):
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip()
    with _lock:
        if (
            _account is None
            or not token
            or len(token) != len(_account["token"])
            or not secrets.compare_digest(token, _account["token"])
        ):
            raise HTTPException(status_code=401, detail="Sign in required.")
        return _public_account()


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy():
    return _page(
        "JARVIS Privacy Policy",
        """
        <p class="muted">Last updated 17 November 2026</p>
        <h1>Privacy Policy</h1>
        <p>JARVIS is a personal communication assistant that runs on your phone and on the computer where this backend is running. The login stays on this computer. It is not a cloud account, and JARVIS does not sell your data.</p>
        <h2>Data stored on this computer</h2>
        <ul>
          <li>Contact names, handles, channel, and whether they are business, operational, or blocked.</li>
          <li>Message text you log, notification text sent from your Android phone, drafts, and the reply decision.</li>
          <li>The reply log, assistant name, reply mode, and emergency-stop setting.</li>
          <li>Your login email, display name, and a password hash. The password itself is not stored.</li>
        </ul>
        <h2>Phone permissions</h2>
        <p>On Android, notification access can read notification text from apps you allow, such as Gmail or WhatsApp, and send that text to this backend. The microphone is used only when you tap the voice button. iPhone cannot read notifications from other apps.</p>
        <h2>What is not connected</h2>
        <p>Gmail login and the WhatsApp Business API are not connected. JARVIS does not fetch your mailbox or send a WhatsApp message by itself. A draft stays on this computer until you approve it.</p>
        <h2>Optional model</h2>
        <p>If you put an OpenAI key in the backend .env file, the message text can be sent to OpenAI to write a draft. Without that key, drafts are written on this computer and are not sent to a model provider.</p>
        <h2>Delete your data</h2>
        <p>Open the <a href="/delete-account">delete account</a> page and confirm. That removes contacts, inbox messages, drafts, and the reply log from this backend. It does not delete mail or chats stored by Google or WhatsApp, and it does not uninstall the app.</p>
        """,
    )


@app.get("/delete-account", response_class=HTMLResponse)
def delete_account_page():
    return _page(
        "Delete JARVIS data",
        """
        <h1>Delete account</h1>
        <p>This deletes the JARVIS login on this computer, plus contacts, messages, drafts, and the reply log.</p>
        <p>Gmail and WhatsApp keep their own copies. The app stays installed, and you can create a new login.</p>
        <form method="post" action="/api/account/delete">
          <label for="confirm">Type DELETE to confirm</label>
          <input id="confirm" name="confirm" autocomplete="off" required>
          <button type="submit">Delete data on this computer</button>
        </form>
        <p class="muted"><a href="/privacy">Privacy Policy</a></p>
        """,
    )


@app.post("/api/account/delete", response_class=HTMLResponse)
async def delete_account(request: Request):
    raw = (await request.body()).decode()
    confirm = parse_qs(raw).get("confirm", [""])[0]
    if confirm.strip().upper() != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm.")
    with _lock:
        _wipe_account()
    return _page(
        "JARVIS data deleted",
        """
        <h1>Data deleted</h1>
        <p>The login, contacts, inbox messages, drafts, and the reply log on this computer have been removed. Settings are back to the default approval mode.</p>
        <p><a href="/privacy">Privacy Policy</a></p>
        """,
    )
