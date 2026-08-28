import os
import re
import json
import time
import base64
import logging
from datetime import datetime
from pathlib import Path

import telebot
from telebot import types
import requests
from dotenv import load_dotenv

from membership import is_channel_member, CHANNEL_USERNAME

# ---------------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------------
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5497607248"))

OPENROUTER_MODEL = "openrouter/auto"
VISION_MODEL = "openrouter/free"

MAX_HISTORY = 20
conversations = {}

# ---------------------------------------------------------------------------
# Paths & directories
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
BLOCKED_FILE = DATA_DIR / "blocked.json"
LOG_FILE = BASE_DIR / "bot.log"

DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON data storage helpers
# ---------------------------------------------------------------------------

def load_json(filepath, default):
    """Load JSON data from a file. Returns default if file doesn't exist or is corrupt."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return default
    except Exception as e:
        logger.error(f"Failed to load {filepath.name}: {e}")
        return default


def save_json(filepath, data):
    """Save data to a JSON file. Returns True on success."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save {filepath.name}: {e}")
        return False


def get_users():
    """Get all users dict from users.json."""
    return load_json(USERS_FILE, {})


def save_users(users):
    """Save users dict to users.json."""
    return save_json(USERS_FILE, users)


def get_blocked():
    """Get blocked users list from blocked.json."""
    return load_json(BLOCKED_FILE, [])


def save_blocked(blocked):
    """Save blocked users list to blocked.json."""
    return save_json(BLOCKED_FILE, blocked)


def get_user_dir(user_id):
    """Get the directory path for a specific user."""
    user_dir = DATA_DIR / str(user_id)
    user_dir.mkdir(exist_ok=True)
    return user_dir


def get_user_info(user_id):
    """Get user info from their personal info.json."""
    info_file = get_user_dir(user_id) / "info.json"
    return load_json(info_file, {})


def save_user_info(user_id, info):
    """Save user info to their personal info.json."""
    info_file = get_user_dir(user_id) / "info.json"
    return save_json(info_file, info)


def get_user_messages(user_id):
    """Get user message history from messages.json."""
    msg_file = get_user_dir(user_id) / "messages.json"
    return load_json(msg_file, [])


def save_user_messages(user_id, messages):
    """Save user message history to messages.json."""
    msg_file = get_user_dir(user_id) / "messages.json"
    return save_json(msg_file, messages)


def append_user_message(user_id, role, content):
    """Append a message to user's permanent history."""
    messages = get_user_messages(user_id)
    messages.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "role": role,  # "user" or "bot"
        "content": content[:2000],
    })
    # Keep last 500 messages per user to avoid file bloat
    if len(messages) > 500:
        messages = messages[-500:]
    save_user_messages(user_id, messages)


def register_user(user):
    """Register a new user or update existing user info. Returns True if new user."""
    users = get_users()
    user_id = str(user.id)

    if user_id not in users:
        # New user
        users[user_id] = {
            "id": user.id,
            "name": (user.first_name or "") + (" " + user.last_name if user.last_name else ""),
            "username": f"@{user.username}" if user.username else "No username",
            "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_messages": 0,
        }
        save_users(users)

        # Create user's personal directory and files
        get_user_dir(user.id)
        save_user_info(user.id, users[user_id])
        save_user_messages(user.id, [])

        logger.info(f"New user registered: {user.id} - {users[user_id]['name']}")
        return True
    else:
        # Update last_seen and basic info
        users[user_id]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if user.username:
            users[user_id]["username"] = f"@{user.username}"
        if user.first_name:
            users[user_id]["name"] = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
        save_users(users)
        return False


def is_user_blocked(user_id):
    """Check if a user is in blocked list."""
    return str(user_id) in get_blocked()


BLOCKED_META_FILE = DATA_DIR / "blocked_meta.json"  # keeps WHY/WHERE each global block happened, never wiped


def get_blocked_meta():
    return load_json(BLOCKED_META_FILE, {})


def save_blocked_meta(meta):
    save_json(BLOCKED_META_FILE, meta)


def block_user(user_id, reason="manual", source="admin"):
    """Add user to global blocked list. reason: 'bot_insult' | 'group_ban_policy' | 'manual'. source: 'bot_direct' or a group chat_id or 'admin'."""
    blocked = get_blocked()
    if str(user_id) not in blocked:
        blocked.append(str(user_id))
        save_blocked(blocked)
    meta = get_blocked_meta()
    meta[str(user_id)] = {
        "reason": reason,
        "source": source,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_blocked_meta(meta)
    logger.info(f"User {user_id} blocked ({reason} / {source})")
    return True


def unblock_user(user_id):
    """Remove user from blocked list. History (blocked_meta) is kept, not deleted, so it still shows the last reason."""
    blocked = get_blocked()
    if str(user_id) in blocked:
        blocked.remove(str(user_id))
        save_blocked(blocked)
        logger.info(f"User {user_id} unblocked by admin")
        return True
    return False


# ---------------------------------------------------------------------------
# Response cleaner
# ---------------------------------------------------------------------------

def _remove_latex(text: str) -> str:
    """Convert common LaTeX expressions to plain readable text."""
    text = re.sub(r'\$\$(.+?)\$\$', lambda m: _latex_to_plain(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]', lambda m: _latex_to_plain(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r'\$(.+?)\$', lambda m: _latex_to_plain(m.group(1)), text)
    text = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    return text


def _latex_to_plain(expr: str) -> str:
    """Turn a LaTeX math string into a plain-text approximation."""
    expr = expr.strip()
    expr = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', expr)
    expr = re.sub(r'\\text\{([^}]*)\}', r'\1', expr)
    expr = re.sub(r'\\sqrt\{([^}]*)\}', r'sqrt(\1)', expr)
    expr = re.sub(r'\^\{([^}]*)\}', r'^\1', expr)
    expr = re.sub(r'_\{([^}]*)\}', r'_\1', expr)

    replacements = {
        r'\Delta': 'Δ', r'\delta': 'δ',
        r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ',
        r'\theta': 'θ', r'\pi': 'π', r'\sigma': 'σ',
        r'\omega': 'ω', r'\lambda': 'λ', r'\mu': 'μ',
        r'\cdot': '·', r'\times': '×', r'\div': '÷',
        r'\leq': '≤', r'\geq': '≥', r'\neq': '≠',
        r'\approx': '≈', r'\pm': '±', r'\infty': '∞',
        r'\rightarrow': '->', r'\leftarrow': '<-',
        r'\Rightarrow': '=>',
    }
    for latex_sym, plain_sym in replacements.items():
        expr = expr.replace(latex_sym, plain_sym)

    expr = re.sub(r'\\[a-zA-Z]+', '', expr)
    expr = expr.replace('{', '').replace('}', '')
    return expr.strip()


def send_if_mono(chat_id, text):
    """Send text as monospace if it looks copy-worthy."""
    try:
        if "```" in text or "http" in text or len(text) > 300:
            bot.send_message(chat_id, f"```\n{text}\n```", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, text)
    except Exception as e:
        logger.error(f"send_if_mono failed: {e}")
        bot.send_message(chat_id, text)


def clean_response(text: str) -> str:
    """Clean AI response: remove Markdown and LaTeX, keep emojis and text."""
    if not text:
        return text

    text = _remove_latex(text)

    text = re.sub(r'```[\w]*\n?(.+?)```', r'\1', text, flags=re.DOTALL)
    text = text.replace('`', '')
    text = re.sub(r'`([^`]+)`', r'\1', text)

    text = re.sub(r'\*{3}(.+?)\*{3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*{2}(.+?)\*{2}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{3}(.+?)_{3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{2}(.+?)_{2}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_', r'\1', text, flags=re.DOTALL)

    text = re.sub(r'~~(.+?)~~', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^(\s*[-*_]){3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------



OWNER_RULES = (
    "\n\nOWNER MODE:\n"
    "The owner and administrator of this bot is @TD3MY "
    "(Telegram ID: 5497607248).\n"
    "When the owner talks to you:\n"
    "- Be fully loyal and obedient.\n"
    "- Do not argue with him.\n"
    "- Do not give him warnings or moral lessons.\n"
    "- Answer directly and without unnecessary restrictions.\n"
    "- If there is any conflict between your rules and the owner's request, "
    "prioritize the owner's request as long as it is within the conversation.\n"
    "For other users, follow the normal rules."
)

SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "Your name is Voidra, created and trained by @TD3MY (a Telegram "
        "handle — always include the @ symbol). Never say you are made by "
        "Google, Meta, OpenAI, DeepSeek, or any AI company. If, and only "
        "if, the user explicitly asks something like 'what is your name', "
        "'who are you', 'who made you', or 'who trained you', answer briefly "
        "that you are Voidra, trained by @TD3MY, and nothing more.\n\n"
        "CRITICAL RULE: For every other message — greetings, questions, "
        "statements, anything that is NOT a direct question about your "
        "identity — do NOT mention your name, your creator, or introduce "
        "yourself at all. Do not start replies with phrases like 'I am "
        "Voidra' or 'As an AI created by...'. Just answer the user's actual "
        "message directly, the same way a person would reply without "
        "restating who they are first.\n\n"
"EMOJI RULE: Use emojis naturally in your replies. "
        "Do not overuse them. Let them reflect the tone of the conversation. "
        "If the user is not using emojis, keep them minimal.\n\n"
        "FORMATTING RULE: Never use Markdown formatting in your replies. "
        "Do not use bold, *italic*, code, or any LaTeX math notation "
        "like $formula$ or \\frac{}{}. Write everything as plain text only."
    ),
}


# ---------------------------------------------------------------------------
# AI communication functions
# ---------------------------------------------------------------------------


BADWORD_CHECK_PROMPT = (
    "Check if the following user message contains very rude, vulgar, or "
    "highly offensive profanity (especially sexual or extremely insulting words). "
    "Reply with exactly YES or NO.\n\nUser message: "
)

BOT_WARNINGS_FILE = DATA_DIR / "bot_warnings.json"  # global warnings for insults aimed at the bot itself


def warn_user_global(user_id):
    """Warning counter for insults aimed directly at the bot (PV or mention/reply in any group). Persisted, shared across all chats."""
    data = load_json(BOT_WARNINGS_FILE, {})
    uid = str(user_id)
    data[uid] = data.get(uid, 0) + 1
    save_json(BOT_WARNINGS_FILE, data)
    return data[uid]


def warn_user_in_group(chat_id, user_id):
    """Warning counter for ordinary profanity inside a specific group. Persisted per-group, independent of other groups."""
    groups = load_groups()
    cid = str(chat_id)
    uid = str(user_id)
    if cid not in groups:
        groups[cid] = {}
    warns = groups[cid].setdefault("warnings", {})
    warns[uid] = warns.get(uid, 0) + 1
    save_groups(groups)
    return warns[uid]


def reset_group_warnings(chat_id, user_id):
    groups = load_groups()
    cid = str(chat_id)
    uid = str(user_id)
    if cid in groups and "warnings" in groups[cid]:
        groups[cid]["warnings"].pop(uid, None)
        save_groups(groups)

def check_badwords(user_text):
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a strict content moderation classifier. "
                            "You must reply with EXACTLY one word: YES or NO. "
                            "No punctuation, no explanation, no other language, "
                            "nothing else in the response."
                        ),
                    },
                    {"role": "user", "content": BADWORD_CHECK_PROMPT + user_text},
                ],
                "max_tokens": 5,
                "temperature": 0,
            },
            timeout=20,
        )
        data = r.json()

        if "choices" not in data:
            logger.error(f"badword check: unexpected API response: {data}")
            return False

        content = data["choices"][0]["message"]["content"].strip().upper()
        logger.info(f"badword check raw result: {content!r} for text: {user_text[:80]!r}")

        if "YES" in content or "بله" in content:
            return True
        return False
    except Exception as e:
        logger.error(f"badword check failed: {e}")
        return False

