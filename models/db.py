import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "instance" / "app.db"

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name  TEXT NOT NULL,
            email      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            workout_date TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            exercise TEXT NOT NULL,
            sets INTEGER NOT NULL,
            reps INTEGER NOT NULL,
            duration_minutes INTEGER NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES workout_sessions(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS diet_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            food_name TEXT NOT NULL,
            grams REAL NOT NULL,
            calories REAL,
            protein REAL,
            carbs REAL,
            fat REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # NEW: Quotes table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_text TEXT NOT NULL,
            author TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default quotes if table is empty
    existing = conn.execute("SELECT COUNT(*) as count FROM quotes").fetchone()
    if existing["count"] == 0:
        default_quotes = [
            ("The only bad workout is the one that didn't happen.", "Unknown"),
            ("Small steps every day lead to big results.", "Unknown"),
            ("Your body can do it. It's your mind you need to convince.", "Unknown"),
            ("Fitness is not about being better than someone else. It's about being better than you used to be.", "Khloe Kardashian"),
            ("The pain you feel today will be the strength you feel tomorrow.", "Arnold Schwarzenegger"),
            ("Don't limit your challenges. Challenge your limits.", "Unknown"),
            ("The only way to define your limits is by going beyond them.", "Arthur Clarke"),
            ("Success is what comes after you stop making excuses.", "Luis Galarza"),
            ("The hard days are the best because that's when champions are made.", "Gabby Douglas"),
            ("You don't have to be great to start, but you have to start to be great.", "Zig Ziglar"),
            ("Motivation is what gets you started. Habit is what keeps you going.", "Jim Ryun"),
            ("The body achieves what the mind believes.", "Napoleon Hill"),
            ("Strive for progress, not perfection.", "Unknown"),
            ("Wake up with determination. Go to bed with satisfaction.", "Unknown"),
            ("It never gets easier. You just get stronger.", "Unknown"),
        ]
        conn.executemany(
            "INSERT INTO quotes (quote_text, author) VALUES (?, ?)",
            default_quotes
        )

    conn.commit()
    conn.close()