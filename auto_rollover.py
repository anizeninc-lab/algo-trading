import json
import logging
from pathlib import Path

from core.auto_config import auto_select_symbols, auto_select_banknifty_symbols, fetch_instruments, auto_select_banknifty_symbols, fetch_instruments

logger = logging.getLogger(__name__)

def perform_rollover():
    """
    Fetches the automated symbol configurations and updates
    all JSON strategy config files in the configs/ directory.
    This effectively rolls over the traded symbols whenever called.
    """
    logger.info("Starting Auto Rollover check...")
    
    try:
        result = auto_select_symbols()
    except Exception as e:
        logger.error(f"Auto config execution failed: {e}")
        return

    # Also fetch BankNifty symbols (reuse instruments already downloaded)
    try:
        bn_result = auto_select_banknifty_symbols()
        if bn_result:
            result.update(bn_result)
    except Exception as e:
        logger.warning(f"BankNifty auto config failed (non-fatal): {e}")

    # Also fetch BankNifty symbols (reuse instruments already downloaded)
    try:
        bn_result = auto_select_banknifty_symbols()
        if bn_result:
            result.update(bn_result)
    except Exception as e:
        logger.warning(f"BankNifty auto config failed (non-fatal): {e}")
        
    if not result:
        logger.warning("Auto config returned empty. Perhaps Upstox token is missing. Skipping rollover.")
        return

    new_option_symbol = result.get("option_symbol")
    new_symbol_initials = result.get("symbol_initials")

    if not new_option_symbol or not new_symbol_initials:
        logger.warning("Missing required symbols from auto_config result. Skipping rollover.")
        return
        
    config_dir = Path("configs")
    if not config_dir.exists():
        logger.warning(f"Configs directory {config_dir} not found.")
        return
        
    updated_files = 0
    for config_file in config_dir.glob("*.json"):
        try:
            with open(config_file, "r") as f:
                data = json.load(f)
            
            changes = False
            
            # Update option_symbol
            if "option_symbol" in data and data["option_symbol"] != new_option_symbol:
                logger.info(f"[{config_file.name}] option_symbol: '{data['option_symbol']}' -> '{new_option_symbol}'")
                data["option_symbol"] = new_option_symbol
                changes = True
                
            # Update symbol_initials
            if "symbol_initials" in data and data["symbol_initials"] != new_symbol_initials:
                logger.info(f"[{config_file.name}] symbol_initials: '{data['symbol_initials']}' -> '{new_symbol_initials}'")
                data["symbol_initials"] = new_symbol_initials
                changes = True
                
            if changes:
                with open(config_file, "w") as f:
                    json.dump(data, f, indent=4)
                logger.info(f"Successfully updated {config_file.name}")
                updated_files += 1
                
        except Exception as e:
            logger.error(f"Failed to process {config_file.name}: {e}")
            
    if updated_files > 0:
        logger.info(f"Auto Rollover complete. Updated {updated_files} configuration file(s).")
    else:
        logger.info("Auto Rollover complete. Configurations are already up to date.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv()
    perform_rollover()
