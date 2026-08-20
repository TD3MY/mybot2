with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the old handler
old_handler = """@bot.message_handler(func=lambda message: message.text in ['🎛️ Panel', '👥 Users', '⛔ Blocked', '📢 Broadcast', '📊 Stats', '📋 Log'])
def handle_admin_keyboard(message):
    \"\"\"Handle admin reply keyboard buttons.\"\"\"
    if not is_admin(message.from_user.id):
        bot.reply_to(message, \"⛔ You are not authorized\")
        return

    button = message.text

    if button == '🎛️ Panel':
        admin_panel_command(message)
    elif button == '👥 Users':
        users_command(message)
    elif button == '⛔ Blocked':
        blocked_command(message)
    elif button == '📢 Broadcast':
        msg = bot.reply_to(message, \"📢 Send me the message you want to broadcast to all users.\\nSend /cancel to cancel.\")
        bot.register_next_step_handler(msg, process_broadcast)
    elif button == '📊 Stats':
        show_stats_from_command(message)
    elif button == '📋 Log':
        show_log_from_command(message)"""

new_handler = """@bot.message_handler(func=lambda message: message.text in ['🎛️ Panel', '👥 Users', '⛔ Blocked', '📢 Broadcast', '📊 Stats', '📋 Log', '⛔ Block', '✅ Unblock', '👤 Info', '🔙 Back'])
def handle_admin_keyboard(message):
    \"\"\"Handle admin reply keyboard buttons.\"\"\"
    if not is_admin(message.from_user.id):
        bot.reply_to(message, \"⛔ You are not authorized\")
        return

    button = message.text

    if button == '🎛️ Panel':
        admin_panel_command(message)
    elif button == '👥 Users':
        users_command(message)
    elif button == '⛔ Blocked':
        blocked_command(message)
    elif button == '📢 Broadcast':
        msg = bot.reply_to(message, \"📢 Send me the message you want to broadcast to all users.\\nSend /cancel to cancel.\", reply_markup=get_back_only_keyboard())
        bot.register_next_step_handler(msg, process_broadcast)
    elif button == '📊 Stats':
        show_stats_from_command(message)
    elif button == '📋 Log':
        show_log_from_command(message)
    elif button == '⛔ Block':
        number_map, display = get_user_number_map()
        if not display:
            bot.reply_to(message, '👥 No users to block', reply_markup=get_users_keyboard())
            return
        msg = bot.reply_to(message, '⛔ Select user number to block:\\n\\n' + display, reply_markup=get_back_only_keyboard())
        bot.register_next_step_handler(msg, process_block_by_number)
    elif button == '✅ Unblock':
        blocked = get_blocked()
        if not blocked:
            bot.reply_to(message, '⛔ No blocked users', reply_markup=get_blocked_keyboard())
            return
        users = get_users()
        display = ''
        for i, user_id in enumerate(blocked, 1):
            info = users.get(user_id, {})
            name = info.get('name', 'Unknown')
            display += str(i) + '. ' + name + ' [' + user_id + ']\\n'
        msg = bot.reply_to(message, '✅ Select user number to unblock:\\n\\n' + display, reply_markup=get_back_only_keyboard())
        bot.register_next_step_handler(msg, process_unblock_by_number)
    elif button == '👤 Info':
        number_map, display = get_user_number_map()
        if not display:
            bot.reply_to(message, '👥 No users', reply_markup=get_users_keyboard())
            return
        msg = bot.reply_to(message, '👤 Select user number for info:\\n\\n' + display, reply_markup=get_back_only_keyboard())
        bot.register_next_step_handler(msg, process_info_by_number)
    elif button == '🔙 Back':
        admin_panel_command(message)


def process_block_by_number(message):
    if not is_admin(message.from_user.id):
        return
    if message.text == '🔙 Back':
        users_command(message)
        return
    number_map, display = get_user_number_map()
    user_id = number_map.get(message.text)
    if not user_id:
        bot.reply_to(message, '❌ Invalid number. Try again.', reply_markup=get_users_keyboard())
        return
    if block_user(user_id):
        bot.reply_to(message, '⛔ User ' + user_id + ' blocked successfully', reply_markup=get_users_keyboard())
    else:
        bot.reply_to(message, '⚠️ User ' + user_id + ' is already blocked', reply_markup=get_users_keyboard())


def process_unblock_by_number(message):
    if not is_admin(message.from_user.id):
        return
    if message.text == '🔙 Back':
        blocked_command(message)
        return
    blocked = get_blocked()
    users = get_users()
    try:
        idx = int(message.text) - 1
        if idx < 0 or idx >= len(blocked):
            bot.reply_to(message, '❌ Invalid number. Try again.', reply_markup=get_blocked_keyboard())
            return
        user_id = blocked[idx]
        if unblock_user(user_id):
            bot.reply_to(message, '✅ User ' + user_id + ' unblocked successfully', reply_markup=get_blocked_keyboard())
        else:
            bot.reply_to(message, '⚠️ User ' + user_id + ' is not blocked', reply_markup=get_blocked_keyboard())
    except Exception:
        bot.reply_to(message, '❌ Invalid number. Try again.', reply_markup=get_blocked_keyboard())


def process_info_by_number(message):
    if not is_admin(message.from_user.id):
        return
    if message.text == '🔙 Back':
        users_command(message)
        return
    number_map, display = get_user_number_map()
    user_id = number_map.get(message.text)
    if not user_id:
        bot.reply_to(message, '❌ Invalid number. Try again.', reply_markup=get_users_keyboard())
        return
    userinfo_by_id(message, user_id)


def userinfo_by_id(message, target_id):
    users = get_users()
    if target_id not in users:
        bot.reply_to(message, '❌ User ' + target_id + ' not found', reply_markup=get_users_keyboard())
        return
    info = users[target_id]
    messages = get_user_messages(target_id)
    is_blocked = target_id in get_blocked()
    text = '👤 User Info\\n'
    text += '──────────────\\n'
    text += '🆔 ID: ' + target_id + '\\n'
    text += '👤 Name: ' + info.get('name', 'Unknown') + '\\n'
    text += '🔗 Username: ' + info.get('username', 'No username') + '\\n'
    text += '📅 First seen: ' + info.get('first_seen', 'Unknown') + '\\n'
    text += '📅 Last seen: ' + info.get('last_seen', 'Unknown') + '\\n'
    text += '💬 Total messages: ' + str(info.get('total_messages', 0)) + '\\n'
    status = '⛔ Blocked' if is_blocked else '✅ Active'
    text += '📌 Status: ' + status + '\\n'
    if messages:
        text += '\\n📝 Last 5 messages:\\n'
        for msg in messages[-5:]:
            role_icon = '👤' if msg['role'] == 'user' else '🤖'
            text += '[' + msg['time'] + '] ' + role_icon + ' ' + msg['content'][:100] + '\\n'
    bot.reply_to(message, text[:4000], reply_markup=get_users_keyboard())"""

