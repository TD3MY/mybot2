# Separate module: checks whether a user is a member of a required Telegram channel.
# Keeping this in its own file so it doesn't get mixed into the main bot logic.

# Set this to your channel's username, including the @ symbol.
# Example: "@my_channel"
CHANNEL_USERNAME = "@Apex_m4"


def is_channel_member(bot, user_id):
    """
    Returns True if the given user_id is currently a member of CHANNEL_USERNAME.
    Returns False if they're not a member, or if the check fails for any reason
    (e.g. the bot isn't an admin in the channel yet).
    """
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return False