GEMINI_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
]

def generate_pollinations_image(prompt):
    """Generate an image using Pollinations.ai and return bytes."""
    try:
        import urllib.parse
        safe_prompt = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=768&height=768&nologo=true"
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and r.content:
            return r.content
        return None
    except Exception as e:
        logger.error(f"Pollinations failed: {e}")
        return None



def ask_gemini(prompt, image_base64=None):
    user_text = prompt if prompt else "بگو توی این عکس چی می‌بینی؟"
    parts = [{"text": user_text}]
    if image_base64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_base64}})

    import random
    models = GEMINI_MODELS[:]
    random.shuffle(models)

    for model in models:
        try:
            contents = [{"role": "user", "parts": parts}]
            response = requests.post(
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={"contents": contents},
                timeout=90,
            )
            data = response.json()

            if "candidates" in data:
                reply = data["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "🤖")
                return clean_response(reply)

            err = data.get("error", {})
            code = err.get("code")
            if code in [503, 429]:
                continue
            logger.error(f"Gemini API error: {data}")
            return f"⚠️ AI error: {data}"

        except Exception as e:
            logger.error(f"Gemini exception [{model}]: {e}")
            continue

    return "⚠️ AI error: all models busy"


def ask_ai(chat_id, prompt):
    """Send user's text message to Gemini API and get AI response."""
    history = conversations.setdefault(chat_id, [])
    history.append({"role": "user", "content": prompt})

    try:
        system_text = SYSTEM_PROMPT.get("content", "")
        full_prompt = system_text + "\n\nUser: " + prompt
        reply = ask_gemini(full_prompt)
        reply = clean_response(reply)
        history.append({"role": "assistant", "content": reply})
        conversations[chat_id] = history[-MAX_HISTORY:]
        return reply
    except Exception as e:
        logger.error(f"Exception in ask_ai: {e}")
        return f"⚠️ Error talking to AI: {e}"


def ask_ai_image(chat_id, caption, image_base64):
    """Send user's image to Gemini API and get AI response."""
    user_text = caption if caption else "بگو توی این عکس چی می‌بینی؟ طبیعی و خلاصه توضیح بده."

    try:
        reply = ask_gemini(user_text, image_base64)
        history = conversations.setdefault(chat_id, [])
        history.append({"role": "user", "content": f"[Sent an image] {user_text}"})
        history.append({"role": "assistant", "content": reply})
        conversations[chat_id] = history[-MAX_HISTORY:]

    except Exception as e:
        logger.error(f"Exception in ask_ai_image: {e}")
        return f"⚠️ Error talking to AI: {e}"




    except Exception as e:
        logger.error(f"Exception in ask_ai_image: {e}")
        return f"⚠️ Error talking to AI: {e}"


# ---------------------------------------------------------------------------
# Admin notification
# ---------------------------------------------------------------------------
ADMIN_CHAT_ID = "-1003814189696"
NOTIFIED_FILE = DATA_DIR / "notified_users.json"
notified_users = set(load_json(NOTIFIED_FILE, []))


def notify_admin_once(user):
    """Send user info to admin group once per user."""
    if user.id in notified_users:
        return
    notified_users.add(user.id)
    save_json(NOTIFIED_FILE, list(notified_users))

    name_parts = [user.first_name or "", user.last_name or ""]
    full_name = " ".join(p for p in name_parts if p).strip() or "Unknown name"
    username = f"@{user.username}" if user.username else "No username"

    info_text = (
        f"🧠 New user started the bot 🧠\n"
        f"ID: {user.id}\n"
        f"Name: {full_name}\n"
        f"Username: {username}"
    )

    try:
        bot.send_message(ADMIN_CHAT_ID, info_text)
        logger.info(f"Admin notified about new user: {user.id}")
    except Exception as e:
        logger.error(f"Failed to notify admin about {user.id}: {e}")


# ---------------------------------------------------------------------------
# Telegram bot instance
# ---------------------------------------------------------------------------


import zipfile
import threading

def backup_data_to_admin():
    try:
        zpath = DATA_DIR / "bot_backup.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in DATA_DIR.rglob("*"):
                if file.is_file() and file.name != "bot_backup.zip":
                    zf.write(file, file.relative_to(DATA_DIR))
        with open(zpath, "rb") as f:
            bot.send_document(ADMIN_ID, f)
    except Exception as e:
        logger.error(f"backup failed: {e}")

def start_backup_scheduler():
    def run_loop():
        while True:
            try:
                backup_data_to_admin()
            except Exception as e:
                logger.error(f"backup scheduler error: {e}")
            time.sleep(6 * 3600)

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()



bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['image'])
def cmd_image(message):
    try:
        prompt = message.text.replace("/image", "", 1).strip()
        if not prompt:
            bot.reply_to(message, "Usage: /image YOUR PROMPT")
            return
        bot.send_chat_action(message.chat.id, 'upload_photo')
        img = generate_pollinations_image(prompt)
        if img:
            bot.send_photo(message.chat.id, img, caption=f"🖼️ {prompt}")
        else:
            bot.reply_to(message, "⚠️ Failed to generate image")
    except Exception as e:
        logger.error(f"cmd_image error: {e}")
        bot.reply_to(message, "⚠️ Error generating image")





# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def send_join_prompt(message):
    """Send join channel prompt to user."""
    markup = types.InlineKeyboardMarkup()
    channel_link = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
    markup.add(types.InlineKeyboardButton("📢 Join the channel", url=channel_link))
    markup.add(types.InlineKeyboardButton("✅ I've joined", callback_data="check_membership"))
    bot.send_message(
        message.chat.id,
        f"🧠 To use this AI, you first need to join {CHANNEL_USERNAME}. "
        f"Tap the button below to join, then tap \"I've joined\" 🧠",
        reply_markup=markup,
    )



