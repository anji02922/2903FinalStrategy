import sqlite3
import os
from datetime import datetime


class TradeStore:
    def __init__(self, db_path="data/trades.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._create_tables()

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                strategy TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                size REAL,
                pnl REAL,
                fees REAL,
                net_pnl REAL,
                duration_minutes REAL,
                exit_reason TEXT,
                regime TEXT
            )
        """)
        self.conn.commit()

    def log_trade(self, trade: dict):
        self.conn.execute("""
            INSERT INTO trades (timestamp, strategy, side, entry_price, exit_price, size, pnl, fees, net_pnl, duration_minutes, exit_reason, regime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get("timestamp", datetime.utcnow().isoformat()),
            trade.get("strategy", ""),
            trade.get("side", ""),
            trade.get("entry_price", 0),
            trade.get("exit_price", 0),
            trade.get("size", 0),
            trade.get("pnl", 0),
            trade.get("fees", 0),
            trade.get("net_pnl", 0),
            trade.get("duration_minutes", 0),
            trade.get("exit_reason", ""),
            trade.get("regime", ""),
        ))
        self.conn.commit()

    def get_all_trades(self):
        cur = self.conn.execute("SELECT * FROM trades ORDER BY id")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self):
        self.conn.close()
