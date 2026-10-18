"""NumPy＋貪欲法による高速資金配分エンジン
"""
import copy
from typing import List, Tuple
import numpy as np

from .models import (
    SingleBet,
    SimulationConfig,
    AllocationResult,
    BetRole,
    StrategyMode,
)


def calculate_synthetic_odds(odds_list: List[float]) -> float:
    """合成オッズの計算: (Σ (1 / odds_i)) ^ (-1)"""
    valid_odds = [o for o in odds_list if o > 0]
    if not valid_odds:
        return 0.0
    reciprocal_sum = sum(1.0 / o for o in valid_odds)
    if reciprocal_sum <= 0:
        return 0.0
    return round(1.0 / reciprocal_sum, 2)


def allocate_funds(bets: List[SingleBet], config: SimulationConfig) -> AllocationResult:
    """資金配分メイン関数

    Args:
        bets: 買い目リスト
        config: シミュレーション設定

    Returns:
        AllocationResult: 配分結果
    """
    if not bets:
        return AllocationResult(
            bets=[],
            total_invested=0,
            remaining_budget=config.total_budget,
            synthetic_odds=0.0,
            min_defense_payout=0.0,
            warnings=["買い目が指定されていません。"],
        )

    # 作業用のコピー作成
    allocated_bets = [copy.deepcopy(b) for b in bets]
    for b in allocated_bets:
        b.allocated_amount = 0
        b.expected_payout_min = 0.0
        b.expected_payout_max = 0.0
        b.is_torigami = False
        b.shortfall_amount = 0

    warnings: List[str] = []
    total_budget = int(config.total_budget)
    target_recovery = total_budget * config.target_recovery_rate

    # 合成オッズの事前算出
    all_odds = [b.odds_min for b in allocated_bets]
    synthetic_odds = calculate_synthetic_odds(all_odds)
    if synthetic_odds > 0 and synthetic_odds < config.synthetic_odds_threshold:
        warnings.append(
            f"合成オッズが {synthetic_odds:.2f}倍 と閾値({config.synthetic_odds_threshold:.1f}倍)を下回っています。"
        )


    mode = config.strategy_mode
    if mode == StrategyMode.HYBRID:
        result = _allocate_hybrid(allocated_bets, config, target_recovery, warnings)
    elif mode == StrategyMode.EQUAL_PAYOUT:
        result = _allocate_equal_payout(allocated_bets, config, warnings)
    elif mode == StrategyMode.INSURANCE:
        result = _allocate_insurance(allocated_bets, config, target_recovery, warnings)
    else:
        # デフォルトはHYBRID
        result = _allocate_hybrid(allocated_bets, config, target_recovery, warnings)

    return result


