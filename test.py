import sqlite3

conn = sqlite3.connect("databases/meanings.db")
row = conn.execute(
    "SELECT root, short_override, short_meaning, short_confident "
    "FROM roots WHERE root = ?",
    ("أخر",),
).fetchone()
print(row)
conn.close()