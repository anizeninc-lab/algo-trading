"""
run_eod_report.py

Step 10 of the staged self-learning plan: automated EOD report at 3:30 PM
IST (after NSE market close).

Calls the EXISTING core/eod_report.py generate_eod_report() function
directly -- no new report logic was needed. It already reads persisted
state from disk: trade_log.db for trades, and configs/risk_state.json for
risk state (deployed capital, daily P&L, halted status, trade counts).
risk_manager's singleton automatically loads that JSON file in its own
__init__ via _load_state(), and it gets saved after every real trade (see
core/risk_manager.py's _save_state() call sites) -- so a FRESH process
reading it, like this standalone script, sees accurate data as of the
last trade, not stale/default values.

WHY A SEPARATE SCHEDULED SCRIPT, GIVEN generate_eod_report() ALREADY GETS
CALLED SOMEWHERE: currently it only fires as a side effect inside
strategy/saviour_combo.py's stop(), and only for reason in (AUTO_STOP,
MAX_DAILY_LOSS). That means if the bot stops any other way on a given day
-- manual stop, a crash, a PM2 restart -- no report gets written at all
for that day. A cron-scheduled standalone run decouples "did today get a
report" from "how did the bot happen to stop today", and always produces
one at a predictable time.

KNOWN LIMITATION: the report's 'combined_pnl' field will show 0.0 when
run this way, vs. a real value when triggered from inside the live
process. That field needs a live SaviourCombo instance's
_get_combined_pnl(), which a standalone script doesn't have access to.
Every other field (overall summary, per-strategy P&L, risk state) is
identical either way, since both paths read the same persisted files.

TO SCHEDULE (run once, on your server, NOT through this chat):
    crontab -e
Then add this line (adjust the path to match your actual install):
    30 15 * * 1-5 cd /home/ubuntu/trading-algo && /usr/bin/python3 run_eod_report.py >> logs/eod_report.log 2>&1
(1-5 = Monday through Friday only; 30 15 = 15:30 = 3:30 PM)
"""
import logging
logging.basicConfig(level=logging.INFO)

# main.py calls this at its own startup, but standalone scripts like this
# one never go through main.py -- without this, core/alerting.py's
# TELEGRAM_TOKEN reads as empty (os.getenv default), producing a request
# to a malformed URL and a confusing 404 from Telegram's API. Found this
# by testing the token/chat_id directly (both were actually fine) and
# noticing this script never loads .env at all.
from dotenv import load_dotenv
load_dotenv(override=True)

from core.eod_report import generate_eod_report

if __name__ == "__main__":
    path = generate_eod_report(reason="SCHEDULED_EOD")
    if path:
        print(f"[run_eod_report] EOD report written: {path}")
        print(f"[run_eod_report] Also check: {path.with_suffix('.md')}")
    else:
        print("[run_eod_report] EOD report generation FAILED -- see logs above")
