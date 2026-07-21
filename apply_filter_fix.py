path = "/home/ubuntu/trading-algo/core/strategy_filter.py"

with open(path, "r") as f:
    content = f.read()

old_survivor = '''                if regime in (REGIME_RANGE, REGIME_REVERSAL_WATCH):
                    logger.info(
                        f"[strategy_filter] survivor: session plan says BLOCKED "
                        f"but live regime={regime} — OVERRIDING to ALLOW"
                    )'''

new_survivor = '''                if regime in (REGIME_RANGE, REGIME_REVERSAL_WATCH):
                    override_key = f"survivor_override_{regime}"
                    if self._last_state_logged.get("survivor_override") != override_key:
                        logger.info(
                            f"[strategy_filter] survivor: session plan says BLOCKED "
                            f"but live regime={regime} — OVERRIDING to ALLOW"
                        )
                        self._last_state_logged["survivor_override"] = override_key'''

old_wave = '''                if regime in (REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR):
                    logger.info(
                        f"[strategy_filter] wave_extractor: session plan says BLOCKED "
                        f"but live regime={regime} — OVERRIDING to ALLOW"
                    )'''

new_wave = '''                if regime in (REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR):
                    override_key = f"wave_extractor_override_{regime}"
                    if self._last_state_logged.get("wave_extractor_override") != override_key:
                        logger.info(
                            f"[strategy_filter] wave_extractor: session plan says BLOCKED "
                            f"but live regime={regime} — OVERRIDING to ALLOW"
                        )
                        self._last_state_logged["wave_extractor_override"] = override_key'''

assert content.count(old_survivor) == 1, f"survivor block match count: {content.count(old_survivor)}"
assert content.count(old_wave) == 1, f"wave block match count: {content.count(old_wave)}"

content = content.replace(old_survivor, new_survivor)
content = content.replace(old_wave, new_wave)

with open(path, "w") as f:
    f.write(content)

print("Both replacements applied successfully.")
