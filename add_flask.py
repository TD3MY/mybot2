from pathlib import Path

p = Path("main.py")
text = p.read_text(encoding="utf-8")

old = 'if __name__ == "__main__":'
new = '''from flask import Flask
app = Flask(__name__)

@app.route("/")
def index():
    return "ok"

if __name__ == "__main__":'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("✅ done")
else:
    print("❌ pattern not found")
