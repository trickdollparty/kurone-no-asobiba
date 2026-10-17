"""競馬ポートフォリオ最適化＆リスクヘッジシミュレーター Webアプリケーション
"""
import io
import json
from typing import List, Optional, Dict, Any, Tuple
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from core.models import (
    RaceSummary,
    RaceInfo,
    HorseEntry,
    TicketType,
    BetRole,
    StrategyMode,
    SingleBet,
    SimulationConfig,
    AllocationResult,
)
from core.race_data import (
    get_preset_races,
    fetch_monthly_race_list,
    fetch_race_detail,
    fetch_shutuba_by_url,
    parse_pasted_shutuba_text,
    estimate_composite_odds,
    export_race_to_json,
    import_race_from_json,
    export_bets_to_csv,
    import_bets_from_csv,
)
from core.combinatorics import (
    generate_formation_bets,
    generate_box_bets,
    parse_combination_horses,
)
from core.allocator import allocate_funds
from core.evaluator import (
    evaluate_race_result,
    generate_scenario_matrix,
)
from core.auth import (
    init_auth_db,
    verify_user,
    add_user,
    get_all_users,
    delete_user,
)

# ==============================================================================
# ページ基本設定 & デジタル庁デザインシステムCSS注入
# ==============================================================================

st.set_page_config(
    page_title="kurone no asobiba",
    
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
/* フォントとタイポグラフィ */
html, body, [class*="css"] {
    font-family: 'Noto Sans JP', 'Hiragino Kaku Gothic ProN', 'Meiryo', sans-serif;
    color: #1A1A1C;
    font-variant-numeric: tabular-nums;
}

/* メイン背景とコンテナ */
.main {
    background-color: #FFFFFF;
}

/* デジタル庁カラーヘッダー */
.header-badge {
    background: #0017C1;
    color: #FFFFFF;
    padding: 4px 12px;
    border-radius: 4px;
    font-size: 0.85rem;
    font-weight: 700;
    display: inline-block;
    margin-bottom: 8px;
}

/* アラートスタイル */
.da-alert-error {
    background-color: #FDECEC;
    border-left: 6px solid #EC0000;
    padding: 12px 16px;
    border-radius: 4px;
    color: #9C0000;
    margin: 10px 0;
    font-weight: 500;
}
.da-alert-warning {
    background-color: #FFF9E6;
    border-left: 6px solid #B78F00;
    padding: 12px 16px;
    border-radius: 4px;
    color: #7A5F00;
    margin: 10px 0;
    font-weight: 500;
}
.da-alert-success {
    background-color: #E6F5EC;
    border-left: 6px solid #259D63;
    padding: 12px 16px;
    border-radius: 4px;
    color: #18633F;
    margin: 10px 0;
    font-weight: 500;
}

/* KPIカードスタイル */
.kpi-card {
    background-color: #F8F9FA;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 16px;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.kpi-title {
    font-size: 0.85rem;
    color: #53565A;
    font-weight: 600;
    margin-bottom: 6px;
}
.kpi-value {
    font-size: 1.6rem;
    font-weight: 700;
    color: #0017C1;
    font-variant-numeric: tabular-nums;
}
.kpi-sub {
    font-size: 0.8rem;
    color: #53565A;
    margin-top: 4px;
}

/* 枠番バッジ */
.frame-badge {
    display: inline-block;
    width: 24px;
    height: 24px;
    line-height: 24px;
    text-align: center;
    border-radius: 4px;
    font-weight: bold;
    font-size: 0.85rem;
    margin-right: 6px;
}
.frame-1 { background-color: #FFFFFF; color: #000000; border: 1px solid #CCCCCC; }
.frame-2 { background-color: #222222; color: #FFFFFF; }
.frame-3 { background-color: #EE3333; color: #FFFFFF; }
.frame-4 { background-color: #2277EE; color: #FFFFFF; }
.frame-5 { background-color: #FFCC00; color: #000000; }
.frame-6 { background-color: #22AA55; color: #FFFFFF; }
.frame-7 { background-color: #FF8800; color: #FFFFFF; }
.frame-8 { background-color: #FF88AA; color: #000000; }

/* 役割バッジ */
.role-badge-defense {
    background-color: #E2E8F0;
    color: #1E293B;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
}
.role-badge-offense {
    background-color: #FEE2E2;
    color: #991B1B;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ==============================================================================
# セッションステート初期化
# ==============================================================================

def init_session_state():
    # 認証データベース初期化
    init_auth_db()

    if "is_authenticated" not in st.session_state:
        st.session_state.is_authenticated = False

    if "auth_username" not in st.session_state:
        st.session_state.auth_username = None

    if "simulation_started" not in st.session_state:
        st.session_state.simulation_started = False

    presets = get_preset_races()

    if "monthly_races" not in st.session_state:
        # 初回はプリセットをベースに当月レース一覧を保持（不要な初期通信を防止）
        preset_summaries = [
            RaceSummary(
                race_id=p.race_id,
                date=p.date,
                venue=p.venue,
                race_number=p.race_number,
                race_name=p.race_name,
                grade=p.grade,
                departure_time=p.departure_time,
                detail_url=p.source_url,
            )
            for p in presets
        ]
        st.session_state.monthly_races = preset_summaries
        st.session_state.monthly_races_fetched_at = "2026-10-18 10:00:00 (プリセット)"

    if "loaded_race_details" not in st.session_state:
        # race_idごとのキャッシュ辞書
        st.session_state.loaded_race_details = {p.race_id: p for p in presets}

    if "current_race" not in st.session_state:
        # デフォルトは秋華賞
        st.session_state.current_race = presets[0]

    if "bets" not in st.session_state:
        # 初期の買い目リスト
        st.session_state.bets = []

    if "sim_config" not in st.session_state:
        st.session_state.sim_config = SimulationConfig(
            total_budget=10000,
            strategy_mode=StrategyMode.HYBRID,
            target_recovery_rate=1.0,
            min_bet_unit=100,
            synthetic_odds_threshold=2.0,
            surplus_rule="boost_min_payout",
        )

init_session_state()


# ==============================================================================
# ヘルパー関数: 出馬表DataFrame変換
# ==============================================================================

def entries_to_df(entries: List[HorseEntry]) -> pd.DataFrame:
    data = []
    for e in entries:
        data.append({
            "枠": e.frame_number,
            "馬番": e.horse_number,
            "予想印": e.mark,
            "馬名": e.horse_name,
            "騎手": e.jockey,
            "単勝オッズ": float(e.win_odds),
            "人気": e.popularity,
        })
    return pd.DataFrame(data)


def update_entries_from_df(race: RaceInfo, df: pd.DataFrame):
    entry_dict = {e.horse_number: e for e in race.entries}
    for _, row in df.iterrows():
        num = int(row["馬番"])
        if num in entry_dict:
            entry_dict[num].mark = str(row["予想印"])
            entry_dict[num].win_odds = float(row["単勝オッズ"])


# ==============================================================================
# 【フェーズ1】：レース選択＆出馬表プレビュー画面
# ==============================================================================

def render_phase1():
    st.markdown('<span class="header-badge">フェーズ1</span>', unsafe_allow_html=True)
    st.title("レース選択・出馬表確認")
    st.markdown(
        "シミュレーションの対象となるレースを選択し、出馬表やオッズを確認・調整してください。"
        "選択後、最下部のボタンからシミュレーションへ進みます。"
    )

    tab_source_a, tab_source_b, tab_source_c = st.tabs([
        "タブA：当月の開催レース一覧（推奨）",
        "タブB：URL・レースIDから単発取得",
        "タブC：出馬表テキスト貼り付け取込 / CSV",
    ])

    # --------------------------------------------------------------------------
    # タブA: 当月レース一覧
    # --------------------------------------------------------------------------
    with tab_source_a:
        col_ctrl1, col_ctrl2 = st.columns([3, 1])
        with col_ctrl1:
            st.markdown(
                f"**現在保持しているレース一覧**（最終更新: `{st.session_state.monthly_races_fetched_at}`）"
            )
        with col_ctrl2:
            if st.button("レース一覧を手動更新", use_container_width=True):
                with st.spinner("当月のレース一覧をオンデマンド取得中..."):
                    st.session_state.monthly_races = fetch_monthly_race_list()
                    st.session_state.monthly_races_fetched_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
                st.success("レース一覧を更新しました！")
                st.rerun()

        # フィルタリング
        summaries: List[RaceSummary] = st.session_state.monthly_races
        venues = ["すべて"] + sorted(list(set(s.venue for s in summaries if s.venue)))
        dates = ["すべて"] + sorted(list(set(s.date for s in summaries if s.date)))

        f_col1, f_col2 = st.columns(2)
        with f_col1:
            selected_venue = st.selectbox("開催場で絞り込み", venues)
        with f_col2:
            selected_date = st.selectbox("開催日で絞り込み", dates)

        filtered_summaries = summaries
        if selected_venue != "すべて":
            filtered_summaries = [s for s in filtered_summaries if s.venue == selected_venue]
        if selected_date != "すべて":
            filtered_summaries = [s for s in filtered_summaries if s.date == selected_date]

        # レース選択セレクトボックス
        race_options = {
            f"{s.date} {s.venue}{s.race_number}R {s.race_name} ({s.grade})": s.race_id
            for s in filtered_summaries
        }

        if race_options:
            selected_label = st.selectbox(
                "対象レースを選択してください",
                list(race_options.keys()),
                index=0,
            )
            selected_race_id = race_options[selected_label]

            # 選択されたレースが未読み込みならオンデマンド取得
            if selected_race_id not in st.session_state.loaded_race_details:
                with st.spinner("出馬表とオッズをオンデマンド取得中..."):
                    st.session_state.loaded_race_details[selected_race_id] = fetch_race_detail(selected_race_id)

            st.session_state.current_race = st.session_state.loaded_race_details[selected_race_id]
        else:
            st.info("条件に一致するレースがありません。")

    # --------------------------------------------------------------------------
    # タブB: URL / レースID単発取得
    # --------------------------------------------------------------------------
    with tab_source_b:
        st.markdown("netkeibaなどの出馬表URLまたは12桁のレースIDを入力して単発取得します。")
        input_url = st.text_input(
            "出馬表URLまたは12桁レースID",
            value="https://race.netkeiba.com/race/shutuba.html?race_id=202608050611",
            placeholder="https://race.netkeiba.com/race/shutuba.html?race_id=...",
        )
        if st.button("このレースの詳細を手動取得", use_container_width=True):
            with st.spinner("指定されたレースの出馬表を取得中..."):
                r_info = fetch_shutuba_by_url(input_url)
                st.session_state.loaded_race_details[r_info.race_id] = r_info
                st.session_state.current_race = r_info
            st.success(f"「{r_info.race_name}」の出馬表を読み込みました！")
            st.rerun()

    # --------------------------------------------------------------------------
    # タブC: コピペ貼り付け解析
    # --------------------------------------------------------------------------
    with tab_source_c:
        st.markdown(
            "JRA公式やnetkeibaの出馬表画面をブラウザで全選択してコピーし、下記に貼り付けると自動抽出します。"
        )
        pasted_text = st.text_area(
            "出馬表テキスト貼り付けエリア",
            height=150,
            placeholder="例:\n11R 秋華賞 (G1) 芝2000m\n1 1 ホーエリート 牝3 55.0 北村友一 42.5 12\n1 2 ミアネーロ 牝3 55.0 津村明秀 11.2 5 ...",
        )
        col_c1, col_c2 = st.columns([1, 1])
        with col_c1:
            if st.button("貼り付けテキストを解析して取り込む", use_container_width=True):
                try:
                    parsed_race = parse_pasted_shutuba_text(pasted_text)
                    st.session_state.loaded_race_details[parsed_race.race_id] = parsed_race
                    st.session_state.current_race = parsed_race
                    st.success(f"解析完了: {parsed_race.race_name} ({len(parsed_race.entries)}頭) を取り込みました！")
                    st.rerun()
                except Exception as ex:
                    st.error(f"解析エラー: {ex}")
        with col_c2:
            uploaded_json = st.file_uploader("またはレースJSONファイルを読み込む", type=["json"])
            if uploaded_json is not None:
                try:
                    content = uploaded_json.read().decode("utf-8")
                    imported = import_race_from_json(content)
                    st.session_state.loaded_race_details[imported.race_id] = imported
                    st.session_state.current_race = imported
                    st.success(f"JSON読み込み完了: {imported.race_name}")
                    st.rerun()
                except Exception as ex:
                    st.error(f"JSONインポートエラー: {ex}")

    st.markdown("---")

    # --------------------------------------------------------------------------
    # 選択中レースの出馬表プレビュー＆予想印・オッズ編集 (@st.fragment)
    # --------------------------------------------------------------------------
    render_shutuba_fragment()

    st.markdown("---")

    # --------------------------------------------------------------------------
    # シミュレーション開始ボタン
    # --------------------------------------------------------------------------
    col_start1, col_start2 = st.columns(2)
    with col_start1:
        if st.button(
            "このレースでシミュレーションを開始する",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.simulation_started = True
            st.rerun()
    with col_start2:
        if st.button(
            "サンプル買い目付きでシミュレーションを開始する",
            use_container_width=True,
        ):
            _setup_sample_bets(st.session_state.current_race)
            st.session_state.simulation_started = True
            st.rerun()


@st.fragment
def render_shutuba_fragment():
    """フェーズ1：出馬表プレビュー＆オッズ・印のインライン編集フラグメント"""
    current_race: RaceInfo = st.session_state.current_race

    head_col1, head_col2 = st.columns([3, 1])
    with head_col1:
        st.subheader(
            f"{current_race.venue}{current_race.race_number}R {current_race.race_name} ({current_race.grade})"
        )
        st.caption(
            f"開催日: {current_race.date} | コース: {current_race.surface} {current_race.distance}m | "
            f"馬場: {current_race.track_condition} | 発走: {current_race.departure_time} | "
            f"最終取得: {current_race.fetched_at or '不明'}"
        )
    with head_col2:
        if st.button("この出馬表・オッズを再取得", use_container_width=True):
            with st.spinner("出馬表を再取得中..."):
                if current_race.source_url and current_race.source_url.startswith("http"):
                    refreshed = fetch_shutuba_by_url(current_race.source_url, current_race.race_id)
                else:
                    refreshed = fetch_race_detail(current_race.race_id)
                st.session_state.loaded_race_details[current_race.race_id] = refreshed
                st.session_state.current_race = refreshed
            st.success("最新データに更新しました！")
            st.rerun()

    df_entries = entries_to_df(current_race.entries)

    column_config = {
        "枠": st.column_config.NumberColumn("枠", width="small", disabled=True),
        "馬番": st.column_config.NumberColumn("馬番", width="small", disabled=True),
        "予想印": st.column_config.SelectboxColumn(
            "予想印",
            options=["◎", "○", "▲", "△", "☆", "無"],
            required=True,
            width="small",
        ),
        "馬名": st.column_config.TextColumn("馬名", width="medium", disabled=True),
        "騎手": st.column_config.TextColumn("騎手", width="small", disabled=True),
        "単勝オッズ": st.column_config.NumberColumn(
            "単勝オッズ",
            min_value=1.0,
            max_value=999.9,
            step=0.1,
            format="%.1f倍",
            width="small",
        ),
        "人気": st.column_config.NumberColumn("人気", width="small", disabled=True),
    }

    st.markdown("※予想印や最新の単勝オッズは下表内で直接編集できます。")
    edited_df = st.data_editor(
        df_entries,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        key=f"shutuba_editor_{current_race.race_id}",
    )
    # 反映
    update_entries_from_df(current_race, edited_df)


def _setup_sample_bets(race: RaceInfo):
    """サンプルのハイブリッド買い目を構築"""
    st.session_state.bets = []
    # 予想印または人気上位から自動生成
    # 例: 1番人気・2番人気のワイド・馬連を守り枠、三連複・三連単を攻め枠に
    entries = sorted(race.entries, key=lambda x: x.popularity)
    if len(entries) >= 3:
        h1 = entries[0].horse_number
        h2 = entries[1].horse_number
        h3 = entries[2].horse_number

        # 守り枠: ワイド
        w12_min, w12_max = estimate_composite_odds(TicketType.WIDE, [h1, h2], race.entries)
        w13_min, w13_max = estimate_composite_odds(TicketType.WIDE, [h1, h3], race.entries)
        st.session_state.bets.append(
            SingleBet(
                ticket_type=TicketType.WIDE,
                combination=f"{min(h1, h2)}-{max(h1, h2)}",
                odds_min=w12_min,
                odds_max=w12_max,
                role=BetRole.DEFENSE,
            )
        )
        st.session_state.bets.append(
            SingleBet(
                ticket_type=TicketType.WIDE,
                combination=f"{min(h1, h3)}-{max(h1, h3)}",
                odds_min=w13_min,
                odds_max=w13_max,
                role=BetRole.DEFENSE,
            )
        )

        # 攻め枠: 三連複
        t_min, _ = estimate_composite_odds(TicketType.TRIO, [h1, h2, h3], race.entries)
        sorted_3 = sorted([h1, h2, h3])
        st.session_state.bets.append(
            SingleBet(
                ticket_type=TicketType.TRIO,
                combination=f"{sorted_3[0]}-{sorted_3[1]}-{sorted_3[2]}",
                odds_min=t_min,
                role=BetRole.OFFENSE,
            )
        )


# ==============================================================================
# 【フェーズ2】：ポートフォリオ構築 ＆ シミュレーション画面
# ==============================================================================

def render_phase2():
    current_race: RaceInfo = st.session_state.current_race

    # --------------------------------------------------------------------------
    # サイドバー（基本設定＆データ入出力）
    # --------------------------------------------------------------------------
    with st.sidebar:
        st.markdown(
            f'<div class="header-badge">{current_race.venue}{current_race.race_number}R {current_race.grade}</div>',
            unsafe_allow_html=True,
        )
        st.subheader(f"{current_race.race_name}")
        st.caption(f"{current_race.date} / {current_race.surface} {current_race.distance}m")

        if st.button("別のレースを選び直す", use_container_width=True):
            st.session_state.simulation_started = False
            st.rerun()

        st.markdown("---")
        st.subheader("資金配分パラメータ")

        # 総予算 (数値入力＋スライダー連動、100円単位、デフォルト10,000円)
        st.markdown("**総予算 (円・100円単位)**")
        if "budget_input_val" not in st.session_state:
            st.session_state.budget_input_val = st.session_state.sim_config.total_budget

        def _sync_num_to_slider():
            val = int(st.session_state.sb_num_budget)
            # 100円単位に丸め
            val = max(1000, (val // 100) * 100)
            st.session_state.budget_input_val = val
            st.session_state.sim_config.total_budget = val

        def _sync_slider_to_num():
            val = int(st.session_state.sb_slider_budget)
            st.session_state.budget_input_val = val
            st.session_state.sim_config.total_budget = val

        col_b_num, col_b_slider = st.columns([1, 1])
        with col_b_num:
            st.number_input(
                "予算数値",
                min_value=1000,
                max_value=1000000,
                value=st.session_state.budget_input_val,
                step=100,
                key="sb_num_budget",
                on_change=_sync_num_to_slider,
                label_visibility="collapsed",
            )
        with col_b_slider:
            slider_max = max(50000, st.session_state.budget_input_val)
            st.slider(
                "予算スライダー",
                min_value=1000,
                max_value=slider_max,
                value=st.session_state.budget_input_val,
                step=500,
                key="sb_slider_budget",
                on_change=_sync_slider_to_num,
                label_visibility="collapsed",
            )

        # 戦略モード
        strategy_options = {
            "ハイブリッド戦略 (守り元金回収＋攻め万馬券)": StrategyMode.HYBRID,
            "均等払戻戦略 (傾斜配分・全目均等化)": StrategyMode.EQUAL_PAYOUT,
            "元返し保険戦略 (1点集中ヘッジ＋残金均等)": StrategyMode.INSURANCE,
        }
        current_mode_label = [
            k for k, v in strategy_options.items() if v == st.session_state.sim_config.strategy_mode
        ][0]
        selected_mode_label = st.selectbox(
            "戦略モード",
            list(strategy_options.keys()),
            index=list(strategy_options.keys()).index(current_mode_label),
        )
        st.session_state.sim_config.strategy_mode = strategy_options[selected_mode_label]

        # 守り目標回収率
        recovery_pct = st.slider(
            "守り枠の目標回収率 (%)",
            min_value=50,
            max_value=200,
            value=int(st.session_state.sim_config.target_recovery_rate * 100),
            step=10,
            help="100%に設定すると、守り枠のどれか1点が的中した時点で総投資額全額が回収されます。",
        )
        st.session_state.sim_config.target_recovery_rate = recovery_pct / 100.0

        # 余剰金配分ルール
        surplus_rule_map = {
            "最低払戻の底上げ (boost_min_payout)": "boost_min_payout",
            "最大オッズへの加算 (max_odds)": "max_odds",
        }
        selected_rule_label = st.selectbox(
            "端数・余剰金配分ルール",
            list(surplus_rule_map.keys()),
            index=0 if st.session_state.sim_config.surplus_rule == "boost_min_payout" else 1,
        )
        st.session_state.sim_config.surplus_rule = surplus_rule_map[selected_rule_label]

        # 合成オッズ閾値
        syn_thresh = st.number_input(
            "合成オッズ警告閾値 (倍)",
            min_value=1.1,
            max_value=10.0,
            value=float(st.session_state.sim_config.synthetic_odds_threshold),
            step=0.1,
        )
        st.session_state.sim_config.synthetic_odds_threshold = float(syn_thresh)

        st.markdown("---")
        st.subheader("データ入出力 (CSV / JSON)")

        # エクスポート
        if st.session_state.bets:
            col_exp1, col_exp2 = st.columns(2)
            with col_exp1:
                csv_data = export_bets_to_csv(st.session_state.bets)
                st.download_button(
                    label="買い目CSV",
                    data=csv_data,
                    file_name=f"bets_{current_race.race_id}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with col_exp2:
                portfolio_dump = json.dumps({
                    "race": current_race.to_dict(),
                    "config": st.session_state.sim_config.to_dict(),
                    "bets": [b.to_dict() for b in st.session_state.bets],
                }, ensure_ascii=False, indent=2)
                st.download_button(
                    label="全体JSON",
                    data=portfolio_dump,
                    file_name=f"portfolio_{current_race.race_id}.json",
                    mime="application/json",
                    use_container_width=True,
                )

        # インポート
        uploaded_file = st.file_uploader(
            "買い目データ (CSV / JSON) のインポート",
            type=["csv", "json"],
            key="bets_file_uploader",
        )
        if uploaded_file is not None:
            try:
                content_str = uploaded_file.read().decode("utf-8")
                if uploaded_file.name.endswith(".json"):
                    data = json.loads(content_str)
                    if "bets" in data:
                        new_bets = [SingleBet.from_dict(b) for b in data["bets"]]
                    elif isinstance(data, list):
                        new_bets = [SingleBet.from_dict(b) for b in data]
                    else:
                        raise ValueError("有効な買い目JSONフォーマットではありません。")
                else:  # CSV
                    parsed_bets_data = import_bets_from_csv(content_str)
                    new_bets = [SingleBet.from_dict(b) for b in parsed_bets_data]

                st.session_state.bets = new_bets
                st.success(f"{len(new_bets)}点の買い目を取り込みました！")
                st.rerun()
            except Exception as ex:
                st.error(f"インポートエラー: {ex}")

    # --------------------------------------------------------------------------
    # メイン上部：買い目構築＆オッズ編集フラグメント (@st.fragment)
    # --------------------------------------------------------------------------
    render_bet_management_fragment()

    st.markdown("---")

    # --------------------------------------------------------------------------
    # メイン中部：配分結果・KPI・チャート・シナリオ分析フラグメント (@st.fragment)
    # --------------------------------------------------------------------------
    render_allocation_dashboard_fragment()

    st.markdown("---")

    # --------------------------------------------------------------------------
    # メイン下部：着順シミュレーション＆結果検証パネル (@st.fragment)
    # --------------------------------------------------------------------------
    render_simulation_panel_fragment()


# ==============================================================================
# フラグメント1: 買い目構築＆編集
# ==============================================================================

@st.fragment
def render_bet_management_fragment():
    """フォーメーション展開＆買い目一覧エディタ"""
    current_race: RaceInfo = st.session_state.current_race

    st.markdown('<span class="header-badge">STEP 1</span>', unsafe_allow_html=True)
    st.subheader("買い目の構築・オッズ設定")

    # 出馬表折りたたみ参照
    with st.expander("このレースの出馬表・オッズ・予想印を確認する", expanded=False):
        st.dataframe(entries_to_df(current_race.entries), use_container_width=True, hide_index=True)

    tab_create, tab_edit = st.tabs(["フォーメーション・ボックスで買い目を追加", "買い目一覧・オッズ・役割の編集"])

    # 選択肢用ラベル作成 (例: "1: ホーエリート (42.5倍 / 無)")
    horse_options = {}
    for e in current_race.entries:
        label = f"{e.horse_number}: {e.horse_name} ({e.win_odds:.1f}倍 / {e.mark})"
        horse_options[label] = e.horse_number

    # --------------------------------------------------------------------------
    # 買い目追加タブ
    # --------------------------------------------------------------------------
    with tab_create:
        col_t1, col_t2 = st.columns([1, 1])
        with col_t1:
            ticket_type_label = st.selectbox(
                "券種を選択",
                [t.value for t in TicketType],
                index=4,  # デフォルトはワイド
            )
            ticket_type = TicketType(ticket_type_label)

        with col_t2:
            default_role = st.selectbox(
                "追加する買い目の初期役割",
                ["defense (守り・元金回収)", "offense (攻め・万馬券狙い)"],
                index=0 if ticket_type in (TicketType.WIDE, TicketType.PLACE, TicketType.QUINELLA) else 1,
            )
            bet_role = BetRole.DEFENSE if "defense" in default_role else BetRole.OFFENSE

        # 1着〜3着選択
        st.markdown("**馬番の選択**（複数選択可能）")
        c1, c2, c3 = st.columns(3)
        with c1:
            first_selected = st.multiselect("1頭目 / 1着候補", list(horse_options.keys()))
        with c2:
            second_selected = []
            if ticket_type not in (TicketType.WIN, TicketType.PLACE):
                second_selected = st.multiselect("2頭目 / 2着候補", list(horse_options.keys()))
        with c3:
            third_selected = []
            if ticket_type in (TicketType.TRIO, TicketType.TRIFECTA):
                third_selected = st.multiselect("3頭目 / 3着候補", list(horse_options.keys()))

        h_first = [horse_options[x] for x in first_selected]
        h_second = [horse_options[x] for x in second_selected]
        h_third = [horse_options[x] for x in third_selected]

        col_btn1, col_btn2, col_btn3 = st.columns([2, 2, 2])
        with col_btn1:
            if st.button("フォーメーションを展開して追加", type="primary", use_container_width=True):
                new_combs = generate_formation_bets(ticket_type, h_first, h_second, h_third)
                _append_combinations(new_combs, ticket_type, bet_role, current_race)
                st.success(f"{len(new_combs)}点の買い目を追加しました！")
                st.rerun()

        with col_btn2:
            if ticket_type not in (TicketType.WIN, TicketType.PLACE):
                if st.button("1頭目候補の馬番でボックス展開", use_container_width=True):
                    new_combs = generate_box_bets(ticket_type, h_first)
                    _append_combinations(new_combs, ticket_type, bet_role, current_race)
                    st.success(f"{len(new_combs)}点のボックス買い目を追加しました！")
                    st.rerun()

        with col_btn3:
            if st.button("印（◎○▲△）から推奨買い目を自動追加", use_container_width=True):
                _auto_generate_from_marks(current_race)
                st.success("推奨買い目を自動展開しました！")
                st.rerun()

    # --------------------------------------------------------------------------
    # 買い目編集タブ
    # --------------------------------------------------------------------------
    with tab_edit:
        if not st.session_state.bets:
            st.info("現在買い目が登録されていません。「買い目を追加」タブから買い目を展開してください。")
        else:
            col_ed_ctrl1, col_ed_ctrl2 = st.columns([3, 1])
            with col_ed_ctrl1:
                st.markdown(f"現在 **{len(st.session_state.bets)}点** の買い目が登録されています。")
            with col_ed_ctrl2:
                if st.button("全買い目をクリア", use_container_width=True):
                    st.session_state.bets = []
                    st.rerun()

            # Data Editor用DataFrame
            b_data = []
            for idx, b in enumerate(st.session_state.bets):
                # 馬名注釈
                horses = parse_combination_horses(b.combination)
                h_names = []
                for num in horses:
                    match = next((e.horse_name for e in current_race.entries if e.horse_number == num), str(num))
                    h_names.append(f"{num}:{match}")
                note = " - ".join(h_names)

                b_data.append({
                    "券種": b.ticket_type.value if hasattr(b.ticket_type, "value") else b.ticket_type,
                    "目": b.combination,
                    "出走馬": note,
                    "役割": b.role.value if hasattr(b.role, "value") else b.role,
                    "下限オッズ": float(b.odds_min),
                    "上限オッズ": float(b.odds_max) if b.odds_max is not None else float(b.odds_min),
                })
            df_bets = pd.DataFrame(b_data)

            bet_col_config = {
                "券種": st.column_config.TextColumn("券種", width="small", disabled=True),
                "目": st.column_config.TextColumn("目", width="small", disabled=True),
                "出走馬": st.column_config.TextColumn("出走馬", width="medium", disabled=True),
                "役割": st.column_config.SelectboxColumn(
                    "役割",
                    options=["defense", "offense"],
                    required=True,
                    width="small",
                    help="defense: 元金回収ヘッジ / offense: 万馬券狙い",
                ),
                "下限オッズ": st.column_config.NumberColumn(
                    "想定/下限オッズ",
                    min_value=1.0,
                    max_value=99999.9,
                    step=0.1,
                    format="%.1f",
                    width="small",
                ),
                "上限オッズ": st.column_config.NumberColumn(
                    "上限オッズ(ワイド等)",
                    min_value=1.0,
                    max_value=99999.9,
                    step=0.1,
                    format="%.1f",
                    width="small",
                ),
            }

            edited_bets_df = st.data_editor(
                df_bets,
                column_config=bet_col_config,
                use_container_width=True,
                num_rows="dynamic",
                hide_index=True,
                key="bets_editor_key",
            )

            # 編集の反映
            updated_bets = []
            for _, r in edited_bets_df.iterrows():
                t_val = r["券種"]
                tt = TicketType(t_val) if t_val in [x.value for x in TicketType] else TicketType.WIN
                r_val = BetRole(r["役割"]) if r["役割"] in ["defense", "offense"] else BetRole.OFFENSE
                o_min = float(r["下限オッズ"])
                o_max = float(r["上限オッズ"]) if pd.notnull(r["上限オッズ"]) else o_min
                updated_bets.append(
                    SingleBet(
                        ticket_type=tt,
                        combination=str(r["目"]),
                        odds_min=o_min,
                        odds_max=o_max,
                        role=r_val,
                    )
                )
            st.session_state.bets = updated_bets


def _append_combinations(
    combs: List[str],
    ticket_type: TicketType,
    role: BetRole,
    race: RaceInfo,
):
    """買い目リストに複合オッズを推計して追加（既存重複はスキップ）"""
    existing_keys = {(b.ticket_type, b.combination) for b in st.session_state.bets}
    for c in combs:
        key = (ticket_type, c)
        if key not in existing_keys:
            horses = parse_combination_horses(c)
            o_min, o_max = estimate_composite_odds(ticket_type, horses, race.entries)
            st.session_state.bets.append(
                SingleBet(
                    ticket_type=ticket_type,
                    combination=c,
                    odds_min=o_min,
                    odds_max=o_max,
                    role=role,
                )
            )
            existing_keys.add(key)


def _auto_generate_from_marks(race: RaceInfo):
    """印（◎○▲△☆）から実戦的なハイブリッド買い目を自動生成"""
    mark_horses: Dict[str, List[int]] = {"◎": [], "○": [], "▲": [], "△": [], "☆": []}
    for e in race.entries:
        if e.mark in mark_horses:
            mark_horses[e.mark].append(e.horse_number)

    honmei = mark_horses["◎"][0] if mark_horses["◎"] else (race.entries[0].horse_number if race.entries else None)
    taikou = mark_horses["○"][0] if mark_horses["○"] else (race.entries[1].horse_number if len(race.entries) > 1 else None)
    tanana = mark_horses["▲"][0] if mark_horses["▲"] else (race.entries[2].horse_number if len(race.entries) > 2 else None)
    osae = mark_horses["△"] + mark_horses["☆"]
    if not osae and len(race.entries) > 3:
        osae = [e.horse_number for e in race.entries[3:6]]

    # 1. 守り枠: 本命・対抗のワイド＆馬連
    if honmei and taikou:
        w_min, w_max = estimate_composite_odds(TicketType.WIDE, [honmei, taikou], race.entries)
        q_min, _ = estimate_composite_odds(TicketType.QUINELLA, [honmei, taikou], race.entries)
        w_comb = f"{min(honmei, taikou)}-{max(honmei, taikou)}"
        st.session_state.bets.append(
            SingleBet(ticket_type=TicketType.WIDE, combination=w_comb, odds_min=w_min, odds_max=w_max, role=BetRole.DEFENSE)
        )
        st.session_state.bets.append(
            SingleBet(ticket_type=TicketType.QUINELLA, combination=w_comb, odds_min=q_min, role=BetRole.DEFENSE)
        )

    # 2. 攻め枠: 三連複フォーメーション (本命 - 対抗/単穴 - 相手)
    second_group = [h for h in [taikou, tanana] if h]
    third_group = [h for h in ([taikou, tanana] + osae) if h]
    if honmei and second_group and third_group:
        trio_combs = generate_formation_bets(TicketType.TRIO, [honmei], second_group, third_group)
        _append_combinations(trio_combs, TicketType.TRIO, BetRole.OFFENSE, race)


# ==============================================================================
# フラグメント2: 配分結果・KPI・チャート・シナリオ分析
# ==============================================================================

@st.fragment
def render_allocation_dashboard_fragment():
    """リアルタイム配分計算＆ダッシュボード"""
    st.markdown('<span class="header-badge">STEP 2</span>', unsafe_allow_html=True)
    st.subheader("リアルタイム資金配分・リスク分析ダッシュボード")

    if not st.session_state.bets:
        st.info("買い目を1点以上登録すると、最適な資金配分が自動計算されます。")
        return

    # 計算実行
    alloc_res: AllocationResult = allocate_funds(st.session_state.bets, st.session_state.sim_config)

    # アラートバナー
    if alloc_res.is_bankrupt:
        st.markdown(
            f'<div class="da-alert-error"><strong>[エラー: 予算超過・破綻]</strong> {alloc_res.error_message}</div>',
            unsafe_allow_html=True,
        )
    for warn in alloc_res.warnings:
        st.markdown(
            f'<div class="da-alert-warning"><strong>[警告]</strong> {warn}</div>',
            unsafe_allow_html=True,
        )

    # KPIカード 4列
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-title">総投資金額</div>
                <div class="kpi-value">{alloc_res.total_invested:,}円</div>
                <div class="kpi-sub">予算: {st.session_state.sim_config.total_budget:,}円 (残 {alloc_res.remaining_budget:,}円)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kpi2:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-title">買い目総点数</div>
                <div class="kpi-value">{len(alloc_res.bets)} 点</div>
                <div class="kpi-sub">守り: {sum(1 for b in alloc_res.bets if b.role == BetRole.DEFENSE)} 点 / 攻め: {sum(1 for b in alloc_res.bets if b.role == BetRole.OFFENSE)} 点</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kpi3:
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-title">買い目全体合成オッズ</div>
                <div class="kpi-value">{alloc_res.synthetic_odds:.2f} 倍</div>
                <div class="kpi-sub">目標閾値: {st.session_state.sim_config.synthetic_odds_threshold:.1f}倍以上</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kpi4:
        recovery_pct = (alloc_res.min_defense_payout / alloc_res.total_invested * 100) if alloc_res.total_invested > 0 else 0
        st.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-title">守り的中時 最低回収額</div>
                <div class="kpi-value">{alloc_res.min_defense_payout:,.0f}円</div>
                <div class="kpi-sub">想定回収率: <strong>{recovery_pct:.1f}%</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # 配分結果テーブル
    st.markdown("##### 各買い目の最適配分結果")
    res_rows = []
    for b in alloc_res.bets:
        net_profit_min = b.expected_payout_min - alloc_res.total_invested if b.allocated_amount > 0 else 0
        role_str = "守り" if b.role == BetRole.DEFENSE else "攻め"
        torigami_str = "トリガミ" if b.is_torigami and b.allocated_amount > 0 else ("的中プラス" if b.allocated_amount > 0 else "配分なし")

        odds_str = f"{b.odds_min:.1f}" if b.odds_max is None or b.odds_min == b.odds_max else f"{b.odds_min:.1f}〜{b.odds_max:.1f}"

        res_rows.append({
            "券種": b.ticket_type.value if hasattr(b.ticket_type, "value") else b.ticket_type,
            "買い目": b.combination,
            "役割": role_str,
            "想定オッズ": odds_str,
            "配分金額": f"{b.allocated_amount:,}円",
            "的中時想定払戻金": f"{b.expected_payout_min:,.0f}円" if b.allocated_amount > 0 else "-",
            "純利益": f"{net_profit_min:+,.0f}円" if b.allocated_amount > 0 else "-",
            "判定": torigami_str,
        })
    st.dataframe(pd.DataFrame(res_rows), use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Plotlyチャート & シナリオ別損益マトリクス
    col_chart, col_matrix = st.columns([1, 1])

    with col_chart:
        st.markdown("##### 各買い目 単独的中時の純利益分布")
        labels = [f"{b.ticket_type.value}:{b.combination}" for b in alloc_res.bets]
        profits = [
            (b.expected_payout_min - alloc_res.total_invested) if b.allocated_amount > 0 else 0
            for b in alloc_res.bets
        ]
        colors = [
            "#EC0000" if p < 0 else ("#0017C1" if b.role == BetRole.DEFENSE else "#259D63")
            for p, b in zip(profits, alloc_res.bets)
        ]

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=labels,
                y=profits,
                marker_color=colors,
                text=[f"{p:+,.0f}円" for p in profits],
                textposition="auto",
            )
        )
        # 損益分岐ライン
        fig.add_hline(
            y=0,
            line_dash="dash",
            line_color="#1A1A1C",
            annotation_text="損益分岐ライン (±0円)",
            annotation_position="bottom right",
        )
        fig.update_layout(
            margin=dict(l=20, r=20, t=20, b=50),
            height=320,
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            xaxis=dict(tickangle=-45),
            yaxis=dict(title="純利益 (円)", zeroline=True, zerolinecolor="#CCCCCC"),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_matrix:
        st.markdown("##### 4シナリオ別 損益マトリクス")
        df_scenarios = generate_scenario_matrix(alloc_res.bets)
        st.dataframe(df_scenarios, use_container_width=True, hide_index=True)


# ==============================================================================
# フラグメント3: 着順シミュレーション＆結果検証パネル
# ==============================================================================

@st.fragment
def render_simulation_panel_fragment():
    """仮想確定着順の入力と瞬時結果検証"""
    current_race: RaceInfo = st.session_state.current_race

    st.markdown('<span class="header-badge">STEP 3</span>', unsafe_allow_html=True)
    st.subheader("着順シミュレーション・結果検証")
    st.markdown("仮想の確定着順（1〜3着）を選択し、複数的中や最終的な払戻・回収率を即座にシミュレーションします。")

    if not st.session_state.bets:
        st.info("買い目を登録すると、シミュレーションを実行できます。")
        return

    # 馬番・馬名選択肢
    horse_items = [
        (e.horse_number, f"{e.horse_number}番 {e.horse_name} ({e.win_odds:.1f}倍 / {e.mark})")
        for e in current_race.entries
    ]
    options_dict = {label: num for num, label in horse_items}
    labels_list = list(options_dict.keys())

    c_sim1, c_sim2, c_sim3 = st.columns(3)
    with c_sim1:
        sel_1 = st.selectbox("1着 馬番", labels_list, index=0)
    with c_sim2:
        idx_2 = 1 if len(labels_list) > 1 else 0
        sel_2 = st.selectbox("2着 馬番", labels_list, index=idx_2)
    with c_sim3:
        idx_3 = 2 if len(labels_list) > 2 else 0
        sel_3 = st.selectbox("3着 馬番", labels_list, index=idx_3)

    first = options_dict[sel_1]
    second = options_dict[sel_2]
    third = options_dict[sel_3]

    if len({first, second, third}) < 3:
        st.warning("1着、2着、3着には異なる馬番を選択してください。")
        return

    # シミュレーション評価
    eval_res = evaluate_race_result(st.session_state.bets, first, second, third)

    # 結果カード
    if eval_res.is_hit:
        status_bg = "#E6F5EC" if eval_res.net_profit_min >= 0 else "#FFF9E6"
        status_border = "#259D63" if eval_res.net_profit_min >= 0 else "#B78F00"
        status_text = "的中（プラス収支）" if eval_res.net_profit_min >= 0 else "的中（トリガミ）"

        st.markdown(
            f"""
            <div style="background-color: {status_bg}; border: 2px solid {status_border}; border-radius: 8px; padding: 18px; margin: 15px 0;">
                <h4 style="margin: 0 0 10px 0; color: #1A1A1C;">{status_text}</h4>
                <div style="font-size: 1.1rem; line-height: 1.8;">
                    確定着順: <strong>1着 {first}番 / 2着 {second}番 / 3着 {third}番</strong><br>
                    的中点数: <strong>{len(eval_res.hit_bets)} 点</strong><br>
                    合計投資額: <strong>{eval_res.total_invested:,} 円</strong><br>
                    払戻金合計: <span style="font-size: 1.4rem; font-weight: bold; color: #0017C1;">{eval_res.total_payout_min:,.0f} 円 〜 {eval_res.total_payout_max:,.0f} 円</span><br>
                    純利益: <strong style="color: {'#259D63' if eval_res.net_profit_min >= 0 else '#EC0000'}; font-size: 1.2rem;">
                        {eval_res.net_profit_min:+,.0f} 円 〜 {eval_res.net_profit_max:+,.0f} 円
                    </strong><br>
                    最終回収率: <strong style="font-size: 1.2rem;">{eval_res.recovery_rate_min:.1f}% 〜 {eval_res.recovery_rate_max:.1f}%</strong>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("##### 的中した買い目一覧")
        hit_rows = []
        for h in eval_res.hit_bets:
            hit_rows.append({
                "券種": h.bet.ticket_type.value if hasattr(h.bet.ticket_type, "value") else h.bet.ticket_type,
                "買い目": h.bet.combination,
                "役割": "守り" if h.bet.role == BetRole.DEFENSE else "攻め",
                "購入額": f"{h.bet.allocated_amount:,}円",
                "払戻金": f"{h.payout_min:,.0f}円 〜 {h.payout_max:,.0f}円" if h.payout_min != h.payout_max else f"{h.payout_min:,.0f}円",
                "獲得利益": f"{h.profit_min:+,.0f}円",
            })
        st.dataframe(pd.DataFrame(hit_rows), use_container_width=True, hide_index=True)

    else:
        st.markdown(
            f"""
            <div style="background-color: #FDECEC; border: 2px solid #EC0000; border-radius: 8px; padding: 18px; margin: 15px 0;">
                <h4 style="margin: 0 0 10px 0; color: #EC0000;">不的中 (全ハズレ)</h4>
                確定着順: <strong>1着 {first}番 / 2着 {second}番 / 3着 {third}番</strong><br>
                合計投資額: <strong>{eval_res.total_invested:,} 円</strong><br>
                払戻金: <strong>0 円</strong><br>
                損失額: <strong style="color: #EC0000;">-{eval_res.total_invested:,} 円</strong><br>
                回収率: <strong>0.0%</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ==============================================================================
# ログイン画面 (Private Portal)
# ==============================================================================

def render_login_page():
    """未認証ユーザー向けのプライベートポータル画面
    競馬等のキーワードは一切表示せず、シンプルで洗練されたログイン画面を提供します。
    新規登録機能は管理者ログイン後のみ利用可能なため、ここには表示しません。
    """
    _, col, _ = st.columns([1, 1.8, 1])

    with col:
        st.markdown(
            """
            <div style="text-align: center; margin-top: 50px; margin-bottom: 30px;">
                <h1 style="font-size: 2.2rem; margin-bottom: 8px;">kurone no asobiba</h1>
                <p style="color: #64748B; font-size: 0.95rem;">Private Member Portal & Workspace</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.form("login_form", clear_on_submit=False):
            st.markdown("##### ログイン")
            username = st.text_input("ユーザー名", placeholder="ユーザー名を入力してください")
            password = st.text_input("パスワード", type="password", placeholder="パスワードを入力してください")
            submit = st.form_submit_button("ログイン", use_container_width=True)

            if submit:
                if not username.strip() or not password:
                    st.error("ユーザー名とパスワードを入力してください。")
                elif verify_user(username.strip(), password):
                    st.session_state.is_authenticated = True
                    st.session_state.auth_username = username.strip()
                    st.success("認証に成功しました。読み込み中...")
                    st.rerun()
                else:
                    st.error("ユーザー名またはパスワードが一致しません。")

        st.markdown(
            """
            <div style="text-align: center; margin-top: 40px; color: #94A3B8; font-size: 0.8rem;">
                &copy; 2026 kurone no asobiba. Authorized members only.
            </div>
            """,
            unsafe_allow_html=True,
        )


# ==============================================================================
# ユーザー管理画面 (管理者専用)
# ==============================================================================

def render_user_management_page():
    """管理者用ユーザー管理画面
    ログイン後にのみアクセス可能。登録ユーザー一覧の確認、新規ユーザー追加、ユーザー削除を行います。
    """
    st.markdown(
        """
        <div style="margin-bottom: 24px;">
            <div class="header-badge">ADMINISTRATION</div>
            <h1 style="font-size: 2rem; margin: 0 0 8px 0; color: #1A1A1C;">ユーザー管理</h1>
            <p style="color: #53565A; margin: 0;">アプリを利用可能なユーザーの追加・削除・一覧確認を行います。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([1.2, 1])

    with col1:
        st.markdown("### 登録済みユーザー一覧")
        users = get_all_users()
        if users:
            df_users = pd.DataFrame(users)
            df_users.rename(columns={"id": "ID", "username": "ユーザー名", "created_at": "登録日時"}, inplace=True)
            st.dataframe(df_users, use_container_width=True, hide_index=True)
        else:
            st.info("登録されているユーザーはいません。")

        st.markdown("---")
        st.markdown("### ユーザー削除")
        user_list = [u["username"] for u in users if u["username"] != "kurone" and u["username"] != st.session_state.auth_username]
        if user_list:
            selected_user_to_delete = st.selectbox("削除するユーザーを選択してください", user_list)
            if st.button("このユーザーを削除する", type="secondary"):
                if delete_user(selected_user_to_delete):
                    st.success(f"ユーザー「{selected_user_to_delete}」を削除しました。")
                    st.rerun()
                else:
                    st.error("削除に失敗しました。")
        else:
            st.caption("※削除可能な追加ユーザーは現在いません（管理者 kurone および現在ログイン中のアカウントは保護されています）。")

    with col2:
        st.markdown("### 新規ユーザー追加")
        with st.form("admin_add_user_form", clear_on_submit=True):
            new_user = st.text_input("新規ユーザー名", placeholder="半角英数字を推奨")
            new_pw = st.text_input("パスワード", type="password", placeholder="4文字以上")
            new_pw_confirm = st.text_input("パスワード（確認用）", type="password", placeholder="もう一度入力してください")
            submit_add = st.form_submit_button("ユーザーを登録する", use_container_width=True)

            if submit_add:
                if not new_user.strip() or not new_pw:
                    st.error("ユーザー名とパスワードを入力してください。")
                elif new_pw != new_pw_confirm:
                    st.error("確認用パスワードが一致しません。")
                elif len(new_pw) < 4:
                    st.error("パスワードは4文字以上で設定してください。")
                else:
                    success = add_user(new_user.strip(), new_pw)
                    if success:
                        st.success(f"新規ユーザー「{new_user.strip()}」を登録しました。")
                        st.rerun()
                    else:
                        st.error("このユーザー名は既に使用されています。別のユーザー名をお試しください。")


# ==============================================================================
# メインエントリポイント
# ==============================================================================

def main():
    if not st.session_state.is_authenticated:
        render_login_page()
        return

    # 認証済みの場合、サイドバー上部にユーザー情報、ログアウトボタン、ナビゲーションを設置
    with st.sidebar:
        st.markdown("### kurone no asobiba")
        col_u, col_b = st.columns([1.2, 1])
        with col_u:
            st.caption(f"ユーザー: **{st.session_state.auth_username}**")
        with col_b:
            if st.button("ログアウト", use_container_width=True):
                st.session_state.is_authenticated = False
                st.session_state.auth_username = None
                st.rerun()

        st.divider()

        app_mode = st.radio(
            "メニュー選択",
            ["シミュレーター", "ユーザー管理"],
            index=0,
            key="app_navigation_mode"
        )
        st.divider()

    if app_mode == "ユーザー管理":
        render_user_management_page()
    else:
        if not st.session_state.simulation_started:
            render_phase1()
        else:
            render_phase2()


if __name__ == "__main__":
    main()


