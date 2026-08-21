from pathlib import Path

p = Path("main.py")
text = p.read_text(encoding="utf-8")

handler = '''
@bot.callback_query_handler(func=lambda call: call.data == "panel_groups")
def cb_panel_groups(call):
    if not _require_admin(call):
        return
    pending = load_json(DATA_DIR / "group_requests.json", [])
    groups = load_groups()
    text = "👥 Group Management\\n──────────────\\n"
    if pending:
        text += "\\n📥 Pending Requests:\\n"
        for i, req in enumerate(pending, 1):
            text += f"{i}. {req.get('title','?')} [{req.get('chat_id','')}]\\n"
    text += "\\n✅ Approved Groups:\\n"
    if groups:
        for cid, info in groups.items():
            title = info.get("title", cid)
            status = info.get("status", "?")
            policy = info.get("policy", "kick")
            text += f"• {title} [{cid}]\\n  Status: {status}, Policy: {policy}\\n"
    else:
        text += "None\\n"
    markup = types.InlineKeyboardMarkup()
    if pending:
        markup.add(types.InlineKeyboardButton("✅ Approve First", callback_data="group_approve_first"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="panel_main"))
    _edit_or_send(call.message.chat.id, call.message.message_id, text, markup)
    bot.answer_callback_query(call.id)
'''

anchor = '@bot.callback_query_handler(func=lambda call: call.data == "panel_main")'

if anchor in text and 'panel_groups' not in text:
    text = text.replace(anchor, handler + "\n\n" + anchor)
    p.write_text(text, encoding="utf-8")
    print("✅ panel_groups handler added")
else:
    print("❌ already exists or anchor not found")
