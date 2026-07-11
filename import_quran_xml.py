import sqlite3

conn = sqlite3.connect("databases/quran.db")
cur = conn.cursor()

print("Tables:")
for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print("-", row[0])

print("\nVerse count:")
print(cur.execute("SELECT COUNT(*) FROM verses").fetchone()[0])

conn.close()