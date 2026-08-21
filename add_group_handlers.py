from pathlib import Path

p = Path("main.py")
text = p.read_text(encoding="utf-8")

marker = 'def cb_panel_groups(call):'

handlers = '''@bot.callback_query_handler(func=lambda call: call.data.startswith("group_approve:"))
def cb_group_approve(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":")[1]
    set_group_status(chat_id, "approved")
    pending = load_json(DATA_DIR / "group_requests.json", [])
    pending = [r for r in pending if str(r.get("chat_id")) != str(chat_id)]
    save_json(DATA_DIR / "group_requests.json", pending)
    try:
        bot.send_message(int(chat_id), "✅ This group has been approved by the admin.")
    except:
        pass
    cb_panel_groups(call)


@bot.callback_query_handler(func=lambda call: call.data.startswith("group_reject:"))
def cb_group_reject(call):
    if not _require_admin(call):
        return
    chat_id = call.data.split(":")[1]
    set_group_status(chat_id, "rejected")
    pending = load_json(DATA_DIR / "group_requests.json", [])
    pending = [r for r in pending if str(r.get("chat_id")) != str(chat_id)]
    save_json(DATA_DIR / "group_requests.json", pending)
    cb_panel_groups(call)


'''

if marker in text and 'def cb_group_approve' not in text:
    text = text.replace(marker, handlers + marker)
    p.write_text(text, encoding="utf-8")
    print("✅ handlers added")
else:
    print("❌ marker not found or already exists")