def _allocate_hybrid(
    bets: List[SingleBet],
    config: SimulationConfig,
    target_recovery: float,
    warnings: List[str],
) -> AllocationResult:
    """ハイブリッド戦略配分

    1. 守り枠(defense)の各買い目で目標額Tを回収する最低額（100円単位切り上げ）を算出
    2. 守り枠合計が総予算を超える場合は破綻判定
    3. 残額を攻め枠(offense)に配分（最低100円ずつ保証の上、余剰金を貪欲配分）
    """
    total_budget = config.total_budget
    defense_indices = [i for i, b in enumerate(bets) if b.role == BetRole.DEFENSE]
    offense_indices = [i for i, b in enumerate(bets) if b.role == BetRole.OFFENSE]

    # 1. 守り枠の必要資金計算
    defense_total = 0
    defense_amounts = [0] * len(bets)

    for idx in defense_indices:
        b = bets[idx]
        if b.odds_min <= 0:
            continue
        # Ai = ceil(T / (odds * 100)) * 100
        needed = int(np.ceil(target_recovery / (b.odds_min * 100.0))) * 100
        needed = max(needed, config.min_bet_unit)
        defense_amounts[idx] = needed
        defense_total += needed

    # 2. 破綻判定
    if defense_total > total_budget:
        shortage = defense_total - total_budget
        # 現在の目標で必要な守り合計に対して、予算が占める割合から実現可能な回収率を推定
        if defense_total > 0:
            sugg_pct = int((total_budget / defense_total) * config.target_recovery_rate * 100)
        else:
            sugg_pct = 0

        err_msg = (
            f"守り枠の確保に必要な資金({defense_total:,}円)が総予算({total_budget:,}円)を超過しています。"
            f"成立にはあと {shortage:,}円 の予算追加、または目標回収率を約{sugg_pct}%以下に引き下げる必要があります。"
        )
        for idx in defense_indices:
            bets[idx].allocated_amount = defense_amounts[idx]

        return AllocationResult(
            bets=bets,
            total_invested=defense_total,
            remaining_budget=0,
            synthetic_odds=calculate_synthetic_odds([b.odds_min for b in bets]),
            min_defense_payout=0.0,
            is_bankrupt=True,
            error_message=err_msg,
            warnings=warnings,
        )

    # 守り枠の配分を適用
    for idx in defense_indices:
        bets[idx].allocated_amount = defense_amounts[idx]

    remaining_budget = total_budget - defense_total

    # 3. 攻め枠への配分
    if offense_indices:
        min_offense_needed = len(offense_indices) * config.min_bet_unit
        if remaining_budget < min_offense_needed:
            warnings.append(
                f"守り枠確保後の残予算({remaining_budget:,}円)では、攻め枠全点({len(offense_indices)}点)に100円ずつ購入できません。"
                "一部の攻め枠のみに配分されます。"
            )
            # 可能な限り高オッズ（または先頭）の攻め枠に100円ずつ割り当て
            sorted_offense = sorted(offense_indices, key=lambda i: bets[i].odds_min, reverse=True)
            for idx in sorted_offense:
                if remaining_budget >= config.min_bet_unit:
                    bets[idx].allocated_amount = config.min_bet_unit
                    remaining_budget -= config.min_bet_unit
        else:
            # 各攻め枠に最低100円割り当て
            for idx in offense_indices:
                bets[idx].allocated_amount = config.min_bet_unit
            remaining_budget -= min_offense_needed

            # ------------------------------------------------------------------
            # ★ 10万円超え三連単ターゲット優先配分
            # 攻め枠に三連単または高オッズ(>=50倍)がある場合、少なくとも1点は払戻10万円超えを狙う
            # ------------------------------------------------------------------
            target_100k_payout = 100000.0

            def _score_100k_target(i: int) -> float:
                odds = bets[i].odds_min
                # 60倍〜160倍が最も理想的（少ない金額 700〜1,600円 で10万円超え可能）
                if 60.0 <= odds <= 160.0:
                    return 0.0 + odds
                elif odds > 160.0:
                    return 200.0 + odds
                else:
                    return 500.0 - odds

            high_odds_indices = [
                i for i in offense_indices
                if bets[i].odds_min >= 40.0
            ]
            # 最も効率よく10万円超えを狙える買い目を優先
            high_odds_indices.sort(key=_score_100k_target)

            allocated_100k_count = 0
            for idx in high_odds_indices:
                b = bets[idx]
                target_total = int(np.ceil(target_100k_payout / (b.odds_min * 100.0))) * 100
                add_needed = max(0, target_total - b.allocated_amount)
                # 残り予算から割り当て可能か確認
                if add_needed > 0 and remaining_budget >= add_needed:
                    b.allocated_amount += add_needed
                    remaining_budget -= add_needed
                    allocated_100k_count += 1
                    # 最大2点まで10万円超えを確保（それ以上は中配当や余剰配分に回す）
                    if allocated_100k_count >= 2:
                        break

            # 残余予算の攻め枠傾斜理論配分（まだ10万超えになっていない、または全攻め枠）
            if remaining_budget >= config.min_bet_unit:
                # 10万超え未達成の枠、または全攻め枠へ傾斜
                target_offense_indices = [i for i in offense_indices if (bets[i].allocated_amount * bets[i].odds_min) < target_100k_payout]
                if not target_offense_indices:
                    target_offense_indices = offense_indices

                offense_odds = np.array([bets[i].odds_min for i in target_offense_indices], dtype=float)
                reciprocals = 1.0 / offense_odds
                total_recip = np.sum(reciprocals)
                if total_recip > 0:
                    raw_proportions = reciprocals / total_recip
                    raw_additional = remaining_budget * raw_proportions
                    floor_additional = (np.floor(raw_additional / 100.0) * 100).astype(int)

                    for i, idx in enumerate(target_offense_indices):
                        add_amt = floor_additional[i]
                        bets[idx].allocated_amount += add_amt
                        remaining_budget -= add_amt

            # 端数の余剰金貪欲配分（攻め枠に対して）
            if remaining_budget >= config.min_bet_unit:
                remaining_budget = _distribute_surplus(bets, offense_indices, remaining_budget, config.surplus_rule)
    else:
        # 攻め枠がない場合、守り枠に余剰金を貪欲配分
        if remaining_budget >= config.min_bet_unit and defense_indices:
            remaining_budget = _distribute_surplus(bets, defense_indices, remaining_budget, config.surplus_rule)

    return _finalize_result(bets, total_budget, remaining_budget, warnings)