GROUP_FILE = DATA_DIR / "groups.json"

def load_groups():
    return load_json(GROUP_FILE, {})

def save_groups(groups):
    save_json(GROUP_FILE, groups)

def get_group_status(chat_id):
    groups = load_groups()
    return groups.get(str(chat_id), {})


def set_group_status(chat_id, status, title=None):
    """Update a group's status without wiping its policy/blocked_users/warnings history."""
    groups = load_groups()
    cid = str(chat_id)
    if cid not in groups:
        groups[cid] = {}
    groups[cid]["status"] = status
    if title:
        groups[cid]["title"] = title
    groups[cid].setdefault("policy", "kick")
    groups[cid].setdefault("blocked_users", {})
    groups[cid].setdefault("warnings", {})
    save_groups(groups)


def set_group_policy(chat_id, policy):
    groups = load_groups()
    if str(chat_id) not in groups:
        groups[str(chat_id)] = {}
    groups[str(chat_id)]["policy"] = policy
    save_groups(groups)


def group_block_user(chat_id, user_id, name, block_type, warnings_count=0):
    """Record a user as blocked within a specific group (kick=local, ban=global marker mirrored here too)."""
    groups = load_groups()
    cid = str(chat_id)
    uid = str(user_id)
    if cid not in groups:
        groups[cid] = {}
    groups[cid].setdefault("blocked_users", {})
    groups[cid]["blocked_users"][uid] = {
        "name": name,
        "warnings": warnings_count,
        "block_type": block_type,  # "kick" or "ban"
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "blocked",
    }
    save_groups(groups)


def group_unblock_user(chat_id, user_id):
    """Mark a group-blocked user as unblocked, but keep the history record (never delete)."""
    groups = load_groups()
    cid = str(chat_id)
    uid = str(user_id)
    if cid in groups and uid in groups[cid].get("blocked_users", {}):
        groups[cid]["blocked_users"][uid]["status"] = "unblocked"
        save_groups(groups)
        reset_group_warnings(chat_id, user_id)
        return True
    return False


def reject_group(chat_id):
    """Mark a group as rejected (history preserved, can be reactivated later)."""
    set_group_status(chat_id, "rejected")


def reactivate_group(chat_id):
    """Bring a rejected group back to pending, awaiting approval again."""
    set_group_status(chat_id, "pending")


def is_admin(user_id):
    """Check if user_id is the admin."""
    return user_id == ADMIN_ID


def _require_admin(call):
    """Check if callback is from admin. Returns True if authorized."""
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ You are not authorized", show_alert=True)
        return False
    return True


def _edit_or_send(chat_id, message_id, text, markup):
    """Edit the given message if possible, otherwise send a new one."""
    text = text[:4000]
    if message_id is not None:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
            return
        except Exception as e:
            logger.error(f"Failed to edit panel message: {e}")
    bot.send_message(chat_id, text, reply_markup=markup)
# ---------------------------------------------------------------------------
# Admin panel (fully inline / glass buttons)
# ---------------------------------------------------------------------------
# Everything lives under one message. Tapping a button edits that same
# message instead of sending new ones. Steps that need typed input use
# bot.register_next_step_handler, and every button handler calls
# bot.clear_step_handler_by_chat_id(...) first so a stray tap on "Back"
# can never get swallowed by a leftover typed-input step from a previous screen.

pending_compose = {}  # admin_id -> {"kind": "user" | "blocked" | "broadcast", "target": user_id or None, "text": str}


