"""Stockage SQLite local."""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
import pandas as pd
from finance_core import transaction_fingerprint

DB_PATH = os.environ.get("FINANCE_DB_PATH", "./data/finance.sqlite3")

def connect(db_path: str | None = None):
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con

def init_db(db_path: str | None = None):
    with connect(db_path) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tx_date TEXT NOT NULL,
          value_date TEXT,
          label TEXT NOT NULL,
          amount REAL NOT NULL,
          account TEXT NOT NULL,
          category TEXT NOT NULL,
          confidence REAL NOT NULL DEFAULT 0.2,
          needs_review INTEGER NOT NULL DEFAULT 1,
          source_file TEXT,
          fingerprint TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        # Migration douce pour les bases créées avec une version précédente.
        columns = {row["name"] for row in con.execute("PRAGMA table_info(transactions)").fetchall()}
        if "value_date" not in columns:
            con.execute("ALTER TABLE transactions ADD COLUMN value_date TEXT")
        con.execute("CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(tx_date)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account)")

def insert_transactions(df: pd.DataFrame, db_path: str | None = None) -> tuple[int,int]:
    if df.empty:
        return 0, 0
    init_db(db_path)
    inserted = skipped = 0
    with connect(db_path) as con:
        for row in df.to_dict("records"):
            fp = transaction_fingerprint(row["date"], row["label"], row["amount"], row["account"])
            cur = con.execute("""
                INSERT OR IGNORE INTO transactions
                (tx_date,value_date,label,amount,account,category,confidence,needs_review,source_file,fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (str(row["date"]), str(row.get("value_date", row["date"])), str(row["label"]), float(row["amount"]), str(row["account"]),
                  str(row.get("category","À vérifier")), float(row.get("confidence",0.2)),
                  int(bool(row.get("needs_review",True))), str(row.get("source_file","")) , fp))
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
    return inserted, skipped

def read_transactions(db_path: str | None = None) -> pd.DataFrame:
    init_db(db_path)
    with connect(db_path) as con:
        rows = con.execute("""
          SELECT id, tx_date AS date, value_date, label, amount, account, category, confidence,
                 needs_review, source_file, fingerprint FROM transactions ORDER BY tx_date
        """).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["needs_review"] = df["needs_review"].astype(bool)
    return df

def update_transaction(tx_id: int, category: str, needs_review: bool, db_path: str | None = None):
    with connect(db_path) as con:
        con.execute("UPDATE transactions SET category=?, needs_review=? WHERE id=?",
                    (category, int(needs_review), int(tx_id)))

def delete_transaction(tx_id: int, db_path: str | None = None):
    with connect(db_path) as con:
        con.execute("DELETE FROM transactions WHERE id=?", (int(tx_id),))