if old_handler in content:
    content = content.replace(old_handler, new_handler)
    print('✅ Step 1: Handler updated')
else:
    print('❌ Step 1: Old handler not found')

# Add new keyboard functions before get_main_keyboard
old_func = "def get_main_keyboard():"
new_func = """def get_users_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    markup.add(
        types.KeyboardButton('⛔ Block'),
        types.KeyboardButton('✅ Unblock'),
        types.KeyboardButton('👤 Info'),
        types.KeyboardButton('🔙 Back'),
    )
    return markup


def get_blocked_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton('✅ Unblock'),
        types.KeyboardButton('🔙 Back'),
    )
    return markup


def get_back_only_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton('🔙 Back'))
    return markup


def get_user_number_map():
    users = get_users()
    number_map = {}
    display = ''
    for i, (user_id, info) in enumerate(users.items(), 1):
        number_map[str(i)] = user_id
        name = info.get('name', 'Unknown')
        status = '⛔ Blocked' if user_id in get_blocked() else '✅ Active'
        display += str(i) + '. ' + name + ' [' + user_id + '] ' + status + '\\n'
    return number_map, display


def get_main_keyboard():"""

if old_func in content:
    content = content.replace(old_func, new_func, 1)
    print('✅ Step 2: Keyboard functions added')
else:
    print('❌ Step 2: get_main_keyboard not found')

# Update users_command
old_users = "bot.send_message(message.chat.id, text, reply_markup=get_admin_reply_keyboard())"
if old_users in content:
    content = content.replace(old_users, "bot.send_message(message.chat.id, text, reply_markup=get_users_keyboard())")
    print('✅ Step 3: Users command updated')
else:
    print('❌ Step 3: Users command pattern not found')

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\\n✅ Done!')