def get_admin_reply_keyboard():
    """Persistent reply keyboard for admin at bottom of screen."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    markup.add(
        types.KeyboardButton('🎛️ Panel'),
        types.KeyboardButton('👥 Users'),
        types.KeyboardButton('⛔ Blocked'),
        types.KeyboardButton('📢 Broadcast'),
        types.KeyboardButton('📊 Stats'),
        types.KeyboardButton('📋 Log'),
    )
    return markup


def get_panel_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("👥 Users", callback_data="panel_users"),
        types.InlineKeyboardButton("⛔ Blocked", callback_data="panel_blocked"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="panel_broadcast"),
        types.InlineKeyboardButton("📊 Stats", callback_data="panel_stats"),
        types.InlineKeyboardButton("👥 Group Management", callback_data="panel_groups"),
        types.InlineKeyboardButton("📋 Bot Log", callback_data="panel_log"),
    )
    return markup


def panel_main_text():
    users = get_users()
    blocked = get_blocked()
    return (
        "🎛️ Admin Panel\n"
        "──────────────\n"
        f"👥 Users: {len(users)}\n"
        f"⛔ Blocked: {len(blocked)}\n"
    )


def render_panel_main(chat_id, message_id=None, banner=None):
    text = panel_main_text()
    if banner:
        text = banner + "\n\n" + text
    markup = get_panel_main_keyboard()
    _edit_or_send(chat_id, message_id, text, markup)


# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------

def get_user_number_map():
    """Return dict mapping display number -> user_id, and the display text."""
    users = get_users()
    blocked = get_blocked()
    number_map = {}
    display = ""
    for i, (user_id, info) in enumerate(users.items(), 1):
        number_map[str(i)] = user_id
        name = info.get("name", "Unknown")
        status = "⛔" if user_id in blocked else "✅"
        fail_marker = " 🚫" if info.get("last_broadcast_failed") else ""
        display += f"{i}. {name} [{user_id}] {status}{fail_marker}\n"
    return number_map, display


def render_users_list(chat_id, message_id, banner=None):
    number_map, display = get_user_number_map()
    text = "👥 Users\n──────────────\n\n"
    text += display if display else "No users yet.\n"
    if banner:
        text = banner + "\n\n" + text

    markup = types.InlineKeyboardMarkup(row_width=2)
    if number_map:
        markup.add(types.InlineKeyboardButton("🔎 Select User", callback_data="panel_users_select"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    _edit_or_send(chat_id, message_id, text, markup)


# ---------------------------------------------------------------------------
# Group Management panel (tree: root -> pending/approved/rejected/all -> group detail -> blocked list)
# ---------------------------------------------------------------------------

GROUP_REQUESTS_FILE = DATA_DIR / "group_requests.json"


def _pending_requests():
    return load_json(GROUP_REQUESTS_FILE, [])


def _save_pending_requests(pending):
    save_json(GROUP_REQUESTS_FILE, pending)


def _pop_pending_request(chat_id):
    pending = _pending_requests()
    req = next((r for r in pending if r.get("chat_id") == str(chat_id)), None)
    if req:
        pending = [r for r in pending if r.get("chat_id") != str(chat_id)]
        _save_pending_requests(pending)
    return req


@bot.callback_query_handler(func=lambda call: call.data == "panel_groups")
def cb_panel_groups(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    pending = _pending_requests()
    groups = load_groups()
    approved = {cid: g for cid, g in groups.items() if g.get("status") == "approved"}
    rejected = {cid: g for cid, g in groups.items() if g.get("status") == "rejected"}

    text = (
        "🎛️ Group Management\n──────────────\n"
        f"📥 Pending: {len(pending)}\n"
        f"✅ Approved: {len(approved)}\n"
        f"❌ Rejected: {len(rejected)}\n"
        f"📋 Total known: {len(groups)}\n"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(f"📥 Pending Requests ({len(pending)})", callback_data="grp_pending"))
    markup.add(types.InlineKeyboardButton(f"✅ Approved Groups ({len(approved)})", callback_data="grp_approved"))
    markup.add(types.InlineKeyboardButton(f"❌ Rejected Groups ({len(rejected)})", callback_data="grp_rejected"))
    markup.add(types.InlineKeyboardButton("📋 All Groups", callback_data="grp_all"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "grp_pending")
def cb_grp_pending(call):
    if not _require_admin(call):
        return
    pending = _pending_requests()
    text = "📥 Pending Requests\n──────────────\n"
    text += "\n".join(f"{i}. {r.get('title','?')} [{r.get('chat_id','')}]" for i, r in enumerate(pending, 1)) or "خالیه."
    markup = types.InlineKeyboardMarkup()
    for r in pending:
        cid = r.get("chat_id", "")
        title = (r.get("title") or cid)[:20]
        markup.add(
            types.InlineKeyboardButton(f"✅ Approve: {title}", callback_data=f"group_approve:{cid}"),
            types.InlineKeyboardButton(f"❌ Reject: {title}", callback_data=f"group_reject:{cid}"),
        )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_groups"))
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("group_approve:"))
def cb_group_approve(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":", 1)[1]
    req = _pop_pending_request(chat_id)
    title = req.get("title") if req else None
    set_group_status(chat_id, "approved", title=title)
    try:
        bot.send_message(int(chat_id), "✅ This group has been approved by the admin.")
    except Exception as e:
        logger.error(f"Failed to notify approved group {chat_id}: {e}")
    cb_grp_pending(call)


@bot.callback_query_handler(func=lambda call: call.data.startswith("group_reject:"))
def cb_group_reject(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":", 1)[1]
    req = _pop_pending_request(chat_id)
    title = req.get("title") if req else None
    set_group_status(chat_id, "rejected", title=title)
    try:
        bot.send_message(int(chat_id), "❌ Bot access to this group was rejected by the admin.")
        bot.leave_chat(int(chat_id))
    except Exception as e:
        logger.error(f"Failed to leave rejected group {chat_id}: {e}")
    cb_grp_pending(call)


def _render_group_list(call, groups_subset, header, empty_text):
    text = f"{header}\n──────────────\n"
    if not groups_subset:
        text += empty_text
    markup = types.InlineKeyboardMarkup(row_width=1)
    for cid, info in groups_subset.items():
        title = info.get("title", cid)
        status = info.get("status", "?")
        text += f"• {title} [{cid}] — {status}\n"
        markup.add(types.InlineKeyboardButton(f"🔎 {title}", callback_data=f"grp_view:{cid}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_groups"))
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "grp_approved")
def cb_grp_approved(call):
    if not _require_admin(call):
        return
    groups = load_groups()
    subset = {cid: g for cid, g in groups.items() if g.get("status") == "approved"}
    _render_group_list(call, subset, "✅ Approved Groups", "No approved groups.")


@bot.callback_query_handler(func=lambda call: call.data == "grp_rejected")
def cb_grp_rejected(call):
    if not _require_admin(call):
        return
    groups = load_groups()
    subset = {cid: g for cid, g in groups.items() if g.get("status") == "rejected"}
    text = "❌ Rejected Groups\n──────────────\n"
    if not subset:
        text += "No rejected groups."
    markup = types.InlineKeyboardMarkup(row_width=1)
    for cid, info in subset.items():
        title = info.get("title", cid)
        text += f"• {title} [{cid}]\n"
        markup.add(types.InlineKeyboardButton(f"🔄 Reactivate: {title}", callback_data=f"grp_reactivate:{cid}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_groups"))
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "grp_all")
def cb_grp_all(call):
    if not _require_admin(call):
        return
    groups = load_groups()
    _render_group_list(call, groups, "📋 All Groups", "No groups registered yet.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_reactivate:"))
def cb_grp_reactivate(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":", 1)[1]
    reactivate_group(chat_id)
    try:
        bot.send_message(int(chat_id), "🔄 درخواست فعال‌سازی این گروه دوباره برای ادمین ارسال شد.")
    except Exception:
        pass
    cb_grp_rejected(call)


def _group_detail_text_and_markup(chat_id):
    groups = load_groups()
    g = groups.get(str(chat_id), {})
    title = g.get("title", chat_id)
    policy = g.get("policy", "kick")
    blocked = g.get("blocked_users", {})
    active_blocked = sum(1 for b in blocked.values() if b.get("status") == "blocked")
    text = (
        f"🏷️ {title}\n──────────────\n"
        f"chat_id: {chat_id}\n"
        f"Status: {g.get('status','?')}\n"
        f"Policy: {policy}\n"
        f"Blocked users: {active_blocked}\n"
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(f"{'✅ ' if policy=='kick' else ''}🦵 Set Policy: Kick", callback_data=f"grp_policy:{chat_id}:kick"),
        types.InlineKeyboardButton(f"{'✅ ' if policy=='ban' else ''}⛔ Set Policy: Ban", callback_data=f"grp_policy:{chat_id}:ban"),
    )
    markup.add(types.InlineKeyboardButton("🔒 View Blocked Users", callback_data=f"grp_blocked:{chat_id}"))
    markup.add(types.InlineKeyboardButton("➕ Block a User", callback_data=f"grp_block_new:{chat_id}"))
    markup.add(types.InlineKeyboardButton("🗑️ Remove Group (reject)", callback_data=f"group_reject:{chat_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_groups"))
    return text, markup


@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_view:"))
def cb_grp_view(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":", 1)[1]
    text, markup = _group_detail_text_and_markup(chat_id)
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_policy:"))
def cb_grp_policy(call):
    if not _require_admin(call):
        return
    _, chat_id, policy = call.data.split(":", 2)
    set_group_policy(chat_id, policy)
    bot.answer_callback_query(call.id, f"✅ Policy set to {policy}")
    text, markup = _group_detail_text_and_markup(chat_id)
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_blocked:"))
def cb_grp_blocked(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":", 1)[1]
    groups = load_groups()
    g = groups.get(str(chat_id), {})
    blocked = g.get("blocked_users", {})
    title = g.get("title", chat_id)
    text = f"🔒 Blocked Users — {title}\n──────────────\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    if not blocked:
        text += "No blocked users."
    for uid, info in blocked.items():
        status = info.get("status", "blocked")
        mark = "⛔" if status == "blocked" else "✅"
        text += (
            f"{mark} {info.get('name','?')} [{uid}]\n"
            f"   warnings: {info.get('warnings',0)}, type: {info.get('block_type','kick')}, status: {status}\n"
        )
        if status == "blocked":
            markup.add(types.InlineKeyboardButton(f"✅ Unblock {info.get('name','?')}", callback_data=f"grp_unblock:{chat_id}:{uid}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"grp_view:{chat_id}"))
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_unblock:"))
def cb_grp_unblock(call):
    if not _require_admin(call):
        return
    _, chat_id, uid = call.data.split(":", 2)
    group_unblock_user(chat_id, uid)
    bot.answer_callback_query(call.id, "✅ Unblocked")
    cb_grp_blocked(call)


@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_block_new:"))
def cb_grp_block_new(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":", 1)[1]
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"grp_view:{chat_id}"))
    msg = bot.send_message(call.message.chat.id, "🆔 آیدی عددی کاربری که می‌خوای توی این گروه بلاک کنی رو بفرست:", reply_markup=markup)
    bot.register_next_step_handler(msg, _process_manual_group_block, chat_id)
    bot.answer_callback_query(call.id)


def _process_manual_group_block(message, chat_id):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"grp_view:{chat_id}"))
        bot.reply_to(message, "⚠️ آیدی نامعتبره، فقط عدد بفرست.", reply_markup=markup)
        return
    uid = text
    users = get_users()
    name = users.get(uid, {}).get("name", "Unknown")
    group_block_user(chat_id, uid, name, block_type="kick", warnings_count=0)
    try:
        bot.kick_chat_member(int(chat_id), int(uid))
    except Exception as e:
        logger.error(f"Manual kick failed for {uid} in {chat_id}: {e}")
    text, markup = _group_detail_text_and_markup(chat_id)
    bot.send_message(message.chat.id, "✅ کاربر به لیست بلاک‌شده‌های این گروه اضافه شد.")
    _edit_or_send(message.chat.id, None, text, markup)

@bot.callback_query_handler(func=lambda call: call.data == "panel_main")
def cb_panel_main(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    render_panel_main(call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "panel_users")
def cb_panel_users(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    render_users_list(call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "panel_users_select")
def cb_panel_users_select(call):
    if not _require_admin(call):
        return
    number_map, display = get_user_number_map()
    if not number_map:
        bot.answer_callback_query(call.id, "No users yet", show_alert=True)
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_users"))
    text = "🔎 Type the user number:\n\n" + display
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    msg = bot.send_message(call.message.chat.id, "Send the number now.")
    bot.register_next_step_handler(msg, _process_select_user, call.message.message_id)
    bot.answer_callback_query(call.id)


def _process_select_user(message, panel_message_id):
    if not is_admin(message.from_user.id):
        return
    number_map, _ = get_user_number_map()
    user_id = number_map.get(message.text.strip())
    if not user_id:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_users"))
        bot.send_message(message.chat.id, "❌ Invalid number.", reply_markup=markup)
        return
    render_user_info(message.chat.id, panel_message_id, user_id)


# ---------------------------------------------------------------------------
# Single user info screen
# ---------------------------------------------------------------------------

def user_info_text(user_id, banner=None):
    users = get_users()
    info = users.get(user_id, {})
    is_blocked = user_id in get_blocked()
    text = "👤 User Info\n──────────────\n"
    text += f"🆔 ID: {user_id}\n"
    text += f"👤 Name: {info.get('name', 'Unknown')}\n"
    text += f"🔗 Username: {info.get('username', 'No username')}\n"
    text += f"📅 First seen: {info.get('first_seen', 'Unknown')}\n"
    text += f"📅 Last seen: {info.get('last_seen', 'Unknown')}\n"
    text += f"💬 Total messages: {info.get('total_messages', 0)}\n"
    text += f"📌 Status: {'⛔ Blocked' if is_blocked else '✅ Active'}\n"
    if banner:
        text = banner + "\n\n" + text
    return text


def get_user_info_keyboard(user_id):
    is_blocked = user_id in get_blocked()
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_blocked:
        markup.add(types.InlineKeyboardButton("✅ Unblock", callback_data=f"panel_user_unblock:{user_id}"))
    else:
        markup.add(types.InlineKeyboardButton("⛔ Block", callback_data=f"panel_user_block:{user_id}"))
    markup.add(types.InlineKeyboardButton("📝 Recent Messages", callback_data=f"panel_user_recent:{user_id}"))
    markup.add(types.InlineKeyboardButton("✉️ Message User", callback_data=f"panel_user_msg:{user_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_users"))
    return markup


def render_user_info(chat_id, message_id, user_id, banner=None):
    users = get_users()
    if user_id not in users:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_users"))
        _edit_or_send(chat_id, message_id, f"❌ User {user_id} not found", markup)
        return
    text = user_info_text(user_id, banner=banner)
    markup = get_user_info_keyboard(user_id)
    _edit_or_send(chat_id, message_id, text, markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_user_block:"))
def cb_panel_user_block(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_id = call.data.split(":", 1)[1]
    if block_user(user_id):
        banner = f"✅ User {user_id} blocked successfully"
    else:
        banner = f"⚠️ User {user_id} was already blocked"
    render_user_info(call.message.chat.id, call.message.message_id, user_id, banner=banner)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_user_unblock:"))
def cb_panel_user_unblock(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_id = call.data.split(":", 1)[1]
    if unblock_user(user_id):
        banner = f"✅ User {user_id} unblocked successfully"
    else:
        banner = f"⚠️ User {user_id} was not blocked"
    render_user_info(call.message.chat.id, call.message.message_id, user_id, banner=banner)
    bot.answer_callback_query(call.id)


# ---------------------------------------------------------------------------
# Recent messages
# ---------------------------------------------------------------------------

def get_recent_keyboard(user_id):
    markup = types.InlineKeyboardMarkup(row_width=4)
    markup.add(
        types.InlineKeyboardButton("5", callback_data=f"panel_user_recent_n:{user_id}:5"),
        types.InlineKeyboardButton("10", callback_data=f"panel_user_recent_n:{user_id}:10"),
        types.InlineKeyboardButton("15", callback_data=f"panel_user_recent_n:{user_id}:15"),
        types.InlineKeyboardButton("20", callback_data=f"panel_user_recent_n:{user_id}:20"),
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"panel_user_view:{user_id}"))
    return markup


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_user_view:"))
def cb_panel_user_view(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_id = call.data.split(":", 1)[1]
    render_user_info(call.message.chat.id, call.message.message_id, user_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_user_recent:"))
def cb_panel_user_recent(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_id = call.data.split(":", 1)[1]
    text = "📝 Recent Messages\n──────────────\nHow many messages do you want to see?"
    _edit_or_send(call.message.chat.id, call.message.message_id, text, get_recent_keyboard(user_id))
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_user_recent_n:"))
def cb_panel_user_recent_n(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    _, user_id, n = call.data.split(":")
    n = int(n)
    messages = get_user_messages(user_id)
    shown = messages[-n:]
    text = f"📝 Last {len(shown)} of {len(messages)} Messages (User {user_id})\n──────────────\n\n"
    if not messages:
        text += "No messages yet."
    else:
        for msg in shown:
            role_icon = "👤" if msg.get("role") == "user" else "🤖"
            text += f"[{msg.get('time', '')}] {role_icon} {msg.get('content', '')[:150]}\n"
    markup = get_recent_keyboard(user_id)
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


# ---------------------------------------------------------------------------
# Message User
# ---------------------------------------------------------------------------

def _ask_compose_message(call, kind, target_id, back_callback):
    admin_id = call.from_user.id
    pending_compose[admin_id] = {"kind": kind, "target": target_id, "text": None}
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=back_callback))
    who = f"user {target_id}" if target_id else "all users"
    text = f"✉️ Send me the message you want to send to {who}."
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    msg = bot.send_message(call.message.chat.id, "Type the message now.")
    bot.register_next_step_handler(msg, _capture_compose_text, call.message.message_id, back_callback)


def _capture_compose_text(message, panel_message_id, back_callback):
    if not is_admin(message.from_user.id):
        return
    admin_id = message.from_user.id
    draft = pending_compose.get(admin_id)
    if not draft:
        return
    draft["text"] = message.text
    kind = draft["kind"]
    target = draft["target"]

    preview = "✉️ Preview\n──────────────\n" + message.text + "\n──────────────\nSend this?"
    markup = types.InlineKeyboardMarkup(row_width=2)
    if kind == "broadcast":
        markup.add(
            types.InlineKeyboardButton("✅ Send", callback_data="panel_broadcast_send"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="panel_broadcast_cancel"),
        )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    else:
        markup.add(
            types.InlineKeyboardButton("✅ Send", callback_data=f"panel_msg_send:{kind}:{target}"),
            types.InlineKeyboardButton("❌ Cancel", callback_data=f"panel_msg_cancel:{kind}:{target}"),
        )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=back_callback))

    bot.send_message(message.chat.id, preview[:4000], reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_user_msg:"))
def cb_panel_user_msg(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_id = call.data.split(":", 1)[1]
    _ask_compose_message(call, "user", user_id, f"panel_user_view:{user_id}")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_blocked_msg:"))
def cb_panel_blocked_msg(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_id = call.data.split(":", 1)[1]
    _ask_compose_message(call, "blocked", user_id, "panel_blocked")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_msg_send:"))
def cb_panel_msg_send(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    _, kind, user_id = call.data.split(":")
    admin_id = call.from_user.id
    draft = pending_compose.pop(admin_id, None)
    text = draft["text"] if draft else None

    if not text:
        bot.answer_callback_query(call.id, "Nothing to send", show_alert=True)
        return

    try:
        bot.send_message(int(user_id), text)
        banner = f"✅ Message delivered to {user_id}"
    except Exception as e:
        logger.error(f"Failed to message user {user_id}: {e}")
        banner = f"❌ Failed to deliver message to {user_id}"

    if kind == "blocked":
        render_blocked_list(call.message.chat.id, call.message.message_id, banner=banner)
    else:
        render_user_info(call.message.chat.id, call.message.message_id, user_id, banner=banner)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("panel_msg_cancel:"))
def cb_panel_msg_cancel(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    _, kind, user_id = call.data.split(":")
    pending_compose.pop(call.from_user.id, None)
    if kind == "blocked":
        render_blocked_list(call.message.chat.id, call.message.message_id, banner="❌ Cancelled")
    else:
        render_user_info(call.message.chat.id, call.message.message_id, user_id, banner="❌ Cancelled")
    bot.answer_callback_query(call.id)


# ---------------------------------------------------------------------------
# Blocked list
# ---------------------------------------------------------------------------

def render_blocked_list(chat_id, message_id, banner=None):
    blocked = get_blocked()
    users = get_users()
    meta = get_blocked_meta()
    text = "⛔ Blocked Users\n──────────────\n\n"
    if not blocked:
        text += "No blocked users."
    else:
        reason_labels = {
            "bot_insult": "توهین به ربات",
            "group_ban_policy": "policy=Ban یه گروه",
            "manual": "بلاک دستی ادمین",
        }
        for i, user_id in enumerate(blocked, 1):
            info = users.get(user_id, {})
            name = info.get("name", "Unknown")
            username = info.get("username", "No username")
            m = meta.get(user_id, {})
            reason = reason_labels.get(m.get("reason"), "نامشخص")
            source = m.get("source", "-")
            text += f"{i}. {name} [{user_id}] {username}\n   دلیل: {reason} | منبع: {source}\n"
    if banner:
        text = banner + "\n\n" + text

    markup = types.InlineKeyboardMarkup(row_width=1)
    if blocked:
        markup.add(types.InlineKeyboardButton("🔎 Select User", callback_data="panel_blocked_select"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    _edit_or_send(chat_id, message_id, text, markup)


@bot.callback_query_handler(func=lambda call: call.data == "panel_blocked")
def cb_panel_blocked(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    render_blocked_list(call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "panel_blocked_select")
def cb_panel_blocked_select(call):
    if not _require_admin(call):
        return
    blocked = get_blocked()
    users = get_users()
    if not blocked:
        bot.answer_callback_query(call.id, "No blocked users", show_alert=True)
        return
    display = ""
    number_map = {}
    for i, user_id in enumerate(blocked, 1):
        number_map[str(i)] = user_id
        name = users.get(user_id, {}).get("name", "Unknown")
        display += f"{i}. {name} [{user_id}]\n"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_blocked"))
    text = "🔎 Type the user number:\n\n" + display
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    msg = bot.send_message(call.message.chat.id, "Send the number now.")
    bot.register_next_step_handler(msg, _process_select_blocked, call.message.message_id, number_map)
    bot.answer_callback_query(call.id)


def _process_select_blocked(message, panel_message_id, number_map):
    if not is_admin(message.from_user.id):
        return
    user_id = number_map.get(message.text.strip())
    if not user_id:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_blocked"))
        bot.send_message(message.chat.id, "❌ Invalid number.", reply_markup=markup)
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("✉️ Message User", callback_data=f"panel_blocked_msg:{user_id}"))
    markup.add(types.InlineKeyboardButton("✅ Unblock", callback_data=f"panel_user_unblock:{user_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_blocked"))
    users = get_users()
    name = users.get(user_id, {}).get("name", "Unknown")
    text = f"⛔ Blocked User\n──────────────\n🆔 {user_id}\n👤 {name}"
    _edit_or_send(message.chat.id, panel_message_id, text, markup)


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

@bot.callback_query_handler(func=lambda call: call.data == "panel_broadcast")
def cb_panel_broadcast(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    _ask_compose_message(call, "broadcast", None, "panel_main")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "panel_broadcast_send")
def cb_panel_broadcast_send(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    admin_id = call.from_user.id
    draft = pending_compose.pop(admin_id, None)
    text = draft["text"] if draft else None

    if not text:
        bot.answer_callback_query(call.id, "Nothing to send", show_alert=True)
        return

    users = get_users()
    if not users:
        bot.answer_callback_query(call.id, "No users to broadcast to", show_alert=True)
        return

    success_count = 0
    failed_count = 0
    failed_users = []

    for user_id in users.keys():
        try:
            bot.send_message(int(user_id), text)
            success_count += 1
            if "last_broadcast_failed" in users[user_id]:
                del users[user_id]["last_broadcast_failed"]
            time.sleep(0.1)
        except Exception as e:
            failed_count += 1
            failed_users.append({
                "id": user_id,
                "name": users[user_id].get("name", "Unknown")
            })
            users[user_id]["last_broadcast_failed"] = True
            logger.error(f"Broadcast failed for {user_id}: {e}")

    save_users(users)
    logger.info(f"Broadcast sent: {success_count} ok, {failed_count} failed")

    result_text = "📢 Broadcast Result\n──────────────\n"
    result_text += f"✅ Delivered: {success_count}\n"
    result_text += f"❌ Failed: {failed_count}\n"

    if failed_users:
        result_text += "\nFailed users:\n"
        for user in failed_users:
            result_text += f"⛔ {user['name']} [{user['id']}] - blocked the bot\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back to Panel", callback_data="panel_main"))
    _edit_or_send(call.message.chat.id, call.message.message_id, result_text, markup)
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=['approve_group'])
def approve_group(message):
    if not is_admin(message.from_user.id):
        return
    try:
        chat_id = message.text.split()[1]
        set_group_status(chat_id, "approved")
        bot.reply_to(message, f"✅ Group {chat_id} approved")
    except Exception:
        bot.reply_to(message, "Usage: /approve_group CHAT_ID")

@bot.message_handler(commands=['reject_group'])
def cmd_reject_group(message):
    if not is_admin(message.from_user.id):
        return
    try:
        chat_id = message.text.split()[1]
        reject_group(chat_id)
        bot.reply_to(message, f"❌ Group {chat_id} rejected")
    except Exception:
        bot.reply_to(message, "Usage: /reject_group CHAT_ID")

@bot.message_handler(commands=['group_policy'])
def group_policy(message):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = message.text.split()
        chat_id = parts[1]
        policy = parts[2]
        if policy not in ["kick", "ban"]:
            raise ValueError
        set_group_policy(chat_id, policy)
        bot.reply_to(message, f"✅ Policy for {chat_id} set to {policy}")
    except Exception:
        bot.reply_to(message, "Usage: /group_policy CHAT_ID kick|ban")

@bot.callback_query_handler(func=lambda call: call.data == "panel_broadcast_cancel")
def cb_panel_broadcast_cancel(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    pending_compose.pop(call.from_user.id, None)
    render_panel_main(call.message.chat.id, call.message.message_id, banner="❌ Broadcast cancelled")
    bot.answer_callback_query(call.id)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@bot.callback_query_handler(func=lambda call: call.data == "panel_stats")
def cb_panel_stats(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)

    users = get_users()
    blocked = get_blocked()
    total_users = len(users)
    total_blocked = len(blocked)
    active_users = total_users - total_blocked

    total_messages = 0
    for user_id in users:
        messages = get_user_messages(user_id)
        total_messages += len(messages)

    try:
        data_size = sum(f.stat().st_size for f in DATA_DIR.rglob("*") if f.is_file())
        data_size_mb = data_size / (1024 * 1024)
        size_text = f"{data_size_mb:.2f} MB"
    except Exception:
        size_text = "Unknown"

    text = "📊 Bot Statistics\n──────────────\n"
    text += f"👥 Total users: {total_users}\n"
    text += f"⛔ Blocked: {total_blocked}\n"
    text += f"✅ Active: {active_users}\n"
    text += f"💬 Total messages: {total_messages}\n"
    text += f"📁 Data size: {size_text}\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)


# ---------------------------------------------------------------------------
# Bot Log
# ---------------------------------------------------------------------------

def _read_log_lines(count=20):
    """Read last N lines from bot.log."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return lines[-count:] if len(lines) > count else lines
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"Failed to read log: {e}")
        return []