def _allocate_equal_payout(
    bets: List[SingleBet],
    config: SimulationConfig,
    warnings: List[str],
) -> AllocationResult:
    """均等払戻（傾斜配分）戦略

    全買い目で的中時払戻金が極力均等になるよう100円単位で配分
    """
    total_budget = config.total_budget
    n_bets = len(bets)
    min_needed = n_bets * config.min_bet_unit

    if min_needed > total_budget:
        return AllocationResult(
            bets=bets,
            total_invested=0,
            remaining_budget=total_budget,
            synthetic_odds=calculate_synthetic_odds([b.odds_min for b in bets]),
            min_defense_payout=0.0,
            is_bankrupt=True,
            error_message=f"総予算({total_budget:,}円)が全買い目の最低購入額({min_needed:,}円)を下回っています。",
            warnings=warnings,
        )

    odds_arr = np.array([b.odds_min for b in bets], dtype=float)
    inv_odds = 1.0 / np.maximum(odds_arr, 1.0)
    syn_odds = 1.0 / np.sum(inv_odds)

    # 理論配分額
    raw_amounts = total_budget * (syn_odds * inv_odds)
    floor_amounts = (np.floor(raw_amounts / 100.0) * 100).astype(int)

    # 最低100円保証
    floor_amounts = np.maximum(floor_amounts, config.min_bet_unit)

    allocated_sum = int(np.sum(floor_amounts))
    # 最低保証により総予算を超過した場合の調整
    if allocated_sum > total_budget:
        # 払戻金が大きすぎる買い目から100円ずつ削る
        diff = allocated_sum - total_budget
        while diff > 0:
            payouts = floor_amounts * odds_arr
            # 100円より多く配分されている中で最も払戻が高いインデックス
            eligible = [i for i, amt in enumerate(floor_amounts) if amt > config.min_bet_unit]
            if not eligible:
                break
            max_idx = eligible[np.argmax(payouts[eligible])]
            floor_amounts[max_idx] -= 100
            diff -= 100

    for i, b in enumerate(bets):
        b.allocated_amount = int(floor_amounts[i])

    remaining_budget = total_budget - int(np.sum(floor_amounts))

    # 余剰金の貪欲法配分（全買い目対象）
    all_indices = list(range(n_bets))
    if remaining_budget >= config.min_bet_unit:
        remaining_budget = _distribute_surplus(bets, all_indices, remaining_budget, "boost_min_payout")

    return _finalize_result(bets, total_budget, remaining_budget, warnings)


