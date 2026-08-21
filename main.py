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


def block_user(user_id):
    """Add user to blocked list. Returns True if newly blocked."""
    blocked = get_blocked()
    if str(user_id) not in blocked:
        blocked.append(str(user_id))
        save_blocked(blocked)
        logger.info(f"User {user_id} blocked by admin")
        return True
    return False


def unblock_user(user_id):
    """Remove user from blocked list. Returns True if was blocked."""
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

warnings = {}

def warn_user(user_id):
    warnings[user_id] = warnings.get(user_id, 0) + 1
    return warnings[user_id]

def check_badwords(user_text):
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openrouter/auto",
                "messages": [
                    {"role": "user", "content": BADWORD_CHECK_PROMPT + user_text}
                ],
                "max_tokens": 5,
                "temperature": 0,
            },
            timeout=20,
        )
        data = r.json()
        content = data["choices"][0]["message"]["content"].strip().upper()
        return "YES" in content
    except Exception as e:
        logger.error(f"badword check failed: {e}")
        return False

def ask_ai(chat_id, prompt):
    """Send user's text message to OpenRouter and get AI response."""
    history = conversations.setdefault(chat_id, [])
    history.append({"role": "user", "content": prompt})

    trimmed_history = history[-MAX_HISTORY:]
    messages_to_send = [SYSTEM_PROMPT] + trimmed_history

    for attempt in range(2):
        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": messages_to_send,
                },
                timeout=30,
            )
            data = response.json()

            if "choices" in data:
                reply = data["choices"][0]["message"]["content"]
                reply = clean_response(reply)
                history.append({"role": "assistant", "content": reply})
                conversations[chat_id] = history[-MAX_HISTORY:]
                return reply

            error_code = data.get("error", {}).get("code")
            if error_code == 429 and attempt == 0:
                wait_seconds = data.get("error", {}).get("metadata", {}).get("retry_after_seconds", 5)
                time.sleep(min(wait_seconds, 15))
                continue

            if error_code == 429:
                logger.warning(f"Rate limit exceeded for chat {chat_id}")
                return "🧠 I'm a bit busy right now, please try again in a few seconds 🧠"

            logger.error(f"OpenRouter API error: {data}")
            return f"⚠️ AI error: {data}"

        except Exception as e:
            logger.error(f"Exception in ask_ai: {e}")
            return f"⚠️ Error talking to AI: {e}"


def ask_ai_image(chat_id, caption, image_base64):
    """Send user's image to OpenRouter vision model and get AI response."""
    user_text = caption if caption else "بگو توی این عکس چی می‌بینی؟ طبیعی و خلاصه توضیح بده."

    user_message = {
        "role": "user",
        "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        ],
    }

    messages_to_send = [SYSTEM_PROMPT, user_message]

    for attempt in range(2):
        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": VISION_MODEL,
                    "messages": messages_to_send,
                },
                timeout=60,
            )
            data = response.json()

            if "choices" in data:
                reply = data["choices"][0]["message"]["content"]
                reply = clean_response(reply)
                history = conversations.setdefault(chat_id, [])
                history.append({"role": "user", "content": f"[Sent an image] {user_text}"})
                history.append({"role": "assistant", "content": reply})
                conversations[chat_id] = history[-MAX_HISTORY:]
                return reply

            error_code = data.get("error", {}).get("code")
            if error_code == 429 and attempt == 0:
                wait_seconds = data.get("error", {}).get("metadata", {}).get("retry_after_seconds", 5)
                time.sleep(min(wait_seconds, 15))
                continue

            if error_code == 429:
                logger.warning(f"Rate limit exceeded for image from {chat_id}")
                return "🧠 I'm a bit busy right now, please try again in a few seconds 🧠"

            logger.error(f"OpenRouter vision API error: {data}")
            return f"⚠️ AI error: {data}"

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
bot = telebot.TeleBot(TOKEN)


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

def set_group_status(chat_id, status):
    groups = load_groups()
    groups[str(chat_id)] = {"status": status}
    save_groups(groups)