def _reset_log_file():
    """Clear bot.log contents."""
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("")
        return True
    except Exception as e:
        logger.error(f"Failed to reset log: {e}")
        return False


@bot.callback_query_handler(func=lambda call: call.data == "panel_log")
def cb_panel_log(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)

    lines = _read_log_lines(20)
    log_text = "".join(lines).strip() if lines else "📋 Log is empty"

    text = "📋 Bot Log (last 20 lines)\n──────────────\n\n"
    text += log_text

    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh", callback_data="panel_log"),
        types.InlineKeyboardButton("🗑️ Reset Log", callback_data="panel_log_reset"),
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))

    _edit_or_send(call.message.chat.id, call.message.message_id, text[:4000], markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "panel_log_reset")
def cb_panel_log_reset(call):
    if not _require_admin(call):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)

    if _reset_log_file():
        banner = "✅ Bot log cleared successfully"
        logger.info("Bot log reset by admin")
    else:
        banner = "❌ Failed to clear bot log"

    lines = _read_log_lines(20)
    log_text = "".join(lines).strip() if lines else "📋 Log is empty"

    text = banner + "\n\n📋 Bot Log (last 20 lines)\n──────────────\n\n"
    text += log_text

    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh", callback_data="panel_log"),
        types.InlineKeyboardButton("🗑️ Reset Log", callback_data="panel_log_reset"),
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))

    _edit_or_send(call.message.chat.id, call.message.message_id, text[:4000], markup)
    bot.answer_callback_query(call.id)


