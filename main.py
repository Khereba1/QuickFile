#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QuickFile - all-in-one Telegram file bot: PDF + images + Office + audio + video (Pyrogram / MTProto)

- QuickPDF features: merge, split, convert, compress, extract, print layout, unlock, rename, CRM archive
- Audio/video: compress, convert, split/cut, tag editing, thumbnails, streamable output, audio extraction
- Large files (up to hosting limits) via MTProto, with one smart queue for heavy jobs only
- Light jobs and small files run instantly with no queue
- All limits live at the top of the file or in environment variables
"""
import asyncio
import base64
import html
import io
import contextvars
import inspect
import json
import logging
import os
import random
import re
import signal
import shutil
import sys
import time
import uuid
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import resource          # POSIX only: used to cap the RAM of the book converter
except ImportError:
    resource = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import httpx
try:
    import pymupdf as fitz      # الاسم الجديد للمكتبة (من غير تحذير deprecated)
except ImportError:
    import fitz  # PyMuPDF القديمة
from PIL import Image, ImageOps, ImageStat

# مكتبات صيغ Office/HTML — استيراد آمن عشان عدم توفر مكتبة واحدة ميكسرش تشغيل البوت كله
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    from pptx import Presentation as PptxPresentation
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

from pyrogram import Client, filters, idle, raw
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import BotCommand, BotCommandScopeChat
from pyrogram.types import InlineKeyboardButton as Btn
from pyrogram.types import InlineKeyboardMarkup as Kb
from pyrogram.types import InputMediaPhoto

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
for _noisy in ("pyrogram", "httpx", "httpcore", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("quickfile")
log = logger

# ══════════════════════════ الإعدادات ══════════════════════════
def _int(name, default):
    """Integer env var. An empty or invalid value (a panel often creates blank variables) falls back to the default
    instead of crashing the bot at start-up."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not a whole number; using the default (%s)", name, raw, default)
        return int(default)


API_ID = _int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# --- Gemini (REST مباشر عبر httpx) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL_PRIMARY = os.getenv("GEMINI_MODEL_PRIMARY", "gemini-3.1-flash-lite")
GEMINI_MODEL_BACKUP = os.getenv("GEMINI_MODEL_BACKUP", "gemini-3.5-flash-lite")
# Separate models for lecture transcription and for summarizing it (default to the models above).
# Tip: if flash-lite transcripts are not good enough, set GEMINI_MODEL_TRANSCRIBE to a flash model.
GEMINI_MODEL_TRANSCRIBE = os.getenv("GEMINI_MODEL_TRANSCRIBE") or GEMINI_MODEL_PRIMARY
GEMINI_MODEL_TRANSCRIBE_BACKUP = os.getenv("GEMINI_MODEL_TRANSCRIBE_BACKUP") or GEMINI_MODEL_BACKUP
GEMINI_MODEL_SUMMARY = os.getenv("GEMINI_MODEL_SUMMARY") or GEMINI_MODEL_PRIMARY
GEMINI_MODEL_SUMMARY_BACKUP = os.getenv("GEMINI_MODEL_SUMMARY_BACKUP") or GEMINI_MODEL_BACKUP
ILOVEPDF_PUBLIC_KEY = os.getenv("ILOVEPDF_PUBLIC_KEY", "")
CLOUDCONVERT_API_KEY = os.getenv("CLOUDCONVERT_API_KEY", "")

_missing_env = [_n for _n, _v in [("API_ID", API_ID), ("API_HASH", API_HASH), ("BOT_TOKEN", BOT_TOKEN),
                                  ("GEMINI_API_KEY", GEMINI_API_KEY), ("ILOVEPDF_PUBLIC_KEY", ILOVEPDF_PUBLIC_KEY),
                                  ("CLOUDCONVERT_API_KEY", CLOUDCONVERT_API_KEY)] if not _v]
if _missing_env:
    logger.warning("Missing env vars (related features disabled): %s", ", ".join(_missing_env))

# 🏢 الأرشفة (CRM Topics) — حط 0 في ARCHIVE_GROUP_ID لو عايز توقفها
GROUP_ID = _int("ARCHIVE_GROUP_ID", 0)      # no hard-coded fallback group: archiving is off unless the env var is set
USERS_DB = "users.json"

# حدود الأحجام
MAX_FILE_MB = _int("MAX_FILE_MB", 1024)        # حد واحد لكل أنواع الملفات، ومفيش حد للمدة
MAX_VIDEO_MB = _int("MAX_VIDEO_MB", MAX_FILE_MB)      # each type can be lowered separately (RAM/disk friendly)
MAX_AUDIO_MB = _int("MAX_AUDIO_MB", MAX_FILE_MB)
MAX_PDF_MB = _int("MAX_PDF_MB", MAX_FILE_MB)
MAX_OFFICE_MB = _int("MAX_OFFICE_MB", MAX_FILE_MB)
MAX_IMAGE_MB = _int("MAX_IMAGE_MB", 50)        # حماية للرام بس (الصور بتتفتح في الذاكرة)
MAX_IMAGES = _int("MAX_IMAGES", 40)
MAX_PARTS = _int("MAX_PARTS", 40)  # أقصى عدد أجزاء في تقسيم صوت/فيديو

# الموارد (الاستضافة الضعيفة)
DISK_TOTAL_MB = _int("DISK_TOTAL_MB", 2048)    # 👈 الرقم الوحيد اللي تغيره لما السيرفر يكبر
WORK_BUDGET_MB = _int("WORK_BUDGET_MB", int(DISK_TOTAL_MB * 0.75))   # المسموح للملفات المؤقتة
USER_MAX_JOBS = _int("USER_MAX_JOBS", 3)       # شغالة + انتظار لكل مستخدم
FFMPEG_THREADS = _int("FFMPEG_THREADS", 1)       # keep 1 on hosts that suspend servers for exceeding their CPU quota
FFMPEG_NICE = _int("FFMPEG_NICE", 10)           # lower priority so the bot itself stays responsive
FFMPEG_CPU_PERCENT = _int("FFMPEG_CPU_PERCENT", 50)   # hard cap: ffmpeg may use at most this % of ONE core (0 = no cap)
QUICK_SLOTS = _int("QUICK_SLOTS", 1)           # كام مهمة خفيفة تشتغل فورًا مع بعض
SESSION_IDLE = _int("SESSION_IDLE_MIN", 30) * 60

# Frame-accurate video cut re-encodes only the kept part. On LIGHT_HOST it is allowed up to this many minutes of kept video.
PRECISE_CUT_MAX_MIN = _int("PRECISE_CUT_MAX_MIN", 10)
CLOUD_SLOTS = _int("CLOUD_SLOTS", 2)                       # max cloud conversions (iLovePDF / CloudConvert / Gemini) at once
MAX_MERGE_FILES = _int("MAX_MERGE_FILES", 10)              # audio/video merge: max files per merge
MERGE_REENCODE_MAX_MIN = _int("MERGE_REENCODE_MAX_MIN", 15)  # LIGHT_HOST only: longest merged video that may be re-encoded
GEMINI_PDF_CHUNK_MB = _int("GEMINI_PDF_CHUNK_MB", 8)       # scanned PDFs above this size (or 60+ pages) are sent to Gemini in page chunks
GEMINI_PDF_CHUNK_PAGES = _int("GEMINI_PDF_CHUNK_PAGES", 10)
GEMINI_MAX_OUTPUT_TOKENS = _int("GEMINI_MAX_OUTPUT_TOKENS", 0)   # 0 = leave the model default
TRANSCRIBE_COMPRESS_ABOVE_MB = _int("TRANSCRIBE_COMPRESS_ABOVE_MB", 10)   # bigger audio is compressed before upload (inline limit ~14 MB raw)
TRANSCRIBE_CHUNK_MIN = _int("TRANSCRIBE_CHUNK_MIN", 15)    # long recordings are transcribed in parts of this length
CHAT_MAX_MSGS = _int("TRANSCRIPT_CHAT_MAX_MSGS", 8)        # longer transcripts/summaries are sent as a file instead of chat messages

# إيه اللي يعتبر "خفيف" ومتخليش له طابور
QUICK_COPY_MB = _int("QUICK_COPY_MB", 60)      # عمليات من غير إعادة ترميز (قص/غلاف/تغيير اسم)
QUICK_AUDIO_MB = _int("QUICK_AUDIO_MB", 12)
QUICK_AUDIO_MIN = _int("QUICK_AUDIO_MIN", 15)
QUICK_VIDEO_MB = _int("QUICK_VIDEO_MB", 15)
QUICK_VIDEO_SEC = _int("QUICK_VIDEO_SEC", 120)

VIDEO_TIMEOUT = _int("VIDEO_TIMEOUT_MIN", 120) * 60
AUDIO_TIMEOUT = _int("AUDIO_TIMEOUT_MIN", 30) * 60
PDF_TIMEOUT = _int("PDF_TIMEOUT_MIN", 10) * 60

# Interactive books (.exe) -> PDF. The converter runs in a separate low-priority process.
MAX_BOOK_MB = _int("MAX_BOOK_MB", min(MAX_FILE_MB, 150))   # biggest .exe accepted
BOOK_SNIFF_MB = _int("BOOK_SNIFF_MB", 24)                   # a .exe whose first N MB have no book table is dropped (stops downloading)
BOOK_TIMEOUT = _int("BOOK_TIMEOUT_MIN", 30) * 60
BOOK_CPU_PERCENT = _int("BOOK_CPU_PERCENT", FFMPEG_CPU_PERCENT)   # same hard CPU cap idea as ffmpeg (0 = no cap)
BOOK_MAX_RAM_MB = _int("BOOK_MAX_RAM_MB", 200)              # RAM cap of the converter process (0 = no cap)

# كام ضعف حجم الملف بنحجز من الديسك لكل عملية (الأصل + الناتج المتوقع)
NEED_FACTOR = {
    "rename": 1.1, "vcover": 1.15, "vcompress": 1.6, "acompress": 1.4, "aconvert": 1.7,
    "vconvert": 2.0, "vstream": 2.0, "astream": 1.5, "ameta": 1.5, "vaudio": 1.3,
    "asplit": 1.3, "vsplit": 1.3, "acut": 1.5, "vcut": 1.6,   # الأجزاء بتتبعت وتتمسح واحد واحد
    "atranscribe": 1.6,
}
DEFAULT_NEED = 2.0
CANCEL_AFTER = 8          # زرار الإلغاء بيظهر بعد كام ثانية من بداية المهمة
MAX_RUNNING = _int("MAX_RUNNING", 1)           # أقصى مهام مش سريعة شغالة مع بعض
MAX_HEAVY = _int("MAX_HEAVY", 1)               # منهم أقصى عدد مهام فيديو تقيلة

MB = 1024 * 1024
BASE_DIR = Path(__file__).resolve().parent

# Interactive books (.exe from Flip PDF / FlipBuilder) -> PDF. Needs flip_exe_to_pdf.py next to this file.
try:
    sys.path.insert(0, str(BASE_DIR))
    from flip_exe_to_pdf import BookError, BookSniffer, peek_book, sniff_file
    BOOK_OK = True
except Exception as _e:                       # the rest of the bot keeps working without it
    BOOK_OK = False
    logger.warning("flip_exe_to_pdf.py not usable (%s): .exe books are disabled", _e)

# ══════════════════════════ Language (Arabic / English) ══════════════════════════
# Logs are always English. Bot messages follow each user's saved language (default: Arabic).
# All Arabic texts live in the AR table near the end of the file (key = the English text).
DEFAULT_LANG = "en"
LANG_FILE = BASE_DIR / "lang.json"
_lang_var = contextvars.ContextVar("lang", default=DEFAULT_LANG)
_AR_RE = re.compile("[\u0600-\u06FF]")


def _load_langs():
    try:
        data = json.loads(LANG_FILE.read_text(encoding="utf-8"))
        return {int(k): v for k, v in data.items() if v in ("ar", "en")}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error("lang.json is unreadable: %s", e)
        return {}


LANGS = _load_langs()


DETECTED = {}          # uid -> language guessed from the Telegram app language (memory only)


def note_lang(user):
    """Remember the language of the user's Telegram app (used only until they pick one with /language)."""
    code = (getattr(user, "language_code", None) or "").lower()
    if code:
        DETECTED[user.id] = "ar" if code.startswith("ar") else "en"


def get_lang(uid):
    return LANGS.get(uid) or DETECTED.get(uid) or DEFAULT_LANG


def set_lang(uid, user=None):
    """Bind the current task's language to this user (call at the start of every handler/job)."""
    if user is not None:
        note_lang(user)
    _lang_var.set(get_lang(uid))


def save_lang(uid, lang):
    LANGS[uid] = lang
    try:
        tmp = LANG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({str(k): v for k, v in LANGS.items()}), encoding="utf-8")
        tmp.replace(LANG_FILE)
    except Exception as e:
        logger.error("Could not save lang.json: %s", e)


# Small-caps body text for English mode (lowercase -> ᴀʙᴄ). Skips HTML tags, <code> blocks, {placeholders} and /commands.
_SC_FROM = "abcdefghijklmnopqrstuvwxyz"
_SC_TO = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
_SC_TABLE = str.maketrans({**dict(zip(_SC_FROM, _SC_TO)), **dict(zip(_SC_FROM.upper(), _SC_TO))})
_UNSC_TABLE = str.maketrans({t: f for f, t in zip(_SC_FROM, _SC_TO) if f != "x"})
_SC_KEEP = re.compile(r"(<code>.*?</code>|<[^>]+>|\{[^{}]*\}|/[A-Za-z_]+)", re.S)


def smallcaps(text):
    parts = _SC_KEEP.split(text)
    return "".join(p if i % 2 else p.translate(_SC_TABLE) for i, p in enumerate(parts))


def unsc(text):
    """Back to plain letters (used before applying the bold heading/button style)."""
    return text.translate(_UNSC_TABLE)


def tr(tpl, *args):
    """Translate a message template into the current language, then fill {0}, {1}... placeholders."""
    if _lang_var.get() == "ar":
        tpl = AR.get(tpl, tpl)
    else:
        tpl = smallcaps(tpl)
    return tpl.format(*args) if args else tpl


class TrMap(dict):
    """dict whose values are translated on access (for module-level label tables)."""
    def __getitem__(self, k):
        return tr(dict.__getitem__(self, k))

    def get(self, k, default=None):
        return tr(dict.get(self, k, default)) if k in self else default

    def en(self, k):
        """Raw English value (used for archive/admin texts, which never follow the user's language)."""
        return dict.__getitem__(self, k)

WORK_DIR = BASE_DIR / "work"
COVERS_DIR = BASE_DIR / "covers"      # permanent (work/ is wiped on every start): users' default audio covers

LEVELS = ("light", "medium", "strong")
LEVEL_AR = TrMap({"light": "Light", "medium": "Medium", "strong": "Strong"})

# LIGHT_HOST=1 hides and blocks the heavy tools (video re-encoding, local PDF image compression ...) so the bot
# can run on weak/shared hosts. Set it to 0 (or remove it) on a stronger server to get everything back.
LIGHT_HOST = os.getenv("LIGHT_HOST", "0").strip().lower() in ("1", "true", "yes", "on")
PDF_IMAGES_MAX_PAGES = 30 if LIGHT_HOST else 100
# Callbacks that start heavy work (blocked + never shown when LIGHT_HOST is on)
HEAVY_CALLBACKS = ("vi_compress", "vmode:", "vcp:", "vcr:", "vcn:webm", "compress_imgs", "pdfimg:")   # vcut_precise is allowed (capped by PRECISE_CUT_MAX_MIN)


def is_heavy_callback(data):
    return LIGHT_HOST and any(data == h or (h.endswith(":") and data.startswith(h)) for h in HEAVY_CALLBACKS)


# Compression modes: "fast" trades some quality/fps for speed, "quality" is slower but keeps more detail
VIDEO_DEFAULT_MODE = os.getenv("VIDEO_DEFAULT_MODE", "fast")          # fast | quality
VIDEO_FAST_PRESET = os.getenv("VIDEO_FAST_PRESET", "ultrafast")
VIDEO_QUALITY_PRESET = os.getenv("VIDEO_QUALITY_PRESET", "fast")
VIDEO_FAST_CRF_ADD = _int("VIDEO_FAST_CRF_ADD", 3)    # ultrafast compresses less efficiently; a higher CRF keeps the size similar
VIDEO_FAST_FPS = {"light": 30, "medium": 24, "strong": 15}   # fps caps in fast mode (only applied if the source has more)
VIDEO_LEVELS = {
    "light":  {"h": 1080, "crf": 24, "ab": "96k", "ac": 2},
    "medium": {"h": 720,  "crf": 27, "ab": "64k", "ac": 2},
    "strong": {"h": 480,  "crf": 30, "ab": "48k", "ac": 1},
}
# ضغط الصوت (كلام مش موسيقى، فـ mono كفاية): (bitrate, sample-rate)
AUDIO_LEVELS = {
    "opus": {"light": ("48k", 24000), "medium": ("32k", 16000), "strong": ("24k", 16000)},
    "m4a":  {"light": ("64k", 32000), "medium": ("40k", 24000), "strong": ("32k", 22050)},
}
# ضغط صور الـ PDF محليًا: (أقصى بُعد بالبكسل, جودة JPEG)
PDF_LEVELS = {"light": (2000, 78), "medium": (1500, 62), "strong": (1100, 45)}
# جودة التحويل بين صيغ الصوت
CONV_BITRATE = {
    "mp3": {"hi": "192k", "mid": "128k", "lo": "64k"},
    "m4a": {"hi": "160k", "mid": "112k", "lo": "64k"},
    "opus": {"hi": "96k", "mid": "64k", "lo": "32k"},
}
QUALITY_AR = TrMap({"hi": "High", "mid": "Medium", "lo": "Low"})

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".3gp", ".ts", ".mpeg", ".mpg"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".amr", ".wma", ".oga"}
TAGGABLE_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".oga", ".wav"}
EMBED_COVER_EXTS = {".mp3", ".m4a", ".flac"}


def find_ffmpeg():
    p = os.getenv("FFMPEG_PATH")
    if p and Path(p).exists():
        return p
    local = BASE_DIR / "bin" / "ffmpeg"
    if local.exists():
        return str(local)
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = find_ffmpeg()

# ══════════════════════════ أدوات صغيرة ══════════════════════════
H = html.escape


def fmt_size(n):
    n = max(0, n or 0)
    if n >= 1024 * MB:
        return f"{n / (1024 * MB):.2f} GB"
    if n >= MB:
        return f"{n / MB:.1f} MB"
    return f"{n / 1024:.0f} KB"


def pulse_bar(width=10, block=3):
    """Indeterminate bar: a block bouncing left/right (used when there is no real percentage)."""
    span = width - block
    i = int(time.time() / 1.2) % (span * 2)
    pos = i if i <= span else 2 * span - i
    return "▱" * pos + "▰" * block + "▱" * (width - block - pos)


def bar(cur, total, width=10):
    if not total:
        return ""
    frac = max(0.0, min(1.0, cur / total))
    done = int(frac * width)
    return f"{'▰' * done}{'▱' * (width - done)} {int(frac * 100)}%"


# ---- stylised text (Unicode "sans-serif bold") - for titles and buttons only, never for names/numbers
_BOLD = {}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _BOLD[_c] = chr(0x1D5D4 + _i)
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _BOLD[_c] = chr(0x1D5EE + _i)
for _i, _c in enumerate("0123456789"):
    _BOLD[_c] = chr(0x1D7EC + _i)


def sc(text):
    return "".join(_BOLD.get(c, c) for c in text)


def hd(text):
    """Heading style. English: UPPERCASE bold-sans letters. Arabic: normal bold. Plain text only."""
    if _AR_RE.search(text):
        return f"<b>{text}</b>"
    return sc(unsc(text).upper())


def L(icon, title):
    """Status line for work in progress."""
    return f"{icon} {hd(title)}…"


def okc(title):
    """Caption headline for finished work (light small caps in English, bold in Arabic)."""
    if _AR_RE.search(title):
        return f"✅ <b>{title}</b>"
    return f"✅ {smallcaps(title)}"


def progress_text(label, cur, total, word="received"):
    if total and total >= cur:
        return f"{label}\n{bar(cur, total)}\n<code>{tr('{0} of {1}', fmt_size(cur), fmt_size(total))}</code>"
    return f"{label}\n{pulse_bar()}\n<code>{tr('{0} received' if word == 'received' else '{0} sent', fmt_size(cur))}</code>"


def progress_count(label, cur, total):
    return f"{label}\n{bar(cur, total)}\n<code>{tr('{0} of {1}', cur, total)}</code>"


def fmt_time(sec):
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_time(text):
    """'90' / '1:30' / '01:02:03' / '1.5' (ثواني) -> ثواني (float) أو None."""
    text = text.strip().replace("٫", ".").replace("،", ",")
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_time_ranges(text, total=0):
    """'0:00-10:30, 12:00-15:00' أو '10:00-' (لحد النهاية) -> [(start, end|None)]."""
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).replace("،", ",").replace("–", "-").replace("—", "-")
    out = []
    for chunk in re.split(r"[,\n؛;]+", text):
        chunk = chunk.strip()
        if not chunk or "-" not in chunk:
            continue
        a, _, b = chunk.partition("-")
        start = parse_time(a) if a.strip() else 0.0
        end = parse_time(b) if b.strip() else None
        if start is None or (b.strip() and end is None):
            return None
        if end is not None and end <= start:
            return None
        if total and start >= total:
            continue
        if total and end is not None and end > total:
            end = None
        out.append((start, end))
    return out or None


def safe_name_text(new_name, ext=""):
    s = "".join(c for c in new_name if c.isalnum() or c in " _-().").strip() or "file"
    if ext and s.lower().endswith(ext.lower()):
        s = s[: -len(ext)]
    return s[:120].strip() or "file"


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("Could not delete %s: %s", path, e)


_bg_tasks = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task

def format_page_ranges(pages_zero_based):
    """[0,1,2,4,7,8] -> '1-3, 5, 8-9' (أرقام الصفحات بتبدأ من 1)."""
    if not pages_zero_based:
        return ""
    nums = sorted(p + 1 for p in pages_zero_based)
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(parts)

# ---------------------------------------------------------
# 🛠️ كود التواصل اللامتزامن مع iLovePDF
# ---------------------------------------------------------
async def _stream_to_file(client, url, dest, headers=None):
    """Download straight to disk in small chunks (never holds the whole result in RAM)."""
    try:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:300].decode("utf-8", "ignore")
                raise Exception(f"Download Error: {body}")
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
    except BaseException:
        _safe_remove(dest)          # never leave a half-written file behind
        raise


class ILovePDFDirectAsync:
    def __init__(self, public_key):
        self.public_key = public_key
        self.api_url = "https://api.ilovepdf.com/v1"
        self.token = None

    async def auth(self):
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.api_url}/auth", json={"public_key": self.public_key})
            if resp.status_code == 200: self.token = resp.json()['token']
            else: raise Exception(f"Auth Error: {resp.text}")

    async def process(self, tool, file_path, output_path, **kwargs):
        if not self.token: await self.auth()
        headers = {"Authorization": f"Bearer {self.token}"}

        async with httpx.AsyncClient(timeout=600) as client:
            start_resp = await client.get(f"{self.api_url}/start/{tool}", headers=headers)
            if start_resp.status_code != 200: raise Exception(f"Start Error: {start_resp.text}")
                
            start_data = start_resp.json()
            server_url = f"https://{start_data['server']}/v1"
            task_id = start_data['task']

            with open(file_path, 'rb') as f:
                upload_resp = await client.post(
                    f"{server_url}/upload", headers=headers, data={"task": task_id}, files={"file": f}
                )
            if upload_resp.status_code != 200: raise Exception(f"Upload Error: {upload_resp.text}")
                
            server_filename = upload_resp.json()['server_filename']

            process_data = {
                "task": task_id, "tool": tool,
                "files": [{"server_filename": server_filename, "filename": os.path.basename(file_path)}]
            }
            process_data.update(kwargs)
            
            proc_resp = await client.post(f"{server_url}/process", headers=headers, json=process_data)
            if proc_resp.status_code != 200:
                err_msg = proc_resp.json().get('error', {}).get('message', proc_resp.text) if "error" in proc_resp.text else proc_resp.text
                raise Exception(f"iLovePDF Error: {err_msg}")

            await _stream_to_file(client, f"{server_url}/download/{task_id}", output_path, headers=headers)
            return True

async def process_ilovepdf_async(tool, pdf_path, output_path, **kwargs):
    try:
        api = ILovePDFDirectAsync(ILOVEPDF_PUBLIC_KEY)
        await api.process(tool, pdf_path, output_path, **kwargs)
        return True, "Success"
    except Exception as e:
        logger.error(f"ILovePDF API Error: {e}")
        return False, str(e)

class CloudConvertAsync:
    """تكامل مباشر مع CloudConvert (Jobs API) — بيستخدم endpoint الانتظار الرسمي
    (/jobs/{id}/wait) بدل عمل polling يدوي."""
    def __init__(self, api_key):
        self.api_key = api_key
        self.api_url = "https://api.cloudconvert.com/v2"

    async def convert(self, file_path, output_path, output_format, input_format=None):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        tasks = {
            "import-1": {"operation": "import/upload"},
            "convert-1": {"operation": "convert", "input": "import-1", "output_format": output_format},
            "export-1": {"operation": "export/url", "input": "convert-1"}
        }
        if input_format:
            tasks["convert-1"]["input_format"] = input_format

        async with httpx.AsyncClient(timeout=300) as client:
            job_resp = await client.post(f"{self.api_url}/jobs", headers=headers, json={"tasks": tasks})
            if job_resp.status_code not in (200, 201):
                raise Exception(f"Job Error: {job_resp.text}")
            job_data = job_resp.json()['data']
            job_id = job_data['id']

            import_task = next(t for t in job_data['tasks'] if t['name'] == 'import-1')
            upload_form = import_task['result']['form']

            with open(file_path, 'rb') as f:
                upload_resp = await client.post(
                    upload_form['url'], data=upload_form['parameters'], files={"file": f}
                )
            if upload_resp.status_code not in (200, 201):
                raise Exception(f"Upload Error: {upload_resp.text}")

            wait_resp = await client.get(f"{self.api_url}/jobs/{job_id}/wait", headers=headers)
            if wait_resp.status_code != 200:
                raise Exception(f"Wait Error: {wait_resp.text}")
            job_result = wait_resp.json()['data']

            if job_result['status'] != 'finished':
                failed = next((t for t in job_result['tasks'] if t['status'] == 'error'), None)
                err = failed.get('message', 'Unknown error') if failed else 'Unknown error'
                raise Exception(err)

            export_task = next(t for t in job_result['tasks'] if t['name'] == 'export-1')
            file_url = export_task['result']['files'][0]['url']

            await _stream_to_file(client, file_url, output_path)
            return True

async def process_cloudconvert_async(file_path, output_path, output_format, input_format=None):
    try:
        api = CloudConvertAsync(CLOUDCONVERT_API_KEY)
        await api.convert(file_path, output_path, output_format, input_format=input_format)
        return True, "Success"
    except Exception as e:
        logger.error(f"CloudConvert API Error: {e}")
        return False, str(e)

def is_quota_error(msg):
    """بيحاول يكتشف لو الفشل بسبب نفاد الكوتة المجانية اليومية، عشان نديله رسالة مفهومة."""
    if not msg: return False
    m = msg.lower()
    return any(k in m for k in ["credit", "quota", "limit exceeded", "402", "429", "insufficient"])

# ---------------------------------------------------------
# 🛠️ دوال الذكاء الاصطناعي ومعالجة النصوص
# ---------------------------------------------------------
def sync_check_and_extract(pdf_path):
    doc = fitz.open(pdf_path)
    text_length = 0
    for i in range(min(3, len(doc))):
        text_length += len(doc[i].get_text().strip())
    if text_length < 50:
        doc.close()
        return None, True 
    pages_text = [page.get_text() + "\n" for page in doc]
    doc.close()
    return pages_text, False

def sync_encode_b64(pdf_path):
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

async def call_gemini_engine(prompt, pdf_base64=None, images=None, force_json=False, use_backup=False,
                             audio=None, models=None, timeout=180):
    """طبقة موحدة للاتصال بـ Gemini عبر REST مباشرة (بدون أي SDK) — نفس مبدأ Nerdly:
    تحويل تلقائي للموديل الاحتياطي لو الأساسي فشل (خطأ شبكة أو رد غير ناجح).
    audio = (mime_type, base64) for inline audio; models = (primary, backup) overrides the default model pair."""
    primary, backup = models or (GEMINI_MODEL_PRIMARY, GEMINI_MODEL_BACKUP)
    current_model = backup if use_backup else primary
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={GEMINI_API_KEY}"

    parts = [{"text": prompt}]
    if pdf_base64:
        parts.append({"inline_data": {"mime_type": "application/pdf", "data": pdf_base64}})
    if audio:
        parts.append({"inline_data": {"mime_type": audio[0], "data": audio[1]}})
    if images:
        for img_bytes in images:
            img_b64 = base64.b64encode(img_bytes).decode('utf-8')
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})

    payload = {"contents": [{"parts": parts}]}
    gen_cfg = {}
    if force_json:
        gen_cfg["response_mime_type"] = "application/json"
    if GEMINI_MAX_OUTPUT_TOKENS > 0:
        gen_cfg["maxOutputTokens"] = GEMINI_MAX_OUTPUT_TOKENS
    if gen_cfg:
        payload["generationConfig"] = gen_cfg

    def _retry():
        return call_gemini_engine(prompt, pdf_base64=pdf_base64, images=images, force_json=force_json,
                                  use_backup=True, audio=audio, models=models, timeout=timeout)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)

        if response.status_code == 200:
            result = response.json()
            cand = result['candidates'][0]
            if cand.get("finishReason") == "MAX_TOKENS":
                logger.warning("Gemini [%s] stopped at the output token limit (the text may be cut)", current_model)
            text = "".join(p.get("text", "") for p in cand['content']['parts'] if not p.get("thought"))
            if not text.strip():
                raise ValueError("empty response")
            return text
        else:
            logger.error(f"Gemini [{current_model}] failed with code {response.status_code}: {response.text[:200]}")
            if not use_backup:
                return await _retry()
            return None
    except Exception as e:
        logger.error(f"Gemini [{current_model}] exception: {e}")
        if not use_backup:
            return await _retry()
        return None

OCR_PROMPT = ("Extract all the written or visible text in this file, in page order, in its original language. "
              "Output the text only, with no comments, summary or additions.")


async def call_gemini_ocr_async(pdf_path, job=None):
    """يبعت الـ PDF لـ Gemini يقرأه ويطلع النص (بما فيه الصفحات الممسوحة ضوئيًا) — بدون أي محرك OCR محلي.
    Small files go in one request. Big ones (over GEMINI_PDF_CHUNK_MB or 60+ pages) are sent in page chunks:
    that keeps RAM low, stays under Gemini's inline size limit and avoids a cut-off answer."""
    try:
        size_mb = os.path.getsize(pdf_path) / MB
        total = await asyncio.to_thread(pdf_page_count, pdf_path)
        if size_mb <= GEMINI_PDF_CHUNK_MB and total <= 60:
            encoded_string = await asyncio.to_thread(sync_encode_b64, pdf_path)
            result = await call_gemini_engine(OCR_PROMPT, pdf_base64=encoded_string)
            return [result] if result else None
        step = max(1, GEMINI_PDF_CHUNK_PAGES)
        texts, got_any = [], False
        for i in range(0, total, step):
            if job is not None:
                if job.cancelled:
                    raise JobCancelled
                await edit(job, progress_count(L("🧠", tr('Reading pages')), min(i + step, total), total))
            rng = list(range(i, min(i + step, total)))
            chunk = f"{pdf_path}.ocr{i}.pdf"
            try:
                await asyncio.to_thread(sync_split_pdf, pdf_path, chunk, rng)
                encoded_string = await asyncio.to_thread(sync_encode_b64, chunk)
            finally:
                _safe_remove(chunk)
            res = await call_gemini_engine(OCR_PROMPT, pdf_base64=encoded_string)
            encoded_string = None
            if res:
                texts.append(res.strip())
                got_any = True
            else:
                texts.append(f"[⚠ pages {rng[0] + 1}-{rng[-1] + 1} could not be read]")
        return ["\n\n".join(texts) + "\n"] if got_any else None
    except JobCancelled:
        raise
    except Exception as e:
        logger.error(f"Gemini OCR Exception: {e}")
        return None

async def extract_text_smart_async(pdf_path, job=None):
    pages, is_scanned = await asyncio.to_thread(sync_check_and_extract, pdf_path)
    if is_scanned:
        pages = await call_gemini_ocr_async(pdf_path, job)
        return pages, True
    return pages, False

