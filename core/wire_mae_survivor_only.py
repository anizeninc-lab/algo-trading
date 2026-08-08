#!/usr/bin/env python3
"""Follow-up: wires MAE into strategy/survivor.py's close_trade() call site.
wave_extractor.py was already patched successfully in the first run --
do NOT re-run wire_mae_callsites_patch.py, it would try to re-patch
wave_extractor.py and fail since its anchor no longer matches."""
import shutil
import sys
from datetime import datetime

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD = (
    '        _peak_pnl = risk_manager.get_mfe(trade["id"])\n'
    '        risk_manager.clear_watermark(trade["id"])\n'
    '\n'
    '        trade_logger.close_trade(\n'
    '            trade["id"], exit_price, reason,\n'
    '            net_pnl=pnl, gross_pnl=gross_pnl, total_costs=total_costs,\n'
    '            peak_pnl=_peak_pnl,\n'
    '        )'
)
NEW = (
    '        _peak_pnl = risk_manager.get_mfe(trade["id"])\n'
    '        _trough_pnl = risk_manager.get_mae(trade["id"])\n'
    '        risk_manager.clear_watermark(trade["id"])\n'
    '\n'
    '        trade_logger.close_trade(\n'
    '            trade["id"], exit_price, reason,\n'
    '            net_pnl=pnl, gross_pnl=gross_pnl, total_costs=total_costs,\n'
    '            peak_pnl=_peak_pnl,\n'
    '            trough_pnl=_trough_pnl,\n'
    '        )'
)

def main():
    filepath = "strategy/survivor.py"
    content = open(filepath).read()
    count = content.count(OLD)
    if count != 1:
        print(f"!! anchor matched {count} time(s), expected exactly 1 -- SKIPPED")
        sys.exit(1)
    backup = f"{filepath}.bak_maecallsite_{STAMP}"
    shutil.copy2(filepath, backup)
    print(f"Backed up {filepath} -> {backup}")
    open(filepath, "w").write(content.replace(OLD, NEW))
    print(f"Patched {filepath}")

if __name__ == "__main__":
    main()
