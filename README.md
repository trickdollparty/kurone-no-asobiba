# kurone no asobiba

A personal analytical workspace and portfolio simulator built with Streamlit.

---

## Overview
「kurone no asobiba」は、ポートフォリオ配分シミュレーションとリスクヘッジ検証を行うプライベートWebアプリケーションです。

* **認証機能:** SQLite + ソルト付きSHA-256によるセキュアなメンバーアクセス管理
* **ポートフォリオ最適化:** NumPy＋独自アルゴリズムによるリバランス＆配分シミュレーション
* **シナリオ分析:** マトリクス解析と複数的中判定によるリアルタイムリスク評価
* **UI/UX:** デジタル庁デザインシステム準拠のクリーンで高コントラストなUI

---

## 起動手順

### 1. 依存ライブラリのインストール
```bash
pip install -r requirements.txt
```

### 2. アプリケーションの起動
```bash
streamlit run app.py
```

ブラウザで `http://localhost:8501` にアクセスし、ログインしてご利用ください。

* **初期ログインアカウント:**
  * ユーザー名: `kurone`
  * パスワード: `asobiba2026`
  ※ログイン後、ユーザー管理画面から新規アカウント追加が可能です。

---

## ディレクトリ構成

```text
.
├── app.py                  # Streamlit UI、認証ゲート、画面遷移
├── core/
│   ├── auth.py             # ユーザー認証・SQLiteマスタ管理・ハッシュ化
│   ├── models.py           # データモデル定義
│   ├── allocator.py        # 資金配分・最適化エンジン
│   ├── evaluator.py        # シナリオ評価・損益マトリクス
│   ├── combinatorics.py    # 組み合わせ展開ロジック
│   └── race_data.py        # データ取得・パーサー
├── requirements.txt        # 依存パッケージ
└── README.md
```
