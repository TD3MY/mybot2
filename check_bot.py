from dotenv import dotenv_values
import requests
cfg = dotenv_values()
t = cfg.get("BOT_TOKEN", "")
print("TOKEN_FOUND" if t else "NO_TOKEN")
try:
    r = requests.get(f"https://api.telegram.org/bot{t}/getMe", timeout=10)
    print("STATUS", r.status_code)
    print(r.text[:200])
except Exception as e:
    print("ERROR", e)
