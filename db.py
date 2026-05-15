import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "grocery.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                address_id INTEGER REFERENCES addresses(id),
                ue_store TEXT,
                status TEXT DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER REFERENCES orders(id),
                name TEXT NOT NULL,
                quantity TEXT NOT NULL,
                url TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)


def get_active_order(conn):
    row = conn.execute("SELECT * FROM orders WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO orders DEFAULT VALUES")
        conn.commit()
        row = conn.execute("SELECT * FROM orders WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
    return row


def get_items(conn, order_id):
    return conn.execute(
        "SELECT * FROM items WHERE order_id=? ORDER BY created_at ASC", (order_id,)
    ).fetchall()


def add_item(conn, order_id, name, quantity, url):
    conn.execute(
        "INSERT INTO items (order_id, name, quantity, url) VALUES (?,?,?,?)",
        (order_id, name, quantity, url or None),
    )
    conn.commit()


def update_item(conn, item_id, name, quantity, url):
    conn.execute(
        "UPDATE items SET name=?, quantity=?, url=? WHERE id=?",
        (name, quantity, url or None, item_id),
    )
    conn.commit()


def delete_item(conn, item_id):
    conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.commit()


def get_addresses(conn):
    return conn.execute("SELECT * FROM addresses ORDER BY id ASC").fetchall()


def add_address(conn, name, address):
    conn.execute("INSERT INTO addresses (name, address) VALUES (?,?)", (name, address))
    conn.commit()


def delete_address(conn, addr_id):
    conn.execute("DELETE FROM addresses WHERE id=?", (addr_id,))
    conn.commit()


def close_order(conn, order_id, address_id, ue_store):
    conn.execute(
        "UPDATE orders SET status='placed', address_id=?, ue_store=? WHERE id=?",
        (address_id, ue_store, order_id),
    )
    conn.execute("INSERT INTO orders DEFAULT VALUES")
    conn.commit()


def get_order_history(conn):
    rows = conn.execute(
        "SELECT o.*, a.name as addr_name FROM orders o "
        "LEFT JOIN addresses a ON o.address_id=a.id "
        "WHERE o.status != 'open' ORDER BY o.created_at DESC"
    ).fetchall()
    result = []
    for row in rows:
        items = get_items(conn, row["id"])
        result.append({"order": dict(row), "items": [dict(i) for i in items]})
    return result
