import sqlite3

DB_FILE = "data/messages.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT,
            from_number TEXT,
            to_number TEXT,
            body TEXT,
            timestamp TEXT,
            status TEXT DEFAULT 'unknown',
            error_message TEXT DEFAULT NULL,
            cost TEXT DEFAULT NULL
        )
    ''')
    conn.commit()
    conn.close()

def save_message(direction, from_number, to_number, body, timestamp, status="unknown", error_message=None, cost=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO messages (direction, from_number, to_number, body, timestamp, status, error_message, cost)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (direction, from_number, to_number, body, timestamp, status, error_message, cost))
    conn.commit()
    conn.close()

def get_all_messages():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM messages ORDER BY id DESC')
    messages = cursor.fetchall()
    conn.close()
    return messages