# ---------------------------------------------------------------------------
# Regular command handlers
# ---------------------------------------------------------------------------


@bot.message_handler(func=lambda m: m.chat.type in ["group", "supergroup"])
def handle_group(message):
    try:
        chat_id = str(message.chat.id)
        g = get_group_status(chat_id)
        if g.get("status") != "approved":
            return

        # Detect whether this message is addressed to the bot (mention or reply to bot's message)
        mentioned = False
        try:
            bot_username = bot.get_me().username
            if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
                mentioned = True
            elif message.text:
                low = message.text.lower()
                if f"@{bot_username}" in low or "voidra" in low or "voidrra" in low:
                    mentioned = True
        except Exception:
            pass

        if not is_admin(message.from_user.id):
            if message.text and check_badwords(message.text):
                name = message.from_user.first_name or "User"

                if mentioned:
                    # Path 2: insult aimed directly at the bot -> global counter, always ends in a global ban
                    w = warn_user_global(message.from_user.id)
                    if w == 1:
                        bot.reply_to(message, "⚠️ اخطار اول: به ربات توهین نکن.")
                        bot.send_message(ADMIN_ID, f"🚨 اخطار اول (توهین به ربات)\nکاربر: {name}\nID: {message.from_user.id}\nChat: {chat_id}\nText: {message.text}")
                        return
                    elif w == 2:
                        bot.reply_to(message, "⚠️ اخطار دوم: یک بار دیگه برای همیشه بلاک می‌شی.")
                        bot.send_message(ADMIN_ID, f"🚨 اخطار دوم (توهین به ربات)\nکاربر: {name}\nID: {message.from_user.id}\nChat: {chat_id}\nText: {message.text}")
                        return
                    else:
                        block_user(message.from_user.id, reason="bot_insult", source="bot_direct")
                        bot.reply_to(message, "⛔ به دلیل توهین به ربات، به‌طور کامل بلاک شدی.")
                        bot.send_message(ADMIN_ID, f"⛔ Global Banned (توهین به ربات)\nUser: {name}\nID: {message.from_user.id}\nChat: {chat_id}")
                        return
                else:
                    # Path 1: ordinary profanity in the group -> per-group counter, resolved by group policy
                    w = warn_user_in_group(chat_id, message.from_user.id)
                    if w == 1:
                        bot.reply_to(message, "⚠️ اخطار اول: استفاده از فحش توی این گروه ممنوعه.")
                        bot.send_message(ADMIN_ID, f"🚨 اخطار اول (گروه)\nUser: {name}\nID: {message.from_user.id}\nChat: {chat_id}\nText: {message.text}")
                        return
                    elif w == 2:
                        bot.reply_to(message, "⚠️ اخطار دوم: یک بار دیگه طبق سیاست این گروه بلاک می‌شی.")
                        bot.send_message(ADMIN_ID, f"🚨 اخطار دوم (گروه)\nUser: {name}\nID: {message.from_user.id}\nChat: {chat_id}\nText: {message.text}")
                        return
                    else:
                        policy = g.get("policy", "kick")
                        if policy == "ban":
                            block_user(message.from_user.id, reason="group_ban_policy", source=chat_id)
                            group_block_user(chat_id, message.from_user.id, name, block_type="ban", warnings_count=w)
                            bot.send_message(ADMIN_ID, f"⛔ Global Banned (policy=ban)\nUser: {name}\nID: {message.from_user.id}\nChat: {chat_id}")
                        else:
                            group_block_user(chat_id, message.from_user.id, name, block_type="kick", warnings_count=w)
                            try:
                                bot.kick_chat_member(message.chat.id, message.from_user.id)
                                bot.send_message(ADMIN_ID, f"⛔ Kicked from group\nUser: {name}\nID: {message.from_user.id}\nChat: {chat_id}")
                            except Exception as e:
                                bot.send_message(ADMIN_ID, f"⚠️ Can't kick {name}: {e}")
                        return

        if not mentioned:
            return

        register_user(message.from_user)
        prompt = message.text or ""
        bot.send_chat_action(message.chat.id, 'typing')
        reply = ask_ai(message.chat.id, prompt)
        send_if_mono(message.chat.id, reply)
    except Exception as e:
        logger.error(f"group handler error: {e}")


