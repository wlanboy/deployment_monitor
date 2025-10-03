import sqlite3

def init_db():
    conn = sqlite3.connect("deployment.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY,
            playbook TEXT,
            start_time TEXT,
            end_time TEXT,
            duration INTEGER,
            status INTEGER,
            retries INTEGER
        )
    """)
    conn.commit()
    conn.close()

def log_deployment(playbook, start, end, duration, status, retries):
    conn = sqlite3.connect("deployment.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO deployments (playbook, start_time, end_time, duration, status, retries)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (playbook, start, end, duration, status, retries))
    conn.commit()
    conn.close()
