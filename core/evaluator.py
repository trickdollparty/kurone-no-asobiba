"""着順シミュレーション＆結果検証モジュール
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

from .models import SingleBet, TicketType, BetRole
from .combinatorics import parse_combination_horses


@dataclass
class HitBetDetail:
    """的中した買い目の詳細"""
    bet: SingleBet
    payout_min: float
    payout_max: float
    profit_min: float
    profit_max: float


@dataclass
class RaceEvaluationResult:
    """確定着順に対する評価結果"""
    first: int
    second: int
    third: int
    total_invested: int
    hit_bets: List[HitBetDetail] = field(default_factory=list)
    total_payout_min: float = 0.0
    total_payout_max: float = 0.0
    net_profit_min: float = 0.0
    net_profit_max: float = 0.0
    recovery_rate_min: float = 0.0
    recovery_rate_max: float = 0.0
    is_hit: bool = False


def check_bet_hit(
    ticket_type: TicketType,
    combination: str,
    first: int,
    second: int,
    third: int,
) -> bool:
    """個別買い目の的中判定

    Args:
        ticket_type: 券種
        combination: 買い目 ('1', '1-2', '1-2-3' など)
        first: 1着馬番
        second: 2着馬番
        third: 3着馬番
    """
    horses = parse_combination_horses(combination)
    if not horses:
        return False

    if ticket_type == TicketType.WIN:
        return horses[0] == first

    elif ticket_type == TicketType.PLACE:
        # 3着以内（出走頭数による7頭以下2着払いは通常考慮されるが、ここでは標準3着以内）
        return horses[0] in (first, second, third)

    elif ticket_type == TicketType.QUINELLA:
        # 馬連: 1着2着の順不同
        return sorted(horses[:2]) == sorted([first, second])

    elif ticket_type == TicketType.EXACTA:
        # 馬単: 1着-2着の着順一致
        return len(horses) >= 2 and horses[0] == first and horses[1] == second

    elif ticket_type == TicketType.WIDE:
        # ワイド: 1-2着, 1-3着, 2-3着 のいずれか
        pair = sorted(horses[:2])
        wide_pairs = [
            sorted([first, second]),
            sorted([first, third]),
            sorted([second, third]),
        ]
        return pair in wide_pairs

    elif ticket_type == TicketType.TRIO:
        # 三連複: 1着2着3着の順不同
        return sorted(horses[:3]) == sorted([first, second, third])

    elif ticket_type == TicketType.TRIFECTA:
        # 三連単: 1着-2着-3着の一致
        return len(horses) >= 3 and horses[0] == first and horses[1] == second and horses[2] == third

    return False


def evaluate_race_result(
    bets: List[SingleBet],
    first: int,
    second: int,
    third: int,
) -> RaceEvaluationResult:
    """指定された確定着順における全買い目の的中・払戻・回収率計算

    複数的中（ワイド3点、複勝3点、馬連＋ワイド等）を完全に合算評価
    """
    total_invested = sum(b.allocated_amount for b in bets)
    hit_details: List[HitBetDetail] = []
    total_payout_min = 0.0
    total_payout_max = 0.0

    for b in bets:
        if b.allocated_amount <= 0:
            continue
        if check_bet_hit(b.ticket_type, b.combination, first, second, third):
            max_odds = b.odds_max if b.odds_max is not None else b.odds_min
            p_min = round(b.allocated_amount * b.odds_min, 1)
            p_max = round(b.allocated_amount * max_odds, 1)
            prof_min = p_min - b.allocated_amount
            prof_max = p_max - b.allocated_amount

            hit_details.append(
                HitBetDetail(
                    bet=b,
                    payout_min=p_min,
                    payout_max=p_max,
                    profit_min=prof_min,
                    profit_max=prof_max,
                )
            )
            total_payout_min += p_min
            total_payout_max += p_max

    is_hit = len(hit_details) > 0
    net_profit_min = total_payout_min - total_invested
    net_profit_max = total_payout_max - total_invested
    recovery_rate_min = (total_payout_min / total_invested * 100.0) if total_invested > 0 else 0.0
    recovery_rate_max = (total_payout_max / total_invested * 100.0) if total_invested > 0 else 0.0

    return RaceEvaluationResult(
        first=first,
        second=second,
        third=third,
        total_invested=total_invested,
        hit_bets=hit_details,
        total_payout_min=round(total_payout_min, 1),
        total_payout_max=round(total_payout_max, 1),
        net_profit_min=round(net_profit_min, 1),
        net_profit_max=round(net_profit_max, 1),
        recovery_rate_min=round(recovery_rate_min, 1),
        recovery_rate_max=round(recovery_rate_max, 1),
        is_hit=is_hit,
    )


def generate_scenario_matrix(bets: List[SingleBet]) -> pd.DataFrame:
    """4シナリオ損益シミュレーションテーブルの生成

    シナリオ:
    1. 守りのみ的中（最小〜最大）
    2. 攻めのみ的中（最小〜最大）
    3. ダブル的中（守り＋攻め同時的中）
    4. 全ハズレ
    """
    total_invested = sum(b.allocated_amount for b in bets)

    defense_bets = [b for b in bets if b.role == BetRole.DEFENSE and b.allocated_amount > 0]
    offense_bets = [b for b in bets if b.role == BetRole.OFFENSE and b.allocated_amount > 0]

    def get_range(bet_list: List[SingleBet]) -> Tuple[float, float]:
        if not bet_list:
            return 0.0, 0.0
        mins = [b.expected_payout_min for b in bet_list]
        maxs = [b.expected_payout_max for b in bet_list]
        return min(mins), max(maxs)

    def_min, def_max = get_range(defense_bets)
    off_min, off_max = get_range(offense_bets)

    # ダブル的中（守り最小+攻め最小 〜 守り最大+攻め最大）
    if defense_bets and offense_bets:
        double_min = def_min + off_min
        double_max = def_max + off_max
    else:
        double_min = 0.0
        double_max = 0.0

    rows = []

    # 1. 守りのみ的中
    if defense_bets:
        p_str = f"{def_min:,.0f}円 〜 {def_max:,.0f}円" if def_min != def_max else f"{def_min:,.0f}円"
        prof_min = def_min - total_invested
        prof_max = def_max - total_invested
        prof_str = f"{prof_min:+,.0f}円 〜 {prof_max:+,.0f}円" if prof_min != prof_max else f"{prof_min:+,.0f}円"
        rec_min = (def_min / total_invested * 100) if total_invested > 0 else 0
        rec_max = (def_max / total_invested * 100) if total_invested > 0 else 0
        rec_str = f"{rec_min:.1f}% 〜 {rec_max:.1f}%" if rec_min != rec_max else f"{rec_min:.1f}%"
        rows.append({
            "シナリオ": "🛡️ 守りのみ的中",
            "投資額": f"{total_invested:,}円",
            "想定払戻金": p_str,
            "純利益": prof_str,
            "回収率": rec_str,
            "判定": "元金回収・保険" if rec_min >= 100 else "⚠️ トリガミ注意",
        })
    else:
        rows.append({
            "シナリオ": "🛡️ 守りのみ的中",
            "投資額": f"{total_invested:,}円",
            "想定払戻金": "なし",
            "純利益": "なし",
            "回収率": "-",
            "判定": "守り枠なし",
        })

    # 2. 攻めのみ的中
    if offense_bets:
        p_str = f"{off_min:,.0f}円 〜 {off_max:,.0f}円" if off_min != off_max else f"{off_min:,.0f}円"
        prof_min = off_min - total_invested
        prof_max = off_max - total_invested
        prof_str = f"{prof_min:+,.0f}円 〜 {prof_max:+,.0f}円" if prof_min != prof_max else f"{prof_min:+,.0f}円"
        rec_min = (off_min / total_invested * 100) if total_invested > 0 else 0
        rec_max = (off_max / total_invested * 100) if total_invested > 0 else 0
        rec_str = f"{rec_min:.1f}% 〜 {rec_max:.1f}%" if rec_min != rec_max else f"{rec_min:.1f}%"
        rows.append({
            "シナリオ": "⚔️ 攻めのみ的中",
            "投資額": f"{total_invested:,}円",
            "想定払戻金": p_str,
            "純利益": prof_str,
            "回収率": rec_str,
            "判定": "大幅プラス" if rec_min >= 200 else ("プラス" if rec_min >= 100 else "トリガミ注意"),
        })
    else:
        rows.append({
            "シナリオ": "⚔️ 攻めのみ的中",
            "投資額": f"{total_invested:,}円",
            "想定払戻金": "なし",
            "純利益": "なし",
            "回収率": "-",
            "判定": "攻め枠なし",
        })

    # 3. ダブル的中
    if defense_bets and offense_bets:
        p_str = f"{double_min:,.0f}円 〜 {double_max:,.0f}円" if double_min != double_max else f"{double_min:,.0f}円"
        prof_min = double_min - total_invested
        prof_max = double_max - total_invested
        prof_str = f"{prof_min:+,.0f}円 〜 {prof_max:+,.0f}円" if prof_min != prof_max else f"{prof_min:+,.0f}円"
        rec_min = (double_min / total_invested * 100) if total_invested > 0 else 0
        rec_max = (double_max / total_invested * 100) if total_invested > 0 else 0
        rec_str = f"{rec_min:.1f}% 〜 {rec_max:.1f}%" if rec_min != rec_max else f"{rec_min:.1f}%"
        rows.append({
            "シナリオ": "🎉 ダブル的中（守り＋攻め）",
            "投資額": f"{total_invested:,}円",
            "想定払戻金": p_str,
            "純利益": prof_str,
            "回収率": rec_str,
            "判定": "大勝利！",
        })
    else:
        rows.append({
            "シナリオ": "🎉 ダブル的中（守り＋攻め）",
            "投資額": f"{total_invested:,}円",
            "想定払戻金": "-",
            "純利益": "-",
            "回収率": "-",
            "判定": "単一役割のみ",
        })

    # 4. 全ハズレ
    rows.append({
        "シナリオ": "💀 全ハズレ",
        "投資額": f"{total_invested:,}円",
        "想定払戻金": "0円",
        "純利益": f"-{total_invested:,}円",
        "回収率": "0.0%",
        "判定": "全損",
    })

    return pd.DataFrame(rows)
