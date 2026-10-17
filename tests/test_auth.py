import pytest
from core.auth import init_auth_db, verify_user, add_user, hash_password

def test_auth_workflow(tmp_path):
    test_db = str(tmp_path / "test_users.db")
    init_auth_db(test_db)
    
    # デフォルトユーザーの検証
    assert verify_user("kurone", "asobiba2026", db_path=test_db) is True
    assert verify_user("kurone", "wrong_password", db_path=test_db) is False
    assert verify_user("unknown", "asobiba2026", db_path=test_db) is False
    
    # 新規ユーザー作成
    assert add_user("tester", "mypassword123", db_path=test_db) is True
    assert verify_user("tester", "mypassword123", db_path=test_db) is True
    assert verify_user("tester", "wrongpass", db_path=test_db) is False
    
    # 重複ユーザー作成は失敗する
    assert add_user("tester", "anotherpassword", db_path=test_db) is False

def test_hash_password_salting():
    h1, s1 = hash_password("secret123")
    h2, s2 = hash_password("secret123")
    assert s1 != s2
    assert h1 != h2
    
    # 同じソルトなら同じハッシュ
    h3, _ = hash_password("secret123", salt=s1)
    assert h1 == h3