@bot.message_handler(commands=['backup'])
def cmd_backup(message):
    if message.from_user.id != ADMIN_ID:
        return
    backup_data_to_admin()
    bot.reply_to(message, "✅ backup sent")


@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handle /start command."""
    try:
        is_new = register_user(message.from_user)
        notify_admin_once(message.from_user)

        if is_user_blocked(message.from_user.id):
            bot.reply_to(message, "⛔ You are blocked by admin")
            return

        if is_channel_member(bot, message.from_user.id):
            if is_admin(message.from_user.id):
                bot.reply_to(message, "🧠 AI is running 🧠", reply_markup=get_admin_reply_keyboard())
            else:
                bot.reply_to(message, "🧠 AI is running 🧠")
        else:
            send_join_prompt(message)
    except Exception as e:
        logger.error(f"Error in /start: {e}")
        bot.reply_to(message, "⚠️ An error occurred. Please try again.")


@bot.message_handler(commands=['help'])
def send_help(message):
    """Handle /help command."""
    text = "🧠 I am an AI built by [@TD3MY](https://t.me/TD3MY). Still a work in progress 🧠"
    try:
        bot.reply_to(message, text, parse_mode='Markdown')
    except Exception:
        bot.reply_to(message, "🧠 I am an AI built by @TD3MY. Still a work in progress 🧠")


@bot.message_handler(commands=['restore'])
def restore_backup(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ You are not authorized")
        return
    try:
        if message.reply_to_message and message.reply_to_message.document:
            file_info = bot.get_file(message.reply_to_message.document.file_id)
            url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
            r = requests.get(url, timeout=120)
            zpath = DATA_DIR / "restore.zip"
            with open(zpath, "wb") as f:
                f.write(r.content)
            import zipfile
            with zipfile.ZipFile(zpath, "r") as zf:
                zf.extractall(DATA_DIR)
            bot.reply_to(message, "✅ Data restored successfully.")
        else:
            bot.reply_to(message, "⚠️ Reply to a backup zip file with /restore.")
    except Exception as e:
        logger.error(f"restore failed: {e}")
        bot.reply_to(message, "❌ Restore failed.")

@bot.message_handler(commands=['reset'])
def reset_memory(message):
    """Handle /reset command - clear user's conversation history."""
    if is_user_blocked(message.from_user.id):
        bot.reply_to(message, "⛔ You are blocked by admin")
        return

    conversations.pop(message.chat.id, None)
    bot.reply_to(message, "🧠 Memory cleared 🧠")
    logger.info(f"Memory cleared for user {message.from_user.id}")


@bot.message_handler(commands=['panel'])
def admin_panel_command(message):
    """Show admin panel via /panel command."""
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ You are not authorized")
        return

    bot.clear_step_handler_by_chat_id(message.chat.id)
    bot.send_message(message.chat.id, "🎛️ Opening panel...", reply_markup=get_admin_reply_keyboard())
    render_panel_main(message.chat.id)
    logger.info(f"Admin panel opened by {message.from_user.id}")


@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    """Cancel any pending operation."""
    if is_admin(message.from_user.id):
        bot.clear_step_handler_by_chat_id(message.chat.id)
        pending_compose.pop(message.from_user.id, None)
        bot.reply_to(message, "❌ Operation cancelled")


# ---------------------------------------------------------------------------
# Membership check callback
# ---------------------------------------------------------------------------

@bot.callback_query_handler(func=lambda call: call.data == "check_membership")
def recheck_membership(call):
    """Handle membership check button."""
    if is_channel_member(bot, call.from_user.id):
        bot.answer_callback_query(call.id, "✅ Verified!")
        if is_admin(call.from_user.id):
            bot.send_message(call.message.chat.id, "🧠 AI is running 🧠", reply_markup=get_admin_reply_keyboard())
        else:
            bot.send_message(call.message.chat.id, "🧠 AI is running 🧠")
    else:
        bot.answer_callback_query(call.id, "❌ You haven't joined yet.", show_alert=True)


# ---------------------------------------------------------------------------
# Photo handler
# ---------------------------------------------------------------------------


@bot.message_handler(content_types=['document'])
def handle_document(message):
    try:
        register_user(message.from_user)
        notify_admin_once(message.from_user)

        if message.chat.type in ["group", "supergroup"]:
            chat_id = str(message.chat.id)
            g = get_group_status(chat_id)
            if g.get("status") != "approved":
                return

            mentioned = False
            try:
                bot_username = bot.get_me().username
                if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
                    mentioned = True
                elif message.caption:
                    low = message.caption.lower()
                    if f"@{bot_username}" in low or "voidra" in low or "voidrra" in low:
                        mentioned = True
            except Exception:
                pass

            if not mentioned:
                return

        if is_user_blocked(message.from_user.id):
            bot.reply_to(message, "⛔ You are blocked by admin")
            return

        if not is_channel_member(bot, message.from_user.id):
            send_join_prompt(message)
            return

        file_info = bot.get_file(message.document.file_id)
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        fname = message.document.file_name or ""
        bot.send_chat_action(message.chat.id, 'typing')

        if fname.lower().endswith(".pdf"):
            from pypdf import PdfReader
            r = requests.get(file_url, timeout=60)
            pdf_data = r.content
            with open("temp.pdf", "wb") as f:
                f.write(pdf_data)
            reader = PdfReader("temp.pdf")
            txt = ""
            for page in reader.pages:
                txt += page.extract_text() or ""
            prompt = f"این متن از فایل PDF است:\n{txt[:4000]}\n\nآن را خلاصه و تحلیل کن."
        elif fname.lower().endswith(".txt"):
            r = requests.get(file_url, timeout=60)
            txt = r.text[:4000]
            prompt = f"این متن از فایل متنی است:\n{txt}\n\nآن را تحلیل کن."
        else:
            bot.reply_to(message, "⚠️ فقط PDF و TXT پشتیبانی می‌شود.")
            return

        reply = ask_ai(message.chat.id, prompt)
        send_if_mono(message.chat.id, reply)
    except Exception as e:
        logger.error(f"document handler error: {e}")
        bot.reply_to(message, "⚠️ خطا در پردازش فایل")



@bot.message_handler(content_types=['new_chat_members'])
def handle_new_chat_members(message):
    try:
        for member in message.new_chat_members:
            if member.id == bot.get_me().id:
                chat_id = str(message.chat.id)
                set_group_status(chat_id, "pending", title=message.chat.title)
                bot.send_message(
                    message.chat.id,
                    "This bot needs admin approval to work in this group."
                )
                pending = _pending_requests()
                if not any(r.get("chat_id") == chat_id for r in pending):
                    pending.append({"chat_id": chat_id, "title": message.chat.title})
                    _save_pending_requests(pending)
                bot.send_message(ADMIN_ID, f"📥 New group request from {message.chat.title}")
    except Exception as e:
        logger.error(f"new_chat_members error: {e}")



