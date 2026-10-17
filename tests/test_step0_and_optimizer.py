"""STEP 0 コース分析・有利馬診断および最大100回最適化ループの単体テスト
"""
import pytest
from core.models import RaceInfo, HorseEntry, SimulationConfig, StrategyMode
from core.race_data import get_preset_races
from core.course_analyzer import analyze_course, diagnose_advantageous_horses
from core.optimizer import optimize_bets_from_marks


def test_analyze_course_known():
    """登録済みコースの特性取得テスト"""
    kyoto = analyze_course("京都", "芝", 2000, "良")
    assert "内回り" in kyoto["layout"]
    assert "淀の坂" in kyoto["description"]
    assert "内枠" in kyoto["advantage_frame"]

    tokyo = analyze_course("東京", "芝", 2400, "重")
    assert "525.9m" in tokyo["layout"]
    assert "馬場注意: 重" in tokyo["description"]


def test_analyze_course_fallback():
    """未登録コースの動的生成フォールバックテスト"""
    sapporo = analyze_course("札幌", "ダート", 1700, "稍重")
    assert "札幌" in sapporo["layout"]
    assert "先行" in sapporo["advantage_style"]
    assert "馬場注意: 稍重" in sapporo["description"]


def test_diagnose_advantageous_horses():
    """有利馬自動診断テスト"""
    preset = get_preset_races()[0]  # 秋華賞
    diag = diagnose_advantageous_horses(preset)

    assert diag["honmei"] is not None
    assert diag["honmei"]["entry"] is not None
    assert len(diag["honmei"]["reason"]) > 10

    assert diag["tactical"] is not None
    assert diag["tactical"]["entry"] is not None

    assert diag["upset"] is not None
    assert diag["upset"]["entry"] is not None


def test_optimizer_zero_warnings():
    """通常条件における警告ゼロ買い目選定テスト"""
    preset = get_preset_races()[0]
    config = SimulationConfig(
        total_budget=10000,
        strategy_mode=StrategyMode.HYBRID,
        synthetic_odds_threshold=4.0,
    )
    bets, proposal, iters = optimize_bets_from_marks(preset, config, max_iterations=100)

    assert len(bets) > 0
    assert iters <= 100
    # 通常条件では警告ゼロで即完了するため、proposalはNone
    assert proposal is None


def test_optimizer_fallback_proposal():
    """極端に厳しい条件で100回試行した場合の改善提案生成テスト"""
    preset = get_preset_races()[0]
    config = SimulationConfig(
        total_budget=1000,
        strategy_mode=StrategyMode.HYBRID,
        synthetic_odds_threshold=18.0,
    )
    bets, proposal, iters = optimize_bets_from_marks(preset, config, max_iterations=100)

    assert len(bets) > 0
    assert iters == 100
    assert proposal is not None
    assert "additional_budget" in proposal
    assert "suggested_threshold" in proposal
    assert proposal["additional_budget"] >= 1000
    assert proposal["suggested_threshold"] > 0