def _allocate_insurance(
    bets: List[SingleBet],
    config: SimulationConfig,
    target_recovery: float,
    warnings: List[str],
) -> AllocationResult:
    """元返し保険（1点集中ヘッジ）戦略

    defenseの最有力1点（最高オッズまたは先頭）で目標回収額Tを確保し、残金をoffenseに均等/傾斜配分
    """
    total_budget = config.total_budget
    defense_indices = [i for i, b in enumerate(bets) if b.role == BetRole.DEFENSE]
    offense_indices = [i for i, b in enumerate(bets) if b.role == BetRole.OFFENSE]

    if not defense_indices:
        warnings.append("守り枠(defense)が指定されていないため、先頭買い目を保険枠として扱います。")
        defense_indices = [0]
        bets[0].role = BetRole.DEFENSE
        offense_indices = [i for i in range(1, len(bets))]

    # defenseの中で最もオッズが高い1点（最有力ヘッジ点）を選定
    best_defense_idx = max(defense_indices, key=lambda i: bets[i].odds_min)

    # 1点集中ヘッジの必要資金
    b_def = bets[best_defense_idx]
    needed = int(np.ceil(target_recovery / (b_def.odds_min * 100.0))) * 100
    needed = max(needed, config.min_bet_unit)

    if needed > total_budget:
        err_msg = (
            f"保険枠の確保に必要な資金({needed:,}円)が総予算({total_budget:,}円)を超過しています。"
            f"目標回収率の引き下げまたは予算追加が必要です。"
        )
        bets[best_defense_idx].allocated_amount = needed
        return AllocationResult(
            bets=bets,
            total_invested=needed,
            remaining_budget=0,
            synthetic_odds=calculate_synthetic_odds([b.odds_min for b in bets]),
            min_defense_payout=0.0,
            is_bankrupt=True,
            error_message=err_msg,
            warnings=warnings,
        )

    bets[best_defense_idx].allocated_amount = needed
    remaining_budget = total_budget - needed

    # 他のdefenseがあれば最低100円配分（もしあれば）
    other_defense = [i for i in defense_indices if i != best_defense_idx]
    for idx in other_defense:
        if remaining_budget >= config.min_bet_unit:
            bets[idx].allocated_amount = config.min_bet_unit
            remaining_budget -= config.min_bet_unit

    # offenseへの配分
    if offense_indices:
        min_offense_needed = len(offense_indices) * config.min_bet_unit
        if remaining_budget < min_offense_needed:
            warnings.append("残予算不足のため、一部の攻め枠にのみ配分されます。")
            sorted_offense = sorted(offense_indices, key=lambda i: bets[i].odds_min, reverse=True)
            for idx in sorted_offense:
                if remaining_budget >= config.min_bet_unit:
                    bets[idx].allocated_amount = config.min_bet_unit
                    remaining_budget -= config.min_bet_unit
        else:
            # 均等に配分
            equal_share = (remaining_budget // len(offense_indices) // 100) * 100
            for idx in offense_indices:
                bets[idx].allocated_amount = equal_share
                remaining_budget -= equal_share

            # 余剰金を貪欲配分
            if remaining_budget >= config.min_bet_unit:
                remaining_budget = _distribute_surplus(bets, offense_indices, remaining_budget, config.surplus_rule)
    else:
        # offenseがない場合は保険枠に余剰金を加算（100円単位制約を遵守）
        add_amount = (remaining_budget // 100) * 100
        bets[best_defense_idx].allocated_amount += add_amount
        remaining_budget -= add_amount

    return _finalize_result(bets, total_budget, remaining_budget, warnings)


def _distribute_surplus(
    bets: List[SingleBet],
    target_indices: List[int],
    surplus: int,
    rule: str,
) -> int:
    """100円ステップで余剰金を貪欲法配分

    要件: np.argmin で想定最低払戻金が最小の買い目を特定し、100円を加算する操作を繰り返す

    Args:
        bets: 全買い目リスト
        target_indices: 加算対象インデックスリスト
        surplus: 余剰金（円）
        rule: 'boost_min_payout' or 'max_odds'

    Returns:
        配布後の残り余剰金
    """
    if not target_indices or surplus < 100:
        return surplus

    step = 100
    loops = surplus // step

    for _ in range(loops):
        if rule == "max_odds":
            # 最もオッズの高い買い目をnp.argmaxで特定
            odds_arr = np.array([bets[i].odds_min for i in target_indices])
            local_idx = int(np.argmax(odds_arr))
            selected_idx = target_indices[local_idx]
        else:  # 'boost_min_payout' デフォルト
            # 想定最低払戻金 (allocated_amount * odds_min) が最も小さい買い目をnp.argminで特定
            payouts = np.array([
                bets[i].allocated_amount * bets[i].odds_min for i in target_indices
            ])
            local_idx = int(np.argmin(payouts))
            selected_idx = target_indices[local_idx]

        bets[selected_idx].allocated_amount += step
        surplus -= step

    return surplus


def _finalize_result(
    bets: List[SingleBet],
    total_budget: int,
    remaining_budget: int,
    warnings: List[str],
) -> AllocationResult:
    """払戻金・トリガミ判定・メトリクスを確定してAllocationResultを作成"""
    total_invested = sum(b.allocated_amount for b in bets)

    # 払戻金計算とトリガミ判定
    for b in bets:
        if b.allocated_amount > 0:
            b.expected_payout_min = round(b.allocated_amount * b.odds_min, 1)
            max_odds = b.odds_max if b.odds_max is not None else b.odds_min
            b.expected_payout_max = round(b.allocated_amount * max_odds, 1)

            if b.expected_payout_min < total_invested:
                b.is_torigami = True
                b.shortfall_amount = int(total_invested - b.expected_payout_min)
            else:
                b.is_torigami = False
                b.shortfall_amount = 0
        else:
            b.expected_payout_min = 0.0
            b.expected_payout_max = 0.0
            b.is_torigami = False
            b.shortfall_amount = 0

    # 守り枠の最低回収額
    defense_payouts = [
        b.expected_payout_min
        for b in bets
        if b.role == BetRole.DEFENSE and b.allocated_amount > 0
    ]
    min_defense_payout = min(defense_payouts) if defense_payouts else 0.0

    # トリガミ発生警告
    torigami_count = sum(1 for b in bets if b.is_torigami and b.allocated_amount > 0)
    if torigami_count > 0:
        warnings.append(
            f"配分された買い目のうち {torigami_count}点 でトリガミ（的中しても投資額割れ）が発生しています。"
        )

    all_odds = [b.odds_min for b in bets if b.allocated_amount > 0]
    syn_odds = calculate_synthetic_odds(all_odds)

    return AllocationResult(
        bets=bets,
        total_invested=total_invested,
        remaining_budget=remaining_budget,
        synthetic_odds=syn_odds,
        min_defense_payout=min_defense_payout,
        is_bankrupt=False,
        warnings=warnings,
    )