def set_group_policy(chat_id, policy):
    groups = load_groups()
    if str(chat_id) not in groups:
        groups[str(chat_id)] = {}
    groups[str(chat_id)]["policy"] = policy
    save_groups(groups)


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
    text = "⛔ Blocked Users\n──────────────\n\n"
    if not blocked:
        text += "No blocked users."
    else:
        for i, user_id in enumerate(blocked, 1):
            info = users.get(user_id, {})
            name = info.get("name", "Unknown")
            username = info.get("username", "No username")
            text += f"{i}. {name} [{user_id}] {username}\n"
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
def reject_group(message):
    if not is_admin(message.from_user.id):
        return
    try:
        chat_id = message.text.split()[1]
        set_group_status(chat_id, "rejected")
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

        if not is_admin(message.from_user.id):
            if message.text and check_badwords(message.text):
                w = warn_user(message.from_user.id)
                name = message.from_user.first_name or "User"
                if w == 1:
                    bot.reply_to(message, "⚠️ Warning 1: watch your language.")
                    bot.send_message(ADMIN_ID, f"🚨 Group Warning 1\nUser: {name}\nID: {message.from_user.id}\nChat: {chat_id}\nText: {message.text}")
                    return
                elif w == 2:
                    bot.reply_to(message, "⚠️ Warning 2: one more and you'll be banned.")
                    bot.send_message(ADMIN_ID, f"🚨 Group Warning 2\nUser: {name}\nID: {message.from_user.id}\nChat: {chat_id}\nText: {message.text}")
                    return
                else:
                    policy = g.get("policy", "kick")
                    if policy == "ban":
                        block_user(message.from_user.id)
                        bot.send_message(ADMIN_ID, f"⛔ Global Banned\nUser: {name}\nID: {message.from_user.id}")
                    else:
                        try:
                            bot.kick_chat_member(message.chat.id, message.from_user.id)
                            bot.send_message(ADMIN_ID, f"⛔ Kicked from group\nUser: {name}\nID: {message.from_user.id}")
                        except Exception as e:
                            bot.send_message(ADMIN_ID, f"⚠️ Can't kick {name}: {e}")
                    return

        mentioned = False
        try:
            bot_username = bot.get_me().username
            if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
                mentioned = True
            elif message.text:
                low = message.text.lower()
                if f"@{bot_username}" in low or "voidra" in low or "voidrra" in low:
                    mentioned = True
        except:
            pass

        if not mentioned:
            return

        register_user(message.from_user)
        prompt = message.text or ""
        bot.send_chat_action(message.chat.id, 'typing')
        reply = ask_ai(message.chat.id, prompt)
        bot.reply_to(message, reply)
    except Exception as e:
        logger.error(f"group handler error: {e}")


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
        bot.reply_to(message, reply)
    except Exception as e:
        logger.error(f"document handler error: {e}")
        bot.reply_to(message, "⚠️ خطا در پردازش فایل")



@bot.message_handler(content_types=['new_chat_members'])
def handle_new_chat_members(message):
    try:
        for member in message.new_chat_members:
            if member.id == bot.get_me().id:
                chat_id = str(message.chat.id)
                set_group_status(chat_id, "pending")
                bot.send_message(
                    message.chat.id,
                    "This bot needs admin approval to work in this group."
                )
                bot.send_message(
                    ADMIN_ID,
                    f"📥 Bot added to group\nChat: {chat_id}\nTitle: {message.chat.title}\n\n/approve_group_{chat_id} or /reject_group_{chat_id}"
                )
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

        append_user_message(message.from_user.id, "user", f"[Sent an image] {caption}")

        reply = ask_ai_image(message.chat.id, caption, image_b64)

        append_user_message(message.from_user.id, "bot", reply)

        users = get_users()
        if str(message.from_user.id) in users:
            users[str(message.from_user.id)]["total_messages"] = users[str(message.from_user.id)].get("total_messages", 0) + 1
            save_users(users)

        bot.reply_to(message, reply)

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

        bot.send_chat_action(message.chat.id, 'typing')

        if not is_admin(message.from_user.id):
            if check_badwords(message.text):
                w = warn_user(message.from_user.id)
                name = message.from_user.first_name or "کاربر"
                if w == 1:
                    bot.reply_to(message, "⚠️ اخطار اول: استفاده از فحش ممنوعه.")
                    try:
                        bot.send_message(ADMIN_ID, f"🚨 اخطار اول\nکاربر: {name}\nID: {message.from_user.id}\nمتن: {message.text}")
                    except: pass
                    return
                elif w == 2:
                    bot.reply_to(message, "⚠️ اخطار دوم: یک بار دیگه بلاک می‌شی.")
                    try:
                        bot.send_message(ADMIN_ID, f"🚨 اخطار دوم\nکاربر: {name}\nID: {message.from_user.id}\nمتن: {message.text}")
                    except: pass
                    return
                else:
                    block_user(message.from_user.id)
                    bot.reply_to(message, "⛔ تو به دلیل فحش بلاک شدی.")
                    try:
                        bot.send_message(ADMIN_ID, f"⛔ کاربر بلاک شد\nکاربر: {name}\nID: {message.from_user.id}\nمتن: {message.text}")
                    except: pass
                    return

        extra = ""
        if message.reply_to_message:
            if message.reply_to_message.text:
                extra += "\nریپلای به: " + message.reply_to_message.text[:1000]
        if message.forward_from or message.forward_from_chat:
            extra += "\n(پیام فوروارد شده)"

        append_user_message(message.from_user.id, "user", message.text + extra)

        reply = ask_ai(message.chat.id, message.text)

        append_user_message(message.from_user.id, "bot", reply)

        users = get_users()
        if str(message.from_user.id) in users:
            users[str(message.from_user.id)]["total_messages"] = users[str(message.from_user.id)].get("total_messages", 0) + 1
            save_users(users)

        bot.reply_to(message, reply)

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
