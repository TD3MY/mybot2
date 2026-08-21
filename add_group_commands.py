from pathlib import Path

p = Path("main.py")
text = p.read_text(encoding="utf-8")

anchor = "@bot.callback_query_handler(func=lambda call: call.data == \"panel_broadcast_cancel\")"

admin_group_commands = '''@bot.message_handler(commands=['approve_group'])
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

'''

if anchor in text and 'def approve_group' not in text:
    text = text.replace(anchor, admin_group_commands + anchor)
    p.write_text(text, encoding="utf-8")
    print("✅ admin group commands added")
else:
    print("❌ anchor not found or already exists")
