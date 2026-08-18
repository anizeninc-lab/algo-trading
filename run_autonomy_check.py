"""
run_autonomy_check.py

Standalone entry point for Step 11 (confidence-gated limited autonomy).
Meant to run ONCE DAILY, after run_eod_report.py, via cron -- never
continuously, never during live market hours. Checks every 'proposed'
candidate against core/confidence_gate.py's criteria and auto-applies any
that qualify (in practice today: only survivor.pe_enabled/ce_enabled going
True->False, with 10+ distinct real trading days of gate evidence, 80%+
favourable -- see that module's docstring for the full, deliberately
narrow scope).

Does NOTHING unless configs/autonomy_config.json has "enabled": true.
Ships/defaults to false.

TO SCHEDULE (on your server, not through this chat):
    crontab -e
Add, a few minutes after your EOD report line:
    40 15 * * 1-5 cd /home/ubuntu/trading-algo && /usr/bin/python3 run_autonomy_check.py >> logs/autonomy_check.log 2>&1
"""
import logging
logging.basicConfig(level=logging.INFO)

# Same reasoning as run_eod_report.py -- standalone scripts never go
# through main.py's load_dotenv() call, so core/alerting.py's
# TELEGRAM_TOKEN would read empty without this, silently breaking any
# alert this script tries to send.
from dotenv import load_dotenv
load_dotenv(override=True)

from core.confidence_gate import check_all_proposed, _autonomy_enabled, AUTONOMY_CONFIG_PATH

if __name__ == "__main__":
    if not _autonomy_enabled():
        print(f"[run_autonomy_check] Autonomy is OFF ({AUTONOMY_CONFIG_PATH} missing or "
              f"enabled=false). Nothing to do. This is the default and expected state "
              f"until you explicitly opt in.")
    else:
        print("[run_autonomy_check] Autonomy is ON. Checking all proposed candidates...")
        results = check_all_proposed()
        if not results:
            print("[run_autonomy_check] No proposed candidates to check.")
        for r in results:
            status = "APPLIED" if r["applied"] else "skipped"
            print(f"  [{r['candidate_id']}] {status}: {r['reason']}")
