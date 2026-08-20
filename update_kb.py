with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add reply keyboard function before get_panel_main_keyboard
old = "def get_panel_main_keyboard():"
new = """def get_admin_reply_keyboard():
    \"\"\"Persistent reply keyboard for admin at bottom of screen.\"\"\"
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


def get_panel_main_keyboard():"""

if old in content:
    content = content.replace(old, new, 1)
    print('✅ Step 1: Reply keyboard function added')
else:
    print('❌ Step 1: get_panel_main_keyboard not found')

# 2. Update /start to show reply keyboard for admin
old_start = """        if is_channel_member(bot, message.from_user.id):
            if is_admin(message.from_user.id):
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🎛️ Open Admin Panel", callback_data="panel_main"))
                bot.reply_to(message, "🧠 AI is running 🧠", reply_markup=markup)
            else:
                bot.reply_to(message, "🧠 AI is running 🧠")
        else:
            send_join_prompt(message)"""

new_start = """        if is_channel_member(bot, message.from_user.id):
            if is_admin(message.from_user.id):
                bot.reply_to(message, "🧠 AI is running 🧠", reply_markup=get_admin_reply_keyboard())
            else:
                bot.reply_to(message, "🧠 AI is running 🧠")
        else:
            send_join_prompt(message)"""

if old_start in content:
    content = content.replace(old_start, new_start)
    print('✅ Step 2: /start updated')
else:
    print('❌ Step 2: /start pattern not found')

# 3. Update /panel to show reply keyboard
old_panel = """    bot.clear_step_handler_by_chat_id(message.chat.id)
    render_panel_main(message.chat.id)
    logger.info(f"Admin panel opened by {message.from_user.id}")"""

new_panel = """    bot.clear_step_handler_by_chat_id(message.chat.id)
    bot.send_message(message.chat.id, "🎛️ Opening panel...", reply_markup=get_admin_reply_keyboard())
    render_panel_main(message.chat.id)
    logger.info(f"Admin panel opened by {message.from_user.id}")"""

if old_panel in content:
    content = content.replace(old_panel, new_panel)
    print('✅ Step 3: /panel updated')
else:
    print('❌ Step 3: /panel pattern not found')

# 4. Add handler for reply keyboard buttons BEFORE chat_with_ai
old_chat = """@bot.message_handler(func=lambda message: True)
def chat_with_ai(message):"""

new_chat = """@bot.message_handler(func=lambda message: message.text in ['🎛️ Panel', '👥 Users', '⛔ Blocked', '📢 Broadcast', '📊 Stats', '📋 Log'])
def handle_admin_keyboard(message):
    \"\"\"Handle admin reply keyboard buttons.\"\"\"
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
    \"\"\"Show stats from reply keyboard.\"\"\"
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

    text = "📊 Bot Statistics\\n──────────────\\n"
    text += f"👥 Total users: {total_users}\\n"
    text += f"⛔ Blocked: {total_blocked}\\n"
    text += f"✅ Active: {active_users}\\n"
    text += f"💬 Total messages: {total_messages}\\n"
    text += f"📁 Data size: {size_text}\\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    bot.reply_to(message, text, reply_markup=markup)


def cb_panel_log_from_message(message):
    \"\"\"Show log from reply keyboard.\"\"\"
    lines = _read_log_lines(20)
    log_text = "".join(lines).strip() if lines else "📋 Log is empty"

    text = "📋 Bot Log (last 20 lines)\\n──────────────\\n\\n"
    text += log_text

    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh", callback_data="panel_log"),
        types.InlineKeyboardButton("🗑️ Reset Log", callback_data="panel_log_reset"),
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    bot.reply_to(message, text[:4000], reply_markup=markup)


@bot.message_handler(func=lambda message: True)
def chat_with_ai(message):"""

if old_chat in content:
    content = content.replace(old_chat, new_chat, 1)
    print('✅ Step 4: Reply keyboard handler added')
else:
    print('❌ Step 4: chat_with_ai pattern not found')

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\\n✅ All done!')