async def call_gemini_bmcq_async(action, text=None, pdf_base64=None):
    """يستخرج/يولّد أسئلة BMCQ بصيغة JSON من نص جاهز أو من PDF مباشرة (نفس المبدأ للاتنين)."""
    if action == "bmcq_strict":
        prompt = (
            "Extract ONLY explicitly written questions and their options from the provided text/document.\n"
            "IMPORTANT: Remove all numbering and letters (e.g., 1., 2., A), B), a., b.) from both the questions and the options. Start the text directly.\n"
            "CRITICAL: Fix any broken words caused by OCR formatting (e.g., fix \"cour se\" to \"course\").\n"
            "CRITICAL: DO NOT include newline characters (\\n) INSIDE the question or option strings. Each string must be continuous.\n"
            "CRITICAL: DO NOT generate, invent, or create any new questions if the text is just an explanation, lecture, or notes.\n"
            "If no clear questions are found in the text, you MUST return exactly an empty JSON list [].\n"
            "Return ONLY a JSON list: [{\"question\": \"...\", \"options\": [\"...\"], \"correct_index\": 0}]"
        )
    else:
        prompt = (
            "You are an expert academic question generator. Your task is to read the provided educational text/document and generate "
            "challenging multiple-choice questions (MCQs) to test the reader's understanding. Ensure questions are highly accurate and suitable for university-level evaluation.\n\n"
            "CRITICAL RULE 1 (ANSWER PATTERN): Look for a consistent pattern marking the correct answer (e.g., an asterisk '*' strictly at the beginning of an option). "
            "If this clear pattern exists, you MUST set the 'correct_index' to match it perfectly. Otherwise, rely on your own expertise to determine the correct answer.\n"
            "CRITICAL RULE 2 (TRUE/FALSE): 'True or False' questions ARE fully supported. Create exactly two options: [\"True\", \"False\"] and set the 'correct_index' appropriately.\n"
            "CRITICAL RULE 3 (ANTI-LAZINESS): You MUST comprehensively process the ENTIRE document, all pages and sections evenly. Do not stop early.\n"
            "CRITICAL FORMATTING RULES: DO NOT number the questions or choices. OCR CLEANUP: fix any broken words. DO NOT include newline characters (\\n) INSIDE the question or option strings.\n"
            "You MUST return your output strictly as a JSON list. NO conversational text, NO markdown outside the JSON.\n"
            "Return ONLY a JSON list: [{\"question\": \"...\", \"options\": [\"...\"], \"correct_index\": 0}]"
        )

    if text:
        prompt += f"\n\nText to process:\n\n{text}"
        raw = await call_gemini_engine(prompt, force_json=True)
    else:
        raw = await call_gemini_engine(prompt, pdf_base64=pdf_base64, force_json=True)

    if not raw:
        return None

    result_text = raw.strip()
    if result_text.startswith("```json"):
        result_text = result_text[7:-3].strip()
    elif result_text.startswith("```"):
        result_text = result_text[3:-3].strip()

    try:
        qa_list = json.loads(result_text)
    except json.JSONDecodeError:
        logger.error("Gemini did not return valid JSON.")
        return None

    if not qa_list:
        return "NO_QUESTIONS"

    bmcq_output = ""
    for qa in qa_list:
        q_text = str(qa.get("question", ""))
        q_text = " ".join(q_text.split())
        bmcq_output += q_text + "\n"

        options = qa.get("options", [])
        correct_idx = qa.get("correct_index", -1)

        for idx, opt in enumerate(options):
            opt_text = str(opt)
            opt_text = " ".join(opt_text.split())
            if opt_text.startswith("*"):
                opt_text = opt_text[1:].strip()
            prefix = "*" if idx == correct_idx else ""
            bmcq_output += f"{prefix}{opt_text}\n"

        bmcq_output += "\n"

    return bmcq_output.strip()



# ══════════════════════════ دوال PDF/Office (منقولة من QuickPDF كما هي) ══════════════════════════
def _save_optimized(doc, out_path):
    """حفظ موحّد بـ garbage collection + deflate عشان الناتج ميبقاش أكبر من الأصل
    (نفس المنطق المستخدم أصلاً في sync_compress_local)."""
    try: doc.save(out_path, garbage=4, deflate=True)
    except: doc.save(out_path, deflate=True)

def sync_compress_local(pdf_path, out_path):
    doc = fitz.open(pdf_path)
    _save_optimized(doc, out_path)
    doc.close()

def sync_split_pdf(pdf_path, out_path, pages_to_keep):
    doc = fitz.open(pdf_path)
    new_doc = fitz.open()
    for p in pages_to_keep: new_doc.insert_pdf(doc, from_page=p, to_page=p)
    _save_optimized(new_doc, out_path)
    new_doc.close()
    doc.close()

def sync_merge_pdfs(merge_list, out_path):
    merged_doc = fitz.open()
    for pdf_file in merge_list:
        doc = fitz.open(pdf_file)
        merged_doc.insert_pdf(doc)
        doc.close()
    _save_optimized(merged_doc, out_path)
    merged_doc.close()

def sync_unlock_pdf(pdf_path, out_path, password=None):
    """يفك أي قيود (طباعة/تعديل) دايمًا، ويحتاج كلمة سر بس لو الملف مقفول فتح فعليًا."""
    doc = fitz.open(pdf_path)
    if doc.needs_pass:
        if not password:
            doc.close()
            return "NEEDS_PASSWORD"
        if not doc.authenticate(password):
            doc.close()
            return "WRONG_PASSWORD"
    doc.save(out_path, encryption=fitz.PDF_ENCRYPT_NONE)
    doc.close()
    return "OK"

def sync_nup(pdf_path, out_path, layout, group_size):
    """تجميع عدة صفحات في ورقة واحدة. مقاس الورقة الناتجة يتحدد حسب طبيعة الدمج:
    - فوق بعض / جمب بعض: بدون أي تصغير (كل صفحة بحجمها الطبيعي، هامش شبه معدوم).
    - 4 في الورقة: نفس مقاس الصفحة الأصلية (تصغير حقيقي لتوفير الورق عند الطباعة)."""
    src = fitz.open(pdf_path)
    total = len(src)
    w, h = src[0].rect.width, src[0].rect.height

    if layout == "stack":    # صفحتين فوق بعض: العرض ثابت، الارتفاع يتضاعف
        out_w, out_h = w, h * 2
        cells = [fitz.Rect(0, 0, w, h), fitz.Rect(0, h, w, 2 * h)]
    elif layout == "side":   # صفحتين جمب بعض: الارتفاع ثابت، العرض يتضاعف
        out_w, out_h = w * 2, h
        cells = [fitz.Rect(0, 0, w, h), fitz.Rect(w, 0, 2 * w, h)]
    else:                    # 4 صفحات في الورقة: نفس مقاس الصفحة الأصلية (تصغير حقيقي)
        out_w, out_h = w, h
        cells = [fitz.Rect(0, 0, w / 2, h / 2), fitz.Rect(w / 2, 0, w, h / 2),
                  fitz.Rect(0, h / 2, w / 2, h), fitz.Rect(w / 2, h / 2, w, h)]

    out = fitz.open()
    for i in range(0, total, group_size):
        new_page = out.new_page(width=out_w, height=out_h)
        for j in range(group_size):
            idx = i + j
            if idx < total:
                new_page.show_pdf_page(cells[j], src, idx)
    _save_optimized(out, out_path)
    out.close()
    src.close()

def sync_images_to_pdf(image_list, out_path):
    """Images (in order) -> one PDF, with only ONE image in memory at a time.
    Plain JPEGs are embedded as-is by PyMuPDF (no decode, no re-compression); anything else (PNG, WebP, rotated
    EXIF, CMYK ...) is normalized to a temporary JPEG one by one. Each page has the image's aspect ratio
    (long side = 842 pt) and the image fills it."""
    if not image_list:
        raise ValueError("No images to convert")
    out = fitz.open()
    try:
        for idx, p in enumerate(image_list):
            src = str(p)
            with Image.open(p) as im:
                w, h = im.size
                if w * h > 80_000_000:
                    raise ValueError("Image too large")
                fmt, mode = (im.format or "").upper(), im.mode
                try:
                    orient = im.getexif().get(0x0112, 1)
                except Exception:
                    orient = 1
            if not (fmt == "JPEG" and mode in ("RGB", "L") and orient in (None, 0, 1)):
                tmp = f"{src}.norm{idx}.jpg"
                with Image.open(p) as im:
                    im = ImageOps.exif_transpose(im)
                    if im.mode in ("RGBA", "LA", "P"):
                        im = im.convert("RGBA")
                        bg = Image.new("RGB", im.size, (255, 255, 255))
                        bg.paste(im, mask=im.getchannel("A"))
                        im = bg
                    else:
                        im = im.convert("RGB")
                    w, h = im.size
                    im.save(tmp, "JPEG", quality=92)
                src = tmp
            scale = 842.0 / max(w, h)
            page = out.new_page(width=max(1.0, w * scale), height=max(1.0, h * scale))
            page.insert_image(page.rect, filename=src)
        _save_optimized(out, out_path)
    finally:
        out.close()

def detect_office_type(document):
    """يحدد نوع الملف (docx/pptx/ppt/html) من الامتداد أو الـ mime_type. يرجّع None لو مش مدعوم."""
    if not document:
        return None
    filename = (document.file_name or "").lower()
    mime = document.mime_type or ""
    if filename.endswith(".docx") or "wordprocessingml" in mime:
        return "docx"
    if filename.endswith(".pptx") or "presentationml" in mime:
        return "pptx"
    if filename.endswith(".doc") or mime == "application/msword":
        return "doc"  # صيغة قديمة: تحويل PDF وتغيير اسم واستخراج نص (عبر تحويل داخلي)
    if filename.endswith(".ppt") or mime == "application/vnd.ms-powerpoint":
        return "ppt"  # صيغة قديمة: تحويل PDF وتغيير اسم واستخراج نص (عبر تحويل داخلي)
    if filename.endswith(".html") or filename.endswith(".htm") or mime == "text/html":
        return "html"
    return None

def sync_docx_to_text(path):
    doc = DocxDocument(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)

def sync_pptx_to_text(path):
    prs = PptxPresentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"--- Slide {i} ---")
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
    return "\n".join(parts)

