path = "/home/ubuntu/trading-algo/core/trade_log.py"

with open(path, "r") as f:
    content = f.read()

old_block = '''            if "client_order_id" not in columns:
                try:
                    conn.execute("ALTER TABLE trades ADD COLUMN client_order_id TEXT UNIQUE;")
                    logger.info("Database Migration applied: Added unique client_order_id index to trades table.")
                except Exception as e:
                    logger.error(f"Migration error while adding client_order_id: {e}")'''

new_block = '''            if "client_order_id" not in columns:
                try:
                    # SQLite does not support UNIQUE directly in ALTER TABLE ADD COLUMN,
                    # so add the plain column first, then enforce uniqueness via an index.
                    conn.execute("ALTER TABLE trades ADD COLUMN client_order_id TEXT;")
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_client_order_id "
                        "ON trades(client_order_id);"
                    )
                    logger.info("Database Migration applied: Added unique client_order_id index to trades table.")
                except Exception as e:
                    logger.error(f"Migration error while adding client_order_id: {e}")'''

assert content.count(old_block) == 1, f"match count: {content.count(old_block)}"
content = content.replace(old_block, new_block)

with open(path, "w") as f:
    f.write(content)

print("trade_log.py migration fix applied successfully.")
