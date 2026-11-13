import sqlite3
from pathlib import Path

db_path = Path("data/inventory.db")
if not db_path.exists():
    print("Database does not exist.")
    exit(0)

conn = sqlite3.connect(str(db_path))
conn.execute("PRAGMA foreign_keys = OFF")

# Find all user tables
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
all_tables = [r[0] for r in cursor.fetchall() if not r[0].startswith("sqlite_")]

# Clear transactional tables
for t in all_tables:
    try:
        conn.execute(f"DELETE FROM {t}")
        conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{t}'")
        print(f"Cleared table: {t}")
    except Exception as e:
        print(f"Error clearing {t}: {e}")

conn.commit()
conn.execute("VACUUM")
conn.close()

# Verify
conn = sqlite3.connect(str(db_path))
counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in all_tables}
conn.close()
print("Verification row counts:", counts)
print("Database completely reset and ready for clean import!")
