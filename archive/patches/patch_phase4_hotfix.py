def patch_file(path, old, new, label):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    count = content.count(old)
    if count == 0:
        print(f'  [SKIP/FAIL] {label}: old string not found in {path}. Already patched, or file differs -- check manually.')
        return
    if count > 1:
        print(f'  [WARN] {label}: found {count} times in {path}, expected 1.')
    content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  [OK] {label}: applied to {path}')

old_import = '''    from core.alerting import send_telegram, LEVEL_CRITICAL, LEVEL_INFO'''
new_import = '''    from core.alerting import send_telegram, LEVEL_CRITICAL, LEVEL_PROFIT'''

old_alert = '''        send_telegram(
            "🔴 <b>BOT NOT TRADING</b>\\n"
            "Upstox login failed — token invalid or expired.\\n"
            "Reply /token &lt;code&gt; to fix. Retrying login every 2 min until it succeeds.",
            LEVEL_CRITICAL,
        )'''
new_alert = '''        send_telegram(
            "*BOT NOT TRADING*\\n"
            "Upstox login failed — token invalid or expired.\\n"
            "Reply /token <code> to fix. Retrying login every 2 min until it succeeds.",
            LEVEL_CRITICAL,
        )'''

old_success = '''        send_telegram("✅ Upstox login succeeded — proceeding with normal startup.", LEVEL_INFO)'''
new_success = '''        send_telegram("Upstox login succeeded — proceeding with normal startup.", LEVEL_PROFIT)'''

print("Patching main.py (Telegram Markdown formatting hotfix)...")
patch_file('main.py', old_import, new_import, 'import LEVEL_PROFIT instead of LEVEL_INFO')
patch_file('main.py', old_alert, new_alert, 'fix HTML->Markdown in failure alert')
patch_file('main.py', old_success, new_success, 'fix success alert formatting')

print()
print("Done. Now run: python3 -m py_compile main.py && echo SYNTAX OK")
