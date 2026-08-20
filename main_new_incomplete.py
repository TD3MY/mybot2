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
SELF_BLOCKED_FILE = DATA_DIR / "self_blocked.json"
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
# Data storage helpers
# ---------------------------------------------------------------------------

def load_json(filepath, default):
    """Load JSON data from a file, return default if file doesn't exist."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return default
    except Exception as e:
        logger.error(f"Failed to load {filepath.name}: {e}")
        return default


def save_json(filepath, data):
    """Save data to a JSON file."""
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


def get_self_blocked():
    """Get list of user_ids who failed to receive the last broadcast (self-blocked)."""
    return load_json(SELF_BLOCKED_FILE, [])


def save_self_blocked(user_ids):
    """Save list of user_ids who failed to receive the last broadcast."""
    return save_json(SELF_BLOCKED_FILE, user_ids)




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
        "role": role,
        "content": content[:2000],
    })
    if len(messages) > 500:
        messages = messages[-500:]
    save_user_messages(user_id, messages)


def register_user(user):
    """Register a new user or update existing user info."""
    users = get_users()
    user_id = str(user.id)
    
    if user_id not in users:
        users[user_id] = {
            "id": user.id,
            "name": (user.first_name or "") + (" " + user.last_name if user.last_name else ""),
            "username": f"@{user.username}" if user.username else "No username",
            "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_messages": 0,
        }
        save_users(users)
        
        user_dir = get_user_dir(user.id)
        save_user_info(user.id, users[user_id])
        save_user_messages(user.id, [])
        
        logger.info(f"New user registered: {user.id} - {users[user_id]['name']}")
        return True
    else:
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
    """Add user to blocked list."""
    blocked = get_blocked()
    if str(user_id) not in blocked:
        blocked.append(str(user_id))
        save_blocked(blocked)
        logger.info(f"User {user_id} blocked by admin")
        return True
    return False


def unblock_user(user_id):
    """Remove user from blocked list."""
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
    """
    Clean AI response:
    - Remove Markdown formatting
    - Convert LaTeX math to plain text
    - Keep emojis and Persian/English/Arabic text untouched
    """
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
        "FORMATTING RULE: Never use Markdown formatting in your replies. "
        "Do not use bold, *italic*, code, or any LaTeX math notation "
        "like $formula$ or \\frac{}{}. Write everything as plain text only."
    ),
}


# ---------------------------------------------------------------------------
# AI communication functions
# ---------------------------------------------------------------------------

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
    user_text = caption if caption else "Describe this image."

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
notified_users = set()


def notify_admin_once(user):
    """Send user info to admin group once per user."""
    if user.id in notified_users:
        return
    notified_users.add(user.id)

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


def is_admin(user_id):
    """Check if user_id is the admin."""
    return user_id == ADMIN_ID


# ---------------------------------------------------------------------------
# In-memory session state for the admin panel (per admin, resets on restart)
# ---------------------------------------------------------------------------
admin_pending = {}


def state_for(admin_id):
    return admin_pending.setdefault(admin_id, {})


def clear_state(admin_id):
    admin_pending[admin_id] = {}


# ---------------------------------------------------------------------------
# Regular command handlers
# ---------------------------------------------------------------------------

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handle /start command."""
    try:
        register_user(message.from_user)
        notify_admin_once(message.from_user)

        if is_user_blocked(message.from_user.id):
            bot.reply_to(message, "⛔ You are blocked by admin")
            return

        if is_channel_member(bot, message.from_user.id):
            if is_admin(message.from_user.id):
                bot.reply_to(message, "🧠 AI is running 🧠\nUse /panel to open the admin panel.")
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


# ---------------------------------------------------------------------------
# Admin panel - text builders
# ---------------------------------------------------------------------------

def build_users_list_text():
    """List every user, numbered in the order they first started the bot."""
    users = get_users()
    if not users:
        return "👥 No users yet"

    blocked = set(get_blocked())
    self_blocked = set(get_self_blocked())

    lines = [f"👥 Users: {len(users)}", "──────────────", ""]
    for i, (uid, info) in enumerate(users.items(), 1):
        name = info.get("name", "Unknown")
        username = info.get("username", "No username")
        first_seen = info.get("first_seen", "Unknown")
        status = "⛔" if uid in blocked else "✅"
        sblock = " 🚫" if uid in self_blocked else ""
        lines.append(f"{i}. {name} [{uid}] {username} {status}{sblock}")
    return "\n".join(lines)



