"""リアルスクレイピング機能のテスト"""
import pytest
from core.race_data import fetch_odds_api, fetch_monthly_race_list, fetch_race_detail
from core.models import RaceInfo, HorseEntry, RaceSummary


def test_fetch_monthly_race_list():
    """当月・直近レース一覧が取得できることの検証"""
    races = fetch_monthly_race_list()
    assert isinstance(races, list)
    assert len(races) > 0
    first = races[0]
    assert isinstance(first, RaceSummary)
    assert first.race_id
    assert first.date
    assert first.venue
    assert 1 <= first.race_number <= 12


def test_fetch_race_detail_real():
    """実レースの出馬表およびオッズがリアルタイム取得できることの検証"""
    # 2024菊花賞
    race_id = "202408050611"
    detail = fetch_race_detail(race_id)
    assert isinstance(detail, RaceInfo)
    assert detail.race_name == "菊花賞"
    assert len(detail.entries) >= 15
    for e in detail.entries:
        assert isinstance(e, HorseEntry)
        assert 1 <= e.frame_number <= 8
        assert 1 <= e.horse_number <= 18
        assert len(e.horse_name) > 0
        assert len(e.jockey) > 0
        assert e.win_odds > 0


def test_fetch_odds_api():
    """オッズAPIから単勝オッズと人気順の辞書が返ることの検証"""
    race_id = "202408050611"
    odds_map, pop_map = fetch_odds_api(race_id)
    assert isinstance(odds_map, dict)
    assert isinstance(pop_map, dict)
    assert len(odds_map) > 0
    # 1番のオッズが数値として取得されていること
    assert 1 in odds_map
    assert odds_map[1] > 0.0
