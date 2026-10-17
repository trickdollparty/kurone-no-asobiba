"""コアモジュールの単体テスト
"""
import pytest
import numpy as np

from core.models import (
    TicketType,
    BetRole,
    StrategyMode,
    SingleBet,
    SimulationConfig,
    HorseEntry,
    RaceInfo,
)
from core.combinatorics import (
    generate_formation_bets,
    generate_box_bets,
    format_combination,
)
from core.allocator import (
    allocate_funds,
    calculate_synthetic_odds,
)
from core.evaluator import (
    evaluate_race_result,
    check_bet_hit,
    generate_scenario_matrix,
)
from core.race_data import (
    decode_html_bytes,
    estimate_composite_odds,
    parse_pasted_shutuba_text,
    get_preset_races,
)


def test_combinatorics_formation():
    """フォーメーション展開のテスト"""
    # 馬連: 順不同・昇順ソート重複排除
    quinella = generate_formation_bets(TicketType.QUINELLA, first=[1, 2], second=[2, 3])
    # 1-2, 1-3, 2-3
    assert sorted(quinella) == ["1-2", "1-3", "2-3"]

    # 馬単: 順序保持
    exacta = generate_formation_bets(TicketType.EXACTA, first=[1, 2], second=[2, 3])
    # 1-2, 1-3, 2-3 (2-2は除外)
    assert sorted(exacta) == ["1-2", "1-3", "2-3"]

    # 三連単: 順序あり
    trifecta = generate_formation_bets(TicketType.TRIFECTA, first=[1], second=[2, 3], third=[3, 4])
    # 1-2-3, 1-2-4, 1-3-4
    assert sorted(trifecta) == ["1-2-3", "1-2-4", "1-3-4"]

    # 三連複: 順不同
    trio = generate_formation_bets(TicketType.TRIO, first=[1], second=[2, 3], third=[3, 4])
    # 1-2-3, 1-2-4, 1-3-4
    assert sorted(trio) == ["1-2-3", "1-2-4", "1-3-4"]


def test_box_bets():
    """ボックス展開のテスト"""
    box = generate_box_bets(TicketType.QUINELLA, [1, 2, 3])
    assert sorted(box) == ["1-2", "1-3", "2-3"]


def test_allocator_strict_budget_constraint():
    """100円整数制約＆総予算を1円たりとも超過しないことの検証"""
    bets = [
        SingleBet(ticket_type=TicketType.WIDE, combination="1-2", odds_min=2.5, role=BetRole.DEFENSE),
        SingleBet(ticket_type=TicketType.WIDE, combination="1-3", odds_min=3.8, role=BetRole.DEFENSE),
        SingleBet(ticket_type=TicketType.TRIFECTA, combination="1-2-3", odds_min=45.0, role=BetRole.OFFENSE),
        SingleBet(ticket_type=TicketType.TRIFECTA, combination="1-3-2", odds_min=62.0, role=BetRole.OFFENSE),
    ]

    for budget in [3000, 5000, 10000, 15300, 20000]:
        config = SimulationConfig(total_budget=budget, strategy_mode=StrategyMode.HYBRID)
        result = allocate_funds(bets, config)

        assert result.total_invested <= budget, f"予算超過: {result.total_invested} > {budget}"
        assert result.remaining_budget == budget - result.total_invested
        # 全買い目が100の倍数であること
        for b in result.bets:
            assert b.allocated_amount % 100 == 0
            assert b.allocated_amount >= 0


def test_allocator_equal_payout():
    """均等払戻（傾斜配分）戦略のテスト"""
    bets = [
        SingleBet(ticket_type=TicketType.QUINELLA, combination="1-2", odds_min=3.0),
        SingleBet(ticket_type=TicketType.QUINELLA, combination="1-3", odds_min=6.0),
        SingleBet(ticket_type=TicketType.QUINELLA, combination="1-4", odds_min=12.0),
    ]
    config = SimulationConfig(total_budget=10000, strategy_mode=StrategyMode.EQUAL_PAYOUT)
    result = allocate_funds(bets, config)

    assert result.total_invested <= 10000
    # 低オッズほど配分額が多くなっていること
    assert result.bets[0].allocated_amount >= result.bets[1].allocated_amount >= result.bets[2].allocated_amount


def test_allocator_bankruptcy_handling():
    """守り枠の必要資金が総予算を超えた場合の破綻判定とサジェスト"""
    # 予算1000円に対して、オッズ2.0倍の守り枠で100%回収には500円必要
    # 守り枠が3点あると 500 * 3 = 1500円 必要となり1000円を超過
    bets = [
        SingleBet(ticket_type=TicketType.WIDE, combination="1-2", odds_min=2.0, role=BetRole.DEFENSE),
        SingleBet(ticket_type=TicketType.WIDE, combination="1-3", odds_min=2.0, role=BetRole.DEFENSE),
        SingleBet(ticket_type=TicketType.WIDE, combination="2-3", odds_min=2.0, role=BetRole.DEFENSE),
    ]
    config = SimulationConfig(total_budget=1000, strategy_mode=StrategyMode.HYBRID, target_recovery_rate=1.0)
    result = allocate_funds(bets, config)

    assert result.is_bankrupt is True
    assert result.error_message is not None
    assert "超過しています" in result.error_message
    assert "引き下げる必要があります" in result.error_message