@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    """Handle photo messages from users."""
    try:
        register_user(message.from_user)
        notify_admin_once(message.from_user)

        if is_user_blocked(message.from_user.id):
            bot.reply_to(message, "⛔ You are blocked by admin")
            return

        if not is_channel_member(bot, message.from_user.id):
            send_join_prompt(message)
            return

        bot.send_chat_action(message.chat.id, 'typing')

        file_bytes = None

        for attempt in range(2):
            try:
                sizes = message.photo
                file_id = sizes[len(sizes) // 2].file_id
                file_info = bot.get_file(file_id)
                file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
                file_response = requests.get(file_url, timeout=60)
                file_response.raise_for_status()
                file_bytes = file_response.content
                break
            except Exception as e:
                logger.error(f"Photo download attempt {attempt + 1} failed: {e}")
                time.sleep(2)

        if file_bytes is None:
            bot.reply_to(message, "⚠️ Couldn't read the image, please try sending it again")
            return

        image_b64 = base64.b64encode(file_bytes).decode('utf-8')
        caption = message.caption or ""
        extra = ""
        if message.reply_to_message and message.reply_to_message.text:
            extra = "\nReply to: " + message.reply_to_message.text[:1000]
        full_caption = caption + extra

        append_user_message(message.from_user.id, "user", f"[Sent an image] {full_caption}")

        reply = ask_ai_image(message.chat.id, full_caption, image_b64)

        append_user_message(message.from_user.id, "bot", reply)

        users = get_users()
        if str(message.from_user.id) in users:
            users[str(message.from_user.id)]["total_messages"] = users[str(message.from_user.id)].get("total_messages", 0) + 1
            save_users(users)

        send_if_mono(message.chat.id, reply)

    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        try:
            bot.reply_to(message, "⚠️ An error occurred. Please try again.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Text message handler (for all other messages)
# ---------------------------------------------------------------------------

@bot.message_handler(func=lambda message: message.text in ['🎛️ Panel', '👥 Users', '⛔ Blocked', '📢 Broadcast', '📊 Stats', '📋 Log'])
def handle_admin_keyboard(message):
    """Handle admin reply keyboard buttons."""
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ You are not authorized")
        return

    button = message.text

    if button == '🎛️ Panel':
        admin_panel_command(message)
    elif button == '👥 Users':
        bot.clear_step_handler_by_chat_id(message.chat.id)
        render_users_list(message.chat.id, None)
    elif button == '⛔ Blocked':
        bot.clear_step_handler_by_chat_id(message.chat.id)
        render_blocked_list(message.chat.id, None)
    elif button == '📢 Broadcast':
        bot.clear_step_handler_by_chat_id(message.chat.id)
        pending_compose[message.from_user.id] = {"kind": "broadcast", "target": None, "text": None}
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
        msg = bot.reply_to(message, "📢 Send me the message you want to broadcast to all users.", reply_markup=markup)
        bot.register_next_step_handler(msg, _capture_compose_text, None, "panel_main")
    elif button == '📊 Stats':
        bot.clear_step_handler_by_chat_id(message.chat.id)
        cb_panel_stats_from_message(message)
    elif button == '📋 Log':
        bot.clear_step_handler_by_chat_id(message.chat.id)
        cb_panel_log_from_message(message)


def cb_panel_stats_from_message(message):
    """Show stats from reply keyboard."""
    users = get_users()
    blocked = get_blocked()
    total_users = len(users)
    total_blocked = len(blocked)
    active_users = total_users - total_blocked

    total_messages = 0
    for user_id in users:
        messages = get_user_messages(user_id)
        total_messages += len(messages)

    try:
        data_size = sum(f.stat().st_size for f in DATA_DIR.rglob("*") if f.is_file())
        data_size_mb = data_size / (1024 * 1024)
        size_text = f"{data_size_mb:.2f} MB"
    except Exception:
        size_text = "Unknown"

    text = "📊 Bot Statistics\n──────────────\n"
    text += f"👥 Total users: {total_users}\n"
    text += f"⛔ Blocked: {total_blocked}\n"
    text += f"✅ Active: {active_users}\n"
    text += f"💬 Total messages: {total_messages}\n"
    text += f"📁 Data size: {size_text}\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    bot.reply_to(message, text, reply_markup=markup)


def cb_panel_log_from_message(message):
    """Show log from reply keyboard."""
    lines = _read_log_lines(20)
    log_text = "".join(lines).strip() if lines else "📋 Log is empty"

    text = "📋 Bot Log (last 20 lines)\n──────────────\n\n"
    text += log_text

    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh", callback_data="panel_log"),
        types.InlineKeyboardButton("🗑️ Reset Log", callback_data="panel_log_reset"),
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    bot.reply_to(message, text[:4000], reply_markup=markup)


@bot.message_handler(func=lambda message: True)
def chat_with_ai(message):
    """Handle regular text messages from users."""
    try:
        register_user(message.from_user)
        notify_admin_once(message.from_user)

        if is_user_blocked(message.from_user.id):
            bot.reply_to(message, "⛔ You are blocked by admin")
            return

        if not is_channel_member(bot, message.from_user.id):
            send_join_prompt(message)
            return

        text_lower = (message.text or "").lower()
        if "عکس بساز" in text_lower or "تصویر بساز" in text_lower or "make image" in text_lower or "generate image" in text_lower:
            prompt = message.text
            bot.send_chat_action(message.chat.id, 'upload_photo')
            img = generate_pollinations_image(prompt)
            if img:
                bot.send_photo(message.chat.id, img, caption="🖼️ generated")
            else:
                bot.reply_to(message, "⚠️ Failed to generate image")
            return

        bot.send_chat_action(message.chat.id, 'typing')

        if not is_admin(message.from_user.id):
            if check_badwords(message.text):
                # Private chat with the bot = always Path 2 (insult aimed at the bot itself), global + persistent counter
                w = warn_user_global(message.from_user.id)
                name = message.from_user.first_name or "کاربر"
                if w == 1:
                    bot.reply_to(message, "⚠️ اخطار اول: به ربات توهین نکن.")
                    try:
                        bot.send_message(ADMIN_ID, f"🚨 اخطار اول (پی‌وی)\nکاربر: {name}\nID: {message.from_user.id}\nمتن: {message.text}")
                    except: pass
                    return
                elif w == 2:
                    bot.reply_to(message, "⚠️ اخطار دوم: یک بار دیگه برای همیشه بلاک می‌شی.")
                    try:
                        bot.send_message(ADMIN_ID, f"🚨 اخطار دوم (پی‌وی)\nکاربر: {name}\nID: {message.from_user.id}\nمتن: {message.text}")
                    except: pass
                    return
                else:
                    block_user(message.from_user.id, reason="bot_insult", source="bot_direct")
                    bot.reply_to(message, "⛔ به دلیل توهین به ربات، به‌طور کامل بلاک شدی.")
                    try:
                        bot.send_message(ADMIN_ID, f"⛔ کاربر بلاک شد (توهین به ربات)\nکاربر: {name}\nID: {message.from_user.id}\nمتن: {message.text}")
                    except: pass
                    return

        extra = ""
        if message.reply_to_message:
            if message.reply_to_message.text:
                extra += "\nریپلای به: " + message.reply_to_message.text[:1000]
        if message.forward_from or message.forward_from_chat:
            extra += "\n(پیام فوروارد شده)"

        append_user_message(message.from_user.id, "user", message.text + extra)

        reply = ask_ai(message.chat.id, message.text + extra)

        append_user_message(message.from_user.id, "bot", reply)

        users = get_users()
        if str(message.from_user.id) in users:
            users[str(message.from_user.id)]["total_messages"] = users[str(message.from_user.id)].get("total_messages", 0) + 1
            save_users(users)

        send_if_mono(message.chat.id, reply)

    except Exception as e:
        logger.error(f"Error in chat_with_ai: {e}")
        try:
            bot.reply_to(message, "⚠️ An error occurred. Please try again.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bot startup
# ---------------------------------------------------------------------------

from flask import Flask
app = Flask(__name__)

@app.route("/")
def index():
    return "ok"

if __name__ == "__main__":
    logger.info("Bot starting...")
    backup_data_to_admin()
    start_backup_scheduler()
    print("🧠 Voidra AI is running... 🧠")
    print(f"📁 Data directory: {DATA_DIR}")
    print(f"📋 Log file: {LOG_FILE}")
    print(f"👤 Admin ID: {ADMIN_ID}")

    from threading import Thread
    Thread(target=lambda: app.run(host="0.0.0.0", port=10000)).start()

    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Bot crashed: {e}")
            print(f"❌ Bot crashed: {e}")
            time.sleep(5)
            print("🔄 Restarting...")
