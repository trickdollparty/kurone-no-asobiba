"""認証＆ユーザーマスタ管理モジュール (SQLite)
"""
import sqlite3
import hashlib
import secrets
import os
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "users.db")


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """SQLiteデータベース接続を取得"""
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db(db_path: Optional[str] = None):
    """ユーザーマスタテーブルを作成し、初期ユーザーを登録"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # ユーザーマスタテーブル
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # デフォルトユーザー（kurone / asobiba2026）が存在しなければ登録
    cursor.execute("SELECT id FROM users WHERE username = ?", ("kurone",))
    if not cursor.fetchone():
        add_user("kurone", "asobiba2026", db_path=db_path)

    conn.close()


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """パスワードをソルト付きSHA-256でハッシュ化"""
    if not salt:
        salt = secrets.token_hex(16)
    salted = (salt + password).encode("utf-8")
    pwd_hash = hashlib.sha256(salted).hexdigest()
    return pwd_hash, salt


def add_user(username: str, password: str, db_path: Optional[str] = None) -> bool:
    """新規ユーザーをデータベースに登録"""
    if not username or not password:
        return False

    pwd_hash, salt = hash_password(password)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO users (username, password_hash, salt, created_at)
            VALUES (?, ?, ?, ?)
        """, (username, pwd_hash, salt, now_str))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def verify_user(username: str, password: str, db_path: Optional[str] = None) -> bool:
    """入力されたユーザー名とパスワードをデータベースと照合"""
    if not username or not password:
        return False

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash, salt FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return False

    saved_hash = row["password_hash"]
    salt = row["salt"]
    computed_hash, _ = hash_password(password, salt)

    return secrets.compare_digest(saved_hash, computed_hash)


def get_all_users(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """登録されている全ユーザー情報を取得（ハッシュ・ソルトは除外）"""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, created_at FROM users ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row["id"], "username": row["username"], "created_at": row["created_at"]} for row in rows]


def delete_user(username: str, db_path: Optional[str] = None) -> bool:
    """指定したユーザーを削除（初期管理者 kurone は保護）"""
    if username == "kurone":
        return False
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