def test_evaluator_multiple_hits_wide():
    """ワイドの最大3点的中および複数券種の同時的中判定"""
    bets = [
        # ワイド 1-2, 1-3, 2-3 (着順 1-2-3 なら3点すべて的中)
        SingleBet(ticket_type=TicketType.WIDE, combination="1-2", odds_min=3.0, odds_max=4.0, allocated_amount=1000),
        SingleBet(ticket_type=TicketType.WIDE, combination="1-3", odds_min=5.0, odds_max=6.0, allocated_amount=1000),
        SingleBet(ticket_type=TicketType.WIDE, combination="2-3", odds_min=7.0, odds_max=8.0, allocated_amount=1000),
        # 馬連 1-2 (的中)
        SingleBet(ticket_type=TicketType.QUINELLA, combination="1-2", odds_min=10.0, allocated_amount=1000),
        # 三連複 1-2-3 (的中)
        SingleBet(ticket_type=TicketType.TRIO, combination="1-2-3", odds_min=30.0, allocated_amount=1000),
        # 不的中の買い目
        SingleBet(ticket_type=TicketType.QUINELLA, combination="1-4", odds_min=15.0, allocated_amount=1000),
    ]

    res = evaluate_race_result(bets, first=1, second=2, third=3)

    assert res.is_hit is True
    assert len(res.hit_bets) == 5  # ワイド3点 + 馬連1点 + 三連複1点
    # 払戻金合計 (最小):
    # ワイド1-2: 3000, ワイド1-3: 5000, ワイド2-3: 7000, 馬連1-2: 10000, 三連複: 30000 -> 合計55000
    expected_min = 1000 * 3.0 + 1000 * 5.0 + 1000 * 7.0 + 1000 * 10.0 + 1000 * 30.0
    assert res.total_payout_min == expected_min
    assert res.total_invested == 6000
    assert res.net_profit_min == expected_min - 6000


def test_scenario_matrix():
    """シナリオ別損益マトリクス生成の検証"""
    bets = [
        SingleBet(ticket_type=TicketType.WIDE, combination="1-2", odds_min=3.0, role=BetRole.DEFENSE, allocated_amount=1000, expected_payout_min=3000, expected_payout_max=4000),
        SingleBet(ticket_type=TicketType.TRIFECTA, combination="1-2-3", odds_min=50.0, role=BetRole.OFFENSE, allocated_amount=500, expected_payout_min=25000, expected_payout_max=25000),
    ]
    df = generate_scenario_matrix(bets)
    assert len(df) == 4
    assert "🛡️ 守りのみ的中" in df["シナリオ"].values
    assert "⚔️ 攻めのみ的中" in df["シナリオ"].values
    assert "🎉 ダブル的中（守り＋攻め）" in df["シナリオ"].values
    assert "💀 全ハズレ" in df["シナリオ"].values


def test_html_decode_charset():
    """文字コード自動判別デコードのテスト"""
    # EUC-JP テキスト
    sample_text = "秋華賞 京都 芝2000m"
    euc_bytes = sample_text.encode("euc_jp")
    decoded_euc = decode_html_bytes(euc_bytes)
    assert "秋華賞" in decoded_euc

    # UTF-8 テキスト
    utf8_bytes = sample_text.encode("utf-8")
    decoded_utf8 = decode_html_bytes(utf8_bytes)
    assert "秋華賞" in decoded_utf8


def test_paste_parser():
    """コピペスマートパーサーのテスト"""
    raw_text = """
    11R 第29回 秋華賞 (G1) 芝2000m
    1 1 ホーエリート 牝3 55.0 北村友一 42.5 12
    1 2 ミアネーロ 牝3 55.0 津村明秀 11.2 5
    2 3 クイーンズウォーク 牝3 55.0 川田将雅 4.1 2
    3 5 チェルヴィニア 牝3 55.0 C.ルメール 2.3 1
    """
    race_info = parse_pasted_shutuba_text(raw_text)
    assert race_info.race_name == "第29回" or "秋華賞" in race_info.race_name
    assert len(race_info.entries) == 4
    assert race_info.entries[0].horse_name == "ホーエリート"
    assert race_info.entries[0].win_odds == 42.5
    assert race_info.entries[3].horse_name == "チェルヴィニア"
    assert race_info.entries[3].win_odds == 2.3


def test_composite_odds_estimation():
    """ハービル公式による複合オッズ推計のテスト"""
    presets = get_preset_races()
    race = presets[0]  # 秋華賞

    # 馬連オッズ推計: 5番(2.3倍) - 3番(4.1倍)
    odds_quinella, _ = estimate_composite_odds(TicketType.QUINELLA, [5, 3], race.entries)
    assert odds_quinella > 1.0
    # ワイドオッズ推計: 5番 - 3番 (オッズ幅 min, max)
    w_min, w_max = estimate_composite_odds(TicketType.WIDE, [5, 3], race.entries)
    assert w_min is not None and w_max is not None
    assert w_min <= w_max
    assert w_min > 1.0
