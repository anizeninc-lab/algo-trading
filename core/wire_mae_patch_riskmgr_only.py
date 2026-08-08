#!/usr/bin/env python3
"""Follow-up patch: wires MAE into core/risk_manager.py ONLY.
core/trade_log.py was already patched successfully in the first run --
do NOT re-run the original wire_mae_patch.py, its trade_log.py anchors
will no longer match the now-patched file.

Same safety pattern: validates all 3 anchors match exactly once before
writing anything. Backs up the file first."""
import shutil
import sys
from datetime import datetime

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

EDITS = [
    (
        '        self._mfe_watermarks: dict[str, float] = {}\n'
        '        self._daily_pnl:      dict[str, float] = {}',

        '        self._mfe_watermarks: dict[str, float] = {}\n'
        '        # Unconditional MAE (max adverse excursion) tracker -- mirrors\n'
        '        # _mfe_watermarks but records the trough (most negative net P&L)\n'
        '        # reached at any point in the trade. Read-only bookkeeping for\n'
        '        # future stop-loss tuning; never affects any exit decision. See\n'
        '        # get_mae().\n'
        '        self._mae_watermarks: dict[str, float] = {}\n'
        '        self._daily_pnl:      dict[str, float] = {}'
    ),
    (
        '        if trade_id:\n'
        '            prev_mfe = self._mfe_watermarks.get(trade_id, float("-inf"))\n'
        '            if net_pnl > prev_mfe:\n'
        '                self._mfe_watermarks[trade_id] = net_pnl',

        '        if trade_id:\n'
        '            prev_mfe = self._mfe_watermarks.get(trade_id, float("-inf"))\n'
        '            if net_pnl > prev_mfe:\n'
        '                self._mfe_watermarks[trade_id] = net_pnl\n'
        '            prev_mae = self._mae_watermarks.get(trade_id, float("inf"))\n'
        '            if net_pnl < prev_mae:\n'
        '                self._mae_watermarks[trade_id] = net_pnl'
    ),
    (
        '    def get_mfe(self, trade_id: str) -> float:\n'
        '        """Returns the peak net P&L (Rs) ever reached by this trade, tracked\n'
        '        unconditionally on every tick regardless of trailing-armed state.\n'
        '        Call this at trade close, before clear_watermark(), to persist the\n'
        '        real MFE for future trailing-threshold tuning. Returns 0.0 if the\n'
        '        trade_id was never seen (e.g. trailing wasn\'t called for it)."""\n'
        '        mfe = self._mfe_watermarks.get(trade_id, float("-inf"))\n'
        '        return mfe if mfe != float("-inf") else 0.0\n'
        '\n'
        '    def clear_watermark(self, trade_id: str) -> None:\n'
        '        """Call on trade close to clean up watermark state. Read get_mfe()\n'
        '        BEFORE calling this, since it also clears the MFE tracker."""\n'
        '        self._pnl_watermarks.pop(trade_id, None)\n'
        '        self._mfe_watermarks.pop(trade_id, None)',

        '    def get_mfe(self, trade_id: str) -> float:\n'
        '        """Returns the peak net P&L (Rs) ever reached by this trade, tracked\n'
        '        unconditionally on every tick regardless of trailing-armed state.\n'
        '        Call this at trade close, before clear_watermark(), to persist the\n'
        '        real MFE for future trailing-threshold tuning. Returns 0.0 if the\n'
        '        trade_id was never seen (e.g. trailing wasn\'t called for it)."""\n'
        '        mfe = self._mfe_watermarks.get(trade_id, float("-inf"))\n'
        '        return mfe if mfe != float("-inf") else 0.0\n'
        '\n'
        '    def get_mae(self, trade_id: str) -> float:\n'
        '        """Returns the trough net P&L (Rs) ever reached by this trade (most\n'
        '        negative point), tracked unconditionally on every tick regardless of\n'
        '        trailing-armed state. Call this at trade close, before\n'
        '        clear_watermark(), to persist the real MAE for future stop-loss\n'
        '        tuning. Returns 0.0 if the trade_id was never seen."""\n'
        '        mae = self._mae_watermarks.get(trade_id, float("inf"))\n'
        '        return mae if mae != float("inf") else 0.0\n'
        '\n'
        '    def clear_watermark(self, trade_id: str) -> None:\n'
        '        """Call on trade close to clean up watermark state. Read get_mfe()\n'
        '        and get_mae() BEFORE calling this, since it also clears both\n'
        '        trackers."""\n'
        '        self._pnl_watermarks.pop(trade_id, None)\n'
        '        self._mfe_watermarks.pop(trade_id, None)\n'
        '        self._mae_watermarks.pop(trade_id, None)'
    ),
]


def main():
    filepath = "core/risk_manager.py"
    try:
        content = open(filepath).read()
    except FileNotFoundError:
        print(f"!! {filepath} not found -- run this from ~/trading-algo")
        sys.exit(1)

    problems = []
    for i, (old, new) in enumerate(EDITS):
        count = content.count(old)
        if count != 1:
            problems.append(f"   edit #{i+1}: anchor matched {count} time(s), expected exactly 1")
    if problems:
        print(f"!! {filepath}: validation failed -- SKIPPED, no changes written.")
        for p in problems:
            print(p)
        sys.exit(1)

    backup = f"{filepath}.bak_maewire_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, backup)
    print(f"Backed up {filepath} -> {backup}")

    new_content = content
    for old, new in EDITS:
        new_content = new_content.replace(old, new, 1)
    with open(filepath, "w") as f:
        f.write(new_content)
    print(f"Patched {filepath} (3 edits applied)")
    print("\nNow run:")
    print("python3 -c \"import ast; ast.parse(open('core/risk_manager.py').read()); print('syntax OK')\"")


if __name__ == "__main__":
    main()