def sync_html_to_text(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')
    return soup.get_text(separator="\n")

def sync_office_to_text(path, file_type):
    if file_type == "docx":
        return sync_docx_to_text(path)
    elif file_type == "pptx":
        return sync_pptx_to_text(path)
    elif file_type == "html":
        return sync_html_to_text(path)
    return None

def sync_count_pptx_slides(path):
    try:
        return len(PptxPresentation(path).slides)
    except Exception:
        return None

def create_thumbnail(pdf_path, thumb_path):
    try:
        doc = fitz.open(pdf_path)
        page = doc.load_page(0)
        pix = page.get_pixmap()
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img.thumbnail((320, 320))
        img.save(thumb_path, "JPEG")
        doc.close()
        return True
    except Exception: return False

def create_image_thumbnail(image_path, thumb_path):
    try:
        img = Image.open(image_path).convert('RGB')
        img.thumbnail((320, 320))
        img.save(thumb_path, "JPEG")
        return True
    except Exception: return False

def extract_pptx_thumbnail(pptx_path, thumb_path):
    """يحاول يستخرج الصورة المصغرة المدمجة جوه ملف الـ PowerPoint نفسه (لو موجودة) —
    من غير أي محرك عرض. مش كل الملفات فيها الصورة دي (بيعتمد على إعدادات حفظ الملف الأصلي)."""
    try:
        import zipfile
        with zipfile.ZipFile(pptx_path) as z:
            candidates = [n for n in z.namelist() if n.startswith("docProps/thumbnail")]
            if not candidates:
                return False
            with z.open(candidates[0]) as f:
                img = Image.open(f).convert('RGB')
                img.thumbnail((320, 320))
                img.save(thumb_path, "JPEG")
        return True
    except Exception:
        return False

def parse_page_ranges(range_text, total_pages):
    pages_to_keep = set()
    parts = range_text.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            try:
                start, end = map(int, part.split('-'))
                pages_to_keep.update(range(start - 1, end))
            except ValueError: pass
        elif part.isdigit():
            pages_to_keep.add(int(part) - 1)
    return sorted([p for p in pages_to_keep if 0 <= p < total_pages])


# ══════════════════════════ بنية الجلسات والمهام ══════════════════════════
app: Optional[Client] = None
COVER_BY_ID = False          # هل المكتبة بتدعم video_cover (تغيير الصورة المصغرة من غير إعادة رفع)؟


class JobCancelled(Exception):
    pass


class JobError(Exception):
    """رسالة مفهومة للمستخدم (HTML آمن — اعمل H() لأي نص جاي من برة)."""


@dataclass(eq=False)
class Session:
    uid: int
    chat_id: int
    kind: str                      # pdf | image | office | audio | video
    dir: Path
    user: object = None
    name: str = ""
    size: int = 0
    duration: int = 0
    file_id: str = ""
    orig: str = ""                 # video | audio | voice | document | animation | video_note
    ftype: str = ""                # نوع Office (docx/pptx/doc/ppt/html)
    width: int = 0
    height: int = 0
    path: Optional[Path] = None
    thumb: Optional[Path] = None
    state: Optional[str] = None
    data: dict = field(default_factory=dict)
    last: float = field(default_factory=time.time)


@dataclass(eq=False)
class Out:
    path: Path
    kind: str                      # pdf | doc | text | image | audio | voice | video
    name: str
    caption: str = ""
    thumb: Optional[Path] = None
    duration: int = 0
    width: int = 0
    height: int = 0
    title: Optional[str] = None
    performer: Optional[str] = None
    op: str = ""                   # اسم العملية للأرشيف


@dataclass(eq=False)
class Job:
    id: str
    uid: int
    chat_id: int
    s: Session
    op: str
    params: dict
    queue: str                     # quick | light | heavy
    msg: object
    user: object
    cancelled: bool = False
    proc: object = None
    last_edit: float = 0.0
    last_text: str = ""
    followup: Optional[tuple] = None
    ok: bool = False
    t0: float = field(default_factory=time.time)
    last_pos: int = 0
    base_text: str = ""            # last real status text (the pulse adds elapsed time/bar below it)
    finished: bool = False


sessions = {}
jobs = {}
user_jobs = defaultdict(int)
reserved = {}
waiting = deque()       # طابور واحد بس للمهام المش سريعة
running = set()
QUICK_SEM = None
CLOUD_SEM = None
_locks = {}


def ulock(uid):
    lk = _locks.get(uid)
    if lk is None:
        lk = _locks[uid] = asyncio.Lock()
    return lk


def kb(*rows):
    return Kb([[Btn(t if _AR_RE.search(t) else smallcaps(t), callback_data=d) for t, d in row] for row in rows])


def new_job_id():
    return uuid.uuid4().hex[:8]


# ══════════════════════════ المساحة ══════════════════════════
def work_used():
    total = 0
    for root, _, files in os.walk(WORK_DIR):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def free_budget():
    return WORK_BUDGET_MB * MB - max(sum(reserved.values()), work_used())


def disk_need(s, op_name=None):
    factor = NEED_FACTOR.get(op_name, DEFAULT_NEED)
    return int(max(s.size * factor, 20 * MB))


async def wait_for_disk(job, need):
    if need > WORK_BUDGET_MB * MB:
        raise JobError(tr('This file is too large for the server right now. Try a smaller one.'))
    shown = False
    while free_budget() < need:
        if job.cancelled:
            raise JobCancelled
        if not shown:
            shown = True
            await edit(job, tr('{0}\nWaiting for free space, will start automatically.', L('⏳', tr('Server busy'))), force=True)
        await asyncio.sleep(3)
    reserved[job.id] = need


# ══════════════════════════ جلسات المستخدمين ══════════════════════════
def drop_session(uid):
    s = sessions.pop(uid, None)
    if s:
        shutil.rmtree(s.dir, ignore_errors=True)


def new_session(user, chat_id, kind):
    drop_session(user.id)
    d = WORK_DIR / "s" / f"{user.id}_{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    s = Session(uid=user.id, chat_id=chat_id, kind=kind, dir=d, user=user)
    sessions[user.id] = s
    return s


def touch(s):
    s.last = time.time()


async def cleaner_loop():
    while True:
        await asyncio.sleep(300)
        try:
            now = time.time()
            for uid, s in list(sessions.items()):
                if now - s.last > SESSION_IDLE:
                    drop_session(uid)
            # مجلدات يتيمة (مش تابعة لجلسة ولا مهمة) أقدم من ساعتين
            alive = {str(s.dir) for s in sessions.values()} | {str(j.s.dir) for j in jobs.values()}
            base = WORK_DIR / "s"
            if base.exists():
                for d in base.iterdir():
                    if str(d) not in alive and now - d.stat().st_mtime > 7200:
                        shutil.rmtree(d, ignore_errors=True)
        except Exception as e:
            logger.warning("cleaner: %s", e)


# ══════════════════════════ تحديث رسالة الحالة ══════════════════════════
def _fw(e):
    return getattr(e, "value", getattr(e, "x", 3)) or 3


async def safe_edit(msg, text, markup=None):
    try:
        await msg.edit_text(text, reply_markup=markup)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(min(_fw(e), 5))
    except Exception as e:
        logger.debug("edit failed: %s", e)


EDIT_GAP = 4          # minimum seconds between progress edits (avoids flood waits and noise)


async def edit(job, text, force=False, cancel_btn=True, pulse=False):
    """Edit the job's status message in the owner's language (language is restored afterwards)."""
    tok = _lang_var.set(get_lang(job.uid))
    try:
        return await _edit_impl(job, text, force, cancel_btn, pulse)
    finally:
        _lang_var.reset(tok)


async def _edit_impl(job, text, force=False, cancel_btn=True, pulse=False):
    now = time.time()
    if not force and (now - job.last_edit < EDIT_GAP or text == job.last_text):
        return
    job.last_edit, job.last_text = now, text
    if not pulse:
        job.base_text = text
    # زرار الإلغاء بيظهر بس للمهام الطويلة (طابور، أو عدّت CANCEL_AFTER ثانية)
    show = cancel_btn and (job.queue != "quick" or time.time() - job.t0 > CANCEL_AFTER)
    markup = kb([(tr('❌ Cancel'), f"cx:{job.id}")]) if show else None
    await safe_edit(job.msg, text, markup)


def make_progress(job, label, total_hint=0, word="received"):
    """total_hint = known file size, used when Telegram reports total=0 (unknown)."""
    async def cb(cur, total):
        set_lang(job.uid)
        if job.cancelled:
            try:
                app.stop_transmission()
            except Exception:
                pass
            return
        await edit(job, progress_text(label, cur, total or total_hint, word))
    return cb


# ══════════════════════════ ffmpeg / probe ══════════════════════════
def _nice():
    try:
        os.nice(FFMPEG_NICE)
    except Exception:
        pass


_RE_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


async def probe(path):
    info = {"duration": 0.0, "width": 0, "height": 0, "vcodec": None, "acodec": None, "has_video": False,
            "has_audio": False, "yuv420": False, "fmt": "", "tags": {}, "cover": False, "fps": 0.0,
            "asr": 0, "ach": "", "rot": 0.0}
    if not FFMPEG:
        return info
    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-hide_banner", "-i", str(path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    t = err.decode("utf-8", "ignore")
    m = _RE_DUR.search(t)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Input #0,\s*(\S+),\s+from", t)
    if m:
        info["fmt"] = m.group(1)
    in_tags = False
    for line in t.splitlines():
        if line.startswith("Input #0"):
            in_tags = True
            continue
        if line.startswith("  Duration:"):
            in_tags = False
        if in_tags:
            mt = re.match(r"^\s{4}(\w+)\s*:\s*(.*)$", line)
            if mt:
                info["tags"][mt.group(1).lower()] = mt.group(2).strip()
        mt2 = re.match(r"^\s{6}(title|artist)\s*:\s*(.*)$", line)
        if mt2 and not in_tags:          # وسوم على مستوى الستريم (زي ogg/opus)
            info["tags"].setdefault(mt2.group(1).lower(), mt2.group(2).strip())
        if "Stream #" not in line:
            continue
        if " Video:" in line:
            if "attached pic" in line:
                info["cover"] = True
                continue
            if not info["has_video"]:
                info["has_video"] = True
                mv = re.search(r"Video:\s*([A-Za-z0-9_]+)", line)
                info["vcodec"] = mv.group(1) if mv else None
                ms = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
                if ms:
                    info["width"], info["height"] = int(ms.group(1)), int(ms.group(2))
                info["yuv420"] = "yuv420p" in line or "yuvj420p" in line
                mf = re.search(r"([\d.]+)\s*fps", line)
                if mf:
                    try:
                        info["fps"] = float(mf.group(1))
                    except ValueError:
                        pass
        elif " Audio:" in line and not info["has_audio"]:
            info["has_audio"] = True
            ma = re.search(r"Audio:\s*([A-Za-z0-9_]+)", line)
            info["acodec"] = ma.group(1) if ma else None
            mh = re.search(r",\s*(\d{4,6}) Hz,\s*([^,]+)", line)
            if mh:
                info["asr"], info["ach"] = int(mh.group(1)), mh.group(2).strip()
    mr = re.search(r"rotation of (-?\d+(?:\.\d+)?) degrees", t)
    if mr:
        try:
            info["rot"] = float(mr.group(1))
        except ValueError:
            pass
    return info


def vscale(h):
    """Cap the SHORT side at h pixels (so 480p means 480p for portrait videos too). Never upscales."""
    return f"scale=w='if(gt(iw,ih),-2,min({h},iw))':h='if(gt(iw,ih),min({h},ih),-2)'"


def enc_threads(job):
    return ["-threads", str(FFMPEG_THREADS if job.queue != "quick" else 1)]


CPU_SLICE = 0.25       # seconds per duty cycle of the CPU cap


async def _cpu_governor(proc, percent):
    """Hard CPU cap for a child process: freeze/unfreeze it (SIGSTOP/SIGCONT) in short slices so it never uses more
    than `percent`% of one core. This is what keeps the server under the host's CPU-abuse limit."""
    if os.name != "posix" or percent <= 0 or percent >= 100:
        return
    run = CPU_SLICE * percent / 100
    pause = CPU_SLICE - run
    try:
        while proc.returncode is None:
            await asyncio.sleep(run)
            if proc.returncode is not None:
                break
            os.kill(proc.pid, signal.SIGSTOP)
            await asyncio.sleep(pause)
            os.kill(proc.pid, signal.SIGCONT)
    except (ProcessLookupError, OSError):
        pass
    finally:
        try:
            os.kill(proc.pid, signal.SIGCONT)      # never leave it frozen
        except Exception:
            pass


def _reap_stopped_ffmpeg():
    """If the bot was killed while an ffmpeg was frozen by the governor, remove that orphan on the next start."""
    if not os.path.isdir("/proc"):
        return
    me = str(WORK_DIR)
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                stat = f.read().decode("utf-8", "ignore")
            state = stat.rsplit(")", 1)[1].split()[0]
            if state != "T":
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", "ignore")
            if "ffmpeg" in cmd and me in cmd:
                os.kill(int(pid), signal.SIGKILL)
                logger.warning("Removed a frozen leftover ffmpeg process (pid %s)", pid)
        except Exception:
            continue


async def run_ffmpeg(job, args, duration=0, timeout=3600, label="", check=True):
    if not FFMPEG:
        raise JobError(tr('ffmpeg is not installed on the server.'))
    args = list(args)
    if "-i" in args:                               # limit decoder threads too (not only the encoder)
        i = args.index("-i")
        args[i:i] = ["-threads", str(FFMPEG_THREADS if job.queue != "quick" else 1)]
    cmd = [FFMPEG, "-hide_banner", "-y", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
           "-filter_threads", "1", "-filter_complex_threads", "1", *args]
    kwargs = {"preexec_fn": _nice} if os.name == "posix" else {}
    t_start = time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs)
    job.proc = proc
    governor = asyncio.create_task(_cpu_governor(proc, FFMPEG_CPU_PERCENT))
    err_buf = []

    async def read_err():
        err_buf.append(await proc.stderr.read())

    async def read_prog():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            s = line.decode("utf-8", "ignore").strip()
            if duration and s.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    cur = int(s.split("=")[1]) / 1_000_000
                except ValueError:
                    continue
                if cur >= 0:
                    await edit(job, tr('{0}\n{1}\n<code>{2} of {3}</code>', label, bar(cur, duration), fmt_time(cur), fmt_time(duration)))

    timeout = max(timeout, int(duration * 8))     # المهلة بتكبر مع طول الملف
    try:
        await asyncio.wait_for(asyncio.gather(read_prog(), read_err(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise JobError(tr('This took longer than allowed and was stopped. Try a smaller file or a lighter level.'))
    finally:
        governor.cancel()
        job.proc = None
    if job.cancelled:
        raise JobCancelled
    err = b"".join(err_buf).decode("utf-8", "ignore")
    if proc.returncode != 0 and check:
        logger.error("ffmpeg failed (%s): %s", proc.returncode, err[-300:])
        raise JobError(tr('Processing failed. The file may be corrupted or in an unsupported format.'))
    if duration and proc.returncode == 0:
        took = max(time.time() - t_start, 0.1)
        # only real encodes are worth an INFO line; quick stream-copy jobs (tags, cuts, renames) go to DEBUG
        logger.log(logging.INFO if took >= 20 else logging.DEBUG,
                   "ffmpeg %s: %s of media in %s (%.2fx realtime, threads=%s, cpu cap=%s)", job.op,
                   fmt_time(duration), fmt_time(took), duration / took, FFMPEG_THREADS,
                   f"{FFMPEG_CPU_PERCENT}%" if FFMPEG_CPU_PERCENT else "off")
    return proc.returncode, err


async def ffmpeg_quick(args, timeout=90):
    """أوامر قصيرة من غير شريط تقدم (لقطات/استخراج غلاف). بترجع كود الخروج."""
    if not FFMPEG:
        return 1
    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-y", "-nostdin", "-loglevel", "error", *args,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout)
        return proc.returncode
    except Exception:
        return 1


# ══════════════════════════ الصور المصغرة ══════════════════════════
def make_jpeg(src, dst, side=320, limit_kb=190):
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((side, side))
        q = 88
        while True:
            im.save(dst, "JPEG", quality=q)
            if os.path.getsize(dst) <= limit_kb * 1024 or q <= 40:
                break
            q -= 10
    return dst


async def grab_frame(src, dst, t, side=320):
    rc = await ffmpeg_quick(["-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                             "-vf", f"scale='min({side},iw)':-2", "-q:v", "3", str(dst)])
    return rc == 0 and Path(dst).exists() and Path(dst).stat().st_size > 0


def _luma(p):
    try:
        with Image.open(p) as im:
            return ImageStat.Stat(im.convert("L")).mean[0]
    except Exception:
        return 0


async def auto_thumb(src, dst, duration=0):
    """يختار لقطة مناسبة (من أول ثانية/اتنين/تلاتة) ويتجنب اللقطات السودا."""
    dst = Path(dst)
    cands = [1.0, 2.0, 3.0]
    if duration:
        cands = [t for t in cands if t < duration - 0.2] or [max(0.0, duration / 2)]
    best = None
    tmps = []
    for i, t in enumerate(cands):
        tmp = dst.with_name(f"{dst.stem}_c{i}.jpg")
        if not await grab_frame(src, tmp, t):
            continue
        tmps.append(tmp)
        luma = _luma(tmp)
        if best is None or luma > best[0]:
            best = (luma, tmp)
        if luma >= 30:
            break
    ok = False
    if best:
        shutil.copyfile(best[1], dst)
        ok = True
    for t in tmps:
        _safe_remove(t)
    return ok


async def extract_cover(src, dst):
    """بيطلّع الغلاف المدمج في ملف صوت (لو موجود)."""
    rc = await ffmpeg_quick(["-i", str(src), "-an", "-map", "0:v:0", "-frames:v", "1",
                             "-vf", "scale='min(320,iw)':-2", str(dst)])
    return rc == 0 and Path(dst).exists() and Path(dst).stat().st_size > 0


# ══════════════════════════ Default audio cover (/audio_cover) ══════════════════════════
# One small JPEG per user in covers/<uid>.jpg (permanent) + covers/settings.json with the on/off switch.
# When on, the cover is placed automatically in the "Edit Tags" panel; sending another image there replaces it for that file only.
COVER_SETTINGS = COVERS_DIR / "settings.json"
COVER_WAIT = {}            # uid -> time the user tapped "Change" (the next image they send becomes their default cover)
COVER_WAIT_TTL = 300


def _cover_file(uid):
    return COVERS_DIR / f"{uid}.jpg"


def has_default_cover(uid):
    return _cover_file(uid).exists()


def _load_cover_settings():
    try:
        return json.loads(COVER_SETTINGS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error("covers/settings.json is unreadable: %s", e)
        return {}


def cover_enabled(uid):
    return bool(_load_cover_settings().get(str(uid), {}).get("on", True))      # on by default once a cover is saved


def set_cover_enabled(uid, on):
    data = _load_cover_settings()
    data.setdefault(str(uid), {})["on"] = bool(on)
    try:
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = COVER_SETTINGS.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(COVER_SETTINGS)
    except Exception as e:
        logger.error("Could not save covers/settings.json: %s", e)


def active_default_cover(uid):
    """Path of the user's default cover if it exists AND is switched on, else None."""
    return _cover_file(uid) if (has_default_cover(uid) and cover_enabled(uid)) else None


def save_default_cover(uid, src):
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = COVERS_DIR / f"{uid}.new.jpg"
    make_jpeg(src, tmp, 600, 150)
    tmp.replace(_cover_file(uid))
    set_cover_enabled(uid, True)


def delete_default_cover(uid):
    _safe_remove(_cover_file(uid))


def cover_panel(uid):
    has, on = has_default_cover(uid), cover_enabled(uid)
    state = tr('✅ Saved') if has else tr('— None yet')
    auto = (tr('✅ On') if on else tr('⛔ Off')) if has else "—"
    text = tr('🖼️ {0}\n\n📌 <b>Default cover:</b> {1}\n⚙️ <b>Auto-apply in Edit Tags:</b> {2}\n\n'
              '<i>It is added automatically when you open Edit Tags on an audio file. Sending another image there replaces it for that file only.\n'
              'It is embedded inside MP3, M4A and FLAC files; other formats show it as the Telegram thumbnail.</i>',
              hd(tr('Default audio cover')), state, auto)
    if has:
        rows = [[(tr('🖼️ Change'), "ac:change"), (tr('🗑️ Delete'), "ac:delete")],
                [(tr('✅ Auto-apply: On') if on else tr('⛔ Auto-apply: Off'), "ac:toggle")]]
    else:
        rows = [[(tr('🖼️ Set Default Cover'), "ac:change")]]
    return text, kb(*rows)


async def send_cover_panel(target, uid):
    text, markup = cover_panel(uid)
    if has_default_cover(uid):
        await target.reply_photo(str(_cover_file(uid)), caption=text, reply_markup=markup)
    else:
        await target.reply_text(text, reply_markup=markup)


async def cmd_audio_cover(client, m):
    uid = m.from_user.id
    set_lang(uid, m.from_user)
    COVER_WAIT.pop(uid, None)
    await send_cover_panel(m, uid)


async def cb_audio_cover(q, data):
    uid = q.from_user.id
    act = data.split(":", 1)[1] if ":" in data else ""
    msg = q.message
    if act == "change":
        COVER_WAIT[uid] = time.time()
        await q.answer()
        await msg.reply_text(tr('🖼️ Send the image to use as your default audio cover (or /cancel):'))
    elif act == "toggle":
        await q.answer()
        if not has_default_cover(uid):
            return
        set_cover_enabled(uid, not cover_enabled(uid))
        text, markup = cover_panel(uid)
        try:
            await msg.edit_caption(text, reply_markup=markup)
        except MessageNotModified:
            pass
        except Exception:
            await safe_edit(msg, text, markup)
    elif act == "delete":
        delete_default_cover(uid)
        await q.answer(tr('🗑️ Default cover deleted.'))
        try:
            await msg.delete()
        except Exception:
            pass
        await send_cover_panel(msg, uid)
    else:
        await q.answer()


async def save_cover_from_message(m, info):
    """The user sent the image for their default cover."""
    uid = m.from_user.id
    if info["size"] and info["size"] > MAX_IMAGE_MB * MB:
        COVER_WAIT[uid] = time.time()
        await m.reply_text(tr('❌ Image is over the {0} MB limit.', MAX_IMAGE_MB))
        return
    d = WORK_DIR / "s" / f"cov_{uid}_{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        p = await dl(info["file_id"], d / "in.jpg")
        if not p:
            COVER_WAIT[uid] = time.time()
            await m.reply_text(tr("❌ Couldn't download the image. Please try again."))
            return
        try:
            await asyncio.to_thread(save_default_cover, uid, p)
        except Exception as e:
            logger.warning("default cover rejected: %s", e)
            COVER_WAIT[uid] = time.time()
            await m.reply_text(tr("❌ That isn't a valid image. Send another one:"))
            return
    finally:
        shutil.rmtree(d, ignore_errors=True)
    await m.reply_text(tr('✅ Default cover saved.'))
    await send_cover_panel(m, uid)


# ══════════════════════════ تغيير غلاف الفيديو من غير إعادة رفع ══════════════════════════
def detect_cover_support():
    try:
        import inspect
        return "video_cover" in inspect.signature(raw.types.InputMediaDocument.__init__).parameters
    except Exception:
        return False


async def send_video_with_cover(chat_id, file_id, cover_path, caption=""):
    """يبعت نفس الفيديو (بالـ file_id، من غير تحميل ولا رفع) بغلاف جديد."""
    from pyrogram.file_id import FileId
    fid = FileId.decode(file_id)
    peer = await app.resolve_peer(chat_id)
    up = await app.invoke(raw.functions.messages.UploadMedia(
        peer=peer,
        media=raw.types.InputMediaUploadedPhoto(file=await app.save_file(str(cover_path)))))
    ph = up.photo
    media = raw.types.InputMediaDocument(
        id=raw.types.InputDocument(id=fid.media_id, access_hash=fid.access_hash, file_reference=fid.file_reference),
        video_cover=raw.types.InputPhoto(id=ph.id, access_hash=ph.access_hash, file_reference=ph.file_reference))
    await app.invoke(raw.functions.messages.SendMedia(
        peer=peer, media=media, message=caption or "", random_id=app.rnd_id()))


# ══════════════════════════ الأرشفة (CRM Topics) ══════════════════════════
_db_lock = None
_topic_names = {}
_BROKEN_HELPERS = set()      # pyrofork helpers that don't match its raw layer (detected once, then skipped)


def load_db():
    if os.path.exists(USERS_DB):
        try:
            with open(USERS_DB, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("users.json is corrupted or unreadable: %s", e)
    return {}


def save_db(data):
    tmp = USERS_DB + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, USERS_DB)


def _get_db_lock():
    global _db_lock
    if _db_lock is None:
        _db_lock = asyncio.Lock()
    return _db_lock


def _as_channel(peer):
    return raw.types.InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash)


def _thread_kw(func, tid):
    """النسخ الجديدة بتستخدم message_thread_id، والقديمة بتعتمد على reply_to_message_id."""
    try:
        import inspect
        if "message_thread_id" in inspect.signature(func).parameters:
            return {"message_thread_id": tid}
    except Exception:
        pass
    return {"reply_to_message_id": tid}


def _tid_from_updates(r):
    for u in getattr(r, "updates", []) or []:
        msg = getattr(u, "message", None)
        if msg is not None and getattr(msg, "id", None):
            return msg.id
    return None


async def _create_topic(title):
    # 1) the library's own helper (some builds ship one that does not match their raw layer -> fall through)
    if hasattr(app, "create_forum_topic") and "create" not in _BROKEN_HELPERS:
        try:
            r = await app.create_forum_topic(GROUP_ID, title)
            tid = getattr(r, "id", None)
            if tid:
                return tid
        except TypeError as e:
            _BROKEN_HELPERS.add("create")       # remember it: warn once, then go straight to the raw API
            logger.warning("create_forum_topic helper is incompatible (%s); using the raw API from now on", e)
    # 2) raw API: newer layers use messages.CreateForumTopic(peer=...), older ones channels.CreateForumTopic(channel=...)
    peer = await app.resolve_peer(GROUP_ID)
    for mod in (raw.functions.messages, raw.functions.channels):
        fn = getattr(mod, "CreateForumTopic", None)
        if fn is None:
            continue
        key = "peer" if "peer" in inspect.signature(fn.__init__).parameters else "channel"
        r = await app.invoke(fn(**{key: peer if key == "peer" else _as_channel(peer)}, title=title, random_id=app.rnd_id()))
        return _tid_from_updates(r)
    raise RuntimeError("CreateForumTopic is not available in this pyrofork build")


async def _edit_topic(tid, title):
    if hasattr(app, "edit_forum_topic") and "edit" not in _BROKEN_HELPERS:
        try:
            await app.edit_forum_topic(GROUP_ID, tid, title)
            return
        except TypeError as e:
            _BROKEN_HELPERS.add("edit")
            logger.warning("edit_forum_topic helper is incompatible (%s); using the raw API from now on", e)
    peer = await app.resolve_peer(GROUP_ID)
    for mod in (raw.functions.messages, raw.functions.channels):
        fn = getattr(mod, "EditForumTopic", None)
        if fn is None:
            continue
        key = "peer" if "peer" in inspect.signature(fn.__init__).parameters else "channel"
        await app.invoke(fn(**{key: peer if key == "peer" else _as_channel(peer)}, topic_id=tid, title=title))
        return
    raise RuntimeError("EditForumTopic is not available in this pyrofork build")


async def get_or_create_topic(user, force_new=False):
    if not GROUP_ID:
        return None
    topic_name = f"{user.first_name or ''} {user.last_name or ''}".strip() + f" [{user.id}]"
    uid = str(user.id)
    async with _get_db_lock():
        db = await asyncio.to_thread(load_db)
        if uid in db and not force_new:
            tid = db[uid]
            if _topic_names.get(uid) != topic_name:
                try:
                    await _edit_topic(tid, topic_name)
                except Exception:
                    pass
                _topic_names[uid] = topic_name
            return tid
        try:
            tid = await _create_topic(topic_name)
            if not tid:
                logger.error("Failed to create topic: topic id not found in response")
                return None
            db[uid] = tid
            await asyncio.to_thread(save_db, db)
            _topic_names[uid] = topic_name
            return tid
        except Exception as e:
            logger.error("Failed to create topic: %s", e)
            return None


def _crm_header(user):
    """Name line only (the topic title already carries the ID). Linked to @username, or to the profile if none."""
    full = f"{user.first_name or ''} {user.last_name or ''}".strip() or "User"
    safe = H(full)
    if user.username:
        return f'👤 <a href="https://t.me/{user.username}">{safe}</a>'
    return f'👤 <a href="tg://user?id={user.id}">{safe}</a>'


def _is_missing_topic_error(e):
    msg = str(e).lower()
    return ("thread" in msg or "topic" in msg or "reply" in msg) and ("not found" in msg or "invalid" in msg)


async def _archive_send(user, factory):
    for attempt in range(2):
        tid = await get_or_create_topic(user, force_new=(attempt == 1))
        if not tid:
            return
        try:
            await factory(tid)
            return
        except Exception as e:
            if attempt == 0 and _is_missing_topic_error(e):
                continue
            logger.error("CRM Send Error: %s", e)
            return


def file_id_of(m):
    for attr in ("document", "video", "audio", "voice", "video_note", "animation"):
        obj = getattr(m, attr, None)
        if obj:
            return obj.file_id
    return None


def _op_label(op):
    """'Compress audio (Strong)' -> 'Compress audio | Strong';  'Convert video to MKV' -> 'Convert video | MKV'."""
    m = re.match(r"^(Convert (?:audio|video)) to (.+)$", op)
    if m:
        return f"{m.group(1)} | {m.group(2)}"
    m = re.match(r"^(.*?) \((.*)\)$", op)
    if m:
        return f"{m.group(1)} | {m.group(2)}"
    return op


def _arch_card(user, icon, title, detail=""):
    """Archive message (always English): light small-caps heading, then the user (clickable name) and optional stats."""
    lines = [f"{icon} {H(smallcaps(_op_label(title)))}", "", _crm_header(user)]
    if detail:
        lines.append(detail)
    return "\n".join(lines)


def archive_media(user, file_id, state, operation=None, name=None, extra=None):
    """Archive by file_id (no download/upload - zero RAM/disk use)."""
    if not GROUP_ID or not file_id:
        return
    if state == "received":
        caption = _arch_card(user, "📥", "Received", extra or "")
    else:
        caption = _arch_card(user, "✅", str(operation), extra or "")

    async def _send(tid):
        await app.send_cached_media(GROUP_ID, file_id, caption=caption, parse_mode=ParseMode.HTML,
                                    **_thread_kw(app.send_cached_media, tid))

    _spawn(_archive_send(user, _send))


def archive_note(user, icon, title, detail=""):
    if not GROUP_ID:
        return
    text = _arch_card(user, icon, title, detail)

    async def _send(tid):
        await app.send_message(GROUP_ID, text, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True, **_thread_kw(app.send_message, tid))

    _spawn(_archive_send(user, _send))


# ══════════════════════════ محرك المهام (طابور + تنفيذ فوري) ══════════════════════════
OPS = {}
CLOUD_OPS = {"pdf_convert", "office_pdf", "office_txt", "pdf_bmcq", "img_text", "pdf_txt", "tsum"}


def op(name):
    def deco(fn):
        OPS[name] = fn
        return fn
    return deco


def is_cloud(job):
    if job.op in CLOUD_OPS:
        return True
    return job.op == "pdf_compress" and job.params.get("action") in ("compress_recommended", "compress_extreme")


def rand2():
    return random.randint(10, 99)


def decide_queue(s, op_name, params):
    """quick = فورًا من غير طابور | light = طابور خفيف | heavy = طابور الفيديو."""
    mb = s.size / MB
    dur = s.duration or 0
    if s.kind in ("pdf", "office", "image"):
        return "quick"
    if s.kind == "book":
        return "light"          # CPU work: waits its turn in the single queue
    if op_name == "amerge" or op_name == "atranscribe":
        return "light"
    if op_name == "vmerge":
        return "heavy"          # may need a full re-encode when the videos don't match
    if op_name == "tsum":
        return "quick"          # a single Gemini call on text already in the session
    if s.kind == "audio":
        if op_name in ("ameta", "asplit", "acut", "arename", "astream"):
            return "quick" if mb <= QUICK_COPY_MB else "light"
        small = mb <= QUICK_AUDIO_MB and dur <= QUICK_AUDIO_MIN * 60
        return "quick" if small else "light"
    # video
    if op_name == "vcover":
        if COVER_BY_ID and s.orig == "video" and params.get("time") is None:
            return "quick"
        return "quick" if mb <= QUICK_COPY_MB else "light"
    if op_name in ("vsplit", "vrename") or (op_name == "vcut" and not params.get("precise")):
        return "quick" if mb <= QUICK_COPY_MB else "light"
    if op_name == "vaudio":
        return "quick" if (mb <= QUICK_COPY_MB and dur <= QUICK_AUDIO_MIN * 60) else "light"
    small = mb <= QUICK_VIDEO_MB and dur <= QUICK_VIDEO_SEC
    return "quick" if small else "heavy"


def finish(job):
    if jobs.pop(job.id, None) is not None:
        user_jobs[job.uid] = max(0, user_jobs[job.uid] - 1)


def cleanup_job(job):
    reserved.pop(job.id, None)
    if job.followup and job.ok:
        return
    shutil.rmtree(job.s.dir, ignore_errors=True)


def queued_text(pos, total, uid=None):
    tok = _lang_var.set(get_lang(uid)) if uid else None
    try:
        return (tr('⏳ {0}\n📍 Position <b>{1}</b> of {2}\nStarts automatically on your turn.', hd(tr('In queue')), pos, total))
    finally:
        if tok is not None:
            _lang_var.reset(tok)


async def refresh_positions():
    total = len(waiting)
    for i, j in enumerate(list(waiting), 1):
        if j.last_pos != i:
            j.last_pos = i
            await edit(j, queued_text(i, total, j.uid), force=True)


def _can_start(job):
    if len(running) >= MAX_RUNNING:
        return False
    if job.queue == "heavy" and sum(1 for j in running if j.queue == "heavy") >= MAX_HEAVY:
        return False
    return True


def dispatch():
    """طابور واحد ذكي: بنمشي عليه بالترتيب، والمهمة الخفيفة بتعدي قدام تقيلة مستنية مكان."""
    for job in list(waiting):
        if len(running) >= MAX_RUNNING:
            break
        if _can_start(job):
            waiting.remove(job)
            running.add(job)
            _spawn(_run_queued(job))
    if waiting:
        _spawn(refresh_positions())


async def _run_queued(job):
    try:
        await process(job)
    except Exception:
        logger.exception("queued job crashed")
    finally:
        running.discard(job)
        finish(job)
        dispatch()


PULSE_GAP = 4          # seconds between "still working" updates


async def _pulse(job):
    """Keeps long status messages alive when there is NO real percentage: moving bar + elapsed time.
    Works for waiting jobs (queue) and running jobs; texts that already show a real % are never touched."""
    try:
        while True:
            await asyncio.sleep(PULSE_GAP)
            if job.finished or job.cancelled or job.id not in jobs:
                return
            base = job.base_text
            if not base or "%" in base:          # real percentage shown -> leave it alone
                continue
            if time.time() - job.last_edit < PULSE_GAP - 0.5:
                continue
            el = fmt_time(time.time() - job.t0)
            # unknown-size progress already has a moving bar -> only add the elapsed time
            extra = f"⏱ {el}" if ("▰" in base or "▱" in base) else f"{pulse_bar()}  ⏱ {el}"
            await edit(job, f"{base}\n{extra}", force=True, pulse=True)
    except Exception as e:
        logger.debug("pulse stopped: %s", e)


async def _late_cancel_button(job):
    """المهام السريعة مفيهاش زرار إلغاء، إلا لو طولت."""
    await asyncio.sleep(CANCEL_AFTER + 0.5)
    if job.id in jobs and not job.cancelled:
        await edit(job, job.base_text or L("⚙️", tr('Processing')), force=True)


async def submit(job):
    jobs[job.id] = job
    user_jobs[job.uid] += 1
    _spawn(_pulse(job))
    if job.queue == "quick":
        _spawn(run_quick(job))
        _spawn(_late_cancel_button(job))
        return
    waiting.append(job)
    dispatch()
    if job in waiting:
        job.last_pos = len(waiting)
        await edit(job, queued_text(len(waiting), len(waiting), job.uid), force=True)
    else:
        await edit(job, L("⚙️", tr('Preparing')), force=True)


async def run_quick(job):
    try:
        if is_cloud(job):
            async with CLOUD_SEM:          # a few cloud jobs at a time (each one holds files + HTTP buffers in RAM)
                await process(job)
        else:
            async with QUICK_SEM:
                await process(job)
    except Exception:
        logger.exception("quick job crashed")
    finally:
        finish(job)


async def start_job(s, op_name, params, status_msg, notify_msg=None):
    """بيحوّل الجلسة لمهمة. بيرجع False لو المستخدم عنده مهام كتير شغالة."""
    if user_jobs[s.uid] >= USER_MAX_JOBS:
        target = notify_msg or status_msg
        await target.reply_text(tr('⚠️ You already have {0} tasks running. Wait for one to finish, then try again.', user_jobs[s.uid]))
        return False
    sessions.pop(s.uid, None)
    job = Job(id=new_job_id(), uid=s.uid, chat_id=s.chat_id, s=s, op=op_name, params=params,
              queue=decide_queue(s, op_name, params), msg=status_msg, user=s.user)
    await submit(job)
    return True


async def process(job):
    set_lang(job.uid)
    s = job.s
    try:
        if job.queue != "quick":          # quick jobs already show a status line; avoid a redundant edit
            await edit(job, L("⚙️", tr('Starting')), force=True)
        outs = await OPS[job.op](job)
        if job.cancelled:
            raise JobCancelled
        await send_outs(job, outs or [])
        job.finished = True
        job.ok = True
        try:
            await job.msg.delete()
        except Exception:
            pass
        if job.followup:
            text, markup = job.followup
            s.state = None
            s.data["followup_shown"] = True
            sessions[s.uid] = s
            touch(s)
            await app.send_message(job.chat_id, text, reply_markup=markup)
    except JobCancelled:
        job.finished = True
        await safe_edit(job.msg, CANCELLED())
    except JobError as e:
        job.finished = True
        await safe_edit(job.msg, f"❌ {e}")
    except Exception:
        job.finished = True
        logger.exception("job failed (%s)", job.op)
        await safe_edit(job.msg, tr('❌ Unexpected error while processing. Please try again.'))
    finally:
        cleanup_job(job)


async def cb_cancel_job(q, jid):
    job = jobs.get(jid)
    if not job or job.uid != q.from_user.id:
        await q.answer(tr('This task has already finished or was cancelled.'), show_alert=True)
        return
    job.cancelled = True
    if job in waiting:
        waiting.remove(job)
        finish(job)
        cleanup_job(job)
        await safe_edit(job.msg, CANCELLED())
        _spawn(refresh_positions())
    elif job.proc:
        try:
            job.proc.kill()
        except ProcessLookupError:
            pass
    await q.answer("Cancelled")


async def ensure_src(job):
    s = job.s
    if s.path and Path(s.path).exists():
        return Path(s.path)
    await wait_for_disk(job, disk_need(s, job.op))
    ext = Path(s.name).suffix or (".mp4" if s.kind == "video" else ".mp3")
    dest = s.dir / f"in{ext}"
    label = L("📥", tr('Downloading'))
    await edit(job, label, force=True)
    p = await app.download_media(s.file_id, file_name=str(dest),
                                 progress=make_progress(job, label, total_hint=s.size, word="received"))
    if job.cancelled or not p:
        raise JobCancelled
    s.path = Path(p)
    return s.path


async def run_cpu(job, fn, *args):
    try:
        res = await asyncio.wait_for(asyncio.to_thread(fn, *args), PDF_TIMEOUT)
    except asyncio.TimeoutError:
        raise JobError(tr('This took too long and was stopped. Try a smaller file.'))
    if job.cancelled:
        raise JobCancelled
    return res


# ══════════════════════════ الإرسال ══════════════════════════
AUDIO_AS_AUDIO = {".mp3", ".m4a", ".ogg", ".opus"}
VIDEO_AS_VIDEO = {".mp4", ".mov", ".m4v"}


async def send_one(job, o, idx, total):
    chat = job.chat_id
    label = L("📤", tr('Uploading {0}/{1}', idx, total)) if total > 1 else L("📤", tr('Uploading'))
    try:
        fsize = os.path.getsize(o.path)
    except OSError:
        fsize = 0
    prog = make_progress(job, label, total_hint=fsize, word="sent")
    await edit(job, label, force=True)
    cap = o.caption or None
    thumb = str(o.thumb) if o.thumb and Path(o.thumb).exists() else None
    path = str(o.path)
    if o.kind == "video":
        return await app.send_video(chat, path, caption=cap, duration=int(o.duration), width=int(o.width),
                                    height=int(o.height), thumb=thumb, file_name=o.name,
                                    supports_streaming=True, progress=prog)
    if o.kind == "audio":
        return await app.send_audio(chat, path, caption=cap, duration=int(o.duration), performer=o.performer,
                                    title=o.title, thumb=thumb, file_name=o.name, progress=prog)
    if o.kind == "voice":
        return await app.send_voice(chat, path, caption=cap, duration=int(o.duration), progress=prog)
    return await app.send_document(chat, path, caption=cap, file_name=o.name, thumb=thumb,
                                   force_document=True, progress=prog)


async def send_outs(job, outs, offset=0, grand=None):
    total = grand or len([o for o in outs if o.kind != "image"]) or 0
    n = offset
    imgs = [o for o in outs if o.kind == "image"]
    for o in outs:
        if o.kind == "image":
            continue
        if job.cancelled:
            raise JobCancelled
        n += 1
        m = await send_one(job, o, n, total)
        if job.cancelled:
            raise JobCancelled
        if m is not None:
            op_name = o.op or job.op
            try:
                osz = os.path.getsize(o.path)
            except OSError:
                osz = 0
            extra = None
            if op_name.startswith("Compress") and job.s.size and osz:
                pct = (1 - osz / job.s.size) * 100
                tail = f"(−{pct:.0f}%)" if pct > 0 else "(no reduction)"
                extra = f"📉 {fmt_size(job.s.size)} → {fmt_size(osz)} {tail}"
            elif o.kind in ("audio", "voice") and osz:
                extra = f"💾 {fmt_size(osz)}"
            archive_media(job.user, file_id_of(m), "processed", op_name, o.name, extra)
    for i in range(0, len(imgs), 10):
        if job.cancelled:
            raise JobCancelled
        batch = imgs[i:i + 10]
        if len(batch) == 1:
            await app.send_photo(job.chat_id, str(batch[0].path))
        else:
            await app.send_media_group(job.chat_id, [InputMediaPhoto(str(b.path)) for b in batch])


# ══════════════════════════ إصدارات معدّلة من دوال QuickPDF (مسارات مؤقتة آمنة) ══════════════════════════
def sync_split_equal2(pdf_path, chunk_size, base_name, out_dir):
    doc = fitz.open(pdf_path)
    total = len(doc)
    out_files = []
    name_no_ext = os.path.splitext(base_name)[0]
    for i in range(0, total, chunk_size):
        new_doc = fitz.open()
        end = min(i + chunk_size - 1, total - 1)
        new_doc.insert_pdf(doc, from_page=i, to_page=end)
        out_path = str(Path(out_dir) / f"{name_no_ext}_Part{i // chunk_size + 1}_{rand2()}.pdf")
        _save_optimized(new_doc, out_path)
        new_doc.close()
        out_files.append(out_path)
    doc.close()
    return out_files


def sync_pdf_to_images2(pdf_path, pages_to_keep, base_name, out_dir):
    """يرندر الصفحات كصور JPG بدقة 150 DPI — خفيف على الذاكرة."""
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(150 / 72, 150 / 72)
    out_files = []
    for p in pages_to_keep:
        pix = doc[p].get_pixmap(matrix=mat)
        out_path = str(Path(out_dir) / f"{base_name}_p{p + 1}_{random.randint(100, 999)}.jpg")
        pix.save(out_path)
        out_files.append(out_path)
        pix = None
    doc.close()
    return out_files


def sync_compress_images(pdf_path, out_path, max_side, quality):
    """ضغط حقيقي للصور الداخلية في الـ PDF (محليًا، من غير أي خدمة خارجية)."""
    doc = fitz.open(pdf_path)
    seen = set()
    for page in doc:
        for img in page.get_images(full=True):
            xref, smask = img[0], img[1]
            if xref in seen or smask:   # بنتخطى الصور الشفافة عشان منبوّظهاش
                continue
            seen.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.alpha:
                    continue
                if pix.n not in (1, 3):
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                mode = "L" if pix.n == 1 else "RGB"
                im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                if max(im.size) > max_side:
                    im.thumbnail((max_side, max_side))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
                orig = len(doc.xref_stream_raw(xref) or b"")
                if orig and len(data) >= orig:
                    continue
                page.replace_image(xref, stream=data)
            except Exception as e:
                logger.debug("skip image %s: %s", xref, e)
    _save_optimized(doc, out_path)
    doc.close()


def check_pdf_password(pdf_path, password=None):
    doc = fitz.open(pdf_path)
    try:
        if not doc.needs_pass:
            return "OK"
        if not password:
            return "NEEDS_PASSWORD"
        return "OK" if doc.authenticate(password) else "WRONG_PASSWORD"
    finally:
        doc.close()


def pdf_page_count(pdf_path):
    doc = fitz.open(pdf_path)
    n = len(doc)
    doc.close()
    return n


async def get_or_convert_legacy_pdf2(job):
    s = job.s
    cached = s.data.get("office_pdf")
    if cached and os.path.exists(cached):
        return cached, None
    out_pdf = str(s.dir / f"_officepdf_{random.randint(1000, 9999)}.pdf")
    success, msg = await process_ilovepdf_async("officepdf", str(s.path), out_pdf)
    if not success:
        return None, msg
    s.data["office_pdf"] = out_pdf
    if not s.thumb:
        tp = s.dir / "office_thumb.jpg"
        if await asyncio.to_thread(create_thumbnail, out_pdf, str(tp)):
            s.thumb = tp
    return out_pdf, None


def quota_or_fail(msg, prefix):
    if is_quota_error(msg):
        return JobError(tr('The free daily quota for this service is used up. Try again tomorrow.'))
    return JobError(f"{prefix}:\n<code>{H(str(msg))}</code>")


# ══════════════════════════ عمليات PDF / صور / Office ══════════════════════════
@op("pdf_compress")
async def op_pdf_compress(job):
    s = job.s
    action = job.params["action"]
    if LIGHT_HOST and str(action).startswith("img_"):
        raise JobError(tr("This tool is turned off on this server."))
    base = Path(s.name).stem
    out = s.dir / f"{base}_Compr_{rand2()}.pdf"
    await edit(job, L("🗜️", tr('Compressing PDF')), force=True)
    if action == "compress_local":
        await run_cpu(job, sync_compress_local, str(s.path), str(out))
        opn = "Compress PDF (light)"
    elif action in ("compress_recommended", "compress_extreme"):
        level = "recommended" if action == "compress_recommended" else "extreme"
        ok, msg = await process_ilovepdf_async("compress", str(s.path), str(out), compression_level=level)
        if not ok:
            raise JobError(tr('Compression failed:\n<code>{0}</code>', H(str(msg))))
        opn = "Compress PDF (cloud)"
    else:
        lvl = action.split("_", 1)[1]
        await run_cpu(job, sync_compress_images, str(s.path), str(out), *PDF_LEVELS[lvl])
        opn = f"Compress PDF images ({LEVEL_AR.en(lvl)})"
    after, before = out.stat().st_size, s.path.stat().st_size
    note = ""
    if after >= before:
        note = tr('\nℹ️ Already optimized, nothing more to shrink.')
    else:
        note = f"\n📉 {fmt_size(before)} → {fmt_size(after)} (−{(1 - after / before) * 100:.0f}%)"
    return [Out(out, "pdf", out.name, okc(tr('PDF compressed')) + note, thumb=s.thumb, op=opn)]


@op("pdf_split_custom")
async def op_pdf_split_custom(job):
    s = job.s
    out = s.dir / f"{Path(s.name).stem}_Split_{rand2()}.pdf"
    await run_cpu(job, sync_split_pdf, str(s.path), str(out), job.params["pages"])
    return [Out(out, "pdf", out.name, okc(tr('PDF split')), thumb=s.thumb, op="Split PDF")]


@op("pdf_split_equal")
async def op_pdf_split_equal(job):
    s = job.s
    files = await run_cpu(job, sync_split_equal2, str(s.path), job.params["chunk"], s.name, str(s.dir))
    return [Out(Path(f), "pdf", Path(f).name, f"✂️ {hd(tr('Part'))} {i + 1}/{len(files)}", thumb=s.thumb,
                op=f"Split PDF evenly (part {i + 1})") for i, f in enumerate(files)]


@op("pdf_merge")
async def op_pdf_merge(job):
    s = job.s
    lst = [str(p) for p in s.data["merge_list"]]
    out = s.dir / f"{Path(s.name).stem}_Merged_{rand2()}.pdf"
    await edit(job, L("🔗", tr('Merging {0} files', len(lst))), force=True)
    await run_cpu(job, sync_merge_pdfs, lst, str(out))
    return [Out(out, "pdf", out.name, okc(tr('PDFs merged')), thumb=s.thumb, op="Merge PDFs")]


@op("pdf_unlock")
async def op_pdf_unlock(job):
    s = job.s
    out = s.dir / f"{Path(s.name).stem}_Unlocked_{rand2()}.pdf"
    res = await run_cpu(job, sync_unlock_pdf, str(s.path), str(out), job.params.get("password"))
    if res != "OK":
        raise JobError(tr('Something went wrong while unlocking.'))
    return [Out(out, "pdf", out.name, okc(tr('PDF unlocked')), thumb=s.thumb, op="Unlock PDF")]


@op("pdf_nup")
async def op_pdf_nup(job):
    s = job.s
    layout, group = job.params["layout"], job.params["group"]
    out = s.dir / f"{Path(s.name).stem}_Nup_{rand2()}.pdf"
    await run_cpu(job, sync_nup, str(s.path), str(out), layout, group)
    return [Out(out, "pdf", out.name, okc(tr('Pages combined')), thumb=s.thumb, op="Print layout")]


@op("pdf_images")
async def op_pdf_images(job):
    s = job.s
    pages = job.params["pages"]
    if len(pages) > PDF_IMAGES_MAX_PAGES:
        pages = pages[:PDF_IMAGES_MAX_PAGES]
        await app.send_message(job.chat_id, tr('⚠️ Large file: only the first {0} pages will be converted.', PDF_IMAGES_MAX_PAGES))
    files = await run_cpu(job, sync_pdf_to_images2, str(s.path), pages, Path(s.name).stem, str(s.dir))
    if files:
        archive_note(job.user, "🖼️", "PDF to images",
                     f"🖼️ {len(files)} images | pages <code>{format_page_ranges(pages)}</code>")
    return [Out(Path(f), "image", Path(f).name) for f in files]


@op("pdf_convert")
async def op_pdf_convert(job):
    s = job.s
    fmt = job.params["fmt"]
    label = "Word" if fmt == "docx" else "PowerPoint"
    out = s.dir / f"{Path(s.name).stem}_{rand2()}.{fmt}"
    await edit(job, tr('{0}\nThis can take about a minute.', L('🔄', tr('Converting to {0}', label))), force=True)
    ok, msg = await process_cloudconvert_async(str(s.path), str(out), fmt, input_format="pdf")
    if not ok:
        raise quota_or_fail(msg, tr('Conversion failed'))
    return [Out(out, "doc", out.name, okc(tr('Converted to {0}', label)), op=f"PDF to {label}")]


@op("pdf_txt")
async def op_pdf_txt(job):
    s = job.s
    await edit(job, L("📄", tr('Extracting text')), force=True)
    pages_data, _ = await extract_text_smart_async(str(s.path), job)
    if not pages_data:
        raise JobError(tr('Text extraction failed.'))
    text = "".join(pages_data) if isinstance(pages_data, list) else str(pages_data)
    out = s.dir / f"{Path(s.name).stem}_TXT_{rand2()}.txt"
    out.write_text(text, encoding="utf-8")
    return [Out(out, "text", out.name, okc(tr('Text extracted')), op="Extract text (TXT)")]


@op("pdf_bmcq")
async def op_pdf_bmcq(job):
    s = job.s
    action = job.params["action"]
    pdf_path = str(s.path)
    pages_data, is_scanned = await asyncio.to_thread(sync_check_and_extract, pdf_path)
    chunk_size = 10
    final = ""
    if is_scanned:
        total = await asyncio.to_thread(pdf_page_count, pdf_path)
        for i in range(0, total, chunk_size):
            if job.cancelled:
                raise JobCancelled
            await edit(job, progress_count(L("🧠", tr('Analyzing pages')), min(i + chunk_size, total), total))
            pages_range = list(range(i, min(i + chunk_size, total)))
            chunk = str(s.dir / f"_bmcqchunk_{i}.pdf")
            try:
                await asyncio.to_thread(sync_split_pdf, pdf_path, chunk, pages_range)
                b64 = await asyncio.to_thread(sync_encode_b64, chunk)
                text = await call_gemini_bmcq_async(action, pdf_base64=b64)
            finally:
                _safe_remove(chunk)
            if text and text != "NO_QUESTIONS":
                final += text.strip() + "\n\n"
    else:
        if not pages_data:
            raise JobError(tr('Could not read the file.'))
        for i in range(0, len(pages_data), chunk_size):
            if job.cancelled:
                raise JobCancelled
            await edit(job, progress_count(L("🧠", tr('Analyzing text')), min(i + chunk_size, len(pages_data)), len(pages_data)))
            chunk_text = "".join(pages_data[i:i + chunk_size])
            if len(chunk_text.strip()) < 50:
                continue
            text = await call_gemini_bmcq_async(action, text=chunk_text)
            if text and text != "NO_QUESTIONS":
                final += text.strip() + "\n\n"
    if not final.strip():
        raise JobError(tr('No ready-made questions were found in this file. Try the AI generate option instead.'))
    out = s.dir / f"{Path(s.name).stem}_BMCQ_{random.randint(10, 99)}.txt"
    out.write_text(final.strip(), encoding="utf-8")
    opn = "Generate questions (AI)" if action == "bmcq_generate" else "Extract questions"
    return [Out(out, "text", out.name, okc(tr('Questions ready')), op=opn)]


@op("office_pdf")
async def op_office_pdf(job):
    s = job.s
    ftype = s.ftype
    base = Path(s.name).stem
    await edit(job, tr('{0}\nThis can take about a minute.', L('📕', tr('Converting to PDF'))), force=True)
    if ftype in ("doc", "ppt"):
        send_path, msg = await get_or_convert_legacy_pdf2(job)
        success = send_path is not None
    elif ftype in ("docx", "pptx"):
        send_path = str(s.dir / f"{base}_{rand2()}.pdf")
        success, msg = await process_ilovepdf_async("officepdf", str(s.path), send_path)
    else:
        send_path = str(s.dir / f"{base}_{rand2()}.pdf")
        success, msg = await process_cloudconvert_async(str(s.path), send_path, "pdf", input_format="html")
    if not success:
        raise quota_or_fail(msg, tr('Conversion failed'))
    if ftype in ("doc", "ppt") and not s.data.get("followup_shown"):
        job.followup = (tr('Want the text of this file too?'),
                        kb([(tr('📄 Get Text'), "office_txt")], [(tr('✅ No, Thanks'), "cancel")]))
    return [Out(Path(send_path), "doc", f"{base}.pdf", okc(tr('Converted to PDF')), thumb=s.thumb,
                op="Convert to PDF")]


@op("office_txt")
async def op_office_txt(job):
    s = job.s
    ftype = s.ftype
    await edit(job, L("📄", tr('Extracting text')), force=True)
    if ftype in ("doc", "ppt"):
        cached_pdf, err = await get_or_convert_legacy_pdf2(job)
        if err:
            raise quota_or_fail(err, tr('Text extraction failed'))
        pages_data, _ = await extract_text_smart_async(cached_pdf, job)
        text = "".join(pages_data) if pages_data and isinstance(pages_data, list) else None
    else:
        text = await asyncio.to_thread(sync_office_to_text, str(s.path), ftype)
    if not text or not text.strip():
        raise JobError(tr('This file has no extractable text.'))
    out = s.dir / f"{Path(s.name).stem}_TXT_{rand2()}.txt"
    out.write_text(text.strip(), encoding="utf-8")
    if ftype in ("doc", "ppt") and s.data.get("office_pdf") and not s.data.get("followup_shown"):
        job.followup = (tr('Want a PDF copy of this file too?'),
                        kb([(tr('📕 Get PDF'), "office_pdf_again")], [(tr('✅ No, Thanks'), "cancel")]))
    return [Out(out, "text", out.name, okc(tr('Text extracted')), op=f"Extract text from {OFFICE_TYPE_LABELS.en(ftype)}")]


@op("img_text")
async def op_img_text(job):
    s = job.s
    imgs = s.data["images"]
    await edit(job, L("🔤", tr('Reading text from images')), force=True)
    def _prep(paths):
        # نصغّر كل صورة (1600px JPEG) عشان الرام ميتأثرش لو الصور كتير أو كبيرة
        res = []
        for pth in paths:
            with Image.open(pth) as im:
                im = im.convert("RGB")
                im.thumbnail((1600, 1600))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                res.append(buf.getvalue())
        return res

    images_bytes = await asyncio.to_thread(_prep, imgs)
    prompt = ("Extract all the written or visible text in this image/these images, in order, in its original language. "
              "Output the text only, with no comments, summary or additions.")
    result = await call_gemini_engine(prompt, images=images_bytes)
    if not result or not result.strip():
        raise JobError(tr('Could not extract text from the images.'))
    out = s.dir / f"Images_TXT_{rand2()}.txt"
    out.write_text(result.strip(), encoding="utf-8")
    return [Out(out, "text", out.name, okc(tr('Text extracted')), op=f"Extract text from images ({len(imgs)})")]


@op("img_pdf")
async def op_img_pdf(job):
    s = job.s
    imgs = [str(p) for p in s.data["images"]]
    out = s.dir / f"Images_{rand2()}.pdf"
    try:
        await run_cpu(job, sync_images_to_pdf, imgs, str(out))
    except ValueError:
        raise JobError(tr('One of the images is too large to convert on this server.'))
    tp = s.dir / "img_thumb.jpg"
    thumb = tp if await asyncio.to_thread(create_image_thumbnail, imgs[0], str(tp)) else None
    return [Out(out, "pdf", out.name, okc(tr('Converted to PDF')), thumb=thumb,
                op=f"Images to PDF ({len(imgs)} images)")]


@op("rename")
async def op_rename(job):
    s = job.s
    new = job.params["name"]
    ext = Path(s.name).suffix or {"pdf": ".pdf", "audio": ".mp3", "video": ".mp4"}.get(s.kind, "")
    if s.kind == "office":
        ext = f".{s.ftype}"
    full = f"{new}{ext}"
    if s.kind in ("audio", "video"):
        src = await ensure_src(job)
    else:
        src = s.path
    cap = f"✅ {hd(tr('Renamed'))}\n<code>{H(full)}</code>"
    if s.kind == "video":
        o = await video_out(job, src, full, cap, op_name="Rename file")
        return [o]
    if s.kind == "audio":
        info = await probe(src)
        kind = "audio" if ext.lower() in AUDIO_AS_AUDIO else "doc"
        tags = info["tags"]
        thumb = s.dir / "cover.jpg"
        has = await extract_cover(src, thumb) if info["cover"] else False
        return [Out(src, kind, full, cap, thumb=thumb if has else None, duration=int(info["duration"] or s.duration),
                    title=tags.get("title") or s.data.get("title"), performer=tags.get("artist") or s.data.get("performer"),
                    op="Rename file")]
    return [Out(src, "pdf" if s.kind == "pdf" else "doc", full, cap, thumb=s.thumb, op="Rename file")]


@op("book_pdf")
async def op_book_pdf(job):
    s = job.s
    base = Path(s.name).stem or "book"
    dst = s.dir / f"book_{rand2()}.pdf"
    pages = s.data.get("pages", 0)
    label = L("📕", tr('Converting book to PDF'))
    await edit(job, progress_count(label, 0, pages) if pages else label, force=True)
    res = await run_book(job, Path(s.path), dst, label)
    if res.get("issues"):
        logger.warning("book %s: %d problem(s): %s", s.name, len(res["issues"]), "; ".join(res["issues"][:10]))
    if res.get("notes"):
        logger.info("book %s: %s", s.name, "; ".join(res["notes"]))
    lines = [okc(tr('Converted to PDF')),
             f"{tr('📑 Pages: {0}', res.get('pages', pages))} · {tr('💾 Size: {0}', fmt_size(res.get('bytes', 0)))}"]
    if res.get("issues"):
        lines.append(tr('⚠️ Some parts of the book may look different from the original.'))
    return [Out(dst, "doc", f"{base}.pdf", "\n".join(lines), op="Book to PDF")]


async def run_book(job, src, dst, label):
    """Runs flip_exe_to_pdf.py as a child process: low priority, hard CPU cap, RAM cap, timeout, cancel = kill.
    A child (not a thread) so the bot's event loop and its RAM stay untouched."""
    cmd = [sys.executable, str(BASE_DIR / "flip_exe_to_pdf.py"), "--json", str(src), str(dst)]

    def pre():
        _nice()
        if resource and BOOK_MAX_RAM_MB > 0:
            try:
                lim = BOOK_MAX_RAM_MB * MB
                resource.setrlimit(resource.RLIMIT_DATA, (lim, lim))
            except Exception:
                pass

    kwargs = {"preexec_fn": pre} if os.name == "posix" else {}
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs)
    job.proc = proc
    governor = asyncio.create_task(_cpu_governor(proc, BOOK_CPU_PERCENT))
    result, err_buf = {}, []

    async def read_out():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if "p" in ev:
                await edit(job, progress_count(label, ev["p"], ev["n"]))
            else:
                result.update(ev)

    async def read_err():
        err_buf.append(await proc.stderr.read())

    try:
        await asyncio.wait_for(asyncio.gather(read_out(), read_err(), proc.wait()), BOOK_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise JobError(tr('This took too long and was stopped. Try a smaller file.'))
    finally:
        governor.cancel()
        job.proc = None
    if job.cancelled:
        raise JobCancelled
    if proc.returncode != 0 or not result.get("done"):
        reason = result.get("error")
        if reason:
            logger.error("book converter error for %s: %s\n%s", job.s.name, reason, result.get("trace", ""))
            raise JobError(tr("Couldn't convert this book.") + f"\n<code>{H(str(reason))}</code>")
        logger.error("book converter failed (%s): %s", proc.returncode,
                     b"".join(err_buf).decode("utf-8", "ignore")[-400:])
        raise JobError(tr('Processing failed. The file may be corrupted or in an unsupported format.'))
    return result


# ══════════════════════════ عمليات الصوت والفيديو ══════════════════════════
FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "veryfast")


def _uid4():
    return uuid.uuid4().hex[:4]


async def video_out(job, path, name, caption="", op_name="", thumb=None):
    """يجهّز ملف فيديو للإرسال: قابل للبث لو الصيغة تسمح + صورة مصغرة تلقائية لو مفيش."""
    s = job.s
    info = await probe(path)
    dur = int(info["duration"] or s.duration or 0)
    ext = Path(name).suffix.lower()
    kind = "video" if ext in VIDEO_AS_VIDEO else "doc"
    if thumb is None:
        t = s.dir / f"{Path(name).stem[:30]}_{_uid4()}_t.jpg"
        if await auto_thumb(path, t, dur):
            thumb = t
    return Out(Path(path), kind, name, caption, thumb=thumb, duration=dur, width=info["width"],
               height=info["height"], op=op_name)


async def audio_out(job, path, name, caption="", op_name="", title=None, performer=None, thumb=None, kind=None):
    info = await probe(path)
    ext = Path(name).suffix.lower()
    k = kind or ("audio" if ext in AUDIO_AS_AUDIO else "doc")
    tags = info["tags"]
    if thumb is None and k in ("audio", "doc") and info["cover"]:
        t = job.s.dir / f"{Path(name).stem[:30]}_{_uid4()}_c.jpg"
        if await extract_cover(path, t):
            thumb = t
    return Out(Path(path), k, name, caption, thumb=thumb, duration=int(info["duration"] or 0),
               title=title or tags.get("title"), performer=performer or tags.get("artist"), op=op_name)


def check_video_limits(info):
    return   # مفيش حد للمدة — المهلة بتكبر مع طول الملف تلقائيًا


def size_note(before, after):
    if after >= before:
        return tr("\nℹ️ Output isn't smaller than the original (already compressed).")
    return f"\n📉 {fmt_size(before)} → {fmt_size(after)} (−{(1 - after / before) * 100:.0f}%)"


def _br(fmt, q):
    """q: مستوى قديم (hi/mid/lo) أو رقم kbps دقيق."""
    if q in CONV_BITRATE[fmt]:
        return CONV_BITRATE[fmt][q]
    return f"{int(q)}k"


def audio_enc(fmt, q="mid"):
    if fmt == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", _br("mp3", q)], ".mp3"
    if fmt == "m4a":
        return ["-c:a", "aac", "-b:a", _br("m4a", q), "-movflags", "+faststart"], ".m4a"
    if fmt == "opus":
        return ["-c:a", "libopus", "-b:a", _br("opus", q), "-vbr", "on"], ".ogg"
    if fmt == "voice":
        return ["-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000", "-application", "voip"], ".ogg"
    if fmt == "wav":
        return ["-c:a", "pcm_s16le"], ".wav"
    if fmt == "flac":
        return ["-c:a", "flac"], ".flac"
    raise JobError(tr('Unsupported format.'))


FMT_AR = TrMap({"mp3": "MP3", "m4a": "M4A", "opus": "Opus", "voice": "Voice note", "wav": "WAV", "flac": "FLAC"})


@op("aconvert")
async def op_aconvert(job):
    s = job.s
    fmt, q = job.params["fmt"], job.params.get("q", "mid")
    src = await ensure_src(job)
    info = await probe(src)
    dur = info["duration"] or s.duration
    enc, ext = audio_enc(fmt, q)
    out = s.dir / f"{Path(s.name).stem}_conv{ext}"
    await run_ffmpeg(job, ["-i", str(src), "-map", "0:a:0", "-vn", *enc, *enc_threads(job), str(out)],
                     duration=dur, timeout=AUDIO_TIMEOUT, label=L("🎵", tr('Converting to {0}', FMT_AR[fmt])))
    name = f"{safe_name_text(Path(s.name).stem)}{ext}"
    cap = okc(tr('Converted to {0}', FMT_AR[fmt]))
    if fmt in CONV_BITRATE and str(q).isdigit():
        cap += tr('\n🎚️ Quality: {0} kbps', q)
    if fmt == "voice":
        return [Out(out, "voice", name, cap, duration=int(dur or 0), op=f"Convert audio to {FMT_AR.en(fmt)}")]
    return [await audio_out(job, out, name, cap, f"Convert audio to {FMT_AR.en(fmt)}",
                            title=s.data.get("title"), performer=s.data.get("performer"))]


@op("acompress")
async def op_acompress(job):
    s = job.s
    fmt, level = job.params["fmt"], job.params["level"]
    src = await ensure_src(job)
    info = await probe(src)
    dur = info["duration"] or s.duration
    br, sr = AUDIO_LEVELS[fmt][level]
    ext = ".ogg" if fmt == "opus" else ".m4a"
    out = s.dir / f"{Path(s.name).stem}_compressed{ext}"
    codec = (["-c:a", "libopus", "-application", "voip"] if fmt == "opus"
             else ["-c:a", "aac", "-movflags", "+faststart"])
    await run_ffmpeg(job, ["-i", str(src), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sr), *codec, "-b:a", br,
                           *enc_threads(job), str(out)],
                     duration=dur, timeout=AUDIO_TIMEOUT, label=L("🗜️", tr('Compressing audio ({0})', LEVEL_AR[level])))
    name = f"{safe_name_text(Path(s.name).stem)}_compressed{ext}"
    cap = okc(tr('Audio compressed')) + size_note(src.stat().st_size, out.stat().st_size)
    return [await audio_out(job, out, name, cap, f"Compress audio ({LEVEL_AR.en(level)})",
                            title=s.data.get("title"), performer=s.data.get("performer"))]


@op("astream")
async def op_astream(job):
    s = job.s
    src = await ensure_src(job)
    ext = src.suffix.lower()
    stem = safe_name_text(Path(s.name).stem)
    if ext in AUDIO_AS_AUDIO:
        return [await audio_out(job, src, f"{stem}{ext}", okc(tr('Sent as streamable audio')), "Make streamable",
                                title=s.data.get("title"), performer=s.data.get("performer"))]
    info = await probe(src)
    enc, e2 = audio_enc("mp3", "hi")
    out = s.dir / f"{stem}_stream{e2}"
    await run_ffmpeg(job, ["-i", str(src), "-map", "0:a:0", "-vn", *enc, *enc_threads(job), str(out)],
                     duration=info["duration"], timeout=AUDIO_TIMEOUT, label=L("📡", tr('Converting to streamable MP3')))
    return [await audio_out(job, out, f"{stem}{e2}", okc(tr('Converted to streamable MP3')), "Make streamable")]


@op("ameta")
async def op_ameta(job):
    s = job.s
    p = job.params
    title, artist, cover, remove = p.get("title"), p.get("artist"), p.get("cover"), p.get("remove_cover")
    src = await ensure_src(job)
    info = await probe(src)
    ext = src.suffix.lower()
    stem = safe_name_text(Path(s.name).stem)
    old = info["tags"]
    args = ["-i", str(src)]
    if ext not in TAGGABLE_EXTS:        # صيغة مبتدعمش وسوم → هنحولها MP3 بجودة عالية
        ext = ".mp3"
        enc, _ = audio_enc("mp3", "hi")
        codec = enc
        convert = True
    else:
        codec = ["-c", "copy"]
        convert = False
    can_embed = ext in EMBED_COVER_EXTS
    cover_jpg = None
    if cover:
        cover_jpg = s.dir / "cover600.jpg"
        await asyncio.to_thread(make_jpeg, cover, cover_jpg, 600, 400)
    if cover_jpg and can_embed:
        args += ["-i", str(cover_jpg), "-map", "0:a", "-map", "1:v"]
    elif remove or convert:
        args += ["-map", "0:a"]
    else:
        args += ["-map", "0"]
    args += codec
    if cover_jpg and can_embed:
        args += ["-disposition:v:0", "attached_pic"]
        if ext == ".mp3":
            args += ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    if ext == ".mp3":
        args += ["-id3v2_version", "3"]
    if title:
        args += ["-metadata", f"title={title}"]
    if artist:
        args += ["-metadata", f"artist={artist}"]
    out = s.dir / f"{stem}_tagged{ext}"
    args += [*enc_threads(job), str(out)]
    await run_ffmpeg(job, args, duration=info["duration"], timeout=AUDIO_TIMEOUT, label=L("🏷️", tr('Updating audio tags')))
    # الصورة المصغرة اللي بتظهر في تليجرام
    thumb = None
    if cover:
        thumb = s.dir / "tg_thumb.jpg"
        await asyncio.to_thread(make_jpeg, cover, thumb, 320, 190)
    f_title = title or old.get("title") or s.data.get("title")
    f_artist = artist or old.get("artist") or s.data.get("performer")
    o = await audio_out(job, out, f"{stem}{ext}", okc(tr('Tags updated')), "Edit audio tags",
                        title=f_title, performer=f_artist, thumb=thumb)
    if remove and not cover:
        o.thumb = None
    return [o]


def fmt_hms(sec):
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s2 = divmod(r, 60)
    if h:
        return f"{h}h{m:02d}m{s2:02d}s"
    if m:
        return f"{m}m{s2:02d}s"
    return f"{s2}s"


def range_code(a, b):
    """المدة بتتكتب كـ code (من غير نقطتين) عشان تليجرام ميحولهاش لروابط وقت."""
    end = "end" if b is None else fmt_hms(b)
    return f"<code>{fmt_hms(a)} → {end}</code>"


def _map(audio):
    return ["-map", "0:a:0", "-vn"] if audio else ["-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]


def _norm_ranges(ranges, total):
    out = []
    for a, b in ranges:
        if total and a >= total:
            continue
        if total and b is not None and b > total:
            b = total
        out.append((a, b))
    return out


async def _cut_copy(job, src, out, a, end, audio, mp4ish, label):
    args = ["-ss", f"{a:.3f}", "-i", str(src)]
    if end is not None:
        args += ["-t", f"{end - a:.3f}"]
    args += [*_map(audio), "-c", "copy", "-avoid_negative_ts", "make_zero"]
    if mp4ish:
        args += ["-movflags", "+faststart"]
    args += [str(out)]
    await run_ffmpeg(job, args, duration=(end - a) if end is not None else 0, timeout=VIDEO_TIMEOUT, label=label)


def _media_ctx(s, src, audio):
    ext = src.suffix or (".mp3" if audio else ".mp4")
    mp4ish = ext.lower() in (".mp4", ".m4a", ".mov", ".m4v", ".3gp")
    return ext, mp4ish, safe_name_text(Path(s.name).stem)


async def split_media(job, audio):
    """تقسيم لأجزاء: كل جزء بيتقص ويتبعت ويتمسح (عشان الديسك) قبل اللي بعده."""
    s, p = job.s, job.params
    src = await ensure_src(job)
    info = await probe(src)
    total = info["duration"] or s.duration or 0
    ext, mp4ish, base = _media_ctx(s, src, audio)
    if p["mode"] == "custom":
        ranges = _norm_ranges(p["ranges"], total)
    else:
        seg = p["seconds"]
        if not total:
            raise JobError(tr("Couldn't detect the duration, so equal-length splitting isn't possible. Try custom ranges."))
        n0 = int(-(-total // seg))
        if n0 <= 1:
            raise JobError(tr('Part length is longer than the file itself, nothing to split.'))
        ranges = [(i * seg, min((i + 1) * seg, total)) for i in range(n0)]
    if not ranges:
        raise JobError(tr("Those ranges fall outside the file's duration."))
    if len(ranges) > MAX_PARTS:
        raise JobError(tr('This split would create {0} parts; the maximum is {1}. Pick a longer part length.', len(ranges), MAX_PARTS))
    n, sent, tags = len(ranges), 0, info["tags"]
    for i, (a, b) in enumerate(ranges, 1):
        if job.cancelled:
            raise JobCancelled
        f = s.dir / f"{base}_Part{i}{ext}"
        end = b if b is not None else (total or None)
        await _cut_copy(job, src, f, a, end, audio, mp4ish, L("✂️", tr('Cutting part {0}/{1}', i, n)))
        if not f.exists() or f.stat().st_size == 0:
            continue
        cap = f"✂️ {hd(tr('Part'))} {i}/{n}\n🕒 {range_code(a, end)}"
        if audio:
            t0 = s.data.get("title") or tags.get("title") or Path(s.name).stem
            o = await audio_out(job, f, f.name, cap, f"Split audio (part {i})", title=f"{t0} — {i}",
                                performer=s.data.get("performer"))
        else:
            o = await video_out(job, f, f.name, cap, f"Split video (part {i})")
        await send_outs(job, [o], offset=sent, grand=n)
        sent += 1
        _safe_remove(f)
        if o.thumb:
            _safe_remove(o.thumb)
    if not sent:
        raise JobError(tr('No parts were produced. Check the times you entered.'))
    return []


def _trim_graph(ranges, total, has_video, has_audio):
    parts, labels = [], ""
    for i, (a, b) in enumerate(ranges):
        end = b if b is not None else total
        if has_video:
            parts.append(f"[0:v:0]trim=start={a:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]")
            labels += f"[v{i}]"
        if has_audio:
            parts.append(f"[0:a:0]atrim=start={a:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels += f"[a{i}]"
    outs = ("[v]" if has_video else "") + ("[a]" if has_audio else "")
    parts.append(f"{labels}concat=n={len(ranges)}:v={int(has_video)}:a={int(has_audio)}{outs}")
    return ";".join(parts)


async def cut_one(job, audio):
    """قص النطاقات المحددة وتجميعها في ملف واحد."""
    s, p = job.s, job.params
    src = await ensure_src(job)
    info = await probe(src)
    total = info["duration"] or s.duration or 0
    ranges = _norm_ranges(p["ranges"], total)
    if not ranges:
        raise JobError(tr("Those ranges fall outside the file's duration."))
    ext, mp4ish, base = _media_ctx(s, src, audio)
    spans = sum(((b if b is not None else total) - a) for a, b in ranges)
    precise = (not audio) and bool(p.get("precise"))
    if precise and LIGHT_HOST and spans > PRECISE_CUT_MAX_MIN * 60:
        raise JobError(tr('Precise cut on this server is limited to {0} minutes of kept video. Send shorter ranges, or use Fast.',
                          PRECISE_CUT_MAX_MIN))
    out = None

    async def encode_path():
        o = s.dir / (f"{base}_Cut.mp4" if not audio else f"{base}_Cut.mp3")
        if audio:
            graph = _trim_graph(ranges, total, False, True)
            args = ["-i", str(src), "-filter_complex", graph, "-map", "[a]", "-c:a", "libmp3lame", "-b:a", "192k",
                    *enc_threads(job), str(o)]
        else:
            hv, ha = info["has_video"], info["has_audio"]
            graph = _trim_graph(ranges, total, hv, ha)
            args = ["-i", str(src), "-filter_complex", graph]
            if hv:
                args += ["-map", "[v]"]
            if ha:
                args += ["-map", "[a]"]
            args += ["-c:v", "libx264", "-preset", x264_preset(), "-crf", str(x264_crf()), "-pix_fmt", "yuv420p"]
            if ha:
                args += ["-c:a", "aac", "-b:a", "128k"]
            args += ["-movflags", "+faststart", *enc_threads(job), str(o)]
        await run_ffmpeg(job, args, duration=spans, timeout=VIDEO_TIMEOUT, label=L("🎯", tr('Precise cut and merge')))
        return o

    if precise:
        # Frame-accurate: each kept range is cut with input seeking (decode starts at the nearest keyframe, the extra
        # frames are discarded) and re-encoded. Only the kept part is processed, never the whole video.
        n_r = len(ranges)
        pieces = []
        for i, (a, b) in enumerate(ranges, 1):
            end = b if b is not None else (total or None)
            f = s.dir / (f"{base}_Cut.mp4" if n_r == 1 else f"_pcut{i}.mp4")
            args = ["-ss", f"{a:.3f}", "-i", str(src)]
            if end is not None:
                args += ["-t", f"{end - a:.3f}"]
            args += ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", *_x264_args(job, info),
                     "-movflags", "+faststart", str(f)]
            lbl = L("🎯", tr('Precise cut {0}/{1}', i, n_r)) if n_r > 1 else L("🎯", tr('Precise cut'))
            await run_ffmpeg(job, args, duration=(end - a) if end is not None else 0, timeout=VIDEO_TIMEOUT, label=lbl)
            pieces.append(f)
        if n_r == 1:
            out = pieces[0]
        else:
            listf = s.dir / "concat.txt"
            listf.write_text("".join("file '{}'\n".format(str(f).replace("'", "'\\''")) for f in pieces), encoding="utf-8")
            out = s.dir / f"{base}_Cut.mp4"
            rc, _ = await run_ffmpeg(job, ["-f", "concat", "-safe", "0", "-i", str(listf), "-c", "copy",
                                           "-movflags", "+faststart", str(out)],
                                     duration=0, timeout=VIDEO_TIMEOUT, label=L("🔗", tr('Joining parts')), check=False)
            for f in pieces:
                _safe_remove(f)
            if rc != 0 or not out.exists() or out.stat().st_size == 0:
                out = await encode_path()
    elif len(ranges) == 1:
        a, b = ranges[0]
        out = s.dir / f"{base}_Cut{ext}"
        await _cut_copy(job, src, out, a, b if b is not None else (total or None), audio, mp4ish, L("✂️", tr('Cutting')))
    else:
        pieces = []
        for i, (a, b) in enumerate(ranges, 1):
            f = s.dir / f"_piece{i}{ext}"
            await _cut_copy(job, src, f, a, b if b is not None else (total or None), audio, mp4ish,
                            L("✂️", tr('Cutting part {0}/{1}', i, len(ranges))))
            pieces.append(f)
        listf = s.dir / "concat.txt"
        listf.write_text("".join("file '{}'\n".format(str(f).replace("'", "'\\''")) for f in pieces), encoding="utf-8")
        out = s.dir / f"{base}_Cut{ext}"
        rc, _ = await run_ffmpeg(job, ["-f", "concat", "-safe", "0", "-i", str(listf), "-c", "copy",
                                       *(["-movflags", "+faststart"] if mp4ish else []), str(out)],
                                 duration=0, timeout=VIDEO_TIMEOUT, label=L("🔗", tr('Joining parts')),
                                 check=False)
        for f in pieces:
            _safe_remove(f)
        if rc != 0 or not out.exists() or out.stat().st_size == 0:
            out = await encode_path()      # لو التجميع السريع فشل نعيد الترميز
    if not out or not out.exists() or out.stat().st_size == 0:
        raise JobError(tr('Cut failed. Check the times you entered.'))
    cap = okc(tr('Cut and merged')) if len(ranges) > 1 else okc(tr('Cut complete'))
    if audio:
        return [await audio_out(job, out, out.name, cap, "Cut audio (single output)", title=s.data.get("title"),
                                performer=s.data.get("performer"))]
    return [await video_out(job, out, out.name, cap, "Cut video (single output)")]


@op("asplit")
async def op_asplit(job):
    return await split_media(job, True)


@op("vsplit")
async def op_vsplit(job):
    return await split_media(job, False)


@op("acut")
async def op_acut(job):
    return await cut_one(job, True)


@op("vcut")
async def op_vcut(job):
    return await cut_one(job, False)


# ───────────────────────── دمج (صوت / فيديو) ─────────────────────────
# Files are NOT downloaded while the user is still adding them (only their file_ids are kept), so an open merge
# session costs no disk. They are downloaded one by one when the user taps Merge Now.
def merge_item_from_session(s):
    return {"file_id": s.file_id, "name": s.name, "size": s.size, "duration": s.duration, "orig": s.orig,
            "title": s.data.get("title"), "performer": s.data.get("performer")}


def merge_need(items):
    """Disk to reserve: all inputs + the output (≈ the same size) + a safety margin."""
    return int(max(sum(i["size"] or 0 for i in items) * 2.3, 20 * MB))


def mg_text(s, added=False):
    items = s.data["mg_items"]
    lines = []
    for i, it in enumerate(items, 1):
        bits = [fmt_size(it["size"])] if it["size"] else []
        if it.get("duration"):
            bits.append(fmt_time(it["duration"]))
        lines.append(f"{i}. <code>{H(it['name'])}</code>" + (" · " + " · ".join(bits) if bits else ""))
    tot = [fmt_size(sum(i["size"] or 0 for i in items))]
    if all(i.get("duration") for i in items):
        tot.append(fmt_time(sum(i["duration"] for i in items)))
    tail = tr('📥 Added! Send more files, or tap <b>Merge Now</b>.') if added else tr('📥 Send the remaining files in the order you want.')
    return f"🔗 {hd(tr('Merge mode'))}\n\n" + "\n".join(lines) + f"\n\n{tr('📦 Total: {0}', ' · '.join(tot))}\n\n{tail}"


def mg_kb():
    return kb([(tr('✅ Merge Now'), "mg_go")], [(tr('↩️ Remove Last'), "mg_undo")], [(tr('❌ Cancel'), "cancel_merge")])


async def add_merge_media(m, s, kind, info):
    """A file arrived while the user is in audio/video merge mode."""
    items = s.data["mg_items"]
    if kind != s.kind:
        await m.reply_text(tr('❌ Send {0} files only (or tap Cancel).', tr("audio") if s.kind == "audio" else tr("video")))
        return
    if await reject_if_big(m, info["size"], MAX_VIDEO_MB if kind == "video" else MAX_AUDIO_MB):
        return
    if len(items) >= MAX_MERGE_FILES:
        await m.reply_text(tr('⚠️ Maximum reached ({0} files).', MAX_MERGE_FILES))
        return
    new = {"file_id": info["file_id"], "name": info["name"], "size": info["size"], "duration": info.get("duration", 0),
           "orig": info["orig"], "title": info.get("title"), "performer": info.get("performer")}
    if merge_need(items + [new]) > WORK_BUDGET_MB * MB:
        await m.reply_text(tr('❌ The total size is too large to merge on this server. Remove a file or send smaller ones.'))
        return
    items.append(new)
    touch(s)
    archive_media(m.from_user, info["file_id"], "received", name=new["name"],
                  extra=f"💾 {fmt_size(new['size'])}" if new["size"] else None)
    await safe_edit(s.data["mg_msg"], mg_text(s, added=True), mg_kb())


def _a_sig(inf):
    return (inf["acodec"], inf["asr"], inf["ach"])


def _v_sig(inf):
    return (inf["vcodec"], inf["width"], inf["height"], round(inf["fps"] or 0, 2), inf["yuv420"], inf["rot"],
            _a_sig(inf) if inf["has_audio"] else None)


def _v_diff(infos):
    """Human-readable list of what differs between the videos (for the error message)."""
    names = []
    if len({i["vcodec"] for i in infos}) > 1:
        names.append("codec")
    if len({(i["width"], i["height"], i["rot"]) for i in infos}) > 1:
        names.append("resolution")
    if len({round(i["fps"] or 0, 2) for i in infos}) > 1:
        names.append("frame rate")
    if len({(_a_sig(i) if i["has_audio"] else None) for i in infos}) > 1:
        names.append("audio")
    return ", ".join(names) or "format"


def _concat_list(path, files):
    path.write_text("".join("file '{}'\n".format(str(f).replace("'", "'\\''")) for f in files), encoding="utf-8")


async def merge_media(job, audio):
    s = job.s
    items = s.data.get("mg_items") or []
    n = len(items)
    if n < 2:
        raise JobError(tr('Send at least one more file to merge.'))
    await wait_for_disk(job, merge_need(items))
    paths, infos = [], []
    for i, it in enumerate(items, 1):
        if job.cancelled:
            raise JobCancelled
        label = L("📥", tr('Downloading {0}/{1}', i, n))
        await edit(job, label, force=True)
        ext = Path(it["name"]).suffix or (".mp3" if audio else ".mp4")
        p = await app.download_media(it["file_id"], file_name=str(s.dir / f"mg{i}{ext}"),
                                     progress=make_progress(job, label, total_hint=it["size"], word="received"))
        if job.cancelled:
            raise JobCancelled
        if not p:
            raise JobError(tr('Download failed. Please try again.'))
        p = Path(p)
        paths.append(p)
        inf = await probe(p)
        if audio and not inf["has_audio"]:
            raise JobError(tr('<code>{0}</code> has no audio.', H(it["name"])))
        if not audio and not inf["has_video"]:
            raise JobError(tr('<code>{0}</code> has no video.', H(it["name"])))
        infos.append(inf)
    total_dur = sum((inf["duration"] or it.get("duration") or 0) for inf, it in zip(infos, items))
    exts = [p.suffix.lower() for p in paths]
    stem = safe_name_text(Path(items[0]["name"]).stem)
    out = None
    copied = False

    # 1) fast path: identical streams -> join without re-encoding
    sigs = {(e, _a_sig(i)) if audio else (e, _v_sig(i)) for e, i in zip(exts, infos)}
    if len(sigs) == 1:
        out_ext = exts[0] if exts[0] else (".mp3" if audio else ".mp4")
        if not audio and out_ext not in (".mp4", ".mov", ".m4v", ".mkv", ".webm"):
            out_ext = ".mkv"
        cand = s.dir / f"merged{out_ext}"
        listf = s.dir / "concat.txt"
        _concat_list(listf, paths)
        maps = ["-map", "0:a:0", "-vn"] if audio else ["-map", "0:v:0", "-map", "0:a:0?"]
        extra = ["-movflags", "+faststart"] if out_ext in (".mp4", ".m4a", ".mov", ".m4v") else []
        rc, _ = await run_ffmpeg(job, ["-f", "concat", "-safe", "0", "-i", str(listf), *maps, "-c", "copy", *extra, str(cand)],
                                 duration=total_dur, timeout=VIDEO_TIMEOUT, label=L("🔗", tr('Merging {0} files', n)),
                                 check=False)
        if rc == 0 and cand.exists() and cand.stat().st_size > 0:
            out, copied = cand, True
        else:
            logger.warning("concat copy failed (rc=%s), falling back to re-encode", rc)

    # 2) otherwise re-encode everything to one common format
    if out is None:
        if audio:
            fmt = {".mp3": "mp3", ".m4a": "m4a", ".aac": "m4a", ".mp4": "m4a", ".ogg": "opus", ".opus": "opus",
                   ".oga": "opus"}.get(exts[0], "mp3")
            enc, out_ext = audio_enc(fmt, "hi")
            sr = 48000 if fmt == "opus" else (infos[0]["asr"] if infos[0]["asr"] in (32000, 44100, 48000) else 44100)
            layout = "mono" if all(i["ach"].startswith("mono") for i in infos) else "stereo"
            parts = [f"[{i}:a:0]aresample={sr},aformat=channel_layouts={layout}[a{i}]" for i in range(n)]
            parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]")
            args = []
            for p in paths:
                args += ["-i", str(p)]
            args += ["-filter_complex", ";".join(parts), "-map", "[a]", *enc, *enc_threads(job)]
            out = s.dir / f"merged{out_ext}"
            args.append(str(out))
            label = L("🔗", tr('Merging {0} files', n))
        else:
            if LIGHT_HOST and total_dur > MERGE_REENCODE_MAX_MIN * 60:
                raise JobError(tr('These videos have different formats ({0}), so merging needs re-encoding. On this server that is limited to {1} minutes in total.',
                                  _v_diff(infos), MERGE_REENCODE_MAX_MIN))
            f0 = infos[0]
            w, h = f0["width"] or 1280, f0["height"] or 720
            if abs(f0["rot"]) in (90.0, 270.0):
                w, h = h, w
            short = min(w, h)
            if short > 1080:
                k = 1080 / short
                w, h = int(w * k), int(h * k)
            w, h = w - w % 2, h - h % 2
            fps = int(round(f0["fps"])) if f0["fps"] else 30
            fps = max(15, min(fps, 30))
            any_audio = any(i["has_audio"] for i in infos)
            parts, labels, extra_in = [], "", []
            for i, inf in enumerate(infos):
                parts.append(f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{i}]")
                if any_audio:
                    if inf["has_audio"]:
                        parts.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
                    else:       # a clip without sound gets silence of its own length so the rest stays in sync
                        k_in = n + len(extra_in) // 6
                        dur_i = inf["duration"] or items[i].get("duration") or 1
                        extra_in += ["-f", "lavfi", "-t", f"{dur_i:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
                        parts.append(f"[{k_in}:a]aformat=channel_layouts=stereo[a{i}]")
                    labels += f"[v{i}][a{i}]"
                else:
                    labels += f"[v{i}]"
            parts.append(f"{labels}concat=n={n}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else ""))
            args = []
            for p in paths:
                args += ["-i", str(p)]
            args += extra_in
            args += ["-filter_complex", ";".join(parts), "-map", "[v]"]
            if any_audio:
                args += ["-map", "[a]"]
            args += ["-c:v", "libx264", "-preset", x264_preset(), "-crf", str(x264_crf()), "-pix_fmt", "yuv420p"]
            if any_audio:
                args += ["-c:a", "aac", "-b:a", "128k"]
            out = s.dir / "merged.mp4"
            args += ["-movflags", "+faststart", *enc_threads(job), str(out)]
            label = L("🔗", tr('Merging {0} files', n))
        await run_ffmpeg(job, args, duration=total_dur, timeout=VIDEO_TIMEOUT if not audio else AUDIO_TIMEOUT, label=label)

    if not out or not out.exists() or out.stat().st_size == 0:
        raise JobError(tr('Merge failed. The files may be corrupted or in an unsupported format.'))
    for p in paths:                      # free the disk before uploading the result
        _safe_remove(p)
    name = f"{stem}_Merged{out.suffix}"
    cap = okc(tr('Files merged')) + (tr('\n⚡ Joined without re-encoding.') if copied else tr('\n🔄 Re-encoded so all parts match.'))
    if audio:
        return [await audio_out(job, out, name, cap, "Merge audio", title=items[0].get("title") or stem,
                                performer=items[0].get("performer"))]
    return [await video_out(job, out, name, cap, "Merge video")]


@op("amerge")
async def op_amerge(job):
    return await merge_media(job, True)


@op("vmerge")
async def op_vmerge(job):
    return await merge_media(job, False)


# ───────────────────────── فيديو ─────────────────────────
@op("vcompress")
async def op_vcompress(job):
    if LIGHT_HOST:
        raise JobError(tr("This tool is turned off on this server."))
    s = job.s
    level = job.params["level"]
    mode = job.params.get("mode") or VIDEO_DEFAULT_MODE
    fast = mode == "fast"
    cfg = VIDEO_LEVELS[level]
    src = await ensure_src(job)
    info = await probe(src)
    check_video_limits(info)
    out = s.dir / f"{Path(s.name).stem}_compressed.mp4"
    vf = vscale(cfg["h"])
    if fast:
        cap = VIDEO_FAST_FPS[level]
        if info.get("fps", 0) > cap + 0.5:      # drop frames first (less work for the scaler/encoder); never raise the fps
            vf = f"fps={cap}," + vf
    args = ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", vf,
            "-c:v", "libx264", "-preset", VIDEO_FAST_PRESET if fast else VIDEO_QUALITY_PRESET,
            "-crf", str(cfg["crf"] + (VIDEO_FAST_CRF_ADD if fast else 0)), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", cfg["ab"], "-ac", str(cfg["ac"]),
            "-movflags", "+faststart", *enc_threads(job), str(out)]
    await run_ffmpeg(job, args, duration=info["duration"], timeout=VIDEO_TIMEOUT,
                     label=L("🗜️", tr('Compressing video ({0})', LEVEL_AR[level])))
    cap_txt = okc(tr('Video compressed')) + size_note(src.stat().st_size, out.stat().st_size)
    name = f"{safe_name_text(Path(s.name).stem)}_compressed.mp4"
    return [await video_out(job, out, name, cap_txt,
                            f"Compress video ({LEVEL_AR.en(level)} | {'Fast' if fast else 'Quality'})")]


def x264_preset():
    """LIGHT_HOST uses the fastest preset (the CPU cap is the real limit there)."""
    return VIDEO_FAST_PRESET if LIGHT_HOST else FFMPEG_PRESET


def x264_crf():
    return 23 + (VIDEO_FAST_CRF_ADD if LIGHT_HOST else 0)    # ultrafast is less efficient; a higher CRF keeps the size similar


def _x264_args(job, info, crf=None, h=0):
    crf = x264_crf() if crf is None else crf
    cap = h or (1080 if min(info.get("width") or 0, info.get("height") or 0) > 1080 else 0)
    vf = ["-vf", vscale(cap)] if cap else []
    return [*vf, "-c:v", "libx264", "-preset", x264_preset(), "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", *enc_threads(job)]


def _can_copy(tgt, info):
    v, a = info["vcodec"], info["acodec"]
    if tgt == "mp4":
        return v == "h264" and info["yuv420"] and a in (None, "aac", "mp3")
    if tgt == "mov":
        return v in ("h264", "hevc") and a in (None, "aac", "mp3", "alac")
    if tgt == "mkv":
        return v is not None
    if tgt == "webm":
        return v in ("vp8", "vp9", "av1") and a in (None, "opus", "vorbis")
    if tgt == "avi":
        return v in ("h264", "mpeg4") and a in (None, "aac", "mp3")
    return False


@op("vconvert")
async def op_vconvert(job):
    s = job.s
    tgt = job.params["fmt"]
    h = int(job.params.get("h") or 0)          # 0 = نفس دقة الأصل
    src = await ensure_src(job)
    info = await probe(src)
    out = s.dir / f"{Path(s.name).stem}_conv.{tgt}"
    if LIGHT_HOST and (h or not _can_copy(tgt, info)):
        raise JobError(tr("This change needs re-encoding the video, which is turned off on this server."))
    done = False
    if not h and _can_copy(tgt, info):
        extra = ["-movflags", "+faststart"] if tgt in ("mp4", "mov") else []
        rc, _ = await run_ffmpeg(job, ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", *extra, str(out)],
                                 duration=info["duration"], timeout=VIDEO_TIMEOUT,
                                 label=L("🔄", tr('Converting to {0}', tgt.upper())), check=False)
        done = rc == 0 and out.exists() and out.stat().st_size > 0
    if not done and LIGHT_HOST:
        raise JobError(tr("This change needs re-encoding the video, which is turned off on this server."))
    if not done:
        vf = ["-vf", vscale(h)] if h else []
        if tgt in ("mp4", "mov", "mkv"):
            enc = _x264_args(job, info, h=h)
            if tgt in ("mp4", "mov"):
                enc += ["-movflags", "+faststart"]
        elif tgt == "webm":
            enc = [*vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-deadline", "realtime", "-cpu-used", "8",
                   "-row-mt", "1", "-c:a", "libopus", "-b:a", "96k", *enc_threads(job)]
        else:  # avi
            enc = [*vf, "-c:v", "mpeg4", "-q:v", "5", "-c:a", "libmp3lame", "-b:a", "128k", *enc_threads(job)]
        await run_ffmpeg(job, ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", *enc, str(out)],
                         duration=info["duration"], timeout=VIDEO_TIMEOUT,
                         label=L("🔄", tr('Converting to {0}', tgt.upper()) + (f" ({h}p)" if h else "")))
    name = f"{safe_name_text(Path(s.name).stem)}.{tgt}"
    note = "" if tgt in ("mp4", "mov") else tr("\nℹ️ This format can't be streamed in Telegram, so it was sent as a file.")
    res = tr('\n📐 Resolution: {0}p', h) if h else ""
    return [await video_out(job, out, name, f"{okc(tr('Converted to {0}', tgt.upper()))}{res}{note}",
                            f"Convert video to {tgt.upper()}")]


@op("vstream")
async def op_vstream(job):
    s = job.s
    src = await ensure_src(job)
    info = await probe(src)
    check_video_limits(info)
    out = s.dir / f"{Path(s.name).stem}_stream.mp4"
    done = False
    if _can_copy("mp4", info):
        rc, _ = await run_ffmpeg(job, ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                                       "-movflags", "+faststart", str(out)],
                                 duration=info["duration"], timeout=VIDEO_TIMEOUT,
                                 label=L("📡", tr('Preparing for streaming')), check=False)
        done = rc == 0 and out.exists() and out.stat().st_size > 0
    if not done and LIGHT_HOST:
        raise JobError(tr("This video needs re-encoding to be streamable, which is turned off on this server."))
    if not done:
        await run_ffmpeg(job, ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", *_x264_args(job, info),
                               "-movflags", "+faststart", str(out)],
                         duration=info["duration"], timeout=VIDEO_TIMEOUT,
                         label=L("📡", tr('Converting for streaming')))
    name = f"{safe_name_text(Path(s.name).stem)}.mp4"
    return [await video_out(job, out, name, okc(tr('Sent as streamable video')), "Make video streamable")]


@op("vaudio")
async def op_vaudio(job):
    s = job.s
    fmt = job.params["fmt"]
    src = await ensure_src(job)
    info = await probe(src)
    if not info["has_audio"]:
        raise JobError(tr('This video has no audio.'))
    enc, ext = audio_enc(fmt, "mid")
    out = s.dir / f"{Path(s.name).stem}_audio{ext}"
    await run_ffmpeg(job, ["-i", str(src), "-map", "0:a:0", "-vn", *enc, *enc_threads(job), str(out)],
                     duration=info["duration"], timeout=AUDIO_TIMEOUT, label=L("🎵", tr('Extracting audio ({0})', FMT_AR[fmt])))
    name = f"{safe_name_text(Path(s.name).stem)}{ext}"
    return [await audio_out(job, out, name, okc(tr('Audio extracted')), f"Extract audio ({FMT_AR.en(fmt)})",
                            title=Path(s.name).stem)]


@op("vcover")
async def op_vcover(job):
    s = job.s
    p = job.params
    cover = p.get("cover")
    if p.get("time") is not None:
        src = await ensure_src(job)
        cover = s.dir / "frame_cover.jpg"
        info = await probe(src)
        t = p["time"]
        if info["duration"] and t >= info["duration"]:
            t = max(0.0, info["duration"] - 1)
        if not await grab_frame(src, cover, t, side=1280):
            raise JobError(tr("Couldn't grab a frame at that time. Try another time."))
    caption = okc(tr('Thumbnail updated'))
    if COVER_BY_ID and s.orig == "video" and s.file_id:
        try:
            await edit(job, tr('{0}\nNo re-upload needed.', L('🖼️', tr('Updating thumbnail'))), force=True)
            await send_video_with_cover(job.chat_id, s.file_id, cover, caption)
            archive_note(job.user, "🖼️", "Thumbnail changed")
            return []
        except Exception as e:
            logger.warning("cover-by-id failed, falling back to re-upload: %s", e)
    src = await ensure_src(job)
    thumb = s.dir / "thumb.jpg"
    await asyncio.to_thread(make_jpeg, cover, thumb, 320, 190)
    return [await video_out(job, src, s.name, caption, "Change thumbnail", thumb=thumb)]


# ══════════════════════════ تفريغ بـ Gemini + تلخيص ══════════════════════════
# Flow: Transcribe -> (compress only if the audio is too big for an inline request) -> parts of TRANSCRIBE_CHUNK_MIN minutes
# -> text kept in the session -> user picks "TXT file" or "In chat" -> then a "Summarize" button.
GEMINI_AUDIO_MIME = {".mp3": "audio/mp3", ".wav": "audio/wav", ".ogg": "audio/ogg", ".opus": "audio/ogg",
                     ".oga": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac"}
INLINE_AUDIO_MAX = 14 * MB            # Gemini inline requests are capped at ~20 MB in total, and base64 adds ~33%

TRANSCRIBE_PROMPT = (
    "Transcribe this audio recording word for word, in the language(s) actually spoken. "
    "If the speaker mixes Arabic with English or medical/technical terms, keep those terms in English (Latin letters). "
    "Do not translate and do not summarize. Output only the transcript as plain text, with a paragraph break at natural "
    "pauses or topic changes. No timestamps, no speaker labels, no comments. Mark unclear speech as [inaudible].")

SUMMARY_PROMPT = (
    "You are helping a student review a lecture. Summarize the transcript below. Write in the same language as the "
    "transcript (if it mixes Arabic with English terms, write Arabic and keep the English terms in English). "
    "Structure: a one-line title; the main ideas as short bullet points; key terms, definitions and numbers as bullets; "
    "a short conclusion if the lecture has one. Stay faithful to the transcript and do not add facts that are not in it. "
    "Use plain text with simple '-' bullets and **bold** only for headings and key terms. No tables.")


def split_text_chunks(text, limit=3500):
    """Split text into pieces that each fit one Telegram message, cutting at paragraph/line/sentence/space boundaries."""
    text = text.strip()
    out = []
    while len(text) > limit:
        cut = -1
        for sep in ("\n\n", "\n", ". ", "؟ ", "! ", "، ", " "):
            pos = text.rfind(sep, 0, limit)
            if pos >= limit * 0.5:
                cut = pos + len(sep)
                break
        if cut <= 0:
            cut = limit
        out.append(text[:cut].strip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def md_to_tg_html(text):
    """Tiny markdown → Telegram HTML (bold, headings, bullets). Everything else is escaped as plain text."""
    t = H(text.strip())
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"(?m)^\s*#{1,6}\s*(.+?)\s*$", r"<b>\1</b>", t)
    t = re.sub(r"(?m)^(\s*)[*\-]\s+", r"\1• ", t)
    return t


async def send_text_chunks(chat_id, chunks, as_html=False):
    for i, c in enumerate(chunks):
        body = md_to_tg_html(c) if as_html else H(c)
        await app.send_message(chat_id, body, disable_web_page_preview=True)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.6)           # stay far from Telegram's flood limits


@op("atranscribe")
async def op_atranscribe(job):
    s = job.s
    if not GEMINI_API_KEY:
        raise JobError(tr('Transcription is not available on this server (no Gemini key).'))
    src = await ensure_src(job)
    info = await probe(src)
    dur = info["duration"] or s.duration or 0
    ext = src.suffix.lower()
    chunk_sec = max(60, TRANSCRIBE_CHUNK_MIN * 60)
    size = src.stat().st_size
    per_chunk = size * min(1.0, chunk_sec / dur) if dur else size
    created = []
    try:
        # compress ONLY when needed: video source, a format Gemini can't read, or parts that would be too big to send inline
        cur, mime = src, GEMINI_AUDIO_MIME.get(ext)
        if s.kind == "video" or mime is None or per_chunk > TRANSCRIBE_COMPRESS_ABOVE_MB * MB:
            prep = s.dir / "tx_prep.ogg"
            created.append(prep)
            await run_ffmpeg(job, ["-i", str(src), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus",
                                   "-application", "voip", "-b:a", "24k", *enc_threads(job), str(prep)],
                             duration=dur, timeout=AUDIO_TIMEOUT, label=L("🗜️", tr('Preparing audio')))
            if not prep.exists() or prep.stat().st_size == 0:
                raise JobError(tr('This file has no audio track I can read.'))
            cur, mime = prep, "audio/ogg"
        n = int(-(-dur // chunk_sec)) if dur and dur > chunk_sec else 1
        texts, failed = [], 0
        for i in range(n):
            if job.cancelled:
                raise JobCancelled
            a = i * chunk_sec
            b = min((i + 1) * chunk_sec, dur) if n > 1 else None
            if n > 1:
                piece = s.dir / f"tx_part{i + 1}{cur.suffix}"
                created.append(piece)
                await _cut_copy(job, cur, piece, a, b, True, False, L("✂️", tr('Cutting part {0}/{1}', i + 1, n)))
                await edit(job, progress_count(L("📝", tr('Transcribing')), i, n), force=True)
            else:
                piece = cur
                await edit(job, L("📝", tr('Transcribing')), force=True)
            if not piece.exists() or piece.stat().st_size == 0:
                texts.append(f"[⚠ part {i + 1} is empty]")
                failed += 1
                continue
            if piece.stat().st_size > INLINE_AUDIO_MAX:
                raise JobError(tr('This recording is too large to send as is. Lower <code>TRANSCRIBE_CHUNK_MIN</code> or <code>TRANSCRIBE_COMPRESS_ABOVE_MB</code>.'))
            b64 = await asyncio.to_thread(sync_encode_b64, piece)
            prompt = TRANSCRIBE_PROMPT
            if n > 1:
                prompt += (f" This is part {i + 1} of {n} of one longer recording: start exactly where this audio starts, "
                           "and do not add any introduction or conclusion of your own.")
            res = await call_gemini_engine(prompt, audio=(mime, b64),
                                           models=(GEMINI_MODEL_TRANSCRIBE, GEMINI_MODEL_TRANSCRIBE_BACKUP), timeout=300)
            b64 = None
            if job.cancelled:
                raise JobCancelled
            if res and res.strip():
                texts.append(res.strip())
            else:
                texts.append(f"[⚠ part {i + 1} ({fmt_time(a)}–{fmt_time(b if b is not None else dur)}) could not be transcribed]")
                failed += 1
            if piece is not cur:
                _safe_remove(piece)
        if failed >= n:
            raise JobError(tr('Transcription failed. Please try again later.'))
    finally:
        for f in created:
            _safe_remove(f)
        _safe_remove(src)                 # free the disk now; the session keeps only the (small) transcript
        s.path = None
    transcript = "\n\n".join(texts).strip()
    stem = safe_name_text(Path(s.name).stem)
    out = s.dir / f"{stem}_Transcript.txt"
    out.write_text(transcript, encoding="utf-8")
    s.data["tr_path"], s.data["tr_name"] = str(out), out.name
    words = len(transcript.split())
    archive_note(job.user, "📝", "Transcribe", f"⏱ {fmt_time(dur)} | 📝 {words} words" + (f" | ⚠️ {failed} part(s) failed" if failed else ""))
    note = tr('\n⚠️ {0} part(s) could not be transcribed and are marked in the text.', failed) if failed else ""
    job.followup = (tr('📝 {0}\n\n⏱ {1} · 📝 {2} words{3}\n\nHow do you want to receive it?', hd(tr('Transcript ready')), fmt_time(dur), words, note),
                    kb([(tr('📄 TXT File'), "tr_txt"), (tr('💬 In Chat'), "tr_chat")], [(tr('❌ Cancel'), "cancel")]))
    return []


async def cb_transcript(q, s, data):
    """Buttons after a transcription: TXT file / in chat / summarize / done."""
    msg = q.message
    if data == "tr_done":
        drop_session(s.uid)
        try:
            await msg.delete()
        except Exception:
            await safe_edit(msg, "✅")
        return
    path = s.data.get("tr_path")
    if not path or not os.path.exists(path):
        await safe_edit(msg, STALE())
        return
    if data == "tr_sum":
        await go_job(q, s, "tsum", {})
        return
    text = Path(path).read_text(encoding="utf-8")
    as_file = data == "tr_txt"
    chunks = [] if as_file else split_text_chunks(text)
    if not as_file and len(chunks) > CHAT_MAX_MSGS:
        await app.send_message(s.chat_id, tr('⚠️ This transcript is too long for chat ({0} messages), so it is sent as a file instead.', len(chunks)))
        as_file = True
    await safe_edit(msg, L("📤", tr('Uploading')))
    if as_file:
        sent = await app.send_document(s.chat_id, path, caption=okc(tr('Transcript ready')), file_name=s.data.get("tr_name"),
                                       force_document=True)
        if sent is not None:
            archive_media(s.user, file_id_of(sent), "processed", "Transcribe", s.data.get("tr_name"),
                          f"💾 {fmt_size(os.path.getsize(path))}")
    else:
        await send_text_chunks(s.chat_id, chunks)
    await safe_edit(msg, tr('✅ Delivered.\n\nWant a summary of the transcript?'),
                    kb([(tr('🧠 Summarize'), "tr_sum")], [(tr('✅ No, Thanks'), "tr_done")]))


@op("tsum")
async def op_tsum(job):
    s = job.s
    path = s.data.get("tr_path")
    if not path or not os.path.exists(path):
        raise JobError(tr('The transcript is no longer available. Please transcribe again.'))
    text = Path(path).read_text(encoding="utf-8")
    await edit(job, L("🧠", tr('Summarizing')), force=True)
    res = await call_gemini_engine(SUMMARY_PROMPT + "\n\nTranscript:\n\n" + text,
                                   models=(GEMINI_MODEL_SUMMARY, GEMINI_MODEL_SUMMARY_BACKUP), timeout=300)
    if job.cancelled:
        raise JobCancelled
    if not res or not res.strip():
        # keep the session (transcript) alive so the user can simply try again
        await app.send_message(job.chat_id, tr('❌ Summarizing failed. Please try again.'))
        job.followup = (tr('Try the summary again?'), kb([(tr('🧠 Summarize'), "tr_sum")], [(tr('✅ No, Thanks'), "tr_done")]))
        return []
    chunks = split_text_chunks(res, 3000)
    archive_note(job.user, "🧠", "Summarize transcript", f"📝 {len(text.split())} words")
    if len(chunks) > CHAT_MAX_MSGS:
        out = s.dir / f"{Path(s.data.get('tr_name', 'Transcript')).stem}_Summary.txt"
        out.write_text(res.strip(), encoding="utf-8")
        return [Out(out, "text", out.name, okc(tr('Summary ready')), op="Summarize transcript")]
    await send_text_chunks(job.chat_id, chunks, as_html=True)
    return []


# ══════════════════════════ الواجهة: نصوص وقوائم ══════════════════════════
OFFICE_TYPE_LABELS = TrMap({"docx": "Word", "pptx": "PowerPoint", "doc": "Word (legacy)",
                            "ppt": "PowerPoint (legacy)", "html": "HTML"})
OFFICE_TYPE_LIBS = {"docx": DOCX_AVAILABLE, "pptx": PPTX_AVAILABLE, "doc": True, "ppt": True, "html": BS4_AVAILABLE}

def STALE():
    return tr('⚠️ {0}\nPlease send the file again to start over.', hd(tr('Session expired')))
def CANCELLED():
    return tr('🚫 Cancelled.')
def SUPPORTED():
    return tr('PDF, DOCX, DOC, PPTX, PPT, HTML, interactive books (.exe), images, audio and video')
def PICK():
    return tr('Choose a tool 👇')


def pdf_menu():
    return kb(
        [(tr('🔗 Merge'), "tool_merge_prompt"), (tr('✂️ Split'), "tool_split_prompt")],
        [(tr('🔄 Convert'), "tool_convert"), (tr('🗜️ Compress'), "tool_compress")],
        [(tr('📚 Extract'), "tool_extract"), (tr('📑 Print Layout'), "tool_pages_per_sheet")],
        [(tr('✏️ Rename'), "tool_rename_prompt"), (tr('🔓 Unlock'), "tool_unlock")],
        [(tr('❌ Cancel'), "cancel")])


def image_menu():
    return kb([(tr('🔤 Extract Text'), "img_extract_text")], [(tr('📕 Convert to PDF'), "img_to_pdf")],
              [(tr('❌ Cancel'), "cancel_images")])


def office_menu():
    return kb([(tr('📕 To PDF'), "office_pdf"), (tr('📄 To TXT'), "office_txt")],
              [(tr('✏️ Rename'), "office_rename_prompt")], [(tr('❌ Cancel'), "cancel")])


def audio_menu(s):
    rows = [
        [(tr('🔄 Convert'), "au_convert"), (tr('🗜️ Compress'), "au_compress")],
        [(tr('✂️ Cut / Split'), "au_split"), (tr('🏷️ Edit Tags'), "au_meta")],
    ]
    last = [(tr('📡 Make Streamable'), "au_stream")]
    if s.orig != "voice":
        last.append((tr('✏️ Rename'), "au_rename"))
    rows += [last, [(tr('🔗 Merge'), "mg_start"), (tr('📝 Transcribe'), "au_transcribe")], [(tr('❌ Cancel'), "cancel")]]
    return kb(*rows)


def video_menu(s):
    if LIGHT_HOST:
        return kb([(tr('🖼️ Thumbnail'), "vi_cover"), (tr('🎵 Extract Audio'), "vi_audio")],
                  [(tr('✂️ Cut / Split'), "vi_split"), (tr('🔄 Convert'), "vi_convert")],
                  [(tr('📡 Make Streamable'), "vi_stream"), (tr('✏️ Rename'), "vi_rename")],
                  [(tr('🔗 Merge'), "mg_start"), (tr('📝 Transcribe'), "vi_transcribe")],
                  [(tr('❌ Cancel'), "cancel")])
    rows = [
        [(tr('🗜️ Compress'), "vi_compress"), (tr('✂️ Cut / Split'), "vi_split")],
        [(tr('🖼️ Thumbnail'), "vi_cover"), (tr('🎵 Extract Audio'), "vi_audio")],
        [(tr('🔄 Convert'), "vi_convert"), (tr('📡 Make Streamable'), "vi_stream")],
        [(tr('✏️ Rename'), "vi_rename"), (tr('🔗 Merge'), "mg_start")],
        [(tr('📝 Transcribe'), "vi_transcribe")],
        [(tr('❌ Cancel'), "cancel")],
    ]
    return kb(*rows)


DIV = "━" * 20


def received_card(icon, title, name, rows, notes=()):
    """Layout: title / divider / file name / one fact per line / optional notes / call to action."""
    parts = [f"{icon} {hd(title)}", DIV, f"📎 <code>{H(name)}</code>", ""] + list(rows)
    if notes:
        parts += [""] + list(notes)
    parts += ["", PICK()]
    return "\n".join(parts)


def pdf_text(s):
    rows = [tr("💾 Size: {0}", fmt_size(s.size))]
    if s.data.get("pages"):
        rows.append(tr("📑 Pages: {0}", s.data["pages"]))
    return received_card("📄", tr("PDF received successfully!"), s.name, rows)


def media_text(s):
    audio = s.kind == "audio"
    rows = []
    if s.size:
        rows.append(tr("💾 Size: {0}", fmt_size(s.size)))
    if s.duration:
        rows.append(tr("⏱ Duration: {0}", fmt_time(s.duration)))
    if not audio and s.width and s.height:
        rows.append(tr("📐 Resolution: {0}", f"{s.width}×{s.height}"))
    notes = []
    if s.orig == "document":
        notes.append(tr("ℹ️ <i>Sent as a document. Use 📡 to make it streamable.</i>"))
    elif s.orig == "voice":
        notes.append(tr("🎙️ <i>Voice note. You can convert it to MP3, M4A and more.</i>"))
    title = tr("Audio received successfully!") if audio else tr("Video received successfully!")
    return received_card("🎵" if audio else "🎬", title, s.name, rows, notes)


def menu_for(s):
    if s.kind == "pdf":
        return pdf_text(s), pdf_menu()
    if s.kind in ("audio", "video"):
        return media_text(s), (audio_menu(s) if s.kind == "audio" else video_menu(s))
    if s.kind == "office":
        rows = [tr("💾 Size: {0}", fmt_size(s.size))]
        if s.data.get("slides"):
            rows.append(tr("🖼️ Slides: {0}", s.data["slides"]))
        title = tr("{0} file received successfully!", OFFICE_TYPE_LABELS[s.ftype])
        icon = {"docx": "📝", "doc": "📝", "pptx": "📊", "ppt": "📊", "html": "🌐"}.get(s.ftype, "📎")
        return received_card(icon, title, s.name, rows), office_menu()
    n = len(s.data.get("images", []))
    title = tr("1 image received successfully!") if n == 1 else tr("{0} images received successfully!", n)
    return (f"📷 {hd(title)}\n{DIV}\n{tr('Send more to add them, or choose a tool below 👇')}"), image_menu()


def est_kbps(s):
    return int(s.size * 8 / 1000 / s.duration) if s.duration else 0


def orig_line(s):
    ext = Path(s.name).suffix.lstrip(".").upper() or ("OGG" if s.orig == "voice" else "—")
    bits = [ext, fmt_size(s.size)]
    if s.width and s.height:
        bits.insert(1, f"{s.width}×{s.height}")
    if s.duration:
        bits.append(fmt_time(s.duration))
    line = tr('📌 <b>Original:</b> ') + " · ".join(bits)
    if s.kind == "audio" and s.duration:
        line += tr('\n🎚️ <b>Original quality:</b> ≈{0} kbps <i>(estimated from size ÷ duration)</i>', est_kbps(s))
    return line


BR_OPTIONS = {"mp3": [320, 192, 128, 96, 64], "m4a": [256, 160, 128, 96, 64], "opus": [128, 96, 64, 48, 32]}


def bitrate_menu(s, fmt):
    opts = BR_OPTIONS[fmt]
    est = est_kbps(s)
    if est:      # no point offering a quality higher than the original
        keep = [o for o in opts if o <= est * 1.15]
        opts = keep if len(keep) >= 2 else opts[-2:]
    rows, row = [], []
    for o in opts:
        row.append((f"{o}k", f"auq:{fmt}:{o}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([(tr('↩️ Back'), "au_convert")])
    return f"🎚️ {hd(tr('Choose quality'))} · {FMT_AR[fmt]}\n{orig_line(s)}", kb(*rows)


def res_menu(s, fmt):
    heights = [1080, 720, 480, 360]
    short = min(s.width, s.height) if (s.width and s.height) else s.height
    if short:
        heights = [h for h in heights if h < short]
    orig_lbl = tr('Original ({0}×{1})', s.width, s.height) if s.height else tr("Original")
    rows = [[(orig_lbl, f"vcr:{fmt}:0")]]
    row = []
    for h in heights:
        row.append((f"{h}p", f"vcr:{fmt}:{h}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([(tr('↩️ Back'), "vi_convert")])
    return f"📐 {hd(tr('Choose resolution'))} · {fmt.upper()}\n{orig_line(s)}", kb(*rows)


def _level_line(icon, level, short):
    cap = VIDEO_LEVELS[level]["h"]
    name = LEVEL_AR[level]
    if not short:
        return f"{icon} {name} | " + tr("up to {0}p", cap)
    if cap >= short:                       # the video is already at or below this size: only the quality level changes
        return f"{icon} {name} | {short}p " + tr("(original size)")
    return f"{icon} {name} | {cap}p"


def sane_dims(size, duration, w, h):
    """Telegram sometimes reports the dimensions of another (preview/alternative) version of a video.
    More than ~0.8 bits per pixel per frame (at 30 fps) is not a realistic encode, so such dimensions are ignored."""
    if not (size and duration and w and h):
        return w, h
    bpp = (size * 8 / duration) / (w * h * 30)
    if bpp > 0.8:
        logger.warning("Ignoring suspicious video dimensions %sx%s (%.1f MB, %s): bitrate too high for that resolution",
                       w, h, size / MB, fmt_time(duration))
        return 0, 0
    return w, h


def levels_for(short):
    """Levels worth showing for a video whose short side is `short` px. Levels that would end at the same
    resolution collapse into the strongest one (e.g. a 360p video gets one level, a 720p video gets two)."""
    order = ["light", "medium", "strong"]
    if not short:
        return order
    by_res = {}
    for lv in order:
        by_res[min(VIDEO_LEVELS[lv]["h"], short)] = lv      # later (stronger) level wins
    keep = set(by_res.values())
    return [lv for lv in order if lv in keep]


def vcompress_menu(s):
    mode = s.data.get("vmode") or VIDEO_DEFAULT_MODE
    short = min(s.width, s.height) if (getattr(s, "width", 0) and getattr(s, "height", 0)) else 0
    shown = levels_for(short)
    icons = {"light": "🟢", "medium": "🟡", "strong": "🔴"}

    def pick(name, label):
        return (f"✅ {label}" if mode == name else label, f"vmode:{name}")

    rows = [[pick("fast", tr("⚡ Fast")), pick("quality", tr("🎯 Quality"))],
            *[[(f"{icons[lv]} {LEVEL_AR[lv]}", f"vcp:{lv}")] for lv in shown],
            [(tr("↩️ Back"), "back_main")]]
    lines = "\n".join(_level_line(icons[lv], lv, short) for lv in shown)
    text = tr("🗜️ {0}\n{1}\n"
              "⚡ <b>Fast:</b> quicker, may lower the fps, slightly less detail\n"
              "🎯 <b>Quality:</b> slower, keeps the original fps, best detail\n\n"
              "{2}\n\n"
              "Choose the mode, then the strength 👇", hd(tr("Compress video")), DIV, lines)
    return text, kb(*rows)


def level_kb(prefix, back="back_main"):
    return kb([(f"🟢 {LEVEL_AR['light']}", f"{prefix}:light")], [(f"🟡 {LEVEL_AR['medium']}", f"{prefix}:medium")],
              [(f"🔴 {LEVEL_AR['strong']}", f"{prefix}:strong")], [(tr('↩️ Back'), back)])


# ══════════════════════════ التقاط الملفات ══════════════════════════
def classify(m):
    """بيرجع (kind, info) حسب نوع الرسالة. kind: pdf|image|office|audio|video|None"""
    if m.photo:
        return "image", {"file_id": m.photo.file_id, "size": m.photo.file_size or 0}
    if m.video:
        v = m.video
        return "video", {"file_id": v.file_id, "size": v.file_size or 0, "name": v.file_name or "video.mp4",
                         "duration": v.duration or 0, "orig": "video", "width": v.width or 0, "height": v.height or 0}
    if m.animation:
        a = m.animation
        return "video", {"file_id": a.file_id, "size": a.file_size or 0, "name": a.file_name or "animation.mp4",
                         "duration": a.duration or 0, "orig": "animation", "width": a.width or 0, "height": a.height or 0}
    if m.video_note:
        v = m.video_note
        return "video", {"file_id": v.file_id, "size": v.file_size or 0, "name": "video_note.mp4",
                         "duration": v.duration or 0, "orig": "video_note"}
    if m.audio:
        a = m.audio
        return "audio", {"file_id": a.file_id, "size": a.file_size or 0, "name": a.file_name or "audio.mp3",
                         "duration": a.duration or 0, "orig": "audio", "title": a.title, "performer": a.performer}
    if m.voice:
        v = m.voice
        return "audio", {"file_id": v.file_id, "size": v.file_size or 0, "name": "voice.ogg",
                         "duration": v.duration or 0, "orig": "voice"}
    d = m.document
    if d:
        name = d.file_name or ""
        ext = Path(name).suffix.lower()
        mime = (d.mime_type or "").lower()
        base = {"file_id": d.file_id, "size": d.file_size or 0, "name": name or "file", "orig": "document"}
        if mime == "application/pdf" or ext == ".pdf":
            return "pdf", base
        if mime.startswith("image/"):
            return "image", {"file_id": d.file_id, "size": d.file_size or 0}
        if ext == ".exe":
            return "book", base
        ftype = detect_office_type(d)
        if ftype:
            return "office", {**base, "ftype": ftype}
        if mime.startswith("video/") or ext in VIDEO_EXTS:
            return "video", {**base, "duration": 0}
        if mime.startswith("audio/") or ext in AUDIO_EXTS:
            return "audio", {**base, "duration": 0}
    return None, {}


async def dl(file_id, dest, msg=None, label=None, total=0):
    """Download with an optional live progress line on `msg` (falls back to `total` when Telegram reports 0)."""
    cb = None
    if msg is not None:
        state = {"t": 0.0}
        head = label or L("📥", tr('Receiving file'))
        lg = _lang_var.get()

        async def cb(cur, tot):
            _lang_var.set(lg)
            now = time.time()
            if now - state["t"] < EDIT_GAP:
                return
            state["t"] = now
            await safe_edit(msg, progress_text(head, cur, tot or total, "received"))
    return await app.download_media(file_id, file_name=str(dest), progress=cb)


async def reject_if_big(m, size, limit_mb):
    if size and size > limit_mb * MB:
        await m.reply_text(tr('❌ {0}\n<code>{1}</code> is over the {2} MB limit.\nPlease send a smaller file.', hd(tr('File too large')), fmt_size(size), limit_mb))
        return True
    return False


async def have_space(m, size):
    need = max(size * 1.5, 20 * MB)
    if need > WORK_BUDGET_MB * MB:
        await m.reply_text(tr('❌ This file is larger than the free server space right now. Try a smaller one.'))
        return False
    if free_budget() < need:
        await m.reply_text(tr('⏳ The server is busy right now. Please try again in a minute.'))
        return False
    return True


async def start_pdf(m, info):
    size = info["size"]
    if await reject_if_big(m, size, MAX_PDF_MB) or not await have_space(m, size):
        return
    status = await m.reply_text(L("📥", tr('Receiving file')))
    s = new_session(m.from_user, m.chat.id, "pdf")
    s.name, s.size, s.file_id = info["name"] or "file.pdf", size, info["file_id"]
    try:
        p = await dl(info["file_id"], s.dir / "file.pdf", msg=status, total=size)
        if not p:
            raise RuntimeError("download failed")
        s.path = Path(p)
        try:
            s.data["pages"] = await asyncio.to_thread(pdf_page_count, str(s.path))
        except Exception:
            pass
        tp = s.dir / "thumb.jpg"
        if await asyncio.to_thread(create_thumbnail, str(s.path), str(tp)):
            s.thumb = tp
        archive_media(m.from_user, info["file_id"], "received", name=s.name)
        text, markup = menu_for(s)
        await status.delete()
        await m.reply_text(text, reply_markup=markup)
    except Exception as e:
        logger.error("PDF intake failed: %s", e)
        drop_session(m.from_user.id)
        await safe_edit(status, tr("❌ Couldn't save the file. Please try again."))


async def start_office(m, info):
    ftype = info["ftype"]
    if not OFFICE_TYPE_LIBS.get(ftype):
        await m.reply_text(tr("❌ {0} files aren't supported on this bot right now.", OFFICE_TYPE_LABELS[ftype]))
        return
    size = info["size"]
    if await reject_if_big(m, size, MAX_OFFICE_MB) or not await have_space(m, size):
        return
    status = await m.reply_text(L("📥", tr('Receiving file')))
    s = new_session(m.from_user, m.chat.id, "office")
    s.name, s.size, s.file_id, s.ftype = info["name"] or f"file.{ftype}", size, info["file_id"], ftype
    try:
        p = await dl(info["file_id"], s.dir / f"file.{ftype}", msg=status, total=size)
        if not p:
            raise RuntimeError("download failed")
        s.path = Path(p)
        if ftype == "pptx":
            n = await asyncio.to_thread(sync_count_pptx_slides, str(s.path))
            if n:
                s.data["slides"] = n
            tp = s.dir / "thumb.jpg"
            if await asyncio.to_thread(extract_pptx_thumbnail, str(s.path), str(tp)):
                s.thumb = tp
        archive_media(m.from_user, info["file_id"], "received", name=s.name)
        text, markup = menu_for(s)
        await status.delete()
        await m.reply_text(text, reply_markup=markup)
    except Exception as e:
        logger.error("Office intake failed: %s", e)
        drop_session(m.from_user.id)
        await safe_edit(status, tr("❌ Couldn't save the file. Please try again."))


async def start_media(m, kind, info):
    size = info["size"]
    limit = MAX_VIDEO_MB if kind == "video" else MAX_AUDIO_MB
    if await reject_if_big(m, size, limit):
        return
    dur = info.get("duration", 0)
    s = new_session(m.from_user, m.chat.id, kind)
    s.name, s.size, s.file_id, s.duration, s.orig = info["name"], size, info["file_id"], dur, info["orig"]
    s.width, s.height = info.get("width", 0), info.get("height", 0)
    if kind == "video":
        s.width, s.height = sane_dims(size, dur, s.width, s.height)
    s.data["title"], s.data["performer"] = info.get("title"), info.get("performer")
    archive_media(m.from_user, info["file_id"], "received", name=s.name,
                  extra=f"💾 {fmt_size(size)}" if kind == "audio" and size else None)
    text, markup = menu_for(s)
    await m.reply_text(text, reply_markup=markup)


async def stream_book(m, dest, size, status):
    """Downloads the .exe while checking it. A program that is not a Flip PDF / FlipBuilder book is dropped after
    the first few MB (BookSniffer raises BookError) instead of being downloaded completely."""
    limit = BOOK_SNIFF_MB * MB
    head = L("📥", tr('Receiving file'))
    stream = getattr(app, "stream_media", None)
    if stream is None:                                   # older Pyrogram: normal download, then check the start
        p = await dl(m.document.file_id, dest, msg=status, total=size)
        if not p:
            raise RuntimeError("download failed")
        at = await asyncio.to_thread(sniff_file, str(dest), limit)
        logger.info("Book table found at %.1f MB", at / MB)
        return
    sniff, done, last, logged = BookSniffer(limit), 0, 0.0, False
    agen = stream(m)
    try:
        with open(dest, "wb") as f:
            async for chunk in agen:
                sniff.feed(chunk)
                if sniff.found_at is not None and not logged:
                    logged = True
                    logger.info("Book table found at %.1f MB of %s", sniff.found_at / MB, fmt_size(size))
                await asyncio.to_thread(f.write, chunk)
                done += len(chunk)
                now = time.time()
                if now - last >= EDIT_GAP:
                    last = now
                    await safe_edit(status, progress_text(head, done, size))
    finally:
        await agen.aclose()
    sniff.end()
    if size and done != size:
        raise RuntimeError("incomplete download (%d of %d bytes)" % (done, size))


async def start_book(m, info):
    """.exe interactive book -> PDF. No menu: the only tool is the conversion, so it starts right after the file
    has been checked (nothing sits on the disk waiting for a button)."""
    uid = m.from_user.id
    if not BOOK_OK:
        await m.reply_text(tr("❌ Interactive books (.exe) aren't supported on this bot right now."))
        return
    size = info["size"]
    if user_jobs[uid] >= USER_MAX_JOBS:
        await m.reply_text(tr('⚠️ You already have {0} tasks running. Wait for one to finish, then try again.', user_jobs[uid]))
        return
    if await reject_if_big(m, size, MAX_BOOK_MB) or not await have_space(m, size):
        return
    status = await m.reply_text(L("📥", tr('Checking the file')))
    s = new_session(m.from_user, m.chat.id, "book")
    s.name, s.size, s.file_id = info["name"] or "book.exe", size, info["file_id"]
    dest = s.dir / "book.exe"
    try:
        await stream_book(m, dest, size, status)
        s.data["pages"] = await asyncio.to_thread(peek_book, str(dest))
        s.path = dest
    except BookError as e:
        logger.info("Not a supported book (%s): %s", s.name, e)
        drop_session(uid)
        await safe_edit(status, tr("❌ {0}\nThis .exe isn't an interactive book made by Flip PDF / FlipBuilder, so I stopped without processing it.", hd(tr("Not a supported book"))))
        return
    except Exception as e:
        logger.error("Book intake failed: %s", e)
        drop_session(uid)
        await safe_edit(status, tr("❌ Couldn't save the file. Please try again."))
        return
    archive_media(m.from_user, info["file_id"], "received", name=s.name)
    if not await start_job(s, "book_pdf", {}, status):
        drop_session(uid)


async def add_image(m, info):
    """أول صورة تبدأ الجلسة، وأي صورة بعدها بتتضاف لنفس القائمة تلقائيًا."""
    uid = m.from_user.id
    s = sessions.get(uid)
    if info["size"] and info["size"] > MAX_IMAGE_MB * MB:
        await m.reply_text(tr('❌ Image is over the {0} MB limit.', MAX_IMAGE_MB))
        return
    if s is None or s.kind != "image":
        s = new_session(m.from_user, m.chat.id, "image")
        s.data["images"] = []
    if len(s.data["images"]) >= MAX_IMAGES:
        await m.reply_text(tr('⚠️ Maximum reached ({0} images).', MAX_IMAGES))
        return
    if not await have_space(m, info["size"] or MB):
        return
    p = await dl(info["file_id"], s.dir / f"img_{len(s.data['images'])}_{random.randint(1000, 9999)}.jpg")
    if not p:
        return
    s.data["images"].append(Path(p))
    touch(s)
    _spawn(refresh_images_menu(s, m))


async def refresh_images_menu(s, m):
    """بنأخّر التحديث شوية عشان ألبوم من 10 صور يطلع رسالة واحدة بعدّاد صحيح."""
    s.data["img_tick"] = tick = s.data.get("img_tick", 0) + 1
    await asyncio.sleep(0.8)
    if s.data.get("img_tick") != tick or sessions.get(s.uid) is not s:
        return
    text, markup = menu_for(s)
    msg = s.data.get("img_msg")
    if msg:
        await safe_edit(msg, text, markup)
    else:
        s.data["img_msg"] = await m.reply_text(text, reply_markup=markup)


async def add_merge_pdf(m, s, info):
    if await reject_if_big(m, info["size"], MAX_PDF_MB) or not await have_space(m, info["size"]):
        return
    p = await dl(info["file_id"], s.dir / f"merge_{uuid.uuid4().hex[:6]}.pdf")
    if not p:
        await m.reply_text(tr('❌ Download failed. Please try again.'))
        return
    archive_media(m.from_user, info["file_id"], "received", name=info["name"])
    s.data["merge_list"].append(Path(p))
    s.data["merge_names"].append(info["name"])
    touch(s)
    await safe_edit(s.data["merge_msg"], merge_text(s, added=True), merge_kb())


def merge_kb():
    return kb([(tr('✅ Merge Now'), "do_merge")], [(tr('❌ Cancel'), "cancel_merge")])


def merge_text(s, added=False):
    names = s.data["merge_names"]
    lst = "\n".join(f"{i + 1}. <code>{H(n)}</code>" for i, n in enumerate(names))
    tail = tr('📥 Added! Send more files, or tap <b>Merge Now</b>.') if added else tr('📥 Send the remaining files in the order you want.')
    return f"🔗 {hd(tr('Merge mode'))}\n\n{lst}\n\n{tail}"


# ══════════════════════════ لوحة تعديل بيانات الصوت ══════════════════════════
def meta_text(s):
    d = s.data
    title = d.get("m_title") or d.get("title") or "—"
    artist = d.get("m_artist") or d.get("performer") or "—"
    if d.get("m_cover"):
        cov = tr('✅ Default cover') if d.get("m_default_cover") else tr('✅ New image')
    elif d.get("m_remove"):
        cov = tr('🗑️ Will be removed')
    else:
        cov = tr("Unchanged")
    return (tr('🏷️ {0}\n\n🎵 <b>Title:</b> <code>{1}</code>\n👤 <b>Artist:</b> <code>{2}</code>\n🖼️ <b>Cover:</b> {3}\n\nPick what to change, then tap <b>Apply</b>.', hd(tr('Edit audio tags')), H(str(title)), H(str(artist)), cov))


def meta_kb():
    return kb([(tr('🎵 Title'), "meta_title"), (tr('👤 Artist'), "meta_artist")],
              [(tr('🖼️ Set Cover'), "meta_thumb"), (tr('🗑️ Remove Cover'), "meta_nothumb")],
              [(tr('✅ Apply'), "meta_go"), (tr('↩️ Back'), "back_main")])


# ══════════════════════════ معالجة الأزرار ══════════════════════════
def parse_pages_total(s):
    return pdf_page_count(str(s.path))


async def go_job(q, s, op_name, params, text=None):
    await safe_edit(q.message, L("⚙️", tr('Preparing')))
    await start_job(s, op_name, params, q.message)


async def start_transcribe(q, s):
    if not GEMINI_API_KEY:
        await safe_edit(q.message, tr('❌ Transcription is not available on this server (no Gemini key).'),
                        kb([(tr('↩️ Back'), "back_main")]))
        return
    await go_job(q, s, "atranscribe", {})


async def on_callback(client, q):
    data = q.data or ""
    uid = q.from_user.id
    set_lang(uid, q.from_user)
    if data.startswith("lang:"):
        return await cb_lang(q, data)
    if data.startswith("cx:"):
        return await cb_cancel_job(q, data[3:])
    if data.startswith("ac:"):
        return await cb_audio_cover(q, data)
    async with ulock(uid):
        s = sessions.get(uid)
        if data in ("cancel", "cancel_images", "cancel_merge"):
            await q.answer()
            drop_session(uid)
            await safe_edit(q.message, CANCELLED())
            return
        if s is None:
            await q.answer()
            await safe_edit(q.message, STALE())
            return
        touch(s)
        if is_heavy_callback(data):
            await q.answer(tr("This tool is turned off on this server."), show_alert=True)
            return
        await q.answer()
        try:
            handler = {"pdf": cb_pdf, "office": cb_office, "image": cb_image, "book": cb_book,
                       "audio": cb_audio, "video": cb_video}[s.kind]
            handled = await handler(q, s, data)
            if not handled:
                handled = await cb_common(q, s, data)
            if not handled:
                logger.warning("unhandled callback %s for %s", data, s.kind)
        except Exception as e:
            logger.exception("callback error: %s", e)
            await safe_edit(q.message, tr('❌ Something went wrong. Please try again.'))


async def show_menu(q, s):
    s.state = None
    text, markup = menu_for(s)
    await safe_edit(q.message, text if s.kind in ("audio", "video", "image", "office") else PICK(), markup)


def _split_back(s):
    return "au_split" if s.kind == "audio" else "vi_split"


def EXAMPLES_PAGES():
    return (tr('💡 <b>Examples:</b>\n- Range: <code>1-5</code>\n- Mixed: <code>1-3, 5, 8-10</code>'))


async def cb_common(q, s, data):
    msg = q.message
    if data == "back_main":
        await show_menu(q, s)
        return True
    if s.kind not in ("audio", "video"):
        return False
    dur = s.duration
    dtxt = tr('\n⏱ Duration: <code>{0}</code>', fmt_time(dur)) if dur else ""
    what = tr("audio") if s.kind == "audio" else tr("video")
    if data in ("au_split", "vi_split"):
        await safe_edit(msg, tr('✂️ {0}{1}\n\nWhat would you like to do?', hd(tr('Cut / split')), dtxt),
                        kb([(tr('✂️ Cut (Single Output)'), "cut_one")], [(tr('🗂️ Split Into Parts'), "cut_parts")],
                           [(tr('↩️ Back'), "back_main")]))
    elif data == "cut_parts":
        note = tr('\n<i>Part boundaries may be off by a second or two.</i>') if s.kind == "video" else ""
        await safe_edit(msg, tr('🗂️ {0}{1}{2}\n\nHow should it be split?', hd(tr('Split into parts')), dtxt, note),
                        kb([(tr('🔢 Custom Ranges'), "sp_custom")], [(tr('⏱️ Fixed Length'), "sp_equal")],
                           [(tr('↩️ Back'), _split_back(s))]))
    elif data == "cut_one":
        if s.kind == "video":
            lim = tr('\n⏱ On this server, Precise is limited to {0} minutes of kept video.', PRECISE_CUT_MAX_MIN) if LIGHT_HOST else ""
            await safe_edit(msg, tr('✂️ {0}{1}\n\nChoose the cut type:\n⚡ <b>Fast:</b> no re-encoding, but edges may be off by a second or two.\n🎯 <b>Precise:</b> frame-accurate, but slower.', hd(tr('Cut video')), dtxt) + lim,
                            kb([(tr('⚡ Fast'), "vcut_fast")], [(tr('🎯 Precise'), "vcut_precise")], [(tr('↩️ Back'), _split_back(s))]))
        else:
            s.state = "media_cut_one"
            await safe_edit(msg, _cut_prompt(dtxt, what))
    elif data in ("vcut_fast", "vcut_precise"):
        s.data["precise"] = data == "vcut_precise"
        s.state = "media_cut_one"
        await safe_edit(msg, _cut_prompt(dtxt, what))
    elif data == "sp_custom":
        s.state = "media_split_custom"
        await safe_edit(msg, tr('🔢 {0}{1}\n\nSend the ranges you want. Each one is sent as its own file.\n\n💡 <b>Examples:</b>\n- One part: <code>1:30-5:00</code>\n- Several: <code>0:00-10:30, 12:00-15:00</code>\n- Until the end: <code>20:00-</code>', hd(tr('Custom parts')), dtxt))
    elif data == "sp_equal":
        s.state = "media_split_equal"
        await safe_edit(msg, tr('⏱️ {0}{1}\n\nSend the length of each part in minutes.\n(Example: <code>10</code> = 10-minute parts. Add <code>s</code> for seconds, like <code>90s</code>.)', hd(tr('Fixed-length parts')), dtxt))
    elif data == "mg_start":
        s.state = "media_merge"
        s.data["mg_items"] = [merge_item_from_session(s)]
        s.data["mg_msg"] = msg
        await safe_edit(msg, mg_text(s), mg_kb())
    elif data == "mg_undo":
        items = s.data.get("mg_items") or []
        if len(items) > 1:
            items.pop()
        if items:
            await safe_edit(msg, mg_text(s), mg_kb())
    elif data == "mg_go":
        if len(s.data.get("mg_items", [])) < 2:
            await safe_edit(msg, tr('❌ Send at least one more file to merge.') + "\n\n" + mg_text(s), mg_kb())
            return True
        s.state = None
        await go_job(q, s, "amerge" if s.kind == "audio" else "vmerge", {})
    elif data in ("tr_txt", "tr_chat", "tr_sum", "tr_done"):
        await cb_transcript(q, s, data)
    else:
        return False
    return True


def _cut_prompt(dtxt, what):
    return (tr("✂️ {0}{1}\n\nSend the ranges to keep. They'll be joined into one {2}.\n\n💡 <b>Examples:</b>\n- One part: <code>1:30-5:00</code>\n- Several: <code>0:00-2:00, 10:00-12:30</code>\n- Until the end: <code>20:00-</code>", hd(tr('Cut')), dtxt, what))


# ───────── PDF ─────────
async def cb_pdf(q, s, data):
    msg = q.message
    if data == "tool_extract":
        await safe_edit(msg, tr('📚 {0}\nWhat do you want to extract?', hd(tr('Extract'))),
                        kb([(tr('📄 Text (TXT)'), "extract_txt")], [(tr('📝 Questions (BMCQ)'), "extract_bmcq")],
                           [(tr('↩️ Back'), "back_main")]))
    elif data == "extract_txt":
        await go_job(q, s, "pdf_txt", {})
    elif data == "extract_bmcq":
        await safe_edit(msg, tr('📝 {0}\n\n🎯 <b>Ready-made:</b> the file already has questions and answers, formatted exactly as they are.\n🧠 <b>AI generate:</b> the file is a lecture or transcript, and AI writes questions from it.', hd(tr('Extract questions'))),
                        kb([(tr('🎯 Extract Ready-Made'), "bmcq_strict")],
                           [(tr('🧠 Generate With AI'), "bmcq_generate")],
                           [(tr('↩️ Back'), "tool_extract")]))
    elif data in ("bmcq_strict", "bmcq_generate"):
        await go_job(q, s, "pdf_bmcq", {"action": data})
    elif data == "tool_compress":
        await safe_edit(msg, tr('🗜️ {0}\nChoose a level:', hd(tr('Compress PDF'))),
                        kb([(tr('🟢 Light'), "compress_local")], [(tr('🟡 Balanced'), "compress_recommended")],
                           [(tr('🔴 Maximum'), "compress_extreme")],
                           *([] if LIGHT_HOST else [[(tr('🖼️ Compress Images Only'), "compress_imgs")]]),
                           [(tr('↩️ Back'), "back_main")]))
    elif data == "compress_imgs":
        await safe_edit(msg, tr('🖼️ {0}\nRuns locally, no external service. Choose strength:', hd(tr('Compress images'))),
                        level_kb("pdfimg", "tool_compress"))
    elif data in ("compress_local", "compress_recommended", "compress_extreme"):
        await go_job(q, s, "pdf_compress", {"action": data})
    elif data.startswith("pdfimg:"):
        await go_job(q, s, "pdf_compress", {"action": "img_" + data.split(":")[1]})
    elif data == "tool_rename_prompt":
        s.state = "rename"
        await safe_edit(msg, tr('✏️ Send the new name (without .pdf):'))
    elif data == "tool_unlock":
        await safe_edit(msg, L("🔓", tr('Unlocking')))
        res = await asyncio.to_thread(check_pdf_password, str(s.path))
        if res == "NEEDS_PASSWORD":
            s.state = "unlock_pw"
            await safe_edit(msg, tr('🔑 This file is password-protected. Send the password:'))
        else:
            await start_job(s, "pdf_unlock", {}, msg)
    elif data == "tool_pages_per_sheet":
        await safe_edit(msg, tr('📑 {0}\nHow should the pages be combined?', hd(tr('Print layout'))),
                        kb([(tr('⬇️ 2 Stacked'), "nup_stack"), (tr('➡️ 2 Side by Side'), "nup_side")],
                           [(tr('🔳 4 Per Sheet'), "nup_grid")], [(tr('↩️ Back'), "back_main")]))
    elif data in ("nup_stack", "nup_side", "nup_grid"):
        layout, group = {"nup_stack": ("stack", 2), "nup_side": ("side", 2), "nup_grid": ("grid", 4)}[data]
        await go_job(q, s, "pdf_nup", {"layout": layout, "group": group})
    elif data == "tool_convert":
        await safe_edit(msg, tr('🔄 {0}\nChoose a format:', hd(tr('Convert'))),
                        kb([(tr('🖼️ To Images'), "convert_images")],
                           [(tr('📝 To Word'), "convert_word"), (tr('📊 To PowerPoint'), "convert_ppt")],
                           [(tr('↩️ Back'), "back_main")]))
    elif data in ("convert_word", "convert_ppt"):
        await go_job(q, s, "pdf_convert", {"fmt": "docx" if data == "convert_word" else "pptx"})
    elif data == "convert_images":
        total = await asyncio.to_thread(parse_pages_total, s)
        await safe_edit(msg, tr('🖼️ {0}\n📄 Your file has <code>{1} pages</code>\n\nWhich pages?', hd(tr('To images')), total),
                        kb([(tr('📄 Whole File'), "img_all")], [(tr('🔢 Page Range'), "img_custom")],
                           [(tr('↩️ Back'), "back_main")]))
    elif data == "img_custom":
        total = await asyncio.to_thread(parse_pages_total, s)
        s.state = "img_range"
        await safe_edit(msg, tr('🔢 {0}\n📄 Your file has <code>{1} pages</code>\n\nSend the pages you want.\n\n{2}', hd(tr('Page range')), total, EXAMPLES_PAGES()))
    elif data == "img_all":
        total = await asyncio.to_thread(parse_pages_total, s)
        await go_job(q, s, "pdf_images", {"pages": list(range(total))})
    elif data == "tool_split_prompt":
        total = await asyncio.to_thread(parse_pages_total, s)
        await safe_edit(msg, tr('✂️ {0}\n📄 Your file has <code>{1} pages</code>\n\nHow do you want to split it?', hd(tr('Split PDF')), total),
                        kb([(tr('🔢 Custom Pages'), "split_custom")], [(tr('🗂️ Equal Parts'), "split_equal")],
                           [(tr('↩️ Back'), "back_main")]))
    elif data == "split_custom":
        total = await asyncio.to_thread(parse_pages_total, s)
        s.state = "pdf_split_custom"
        await safe_edit(msg, tr('✂️ {0}\n📄 Your file has <code>{1} pages</code>\n\nSend the pages you want.\n\n{2}\n- Single page: <code>4</code>', hd(tr('Custom pages')), total, EXAMPLES_PAGES()))
    elif data == "split_equal":
        total = await asyncio.to_thread(parse_pages_total, s)
        s.state = "pdf_split_equal"
        await safe_edit(msg, tr('🗂️ {0}\n📄 Your file has <code>{1} pages</code>\n\nSend the number of pages per part.\n(Example: <code>10</code> creates files of 10 pages each.)', hd(tr('Equal parts')), total))
    elif data == "tool_merge_prompt":
        s.state = "merge"
        s.data["merge_list"] = [s.path]
        s.data["merge_names"] = [s.name]
        s.data["merge_msg"] = msg
        await safe_edit(msg, merge_text(s), merge_kb())
    elif data == "do_merge":
        if len(s.data.get("merge_list", [])) < 2:
            await safe_edit(msg, tr('❌ Send at least one more PDF to merge.'), merge_kb())
            return True
        await go_job(q, s, "pdf_merge", {})
    else:
        return False
    return True


# ───────── Office ─────────
async def cb_book(q, s, data):
    return False


async def cb_office(q, s, data):
    if data == "office_rename_prompt":
        s.state = "rename"
        await safe_edit(q.message, tr('✏️ Send the new name (without .{0}):', s.ftype))
    elif data in ("office_pdf", "office_pdf_again"):
        await go_job(q, s, "office_pdf", {})
    elif data == "office_txt":
        await go_job(q, s, "office_txt", {})
    else:
        return False
    return True


# ───────── Images ─────────
async def cb_image(q, s, data):
    imgs = s.data.get("images", [])
    if not imgs:
        await safe_edit(q.message, tr('⚠️ {0}\nPlease send the images again.', hd(tr('Session expired'))))
        return True
    if data in ("img_extract_text", "img_to_pdf"):
        label = "Extract text" if data == "img_extract_text" else "Convert to PDF"
        archive_note(s.user, "📥", "Received images", f"🖼️ {len(imgs)} images | {label}")
        if data == "img_extract_text":
            await go_job(q, s, "img_text", {})
        else:
            await go_job(q, s, "img_pdf", {})
        return True
    return False


# ───────── Audio ─────────
async def cb_audio(q, s, data):
    msg = q.message
    if data == "au_convert":
        rows = [[("MP3", "auc:mp3"), ("M4A", "auc:m4a"), ("Opus", "auc:opus")],
                [(tr('🎙️ Voice Note'), "auc:voice"), ("WAV", "auc:wav"), ("FLAC", "auc:flac")],
                [(tr('↩️ Back'), "back_main")]]
        await safe_edit(msg, tr('🔄 {0}\nChoose a format:\n{1}', hd(tr('Convert audio')), orig_line(s)), kb(*rows))
    elif data.startswith("auc:"):
        fmt = data.split(":")[1]
        if fmt in ("mp3", "m4a", "opus"):
            text, markup = bitrate_menu(s, fmt)
            await safe_edit(msg, text, markup)
        else:
            await go_job(q, s, "aconvert", {"fmt": fmt})
    elif data.startswith("auq:"):
        _, fmt, qual = data.split(":")
        await go_job(q, s, "aconvert", {"fmt": fmt, "q": qual})
    elif data == "au_compress":
        await safe_edit(msg, tr('🗜️ {0}\nBest for speech and lectures. Choose format and strength:', hd(tr('Compress audio'))),
                        kb([(tr('🟢 Opus Light'), "acp:opus:light"), (tr('🟢 M4A Light'), "acp:m4a:light")],
                           [(tr('🟡 Opus Medium'), "acp:opus:medium"), (tr('🟡 M4A Medium'), "acp:m4a:medium")],
                           [(tr('🔴 Opus Strong'), "acp:opus:strong"), (tr('🔴 M4A Strong'), "acp:m4a:strong")],
                           [(tr('↩️ Back'), "back_main")]))
    elif data.startswith("acp:"):
        _, fmt, level = data.split(":")
        await go_job(q, s, "acompress", {"fmt": fmt, "level": level})
    elif data == "au_stream":
        await go_job(q, s, "astream", {})
    elif data == "au_rename":
        s.state = "rename"
        await safe_edit(msg, tr('✏️ Send the new name (without {0}):', Path(s.name).suffix or '.mp3'))
    elif data == "au_meta":
        s.state = None
        for k in ("m_title", "m_artist", "m_cover", "m_remove", "m_default_cover"):
            s.data.pop(k, None)
        dc = active_default_cover(s.uid)           # user's default cover (if on) is applied automatically
        if dc:
            local = s.dir / "cover_default.jpg"
            try:
                shutil.copyfile(dc, local)
                s.data["m_cover"] = local
                s.data["m_default_cover"] = True
            except OSError as e:
                logger.warning("could not apply the default cover: %s", e)
        await safe_edit(msg, meta_text(s), meta_kb())
    elif data in ("meta_title", "meta_artist"):
        s.state = data
        what = tr('the new title') if data == "meta_title" else tr('the new artist name')
        await safe_edit(msg, tr('✏️ Send {0}:', what))
        s.data["meta_msg"] = msg
    elif data == "meta_thumb":
        s.state = "meta_thumb"
        s.data["meta_msg"] = msg
        await safe_edit(msg, tr('🖼️ Send the image to use as the audio cover:'))
    elif data == "meta_nothumb":
        s.data.pop("m_cover", None)
        s.data.pop("m_default_cover", None)
        s.data["m_remove"] = True
        await safe_edit(msg, meta_text(s), meta_kb())
    elif data == "au_transcribe":
        await start_transcribe(q, s)
    elif data == "meta_go":
        d = s.data
        if not (d.get("m_title") or d.get("m_artist") or d.get("m_cover") or d.get("m_remove")):
            await safe_edit(msg, tr('⚠️ Nothing changed yet.\n\n') + meta_text(s), meta_kb())
            return True
        await go_job(q, s, "ameta", {"title": d.get("m_title"), "artist": d.get("m_artist"),
                                      "cover": d.get("m_cover"), "remove_cover": d.get("m_remove")})
    else:
        return False
    return True


# ───────── Video ─────────
async def cb_video(q, s, data):
    msg = q.message
    if data == "vi_compress":
        text, markup = vcompress_menu(s)
        await safe_edit(msg, text, markup)
    elif data.startswith("vmode:"):
        mode = data.split(":")[1]
        if mode in ("fast", "quality"):
            s.data["vmode"] = mode
        text, markup = vcompress_menu(s)
        await safe_edit(msg, text, markup)
    elif data.startswith("vcp:"):
        await go_job(q, s, "vcompress", {"level": data.split(":")[1], "mode": s.data.get("vmode") or VIDEO_DEFAULT_MODE})
    elif data == "vi_cover":
        fast = tr('\n⚡ <i>Changed without downloading or re-uploading the video.</i>') if (COVER_BY_ID and s.orig == "video") else ""
        await safe_edit(msg, tr('🖼️ {0}{1}\n\nChoose a method:', hd(tr('Video thumbnail')), fast),
                        kb([(tr('📤 Send My Own Image'), "vcv_photo")], [(tr('🎞️ Frame From Video'), "vcv_frame")],
                           [(tr('↩️ Back'), "back_main")]))
    elif data == "vcv_photo":
        s.state = "vcv_photo"
        await safe_edit(msg, tr('🖼️ Send the image to use as the video thumbnail:'))
    elif data == "vcv_frame":
        s.state = "vcv_frame"
        dtxt = tr(' (video length <code>{0}</code>)', fmt_time(s.duration)) if s.duration else ""
        await safe_edit(msg, tr('🎞️ Send the time to grab the frame from{0}\nExample: <code>1:30</code> or <code>45</code> (seconds)', dtxt))
    elif data == "vi_audio":
        await safe_edit(msg, tr('🎵 {0}\nChoose a format:', hd(tr('Extract audio'))),
                        kb([("MP3", "vau:mp3"), ("M4A", "vau:m4a"), ("Opus", "vau:opus")], [(tr('↩️ Back'), "back_main")]))
    elif data.startswith("vau:"):
        await go_job(q, s, "vaudio", {"fmt": data.split(":")[1]})
    elif data == "vi_convert":
        rows = [[("MP4", "vcn:mp4"), ("MKV", "vcn:mkv"), ("MOV", "vcn:mov")],
                [("AVI", "vcn:avi")] if LIGHT_HOST else [("WEBM", "vcn:webm"), ("AVI", "vcn:avi")]]
        await safe_edit(msg, tr('🔄 {0}\nChoose a format:\n{1}', hd(tr('Convert video')), orig_line(s)),
                        kb(*rows, [(tr('↩️ Back'), "back_main")]))
    elif data.startswith("vcn:"):
        fmt = data.split(":")[1]
        if LIGHT_HOST:                     # no resolution step: only the container changes (no re-encoding)
            await go_job(q, s, "vconvert", {"fmt": fmt, "h": 0})
        else:
            text, markup = res_menu(s, fmt)
            await safe_edit(msg, text, markup)
    elif data.startswith("vcr:"):
        _, fmt, h = data.split(":")
        await go_job(q, s, "vconvert", {"fmt": fmt, "h": int(h)})
    elif data == "vi_stream":
        await go_job(q, s, "vstream", {})
    elif data == "vi_rename":
        s.state = "rename"
        await safe_edit(msg, tr('✏️ Send the new name (without {0}):', Path(s.name).suffix or '.mp4'))
    elif data == "vi_transcribe":
        await start_transcribe(q, s)
    elif data == "sp_custom_dummy":
        pass
    else:
        return False
    return True


# ══════════════════════════ Text input states ══════════════════════════
TEXT_STATES = {"rename", "unlock_pw", "img_range", "pdf_split_custom", "pdf_split_equal", "media_split_custom",
               "media_split_equal", "media_cut_one", "meta_title", "meta_artist", "vcv_frame"}

def ERR_GENERIC():
    return tr('❌ Something went wrong. Please try again.')


async def on_text_state(client, m):
    uid = m.from_user.id
    set_lang(uid, m.from_user)
    async with ulock(uid):
        s = sessions.get(uid)
        if s is None or s.state not in TEXT_STATES:
            return
        touch(s)
        text = (m.text or "").strip()
        st = s.state
        try:
            if st == "rename":
                new = safe_name_text(text, Path(s.name).suffix if s.kind in ("audio", "video") else
                                     (f".{s.ftype}" if s.kind == "office" else ".pdf"))
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                await start_job(s, "rename", {"name": new}, status, notify_msg=m)
            elif st == "unlock_pw":
                res = await asyncio.to_thread(check_pdf_password, str(s.path), text)
                if res == "WRONG_PASSWORD":
                    await m.reply_text(tr('❌ Wrong password. Try again or send /cancel:'))
                    return
                status = await m.reply_text(L("🔓", tr('Unlocking')))
                await start_job(s, "pdf_unlock", {"password": text}, status, notify_msg=m)
            elif st == "img_range":
                total = await asyncio.to_thread(parse_pages_total, s)
                pages = parse_page_ranges(text, total)
                if not pages:
                    await m.reply_text(tr("❌ Invalid input, or those pages don't exist. Try again:"))
                    return
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                await start_job(s, "pdf_images", {"pages": pages}, status, notify_msg=m)
            elif st == "pdf_split_custom":
                total = await asyncio.to_thread(parse_pages_total, s)
                pages = parse_page_ranges(text, total)
                if not pages:
                    await m.reply_text(tr("❌ Invalid input, or those pages don't exist. Try again:"))
                    return
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                await start_job(s, "pdf_split_custom", {"pages": pages}, status, notify_msg=m)
            elif st == "pdf_split_equal":
                try:
                    chunk = int(text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
                    if chunk <= 0:
                        raise ValueError
                except ValueError:
                    await m.reply_text(tr('❌ Please send a whole number greater than zero:'))
                    return
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                await start_job(s, "pdf_split_equal", {"chunk": chunk}, status, notify_msg=m)
            elif st == "media_split_custom":
                ranges = parse_time_ranges(text, s.duration)
                if not ranges:
                    await m.reply_text(tr('❌ Invalid format. Example: <code>0:00-10:30, 12:00-15:00</code>\nTry again:'))
                    return
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                op_name = "asplit" if s.kind == "audio" else "vsplit"
                await start_job(s, op_name, {"mode": "custom", "ranges": ranges}, status, notify_msg=m)
            elif st == "media_cut_one":
                ranges = parse_time_ranges(text, s.duration)
                if not ranges:
                    await m.reply_text(tr('❌ Invalid format. Example: <code>0:00-2:00, 10:00-12:30</code>\nTry again:'))
                    return
                if s.kind == "video" and s.data.get("precise") and LIGHT_HOST and s.duration:
                    kept = sum(((b if b is not None else s.duration) - a) for a, b in ranges)
                    if kept > PRECISE_CUT_MAX_MIN * 60:
                        await m.reply_text(tr('❌ Precise cut on this server is limited to {0} minutes of kept video (you sent {1}). Send shorter ranges:',
                                              PRECISE_CUT_MAX_MIN, fmt_time(kept)))
                        return
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                op_name = "acut" if s.kind == "audio" else "vcut"
                await start_job(s, op_name, {"ranges": ranges, "precise": bool(s.data.get("precise"))}, status,
                                notify_msg=m)
            elif st == "media_split_equal":
                t = text.lower().replace(" ", "")
                mult = 60.0
                if t.endswith("s"):
                    mult, t = 1.0, t[:-1]
                elif t.endswith("m"):
                    t = t[:-1]
                val = parse_time(t) if ":" not in t else None
                if val is None or val <= 0:
                    await m.reply_text(tr('❌ Send a number greater than zero (minutes, or add s for seconds):'))
                    return
                seconds = val * mult
                if seconds < 5:
                    await m.reply_text(tr('❌ Minimum part length is 5 seconds. Send a larger number:'))
                    return
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                op_name = "asplit" if s.kind == "audio" else "vsplit"
                await start_job(s, op_name, {"mode": "equal", "seconds": seconds}, status, notify_msg=m)
            elif st in ("meta_title", "meta_artist"):
                key = "m_title" if st == "meta_title" else "m_artist"
                s.data[key] = text[:200]
                s.state = None
                await m.reply_text(meta_text(s), reply_markup=meta_kb())
            elif st == "vcv_frame":
                t = parse_time(text)
                if t is None:
                    await m.reply_text(tr('❌ Invalid time. Example: <code>1:30</code> or <code>45</code>'))
                    return
                if s.duration and t >= s.duration:
                    await m.reply_text(tr("❌ That's past the end of the video (<code>{0}</code>). Try an earlier time:", fmt_time(s.duration)))
                    return
                status = await m.reply_text(L("⚙️", tr('Preparing')))
                await start_job(s, "vcover", {"time": t}, status, notify_msg=m)
        except Exception as e:
            logger.exception("text state error: %s", e)
            await m.reply_text(ERR_GENERIC())


async def handle_thumb_photo(m, s, info):
    """A photo arrived while we're waiting for an audio/video cover."""
    if info["size"] and info["size"] > MAX_IMAGE_MB * MB:
        await m.reply_text(tr('❌ Image is over the {0} MB limit.', MAX_IMAGE_MB))
        return
    raw_p = await dl(info["file_id"], s.dir / f"cover_in_{uuid.uuid4().hex[:5]}.jpg")
    if not raw_p:
        await m.reply_text(tr("❌ Couldn't download the image. Please try again."))
        return
    cover = s.dir / f"cover_{uuid.uuid4().hex[:5]}.jpg"
    try:
        await asyncio.to_thread(make_jpeg, raw_p, cover, 1280, 400)
    except Exception:
        await m.reply_text(tr("❌ That isn't a valid image. Send another one:"))
        return
    if s.state == "meta_thumb":
        s.data["m_cover"] = cover
        s.data.pop("m_remove", None)
        s.data.pop("m_default_cover", None)         # a custom image replaces the default for this file only
        s.state = None
        await m.reply_text(meta_text(s), reply_markup=meta_kb())
    else:  # vcv_photo
        status = await m.reply_text(L("⚙️", tr('Preparing')))
        await start_job(s, "vcover", {"cover": cover}, status, notify_msg=m)


# ══════════════════════════ Incoming files & commands ══════════════════════════
async def on_media(client, m):
    uid = m.from_user.id
    set_lang(uid, m.from_user)
    async with ulock(uid):
        s = sessions.get(uid)
        if s:
            touch(s)
        kind, info = classify(m)
        if uid in COVER_WAIT:                      # /audio_cover → "Change": the next image is the default cover
            started = COVER_WAIT.pop(uid)
            if kind == "image" and time.time() - started <= COVER_WAIT_TTL:
                return await save_cover_from_message(m, info)
        if kind == "image" and s and s.state in ("meta_thumb", "vcv_photo"):
            return await handle_thumb_photo(m, s, info)
        if s and s.state == "media_merge":
            return await add_merge_media(m, s, kind, info)
        if s and s.state == "merge":
            if kind == "pdf":
                return await add_merge_pdf(m, s, info)
            return await m.reply_text(tr('❌ Send PDF files only (or tap Cancel).'))
        if kind == "image":
            return await add_image(m, info)
        if kind == "pdf":
            return await start_pdf(m, info)
        if kind == "office":
            return await start_office(m, info)
        if kind == "book":
            return await start_book(m, info)
        if kind in ("audio", "video"):
            return await start_media(m, kind, info)
        await m.reply_text(tr('❌ {0}\nSupported: {1}.', hd(tr('Unsupported file')), SUPPORTED()))


def choose_lang_text():
    return f"🌐 {hd('Choose your language')}\n{smallcaps('You can change it any time.')}"


CMDS_EN = [(c, smallcaps(d)) for c, d in [("start", "Start the bot"), ("help", "How it works"),
                                         ("language", "Change language"), ("audio_cover", "Default audio cover"),
                                         ("cancel", "Cancel the current task"), ("status", "Bot status")]]
CMDS_AR = [("start", "ابدأ استخدام البوت"), ("help", "طريقة الاستخدام"), ("language", "تغيير اللغة"),
           ("audio_cover", "الغلاف الافتراضي للصوت"),
           ("cancel", "إلغاء العملية الحالية"), ("status", "حالة البوت")]


def lang_kb():
    return kb([("🇪🇬 Arabic", "lang:ar"), ("🇬🇧 English", "lang:en")])


def welcome_text():
    return tr("👋 {0}\n📥 Send a file and I'll show you the right tools.", hd(tr('Welcome to QuickFile')))


async def cmd_start(client, m):
    uid = m.from_user.id
    set_lang(uid, m.from_user)
    COVER_WAIT.pop(uid, None)
    drop_session(uid)
    if uid not in LANGS:                      # first time: ask which language to use
        await m.reply_text(choose_lang_text(), reply_markup=lang_kb())
        return
    await m.reply_text(welcome_text())


async def cmd_language(client, m):
    set_lang(m.from_user.id, m.from_user)
    await m.reply_text(choose_lang_text(), reply_markup=lang_kb())


async def sync_chat_commands():
    """Users who already picked a language have a per-chat command menu saved by an older version of the bot, and it
    overrides the global one. Re-apply the current list for them on every start so new commands show up everywhere."""
    done = 0
    for uid, lang in list(LANGS.items()):
        try:
            await app.set_bot_commands([BotCommand(*c) for c in (CMDS_AR if lang == "ar" else CMDS_EN)],
                                       scope=BotCommandScopeChat(uid))
            done += 1
        except Exception as e:                     # e.g. the user blocked the bot
            logger.debug("set_bot_commands (chat %s) failed: %s", uid, e)
        await asyncio.sleep(0.2)
    if done:
        logger.info("Command menu refreshed for %s chat(s)", done)


async def cb_lang(q, data):
    uid = q.from_user.id
    lang = data.split(":")[1] if ":" in data else ""
    if lang not in ("ar", "en"):
        await q.answer()
        return
    first = uid not in LANGS
    save_lang(uid, lang)
    set_lang(uid)
    await q.answer("✅")
    try:       # the "Menu" command list follows the language the user picked
        await app.set_bot_commands([BotCommand(*c) for c in (CMDS_AR if lang == "ar" else CMDS_EN)],
                                   scope=BotCommandScopeChat(uid))
    except Exception as e:
        logger.debug("set_bot_commands (chat %s) failed: %s", uid, e)
    await safe_edit(q.message, welcome_text() if first else tr("✅ Language updated."))


HELP_EN = (
    "ℹ️ {0}\n"
    "Send me any file and I'll show the right tools for it.\n\n"
    "📄 <b>PDF</b>\nMerge, split, compress, convert, extract text, unlock\n\n"
    "🖼️ <b>Images</b>\nTo PDF, extract text\n\n"
    "📎 <b>Word / PowerPoint / HTML</b>\nTo PDF or TXT\n\n"
    "📘 <b>Interactive books (.exe)</b>\nTo PDF (Flip PDF / FlipBuilder)\n\n"
    "🎵 <b>Audio</b>\nConvert, compress, cut, merge, transcribe, edit tags\n\n"
    "🎬 <b>Video</b>\n{2}\n\n"
    "{1}\n⚡ Light jobs run instantly; heavy ones wait in a short queue.\n\n"
    "📌 <b>Commands</b>\n"
    "/start — restart\n"
    "/language — change language\n"
    "/audio_cover — default audio cover\n"
    "/cancel — cancel the current task\n"
    "/status — bot status")


def limits_text():
    vals = {"PDF": MAX_PDF_MB, "Office": MAX_OFFICE_MB, "Audio": MAX_AUDIO_MB, "Video": MAX_VIDEO_MB}
    if len(set(vals.values())) == 1:
        return tr("⚙️ Up to {0} MB per file.", MAX_PDF_MB)
    return tr("⚙️ Max size: PDF {0} MB | Office {1} MB | Audio {2} MB | Video {3} MB",
              MAX_PDF_MB, MAX_OFFICE_MB, MAX_AUDIO_MB, MAX_VIDEO_MB)


def video_tools_text():
    if LIGHT_HOST:
        return tr("Cut, merge, change format, thumbnail, extract audio, transcribe")
    return tr("Compress, cut, merge, convert, thumbnail, extract audio, transcribe")


async def cmd_help(client, m):
    set_lang(m.from_user.id, m.from_user)
    await m.reply_text(tr(HELP_EN, hd(tr('How it works')), limits_text(), video_tools_text()))


async def cmd_cancel(client, m):
    set_lang(m.from_user.id, m.from_user)
    COVER_WAIT.pop(m.from_user.id, None)
    drop_session(m.from_user.id)
    await m.reply_text(CANCELLED())


async def cmd_status(client, m):
    uid = m.from_user.id
    set_lang(uid, m.from_user)
    mine = [j for j in jobs.values() if j.uid == uid]
    lines = [f"📊 {hd(tr('Bot status'))}",
             tr('⚙️ Running: {0} · Queued: {1}', len(running), len(waiting)),
             f"💾 Free space: ≈{max(0, free_budget()) / MB:.0f} MB",
             tr('📌 Your jobs: {0}', len(mine))]
    await m.reply_text("\n".join(lines))


async def on_fallback_text(client, m):
    set_lang(m.from_user.id, m.from_user)
    await m.reply_text(tr("📥 Send me a file (PDF, image, Word, PowerPoint, audio or video) and I'll show you the tools."))


# ══════════════════════════ Arabic translations (key = English text) ══════════════════════════
AR = {
    # --- interactive books (.exe)
    "Checking the file": "جاري فحص الملف",
    "Converting book to PDF": "جاري تحويل الكتاب إلى PDF",
    "Not a supported book": "كتاب غير مدعوم",
    "❌ {0}\nThis .exe isn't an interactive book made by Flip PDF / FlipBuilder, so I stopped without processing it.": "❌ {0}\nالملف ده مش كتاب تفاعلي من Flip PDF / FlipBuilder، فوقفت من غير ما أعالجه.",
    "❌ Interactive books (.exe) aren't supported on this bot right now.": "❌ الكتب التفاعلية (.exe) مش مدعومة على البوت حاليًا.",
    "Couldn't convert this book.": "مقدرتش أحوّل الكتاب ده.",
    "⚠️ Some parts of the book may look different from the original.": "⚠️ ممكن تظهر بعض أجزاء الكتاب مختلفة شوية عن الأصل.",
    # --- progress words
    "{0} of {1}": "{0} من {1}",
    "{0} received": "تم استلام {0}",
    "{0} sent": "تم رفع {0}",
    "{0}\n{1}\n<code>{2} of {3}</code>": "{0}\n{1}\n<code>{2} من {3}</code>",
    # --- level / quality / format names
    "Light": "خفيف", "Medium": "متوسط", "Strong": "قوي", "High": "عالية", "Low": "خفيفة",
    "Voice note": "ريكورد (Voice)", "Word (legacy)": "Word (صيغة قديمة)", "PowerPoint (legacy)": "PowerPoint (صيغة قديمة)",
    # --- engine
    "This file is too large for the server right now. Try a smaller one.": "الملف ده كبير على إمكانيات السيرفر حاليًا. جرّب ملف أصغر.",
    "Server busy": "السيرفر مشغول",
    "{0}\nWaiting for free space, will start automatically.": "{0}\nبستنى مساحة فاضية وهبدأ تلقائيًا.",
    "❌ Cancel": "❌ إلغاء",
    "ffmpeg is not installed on the server.": "ffmpeg مش متثبت على السيرفر.",
    "This took longer than allowed and was stopped. Try a smaller file or a lighter level.": "العملية أخدت وقت أطول من المسموح واتوقفت. جرّب ملف أصغر أو مستوى أخف.",
    "Processing failed. The file may be corrupted or in an unsupported format.": "فشلت معالجة الملف. ممكن يكون تالف أو صيغته غير مدعومة.",
    "In queue": "في الطابور",
    "⏳ {0}\n📍 Position <b>{1}</b> of {2}\nStarts automatically on your turn.": "⏳ {0}\n📍 ترتيبك <b>{1}</b> من {2}\nهنبدأ تلقائيًا أول ما يجي دورك.",
    "⚠️ You already have {0} tasks running. Wait for one to finish, then try again.": "⚠️ عندك {0} مهام شغالة حاليًا. استنى لما واحدة تخلص وابعت تاني.",
    "❌ Unexpected error while processing. Please try again.": "❌ حصل خطأ غير متوقع أثناء المعالجة. حاول مرة تانية.",
    "This task has already finished or was cancelled.": "المهمة دي خلصت أو اتلغت بالفعل.",
    "This took too long and was stopped. Try a smaller file.": "العملية أخدت وقت طويل واتوقفت. جرّب ملف أصغر.",
    "Uploading {0}/{1}": "جاري الرفع {0}/{1}",
    "Uploading": "جاري الرفع", "Downloading": "جاري التحميل", "Processing": "جاري التنفيذ",
    "Preparing": "جاري التجهيز", "Starting": "جاري البدء",
    "The free daily quota for this service is used up. Try again tomorrow.": "نفدت الكوتة المجانية اليومية للخدمة دي، جرّب تاني بكرة.",
    # --- pdf ops
    "Compressing PDF": "جاري ضغط الملف",
    "Compression failed:\n<code>{0}</code>": "فشل الضغط:\n<code>{0}</code>",
    "\nℹ️ Already optimized, nothing more to shrink.": "\nℹ️ الملف كان مضغوط بالفعل ومفيش مجال لتقليل أكتر.",
    "PDF compressed": "تم ضغط الملف",
    "PDF split": "تم تقسيم الملف",
    "✂️ {0} {1}/{2}": "✂️ {0} {1}/{2}",
    "Part": "الجزء",
    "Merging {0} files": "جاري دمج {0} ملفات",
    "PDFs merged": "تم دمج الملفات",
    "Something went wrong while unlocking.": "حصل خطأ أثناء فك الحماية.",
    "PDF unlocked": "تم فك الحماية",
    "Pages combined": "تم تجميع الصفحات",
    "⚠️ Large file: only the first {0} pages will be converted.": "⚠️ الملف كبير جدًا، هيتحول أول {0} صفحة بس.",
    "Converting to {0}": "جاري التحويل لـ {0}",
    "{0}\nThis can take about a minute.": "{0}\nممكن ياخد حوالي دقيقة.",
    "Conversion failed": "فشل التحويل",
    "Converted to {0}": "تم التحويل لـ {0}",
    "Extracting text": "جاري استخراج النص",
    "Text extraction failed.": "فشل استخراج النص.",
    "Text extraction failed": "فشل استخراج النص",
    "Text extracted": "تم استخراج النص",
    "Analyzing pages": "جاري تحليل الصفحات",
    "Analyzing text": "جاري تحليل النص",
    "Could not read the file.": "فشل قراءة الملف.",
    "No ready-made questions were found in this file. Try the AI generate option instead.": "الملف مفيهوش أسئلة جاهزة تتستخرج. جرّب خيار توليد الأسئلة بالـ AI بدلاً من كده.",
    "Questions ready": "الأسئلة جاهزة",
    "Converting to PDF": "جاري التحويل لـ PDF",
    "Want the text of this file too?": "تحب تستخرج نص نفس الملف؟",
    "✅ No, Thanks": "✅ تمام، خلاص",
    "📄 Get Text": "📄 نص الملف كمان",
    "Converted to PDF": "تم التحويل لـ PDF",
    "This file has no extractable text.": "الملف مفيهوش نص يمكن استخراجه.",
    "Want a PDF copy of this file too?": "تحب تاخد نسخة PDF من نفس الملف؟",
    "📕 Get PDF": "📕 نسخة PDF كمان",
    "Reading text from images": "جاري استخراج النص من الصور",
    "Could not extract text from the images.": "فشل استخراج النص من الصور.",
    "Renamed": "تم تغيير الاسم",
    # --- audio / video ops
    "\nℹ️ Output isn't smaller than the original (already compressed).": "\nℹ️ الناتج مش أصغر من الأصل (الملف كان مضغوط بالفعل).",
    "Unsupported format.": "صيغة غير مدعومة.",
    "\n🎚️ Quality: {0} kbps": "\n🎚️ الجودة: {0}kbps",
    "Compressing audio ({0})": "جاري ضغط الصوت ({0})",
    "Audio compressed": "تم ضغط الصوت",
    "Sent as streamable audio": "تم إرسال الصوت بشكل قابل للبث",
    "Converting to streamable MP3": "جاري التحويل لـ MP3 قابل للبث",
    "Converted to streamable MP3": "تم التحويل لـ MP3 قابل للبث",
    "Updating audio tags": "جاري تعديل بيانات الصوت",
    "Tags updated": "تم تعديل بيانات الصوت",
    "Couldn't detect the duration, so equal-length splitting isn't possible. Try custom ranges.": "معرفتش أعرف مدة الملف عشان أقسمه. جرب تحدد أجزاء معينة.",
    "Part length is longer than the file itself, nothing to split.": "مدة الجزء أكبر من مدة الملف نفسه، مفيش حاجة تتقسم.",
    "Those ranges fall outside the file's duration.": "النطاقات اللي كتبتها برة مدة الملف.",
    "This split would create {0} parts; the maximum is {1}. Pick a longer part length.": "التقسيم ده هيطلع {0} جزء، والحد الأقصى {1}. اختار مدة أكبر لكل جزء.",
    "Cutting part {0}/{1}": "جاري قص الجزء {0}/{1}",
    "No parts were produced. Check the times you entered.": "مفيش أجزاء اتولدت. اتأكد من الأوقات اللي كتبتها.",
    "Precise cut and merge": "جاري القص بدقة وتجميع الأجزاء",
    "Joining parts": "جاري تجميع الأجزاء في ملف واحد",
    "Cut failed. Check the times you entered.": "فشل القص. اتأكد من الأوقات اللي كتبتها.",
    "Cut and merged": "تم القص ودمج الأجزاء",
    "Cut complete": "تم القص",
    "Cutting": "جاري القص",
    "Compressing video ({0})": "جاري ضغط الفيديو ({0})",
    "Video compressed": "تم ضغط الفيديو",
    "\nℹ️ This format can't be streamed in Telegram, so it was sent as a file.": "\nℹ️ الصيغة دي مش بتشتغل كبث مباشر في تليجرام فاتبعتت كملف.",
    "\n📐 Resolution: {0}p": "\n📐 الدقة: {0}p",
    "Preparing for streaming": "جاري تجهيز الفيديو للبث",
    "Converting for streaming": "جاري تحويل الفيديو لصيغة قابلة للبث",
    "Sent as streamable video": "تم إرسال الفيديو بشكل قابل للبث",
    "This video has no audio.": "الفيديو ده مفيهوش صوت.",
    "Extracting audio ({0})": "جاري استخراج الصوت ({0})",
    "Audio extracted": "تم استخراج الصوت",
    "Couldn't grab a frame at that time. Try another time.": "معرفتش أجيب لقطة من الوقت ده. جرب وقت تاني.",
    "Thumbnail updated": "تم تغيير الصورة المصغرة",
    "Updating thumbnail": "جاري تغيير الصورة المصغرة",
    "{0}\nNo re-upload needed.": "{0}\nمن غير إعادة رفع الفيديو.",
    # --- general messages / menus
    "Session expired": "انتهت صلاحية الجلسة",
    "⚠️ {0}\nPlease send the file again to start over.": "⚠️ {0}\n📥 ابعت الملف تاني عشان تبدأ من جديد.",
    "🚫 Cancelled.": "🚫 تم إلغاء العملية.",
    "PDF, DOCX, DOC, PPTX, PPT, HTML, interactive books (.exe), images, audio and video": "PDF، DOCX، DOC، PPTX، PPT، HTML، الكتب التفاعلية (.exe)، الصور، الصوت والفيديو",
    "Choose a tool 👇": "اختار الأداة اللي عايزها 👇",
    "✂️ Split": "✂️ تقسيم", "🔗 Merge": "🔗 دمج", "🔄 Convert": "🔄 تحويل", "🗜️ Compress": "🗜️ ضغط",
    "📑 Print Layout": "📑 تجميع للطباعة", "📚 Extract": "📚 استخراج", "✏️ Rename": "✏️ إعادة التسمية",
    "🔓 Unlock": "🔓 فك الحماية", "📕 Convert to PDF": "📕 تحويل لـ PDF", "🔤 Extract Text": "🔤 استخراج نص",
    "📄 To TXT": "📄 تحويل TXT", "📕 To PDF": "📕 تحويل PDF", "✂️ Cut / Split": "✂️ قص / تقسيم",
    "🏷️ Edit Tags": "🏷️ تعديل البيانات", "📡 Make Streamable": "📡 قابل للبث", "🎵 Extract Audio": "🎵 استخراج الصوت",
    "🖼️ Thumbnail": "🖼️ الصورة المصغرة",
    "  ·  📑 {0} pages": "  ·  📑 {0} صفحة",
    "PDF received": "تم استلام الملف",
    "📄 {0}\n<code>{1}</code>\n{2}\n\n{3}": "📄 {0}\n<code>{1}</code>\n{2}\n\n{3}",
    "Audio received": "تم استلام الصوت",
    "Video received": "تم استلام الفيديو",
    "\nℹ️ <i>Sent as a document. Use 📡 to make it streamable.</i>": "\nℹ️ <i>الملف ده مبعوت كمستند. تقدر تحوله لقابل للبث بزرار 📡.</i>",
    "\n🎙️ <i>Voice note. You can convert it to MP3, M4A and more.</i>": "\n🎙️ <i>ريكورد تليجرام. تقدر تحوله لـ MP3 أو M4A أو غيرهم.</i>",
    "  ·  🖼️ {0} slides": "  ·  🖼️ {0} سلايد",
    "📎 {0}\n<code>{1}</code>\n{2}\n\n{3}": "📎 {0}\n<code>{1}</code>\n{2}\n\n{3}",
    "{0} file received": "تم استلام ملف {0}",
    "1 image received": "تم استلام صورة",
    "{0} images received": "تم استلام {0} صور",
    "📷 {0}\nSend more to add them, or choose below 👇": "📷 {0}\nابعت صور تانية لو عايز تضيفها، أو اختار من تحت 👇",
    "📌 <b>Original:</b> ": "📌 <b>الأصل:</b> ",
    "\n🎚️ <b>Original quality:</b> ≈{0} kbps <i>(estimated from size ÷ duration)</i>": "\n🎚️ <b>جودة الأصل:</b> حوالي {0}kbps <i>(تقدير من حجم الملف ÷ المدة)</i>",
    "↩️ Back": "↩️ رجوع",
    "Choose quality": "اختار الجودة",
    "Original ({0}×{1})": "الأصلي ({0}×{1})",
    "Original": "الأصلي",
    "Choose resolution": "اختار الدقة",
    "Receiving file": "جاري استلام الملف",
    "File too large": "الملف كبير جدًا",
    "❌ {0}\n<code>{1}</code> is over the {2} MB limit.\nPlease send a smaller file.": "❌ {0}\n<code>{1}</code> أكبر من الحد المسموح ({2} MB).\nابعت ملف أصغر.",
    "❌ This file is larger than the free server space right now. Try a smaller one.": "❌ الملف ده أكبر من المساحة المتاحة على السيرفر حاليًا. جرّب ملف أصغر.",
    "⏳ The server is busy right now. Please try again in a minute.": "⏳ السيرفر مشغول بملفات تانية دلوقتي. جرّب تبعت الملف بعد دقيقة.",
    "❌ Couldn't save the file. Please try again.": "❌ حصل خطأ أثناء حفظ الملف. حاول تاني.",
    "❌ {0} files aren't supported on this bot right now.": "❌ دعم ملفات {0} مش مفعّل على البوت حاليًا.",
    "❌ Image is over the {0} MB limit.": "❌ الصورة أكبر من الحد المسموح ({0} MB).",
    "⚠️ Maximum reached ({0} images).": "⚠️ وصلت للحد الأقصى ({0} صورة).",
    "❌ Download failed. Please try again.": "❌ فشل تحميل الملف. حاول تاني.",
    "✅ Merge Now": "✅ دمج الملفات الآن",
    "Merge mode": "وضع دمج الملفات",
    "📥 Added! Send more files, or tap <b>Merge Now</b>.": "📥 تمت الإضافة! ابعت المزيد أو اضغط <b>دمج الملفات الآن</b>.",
    "📥 Send the remaining files in the order you want.": "📥 ابعت باقي الملفات بالترتيب اللي عايزه.",
    "✅ New image": "✅ صورة جديدة",
    "🗑️ Will be removed": "🗑️ هتتشال",
    "Unchanged": "كما هي",
    "Edit audio tags": "تعديل بيانات الصوت",
    "🏷️ {0}\n\n🎵 <b>Title:</b> <code>{1}</code>\n👤 <b>Artist:</b> <code>{2}</code>\n🖼️ <b>Cover:</b> {3}\n\nPick what to change, then tap <b>Apply</b>.": "🏷️ {0}\n\n🎵 <b>العنوان:</b> <code>{1}</code>\n👤 <b>المؤلف/الفنان:</b> <code>{2}</code>\n🖼️ <b>الصورة المصغرة:</b> {3}\n\nاختار اللي عايز تغيّره، وبعدين اضغط <b>تنفيذ</b>.",
    "🎵 Title": "🎵 العنوان", "👤 Artist": "👤 المؤلف", "🖼️ Set Cover": "🖼️ صورة مصغرة", "🗑️ Remove Cover": "🗑️ شيل الصورة", "✅ Apply": "✅ تنفيذ",
    "❌ Something went wrong. Please try again.": "❌ حصل خطأ غير متوقع. حاول مرة تانية.",
    "💡 <b>Examples:</b>\n- Range: <code>1-5</code>\n- Mixed: <code>1-3, 5, 8-10</code>": "💡 <b>أمثلة:</b>\n- نطاق متصل: <code>1-5</code>\n- صفحات ونطاقات متفرقة: <code>1-3, 5, 8-10</code>",
    "audio": "الصوت", "video": "الفيديو",
    "\n⏱ Duration: <code>{0}</code>": "\n⏱ مدة الملف: <code>{0}</code>",
    "Cut / split": "قص / تقسيم",
    "✂️ {0}{1}\n\nWhat would you like to do?": "✂️ {0}{1}\n\nعايز إيه؟",
    "✂️ Cut (Single Output)": "✂️ قص (ناتج واحد)", "🗂️ Split Into Parts": "🗂️ تقسيم لأجزاء",
    "\n<i>Part boundaries may be off by a second or two.</i>": "\n<i>حدود الأجزاء ممكن تتفرق ثانية أو اتنين.</i>",
    "Split into parts": "تقسيم لأجزاء",
    "🗂️ {0}{1}{2}\n\nHow should it be split?": "🗂️ {0}{1}{2}\n\nإزاي تحب تتقسم؟",
    "⏱️ Fixed Length": "⏱️ مدة ثابتة لكل جزء", "🔢 Custom Ranges": "🔢 تحديد أجزاء معينة",
    "Cut video": "قص الفيديو",
    "✂️ {0}{1}\n\nChoose the cut type:\n⚡ <b>Fast:</b> no re-encoding, but edges may be off by a second or two.\n🎯 <b>Precise:</b> frame-accurate, but slower.": "✂️ {0}{1}\n\nاختار نوع القص:\n⚡ <b>سريع:</b> من غير إعادة ترميز، بس الحدود ممكن تتفرق ثانية أو اتنين.\n🎯 <b>دقيق:</b> بالثانية بالظبط، بس أبطأ.",
    "⚡ Fast": "⚡ سريع", "🎯 Precise": "🎯 دقيق",
    "Custom parts": "أجزاء معينة",
    "🔢 {0}{1}\n\nSend the ranges you want. Each one is sent as its own file.\n\n💡 <b>Examples:</b>\n- One part: <code>1:30-5:00</code>\n- Several: <code>0:00-10:30, 12:00-15:00</code>\n- Until the end: <code>20:00-</code>": "🔢 {0}{1}\n\nابعت الأجزاء اللي عايزها (كل جزء هيتبعت لوحده).\n\n💡 <b>أمثلة:</b>\n- جزء واحد: <code>1:30-5:00</code>\n- أكتر من جزء: <code>0:00-10:30, 12:00-15:00</code>\n- من وقت لحد النهاية: <code>20:00-</code>",
    "Fixed-length parts": "أجزاء بمدة ثابتة",
    "⏱️ {0}{1}\n\nSend the length of each part in minutes.\n(Example: <code>10</code> = 10-minute parts. Add <code>s</code> for seconds, like <code>90s</code>.)": "⏱️ {0}{1}\n\nابعت مدة كل جزء بالدقائق.\n(مثال: <code>10</code> يعني كل جزء 10 دقايق. وتقدر تكتب <code>90s</code> للثواني.)",
    "Cut": "قص",
    "✂️ {0}{1}\n\nSend the ranges to keep. They'll be joined into one {2}.\n\n💡 <b>Examples:</b>\n- One part: <code>1:30-5:00</code>\n- Several: <code>0:00-2:00, 10:00-12:30</code>\n- Until the end: <code>20:00-</code>": "✂️ {0}{1}\n\nابعت الأجزاء اللي عايز تحتفظ بيها، وهتتجمع في ملف واحد.\n\n💡 <b>أمثلة:</b>\n- جزء واحد: <code>1:30-5:00</code>\n- أكتر من جزء: <code>0:00-2:00, 10:00-12:30</code>\n- من وقت لحد النهاية: <code>20:00-</code>",
    "Extract": "استخراج المحتوى",
    "📚 {0}\nWhat do you want to extract?": "📚 {0}\nعايز تستخرج إيه؟",
    "📄 Text (TXT)": "📄 النص (TXT)", "📝 Questions (BMCQ)": "📝 أسئلة (BMCQ)",
    "Extract questions": "استخراج أسئلة",
    "📝 {0}\n\n🎯 <b>Ready-made:</b> the file already has questions and answers, formatted exactly as they are.\n🧠 <b>AI generate:</b> the file is a lecture or transcript, and AI writes questions from it.": "📝 {0}\n\n🎯 <b>استخراج جاهز:</b> لو الملف فيه أسئلة وإجابات أصلاً وعايز تنسقها زي ما هي بدون أي تعديل.\n🧠 <b>توليد AI:</b> لو الملف شرح أو تفريغ وعايز الذكاء الاصطناعي يولد أسئلة منه.",
    "🎯 Extract Ready-Made": "🎯 استخراج أسئلة جاهزة", "🧠 Generate With AI": "🧠 توليد أسئلة من الشرح (AI)",
    "Compress PDF": "ضغط الملف",
    "🗜️ {0}\nChoose a level:": "🗜️ {0}\nاختار مستوى الضغط:",
    "🟡 Balanced": "🟡 ضغط متوازن", "🟢 Light": "🟢 ضغط خفيف", "🔴 Maximum": "🔴 ضغط أقصى",
    "🖼️ Compress Images Only": "🖼️ ضغط صور الملف فقط",
    "Compress images": "ضغط الصور",
    "🖼️ {0}\nRuns locally, no external service. Choose strength:": "🖼️ {0}\nبيشتغل محليًا من غير أي خدمة خارجية. اختار القوة:",
    "✏️ Send the new name (without .pdf):": "✏️ ابعت الاسم الجديد (من غير .pdf):",
    "🔑 This file is password-protected. Send the password:": "🔑 الملف محمي بكلمة سر. ابعتها عشان نفتحه:",
    "Print layout": "تجميع للطباعة",
    "📑 {0}\nHow should the pages be combined?": "📑 {0}\nعايز تجمع الصفحات إزاي؟",
    "➡️ 2 Side by Side": "➡️ صفحتين جمب بعض", "⬇️ 2 Stacked": "⬇️ صفحتين فوق بعض", "🔳 4 Per Sheet": "🔳 4 صفحات في الورقة",
    "Unlocking": "جاري فك الحماية",
    "Convert": "تحويل الصيغة",
    "🔄 {0}\nChoose a format:": "🔄 {0}\nاختار الصيغة:",
    "🖼️ To Images": "🖼️ تحويل لصور", "📊 To PowerPoint": "📊 تحويل لـ PowerPoint", "📝 To Word": "📝 تحويل لـ Word",
    "To images": "تحويل لصور",
    "🖼️ {0}\n📄 Your file has <code>{1} pages</code>\n\nWhich pages?": "🖼️ {0}\n📄 عدد صفحات ملفك: <code>{1} صفحة</code>\n\nأنهي صفحات؟",
    "📄 Whole File": "📄 الملف كامل", "🔢 Page Range": "🔢 تحديد نطاق معين",
    "Page range": "نطاق الصفحات",
    "🔢 {0}\n📄 Your file has <code>{1} pages</code>\n\nSend the pages you want.\n\n{2}": "🔢 {0}\n📄 عدد صفحات ملفك: <code>{1} صفحة</code>\n\nابعت الصفحات اللي عايزها.\n\n{2}",
    "Split PDF": "تقسيم الملف",
    "✂️ {0}\n📄 Your file has <code>{1} pages</code>\n\nHow do you want to split it?": "✂️ {0}\n📄 عدد صفحات ملفك: <code>{1} صفحة</code>\n\nإزاي تحب تقسم الملف؟",
    "🔢 Custom Pages": "🔢 تحديد صفحات معينة", "🗂️ Equal Parts": "🗂️ تقسيم لأجزاء متساوية",
    "Custom pages": "صفحات معينة",
    "✂️ {0}\n📄 Your file has <code>{1} pages</code>\n\nSend the pages you want.\n\n{2}\n- Single page: <code>4</code>": "✂️ {0}\n📄 عدد صفحات ملفك: <code>{1} صفحة</code>\n\nابعت الصفحات اللي عايزها.\n\n{2}\n- صفحة واحدة: <code>4</code>",
    "Equal parts": "أجزاء متساوية",
    "🗂️ {0}\n📄 Your file has <code>{1} pages</code>\n\nSend the number of pages per part.\n(Example: <code>10</code> creates files of 10 pages each.)": "🗂️ {0}\n📄 عدد صفحات ملفك: <code>{1} صفحة</code>\n\nابعت عدد الصفحات اللي عايزه في كل جزء.\n(مثال: لو بعت <code>10</code> هيتقسم لملفات كل ملف فيه 10 صفحات.)",
    "❌ Send at least one more PDF to merge.": "❌ لازم تبعت ملف إضافي واحد على الأقل للدمج.",
    "✏️ Send the new name (without .{0}):": "✏️ ابعت الاسم الجديد (من غير .{0}):",
    "⚠️ {0}\nPlease send the images again.": "⚠️ {0}\n📥 ابعت الصور تاني.",
    "Convert audio": "تحويل الصوت",
    "🔄 {0}\nChoose a format:\n{1}": "🔄 {0}\nاختار الصيغة:\n{1}",
    "🎙️ Voice Note": "🎙️ ريكورد",
    "Compress audio": "ضغط الصوت",
    "🗜️ {0}\nBest for speech and lectures. Choose format and strength:": "🗜️ {0}\nمناسب للكلام والمحاضرات. اختار الصيغة والقوة:",
    "🟢 M4A Light": "🟢 M4A خفيف", "🟢 Opus Light": "🟢 Opus خفيف", "🟡 M4A Medium": "🟡 M4A متوسط", "🟡 Opus Medium": "🟡 Opus متوسط",
    "🔴 M4A Strong": "🔴 M4A قوي", "🔴 Opus Strong": "🔴 Opus قوي",
    "✏️ Send the new name (without {0}):": "✏️ ابعت الاسم الجديد (من غير {0}):",
    "the new title": "العنوان الجديد", "the new artist name": "اسم المؤلف/الفنان الجديد",
    "✏️ Send {0}:": "✏️ ابعت {0}:",
    "🖼️ Send the image to use as the audio cover:": "🖼️ ابعت الصورة اللي عايزها كصورة مصغرة (غلاف) للصوت:",
    "⚠️ Nothing changed yet.\n\n": "⚠️ لسه معدلتش أي حاجة.\n\n",
    "Compress video": "ضغط الفيديو",
    "🗜️ {0}\n\n🟢 Light: up to 1080p\n🟡 Medium: 720p\n🔴 Strong: 480p": "🗜️ {0}\n\n🟢 خفيف: 1080p كحد أقصى\n🟡 متوسط: 720p\n🔴 قوي: 480p",
    "\n⚡ <i>Changed without downloading or re-uploading the video.</i>": "\n⚡ <i>التغيير هيتم من غير تحميل الفيديو ولا إعادة رفعه.</i>",
    "Video thumbnail": "الصورة المصغرة للفيديو",
    "🖼️ {0}{1}\n\nChoose a method:": "🖼️ {0}{1}\n\nاختار الطريقة:",
    "🎞️ Frame From Video": "🎞️ لقطة من الفيديو", "📤 Send My Own Image": "📤 أبعت صورة من عندي",
    "🖼️ Send the image to use as the video thumbnail:": "🖼️ ابعت الصورة اللي عايزها كصورة مصغرة للفيديو:",
    " (video length <code>{0}</code>)": " (مدة الفيديو <code>{0}</code>)",
    "🎞️ Send the time to grab the frame from{0}\nExample: <code>1:30</code> or <code>45</code> (seconds)": "🎞️ ابعت الوقت اللي عايز تاخد منه اللقطة{0}\nمثال: <code>1:30</code> أو <code>45</code> (ثواني)",
    "Extract audio": "استخراج الصوت",
    "🎵 {0}\nChoose a format:": "🎵 {0}\nاختار الصيغة:",
    "Convert video": "تحويل الفيديو",
    "❌ Wrong password. Try again or send /cancel:": "❌ كلمة السر غير صحيحة. حاول تاني أو ابعت /cancel:",
    "❌ Invalid input, or those pages don't exist. Try again:": "❌ إدخال غير صالح أو الصفحات غير موجودة. حاول مرة أخرى:",
    "❌ Please send a whole number greater than zero:": "❌ ابعت رقم صحيح أكبر من الصفر:",
    "❌ Invalid format. Example: <code>0:00-10:30, 12:00-15:00</code>\nTry again:": "❌ الصيغة غير صحيحة. مثال: <code>0:00-10:30, 12:00-15:00</code>\nحاول مرة أخرى:",
    "❌ Invalid format. Example: <code>0:00-2:00, 10:00-12:30</code>\nTry again:": "❌ الصيغة غير صحيحة. مثال: <code>0:00-2:00, 10:00-12:30</code>\nحاول مرة أخرى:",
    "❌ Send a number greater than zero (minutes, or add s for seconds):": "❌ ابعت رقم أكبر من الصفر (بالدقائق، أو مع s للثواني):",
    "❌ Minimum part length is 5 seconds. Send a larger number:": "❌ أقل مدة للجزء 5 ثواني. ابعت رقم أكبر:",
    "❌ Invalid time. Example: <code>1:30</code> or <code>45</code>": "❌ وقت غير صحيح. مثال: <code>1:30</code> أو <code>45</code>",
    "❌ That's past the end of the video (<code>{0}</code>). Try an earlier time:": "❌ الوقت أكبر من مدة الفيديو (<code>{0}</code>). جرب وقت أصغر:",
    "❌ Couldn't download the image. Please try again.": "❌ فشل تحميل الصورة. حاول تاني.",
    "❌ That isn't a valid image. Send another one:": "❌ الملف ده مش صورة صالحة. ابعت صورة تانية:",
    "❌ Send PDF files only (or tap Cancel).": "❌ ابعت ملفات PDF بس (أو اضغط إلغاء).",
    "Unsupported file": "نوع الملف غير مدعوم",
    "❌ {0}\nSupported: {1}.": "❌ {0}\nالأنواع المدعومة: {1}.",
    "Welcome to QuickFile": "أهلاً بيك في QuickFile",
    "👋 {0}\n📥 Send a file and I'll show you the right tools.": "👋 {0}\n📥 ابعت ملفك وهظهرلك الأدوات المناسبة.",
    "How it works": "إزاي أستخدم البوت؟",
    HELP_EN: (
        "ℹ️ {0}\n"
        "ابعتلي أي ملف وأنا أظهرلك الأدوات المناسبة له.\n\n"
        "📄 <b>ملفات PDF</b>\nدمج، تقسيم، ضغط، تحويل، استخراج نص، فك حماية\n\n"
        "🖼️ <b>الصور</b>\nتحويل لـ PDF، استخراج نص\n\n"
        "📎 <b>ملفات Word و PowerPoint و HTML</b>\nتحويل لـ PDF أو TXT\n\n"
        "📘 <b>الكتب التفاعلية (.exe)</b>\nتحويل لـ PDF (Flip PDF / FlipBuilder)\n\n"
        "🎵 <b>الصوت</b>\nتحويل، ضغط، قص، دمج، تفريغ نصي، تعديل البيانات\n\n"
        "🎬 <b>الفيديو</b>\n{2}\n\n"
        "{1}\n⚡ المهام الخفيفة بتتنفذ فورًا، والتقيلة بتستنى في طابور قصير.\n\n"
        "📌 <b>الأوامر</b>\n"
        "إعادة البدء: /start\n"
        "تغيير اللغة: /language\n"
        "الغلاف الافتراضي للصوت: /audio_cover\n"
        "إلغاء العملية الحالية: /cancel\n"
        "حالة البوت: /status"),
    "Bot status": "حالة البوت",
    "⚙️ Running: {0} · Queued: {1}": "⚙️ شغّال دلوقتي: {0} • في الطابور: {1}",
    "💾 Free space: ≈{0:.0f} MB": "💾 مساحة فاضية تقريبًا: {0:.0f}MB",
    "📌 Your jobs: {0}": "📌 مهامك الحالية: {0}",
    "📥 Send me a file (PDF, image, Word, PowerPoint, audio or video) and I'll show you the tools.": "📥 ابعتلي أي ملف (PDF / صورة / Word / PowerPoint / صوت / فيديو) وأنا أظهرلك الأدوات.",
    "✅ Language updated.": "✅ تم تغيير اللغة.",
    'This tool is turned off on this server.': 'الأداة دي مقفولة على السيرفر ده.',
    'This change needs re-encoding the video, which is turned off on this server.': 'التغيير ده محتاج إعادة ترميز للفيديو، وده مقفول على السيرفر ده.',
    'This video needs re-encoding to be streamable, which is turned off on this server.': 'الفيديو ده محتاج إعادة ترميز عشان يبقى قابل للبث، وده مقفول على السيرفر ده.',
    'Compress, cut, merge, convert, thumbnail, extract audio, transcribe': 'ضغط، قص، دمج، تحويل، صورة مصغرة، استخراج الصوت، تفريغ نصي',
    'Cut, merge, change format, thumbnail, extract audio, transcribe': 'قص، دمج، تغيير الصيغة، صورة مصغرة، استخراج الصوت، تفريغ نصي',
    '⚙️ Up to {0} MB per file.': '⚙️ الحد الأقصى للملف: {0}MB.',
    '⚙️ Max size: PDF {0} MB | Office {1} MB | Audio {2} MB | Video {3} MB': '⚙️ الحد الأقصى: PDF {0}MB | Office {1}MB | صوت {2}MB | فيديو {3}MB',
    '🎯 Quality': '🎯 جودة أعلى',
    '🗜️ {0}\n{1}\n⚡ <b>Fast:</b> quicker, may lower the fps, slightly less detail\n🎯 <b>Quality:</b> slower, keeps the original fps, best detail\n\n{2}\n\nChoose the mode, then the strength 👇':
    '🗜️ {0}\n{1}\n⚡ <b>سريع:</b> أسرع، ممكن يقلل الفريمات، تفاصيل أقل شوية\n🎯 <b>جودة أعلى:</b> أبطأ، بيحافظ على الفريمات الأصلية وتفاصيل أحسن\n\n{2}\n\nاختار الوضع، وبعدها القوة 👇',
    'up to {0}p': 'حتى {0}p',
    '(original size)': '(نفس مقاس الفيديو)',
    '💾 Size: {0}': '\u200f💾 الحجم: {0}',
    '📑 Pages: {0}': '\u200f📑 عدد الصفحات: {0}',
    '⏱ Duration: {0}': '\u200f⏱ المدة: {0}',
    '📐 Resolution: {0}': '\u200f📐 الدقة: {0}',
    '🖼️ Slides: {0}': '\u200f🖼️ عدد السلايدات: {0}',
    'PDF received successfully!': 'تم استلام الملف بنجاح!',
    'Audio received successfully!': 'تم استلام الصوت بنجاح!',
    'Video received successfully!': 'تم استلام الفيديو بنجاح!',
    '{0} file received successfully!': 'تم استلام ملف {0} بنجاح!',
    '1 image received successfully!': 'تم استلام صورة بنجاح!',
    '{0} images received successfully!': 'تم استلام {0} صور بنجاح!',
    '💾 Size  ·  {0}': '💾 الحجم  ·  {0}',
    '📑 Pages  ·  {0}': '📑 عدد الصفحات  ·  {0}',
    '⏱ Duration  ·  {0}': '⏱ المدة  ·  {0}',
    '🖼️ Slides  ·  {0}': '🖼️ عدد السلايدات  ·  {0}',
    '📐 Resolution  ·  {0}': '📐 الدقة  ·  {0}',
    'ℹ️ <i>Sent as a document. Use 📡 to make it streamable.</i>': 'ℹ️ <i>الملف ده مبعوت كمستند. تقدر تحوله لقابل للبث بزرار 📡.</i>',
    '🎙️ <i>Voice note. You can convert it to MP3, M4A and more.</i>': '🎙️ <i>ريكورد تليجرام. تقدر تحوله لـ MP3 أو M4A أو غيرهم.</i>',
    'Send more to add them, or choose a tool below 👇': 'ابعت صور تانية لو عايز تضيفها، أو اختار أداة من تحت 👇',
    # --- precise video cut on the light host
    'Precise cut': 'جاري القص بدقة',
    'Precise cut {0}/{1}': 'جاري القص بدقة {0}/{1}',
    '\n⏱ On this server, Precise is limited to {0} minutes of kept video.': '\n⏱ على السيرفر ده القص الدقيق متاح لحد {0} دقيقة من الفيديو المحتفظ بيه.',
    'Precise cut on this server is limited to {0} minutes of kept video. Send shorter ranges, or use Fast.': 'القص الدقيق على السيرفر ده محدود بـ {0} دقيقة من الفيديو المحتفظ بيه. ابعت نطاقات أقصر أو استخدم القص السريع.',
    '❌ Precise cut on this server is limited to {0} minutes of kept video (you sent {1}). Send shorter ranges:': '❌ القص الدقيق على السيرفر ده محدود بـ {0} دقيقة من الفيديو المحتفظ بيه (إنت بعت {1}). ابعت نطاقات أقصر:',
    # --- ram / limits
    'Reading pages': 'جاري قراءة الصفحات',
    'One of the images is too large to convert on this server.': 'واحدة من الصور كبيرة جدًا على إمكانيات السيرفر ده.',
    # --- default audio cover
    'Default audio cover': 'الغلاف الافتراضي للصوت',
    '✅ Saved': '✅ محفوظ', '— None yet': '— لسه مفيش',
    '✅ On': '✅ شغّال', '⛔ Off': '⛔ متوقف',
    '🖼️ {0}\n\n📌 <b>Default cover:</b> {1}\n⚙️ <b>Auto-apply in Edit Tags:</b> {2}\n\n<i>It is added automatically when you open Edit Tags on an audio file. Sending another image there replaces it for that file only.\nIt is embedded inside MP3, M4A and FLAC files; other formats show it as the Telegram thumbnail.</i>':
    '🖼️ {0}\n\n📌 <b>الغلاف الافتراضي:</b> {1}\n⚙️ <b>التطبيق التلقائي في تعديل البيانات:</b> {2}\n\n<i>بيتحط تلقائيًا أول ما تفتح تعديل البيانات لأي ملف صوت. لو بعت صورة تانية هناك هتحل مكانه للملف ده بس.\nبيتدمج جوه ملفات MP3 وM4A وFLAC؛ الصيغ التانية بيظهر فيها كصورة مصغرة في تليجرام.</i>',
    '🖼️ Change': '🖼️ تغيير', '🗑️ Delete': '🗑️ حذف',
    '✅ Auto-apply: On': '✅ التطبيق التلقائي: شغّال', '⛔ Auto-apply: Off': '⛔ التطبيق التلقائي: متوقف',
    '🖼️ Set Default Cover': '🖼️ تعيين غلاف افتراضي',
    '🖼️ Send the image to use as your default audio cover (or /cancel):': '🖼️ ابعت الصورة اللي عايزها كغلاف افتراضي للصوت (أو /cancel):',
    '🗑️ Default cover deleted.': '🗑️ تم حذف الغلاف الافتراضي.',
    '✅ Default cover saved.': '✅ تم حفظ الغلاف الافتراضي.',
    '✅ Default cover': '✅ الغلاف الافتراضي',
    # --- merge audio / video
    '📦 Total: {0}': '📦 الإجمالي: {0}',
    '↩️ Remove Last': '↩️ حذف آخر ملف',
    '❌ Send {0} files only (or tap Cancel).': '❌ ابعت ملفات {0} بس (أو اضغط إلغاء).',
    '⚠️ Maximum reached ({0} files).': '⚠️ وصلت للحد الأقصى ({0} ملفات).',
    '❌ The total size is too large to merge on this server. Remove a file or send smaller ones.': '❌ الحجم الإجمالي كبير على السيرفر ده. شيل ملف أو ابعت ملفات أصغر.',
    'Send at least one more file to merge.': 'ابعت ملف إضافي واحد على الأقل للدمج.',
    '❌ Send at least one more file to merge.': '❌ ابعت ملف إضافي واحد على الأقل للدمج.',
    'Downloading {0}/{1}': 'جاري التحميل {0}/{1}',
    'Download failed. Please try again.': 'فشل التحميل. حاول تاني.',
    '<code>{0}</code> has no audio.': '<code>{0}</code> مفيهوش صوت.',
    '<code>{0}</code> has no video.': '<code>{0}</code> مفيهوش فيديو.',
    'These videos have different formats ({0}), so merging needs re-encoding. On this server that is limited to {1} minutes in total.': 'الفيديوهات دي صيغها مختلفة ({0})، فالدمج محتاج إعادة ترميز، وده على السيرفر ده محدود بـ {1} دقيقة إجمالي.',
    'Merge failed. The files may be corrupted or in an unsupported format.': 'فشل الدمج. الملفات ممكن تكون تالفة أو صيغتها غير مدعومة.',
    'Files merged': 'تم دمج الملفات',
    '\n⚡ Joined without re-encoding.': '\n⚡ اتدمجت من غير إعادة ترميز.',
    '\n🔄 Re-encoded so all parts match.': '\n🔄 اتعمل لها إعادة ترميز عشان كل الأجزاء تتطابق.',
    # --- transcription and summary
    '📝 Transcribe': '📝 تفريغ نصي',
    'Transcription is not available on this server (no Gemini key).': 'التفريغ مش متاح على السيرفر ده (مفيش Gemini key).',
    '❌ Transcription is not available on this server (no Gemini key).': '❌ التفريغ مش متاح على السيرفر ده (مفيش Gemini key).',
    'Preparing audio': 'جاري تجهيز الصوت',
    'This file has no audio track I can read.': 'الملف ده مفيهوش صوت أقدر أقراه.',
    'This recording is too large to send as is. Lower <code>TRANSCRIBE_CHUNK_MIN</code> or <code>TRANSCRIBE_COMPRESS_ABOVE_MB</code>.': 'التسجيل ده كبير جدًا للإرسال زي ما هو. قلّل <code>TRANSCRIBE_CHUNK_MIN</code> أو <code>TRANSCRIBE_COMPRESS_ABOVE_MB</code>.',
    'Transcribing': 'جاري التفريغ',
    'Transcription failed. Please try again later.': 'فشل التفريغ. حاول تاني بعد شوية.',
    '\n⚠️ {0} part(s) could not be transcribed and are marked in the text.': '\n⚠️ {0} جزء/أجزاء مقدرتش أفرّغها واتعلّمت في النص.',
    '📝 {0}\n\n⏱ {1} · 📝 {2} words{3}\n\nHow do you want to receive it?': '📝 {0}\n\n⏱ {1} · 📝 {2} كلمة{3}\n\nتحب تستلم التفريغ إزاي؟',
    'Transcript ready': 'التفريغ جاهز',
    '📄 TXT File': '📄 ملف TXT', '💬 In Chat': '💬 نص في الشات',
    '⚠️ This transcript is too long for chat ({0} messages), so it is sent as a file instead.': '⚠️ التفريغ طويل جدًا للشات ({0} رسالة)، فهتبعتهولك كملف.',
    '✅ Delivered.\n\nWant a summary of the transcript?': '✅ تم الإرسال.\n\nتحب ألخّصلك التفريغ؟',
    '🧠 Summarize': '🧠 تلخيص التفريغ',
    'The transcript is no longer available. Please transcribe again.': 'التفريغ مبقاش متاح. اعمل تفريغ تاني.',
    'Summarizing': 'جاري التلخيص',
    '❌ Summarizing failed. Please try again.': '❌ فشل التلخيص. حاول تاني.',
    'Try the summary again?': 'تحب نجرّب التلخيص تاني؟',
    'Summary ready': 'التلخيص جاهز',
}


# ══════════════════════════ Startup ══════════════════════════
async def _awaiting_text(_, __, m):
    s = sessions.get(m.from_user.id) if m.from_user else None
    return bool(s and s.state in TEXT_STATES)


async def main():
    global app, QUICK_SEM, CLOUD_SEM, COVER_BY_ID
    if not (API_ID and API_HASH and BOT_TOKEN):
        raise SystemExit("Set API_ID, API_HASH and BOT_TOKEN in the environment variables.")
    if not FFMPEG:
        logger.warning("ffmpeg not found: audio/video features are disabled. Install it or add imageio-ffmpeg.")
    _reap_stopped_ffmpeg()
    logger.info("Light host mode: %s", "ON (heavy tools hidden and blocked)" if LIGHT_HOST else "off")
    shutil.rmtree(WORK_DIR, ignore_errors=True)       # clean leftovers from a previous run
    (WORK_DIR / "s").mkdir(parents=True, exist_ok=True)

    app = Client("quickfile", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
                 workdir=str(BASE_DIR), parse_mode=ParseMode.HTML)

    awaiting = filters.create(_awaiting_text)
    media_filter = filters.private & (filters.document | filters.photo | filters.video | filters.audio |
                                      filters.voice | filters.video_note | filters.animation)
    app.add_handler(MessageHandler(cmd_start, filters.private & filters.command("start")))
    app.add_handler(MessageHandler(cmd_help, filters.private & filters.command("help")))
    app.add_handler(MessageHandler(cmd_language, filters.private & filters.command("language")))
    app.add_handler(MessageHandler(cmd_cancel, filters.private & filters.command("cancel")))
    app.add_handler(MessageHandler(cmd_audio_cover, filters.private & filters.command("audio_cover")))
    app.add_handler(MessageHandler(cmd_status, filters.private & filters.command("status")))
    app.add_handler(MessageHandler(on_text_state, filters.private & filters.text & awaiting))
    app.add_handler(MessageHandler(on_media, media_filter))
    app.add_handler(MessageHandler(on_fallback_text, filters.private & filters.text))
    app.add_handler(CallbackQueryHandler(on_callback))

    await app.start()
    COVER_BY_ID = detect_cover_support()
    try:
        await app.set_bot_commands([BotCommand(*c) for c in CMDS_EN])
        await app.set_bot_commands([BotCommand(*c) for c in CMDS_AR], language_code="ar")
    except Exception as e:
        logger.warning("Could not set bot commands: %s", e)
    _spawn(sync_chat_commands())
    logger.info("QuickFile started (thumbnail without re-upload: %s)",
                "on" if COVER_BY_ID else "off, needs pyrofork")
    if GROUP_ID:
        try:
            chat = await app.get_chat(GROUP_ID)
            logger.info("Archive connected: %s", getattr(chat, "title", GROUP_ID))
            if getattr(chat, "is_forum", True) is False:
                logger.warning("Archive group has no Topics enabled, so archiving won't work.")
        except Exception as e:
            logger.warning("Can't reach the archive group (%s). Check ARCHIVE_GROUP_ID, make the bot admin with "
                           "Manage Topics, send any message in the group, then restart.", e)
    QUICK_SEM = asyncio.Semaphore(QUICK_SLOTS)
    CLOUD_SEM = asyncio.Semaphore(max(1, CLOUD_SLOTS))
    tasks = [asyncio.create_task(cleaner_loop())]
    try:
        await idle()
    finally:
        for t in tasks:
            t.cancel()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
