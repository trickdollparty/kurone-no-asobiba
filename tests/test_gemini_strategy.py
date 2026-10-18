"""Gemini Argon推奨戦略（元本回収多重網＋10万円超え三連単配分）のテスト"""
import pytest
from core.race_data import get_preset_races
from core.models import SimulationConfig, TicketType, BetRole
from core.optimizer import optimize_bets_from_marks, build_gemini_argon_portfolio
from core.allocator import allocate_funds


def test_gemini_argon_portfolio_generation():
    """Gemini Argonスタイルの買い目が守り枠と攻め枠（三連単）を含んで生成されることの検証"""
    preset = get_preset_races()[0]
    config = SimulationConfig(total_budget=10000)
    bets, proposal, iters = optimize_bets_from_marks(preset, config, max_iterations=100)

    assert len(bets) >= 5
    defense_bets = [b for b in bets if b.role == BetRole.DEFENSE]
    offense_bets = [b for b in bets if b.role == BetRole.OFFENSE]

    assert len(defense_bets) >= 2
    assert len(offense_bets) >= 2

    # 攻め枠に三連単が含まれていること
    trifecta_bets = [b for b in offense_bets if b.ticket_type == TicketType.TRIFECTA]
    assert len(trifecta_bets) >= 1


def test_100k_trifecta_allocation():
    """購入する三連単のうち少なくとも1つの払い戻しが10万円を超えることの検証"""
    preset = get_preset_races()[0]
    config = SimulationConfig(total_budget=10000)
    bets, proposal, iters = optimize_bets_from_marks(preset, config, max_iterations=100)

    # 資金配分の実行
    res = allocate_funds(bets, config)
    assert res.total_invested <= config.total_budget
    assert not res.is_bankrupt

    # 守り枠の各買い目が的中時に総予算（10,000円）以上を回収できること
    for b in res.bets:
        if b.role == BetRole.DEFENSE and b.allocated_amount > 0:
            assert b.allocated_amount * b.odds_min >= config.total_budget

    # 攻め枠の三連単のうち少なくとも1点が10万円以上の払戻金になること
    trifectas = [b for b in res.bets if b.ticket_type == TicketType.TRIFECTA and b.allocated_amount > 0]
    if trifectas:
        payouts = [b.allocated_amount * b.odds_min for b in trifectas]
        assert any(p >= 100000.0 for p in payouts)
