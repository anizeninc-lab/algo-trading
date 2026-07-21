path = "/home/ubuntu/trading-algo/core/alerting.py"

with open(path, "r") as f:
    content = f.read()

old_line = 'TELEGRAM_TOKEN   = "8830735820:AAFxqjPAtRHcgK3Zcwotfm9szFGONYWXYpE"'
new_line = 'TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")'

assert content.count(old_line) == 1, f"match count: {content.count(old_line)}"
content = content.replace(old_line, new_line)

with open(path, "w") as f:
    f.write(content)

print("alerting.py token fix applied successfully.")
