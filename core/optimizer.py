"""買い目自動生成＆最大100回最適化ループモジュール (STEP 1 用)

印（◎○▲△☆）から買い目を自動構築し、
トリガミ警告や合成オッズ閾値割れ、破綻警告が発生しなくなるまで
最大100回試行して最適なポートフォリオを選定します。
100回試行しても警告が残る場合は、最善手をセットし、
青帯で表示する「予算追加または合成オッズ閾値引き下げ」の改善提案を生成します。
"""
from typing import List, Optional, Tuple, Dict, Any
import copy
import numpy as np

from .models import (
    RaceInfo,
    HorseEntry,
    TicketType,
    BetRole,
    SingleBet,
    SimulationConfig,
    AllocationResult,
)
from .combinatorics import (
    generate_formation_bets,
    generate_box_bets,
    parse_combination_horses,
)
from .race_data import estimate_composite_odds
from .allocator import allocate_funds


def build_gemini_argon_portfolio(
    race: RaceInfo,
    config: SimulationConfig,
    honmei: int,
    taikou: int,
    tanana: int,
    osae_cands: List[int],
) -> List[SingleBet]:
    """Gemini Argon推奨のプロフェッショナル戦略買い目を構築

    1. リスクヘッジ枠（守り枠）:
       - どの1点が的中しても単体で総予算100%以上を全額回収する多重網
       - 馬連（本命-対抗、本命-単穴、本命-押さえ）およびワイド
    2. 攻め枠（三連単4点）:
       - 1着: 本命（◎）
       - 2着: 対抗（○）、単穴（▲）、先行押さえ（△1）
       - 3着: 穴馬（☆）を据えた10万円超え払戻狙い（2点）
       - 3着: 有力馬同士の中配当狙い（2点）
       - ※配分時に少なくとも1点は払戻が10万円を超えるように自動配分される
    """
    if not race.entries:
        return []

    # 穴馬の選定 (☆印の馬、または単勝20〜60倍の盲点馬を最優先)
    star_horses = [e.horse_number for e in race.entries if e.mark == "☆" and e.horse_number != honmei]
    if star_horses:
        ana = star_horses[0]
    else:
        # 6〜10番人気の中から穴馬候補を探す
        ana_cands = [h for h in osae_cands if h not in (taikou, tanana)]
        ana = ana_cands[-1] if len(ana_cands) >= 3 else (ana_cands[0] if ana_cands else osae_cands[-1])

    # 押さえ候補 (ana 以外の有力押さえ馬から選定)
    remaining_osae = [h for h in osae_cands if h != ana and h not in (taikou, tanana)]
    osae1 = remaining_osae[0] if len(remaining_osae) > 0 else (osae_cands[0] if osae_cands[0] != ana else taikou)
    osae2 = remaining_osae[1] if len(remaining_osae) > 1 else (remaining_osae[0] if remaining_osae else osae1)

    # 守り枠の候補 (馬連およびワイド)
    defense_candidates = [
        ([honmei, taikou], TicketType.QUINELLA),  # 2強本線馬連
        ([honmei, tanana], TicketType.QUINELLA),  # 3番人気馬連
        ([honmei, osae2], TicketType.QUINELLA),   # 押さえ馬連 (中穴)
        ([honmei, osae1], TicketType.WIDE),       # ワイド (3着粘り込みケア)
    ]

    max_defense_budget = int(config.total_budget * 0.65)
    cur_defense_cost = 0
    bets: List[SingleBet] = []

    for pair, t_type in defense_candidates:
        if len(set(pair)) < 2:
            continue
        o_min, o_max = estimate_composite_odds(t_type, pair, race.entries)
        needed = int(np.ceil(config.total_budget / (max(o_min, 1.1) * 100.0))) * 100
        # 守り枠予算枠内、または最低2点確保
        if cur_defense_cost + needed <= max_defense_budget or len(bets) < 2:
            comb = f"{min(pair)}-{max(pair)}"
            if not any(b.combination == comb and b.ticket_type == t_type for b in bets):
                bets.append(SingleBet(t_type, comb, o_min, o_max, BetRole.DEFENSE))
                cur_defense_cost += needed

    # 攻め枠（三連単4点）:
    # 1. 本線穴 (10万超え狙い)
    # 2. 先行穴 (10万超え狙い)
    # 3. 王道決着 (中配当)
    # 4. 先行粘り (中配当)
    trifectas = [
        [honmei, taikou, ana],    # 10万超え狙い（本線穴）
        [honmei, osae1, ana],     # 10万超え狙い（内枠・先行穴）
        [honmei, taikou, osae1],  # 中配当狙い（王道決着）
        [honmei, osae1, taikou],  # 中配当狙い（先行粘り）
    ]
    for tri in trifectas:
        if len(set(tri)) == 3:
            o_min, _ = estimate_composite_odds(TicketType.TRIFECTA, tri, race.entries)
            comb = f"{tri[0]}-{tri[1]}-{tri[2]}"
            if not any(b.combination == comb and b.ticket_type == TicketType.TRIFECTA for b in bets):
                bets.append(SingleBet(TicketType.TRIFECTA, comb, o_min, None, BetRole.OFFENSE))

    return bets


def optimize_bets_from_marks(
    race: RaceInfo,
    config: SimulationConfig,
    max_iterations: int = 100,
) -> Tuple[List[SingleBet], Optional[Dict[str, Any]], int]:
    """印（◎○▲△☆）から最大100回試行して最適な買い目を探索・選定する。

    Returns:
        (selected_bets, proposal_dict_or_none, iteration_count)
    """
    if not race.entries:
        return [], None, 0

    # 印付きの馬を分類
    mark_horses: Dict[str, List[int]] = {"◎": [], "○": [], "▲": [], "△": [], "☆": []}
    for e in race.entries:
        if e.mark in mark_horses:
            mark_horses[e.mark].append(e.horse_number)

    # フォールバック（印がない場合は人気上位）
    sorted_entries = sorted(race.entries, key=lambda x: x.win_odds)
    all_horse_nums = [e.horse_number for e in sorted_entries]

    honmei = mark_horses["◎"][0] if mark_horses["◎"] else all_horse_nums[0]
    
    taikou_cands = [h for h in mark_horses["○"] if h != honmei]
    taikou = taikou_cands[0] if taikou_cands else (all_horse_nums[1] if len(all_horse_nums) > 1 else honmei)

    tanana_cands = [h for h in mark_horses["▲"] if h not in (honmei, taikou)]
    tanana = tanana_cands[0] if tanana_cands else (all_horse_nums[2] if len(all_horse_nums) > 2 else taikou)

    osae_cands = [h for h in (mark_horses["△"] + mark_horses["☆"]) if h not in (honmei, taikou, tanana)]
    # 押さえ候補が少なければ人気上位から補充（常に5頭程度確保）
    for h in all_horse_nums:
        if h not in (honmei, taikou, tanana) and h not in osae_cands:
            osae_cands.append(h)
        if len(osae_cands) >= 5:
            break

    # 探索バリエーションの生成定義 (最大100パターン)
    # 守り枠のバリエーション (6通り)
    defense_patterns = [
        ("wide_main", "ワイド本命-対抗 1点"),
        ("quinella_main", "馬連本命-対抗 1点"),
        ("wide_tanana", "ワイド本命-単穴 1点"),
        ("dual_wide", "ワイド2点 (本命-対抗, 本命-単穴)"),
        ("wide_quinella", "ワイド+馬連 (本命-対抗 2点)"),
        ("none", "守りなし (攻め集中)"),
    ]

    # 攻め枠のバリエーション (18通り)
    # 三連複の相手頭数や券種、オッズカット閾値の組み合わせ
    offense_patterns = []
    # 三連複フォーメーション (3列目の相手数: 1頭〜4頭)
    for third_count in [1, 2, 3, 4]:
        for min_odds_cut in [0.0, 5.0, 10.0]:
            offense_patterns.append({
                "type": "trio_formation",
                "third_count": third_count,
                "min_odds_cut": min_odds_cut,
            })
    # 馬連・ワイドの流し (相手数 1〜3頭)
    for t_type in [TicketType.QUINELLA, TicketType.WIDE]:
        for opp_count in [1, 2, 3]:
            offense_patterns.append({
                "type": "nagashi",
                "ticket_type": t_type,
                "opp_count": opp_count,
                "min_odds_cut": 0.0,
            })

    best_bets: List[SingleBet] = []
    best_penalty = float("inf")
    best_res: Optional[AllocationResult] = None
    best_iter = 0

    iteration = 0

    # --------------------------------------------------------------------------
    # ★ 最優先パターン: Gemini Argon推奨のプロフェッショナル戦略
    # (元本回収の多重網 + 10万円超え三連単フォーメーション)
    # --------------------------------------------------------------------------
    gemini_bets = build_gemini_argon_portfolio(race, config, honmei, taikou, tanana, osae_cands)
    if gemini_bets:
        iteration += 1
        alloc_gemini = allocate_funds(gemini_bets, config)
        torigami_c = sum(1 for b in alloc_gemini.bets if b.is_torigami and b.allocated_amount > 0)
        warn_c = len(alloc_gemini.warnings)
        syn_short = max(0.0, config.synthetic_odds_threshold - alloc_gemini.synthetic_odds)

        # 10万円超え三連単が存在するか確認
        has_100k_trifecta = any(
            (b.allocated_amount * b.odds_min) >= 100000.0 and b.ticket_type == TicketType.TRIFECTA
            for b in alloc_gemini.bets
        )
        # 守り枠が的中時に元本100%回収できるか確認 (最低2点以上配分されていること)
        active_defense = [b for b in alloc_gemini.bets if b.role == BetRole.DEFENSE and b.allocated_amount > 0]
        defense_ok = len(active_defense) >= 2 and all(
            (b.allocated_amount * b.odds_min) >= config.total_budget
            for b in active_defense
        )
        # 破綻なし・トリガミゼロ・守り枠回収OK・10万円超え三連単あり・警告ゼロなら最優先即採用！
        if (
            not alloc_gemini.is_bankrupt
            and torigami_c == 0
            and defense_ok
            and has_100k_trifecta
            and warn_c == 0
        ):
            return gemini_bets, None, iteration

        # 一般的な警告ゼロ・トリガミゼロ・破綻なしの場合も即採用
        if not alloc_gemini.is_bankrupt and torigami_c == 0 and warn_c == 0:
            return gemini_bets, None, iteration

        # ベスト候補として初期登録
        best_penalty = (
            (100000.0 if alloc_gemini.is_bankrupt else 0.0)
            + (torigami_c * 200.0)
            + (syn_short * 150.0)
            + (warn_c * 50.0)
            - (500.0 if has_100k_trifecta else 0.0)
        )
        best_bets = gemini_bets
        best_res = alloc_gemini
        best_iter = iteration

    # 探索グリッド実行（最大100回）
    for d_idx, (d_type, _) in enumerate(defense_patterns):
        for o_idx, o_conf in enumerate(offense_patterns):
            iteration += 1
            if iteration > max_iterations:
                break

            candidate_bets: List[SingleBet] = []

            # 1. 守り枠の構築
            if d_type == "wide_main":
                w_min, w_max = estimate_composite_odds(TicketType.WIDE, [honmei, taikou], race.entries)
                w_comb = f"{min(honmei, taikou)}-{max(honmei, taikou)}"
                candidate_bets.append(SingleBet(TicketType.WIDE, w_comb, w_min, w_max, BetRole.DEFENSE))
            elif d_type == "quinella_main":
                q_min, _ = estimate_composite_odds(TicketType.QUINELLA, [honmei, taikou], race.entries)
                q_comb = f"{min(honmei, taikou)}-{max(honmei, taikou)}"
                candidate_bets.append(SingleBet(TicketType.QUINELLA, q_comb, q_min, None, BetRole.DEFENSE))
            elif d_type == "wide_tanana":
                w_min, w_max = estimate_composite_odds(TicketType.WIDE, [honmei, tanana], race.entries)
                w_comb = f"{min(honmei, tanana)}-{max(honmei, tanana)}"
                candidate_bets.append(SingleBet(TicketType.WIDE, w_comb, w_min, w_max, BetRole.DEFENSE))
            elif d_type == "dual_wide":
                w1_min, w1_max = estimate_composite_odds(TicketType.WIDE, [honmei, taikou], race.entries)
                w1_comb = f"{min(honmei, taikou)}-{max(honmei, taikou)}"
                candidate_bets.append(SingleBet(TicketType.WIDE, w1_comb, w1_min, w1_max, BetRole.DEFENSE))
                w2_min, w2_max = estimate_composite_odds(TicketType.WIDE, [honmei, tanana], race.entries)
                w2_comb = f"{min(honmei, tanana)}-{max(honmei, tanana)}"
                candidate_bets.append(SingleBet(TicketType.WIDE, w2_comb, w2_min, w2_max, BetRole.DEFENSE))
            elif d_type == "wide_quinella":
                w_min, w_max = estimate_composite_odds(TicketType.WIDE, [honmei, taikou], race.entries)
                comb = f"{min(honmei, taikou)}-{max(honmei, taikou)}"
                candidate_bets.append(SingleBet(TicketType.WIDE, comb, w_min, w_max, BetRole.DEFENSE))
                q_min, _ = estimate_composite_odds(TicketType.QUINELLA, [honmei, taikou], race.entries)
                candidate_bets.append(SingleBet(TicketType.QUINELLA, comb, q_min, None, BetRole.DEFENSE))

            # 2. 攻め枠の構築
            if o_conf["type"] == "trio_formation":
                c_second = [taikou, tanana]
                c_third = [taikou, tanana] + osae_cands[:o_conf["third_count"]]
                trio_combs = generate_formation_bets(TicketType.TRIO, [honmei], c_second, c_third)
                for tc in trio_combs:
                    horses = parse_combination_horses(tc)
                    t_min, _ = estimate_composite_odds(TicketType.TRIO, horses, race.entries)
                    if t_min >= o_conf["min_odds_cut"]:
                        # 既存重複チェック
                        if not any(b.combination == tc and b.ticket_type == TicketType.TRIO for b in candidate_bets):
                            candidate_bets.append(SingleBet(TicketType.TRIO, tc, t_min, None, BetRole.OFFENSE))

            elif o_conf["type"] == "nagashi":
                t_type = o_conf["ticket_type"]
                opponents = [taikou, tanana] + osae_cands[:o_conf["opp_count"]]
                opponents = [h for h in opponents if h != honmei]
                for opp in opponents:
                    comb = f"{min(honmei, opp)}-{max(honmei, opp)}"
                    o_min, o_max = estimate_composite_odds(t_type, [honmei, opp], race.entries)
                    if not any(b.combination == comb and b.ticket_type == t_type for b in candidate_bets):
                        candidate_bets.append(SingleBet(t_type, comb, o_min, o_max, BetRole.OFFENSE))

            if not candidate_bets:
                continue

            # 3. 資金配分シミュレーションの実行と警告評価
            alloc_res = allocate_funds(candidate_bets, config)

            # トリガミ件数の確認
            torigami_count = sum(
                1 for b in alloc_res.bets if b.is_torigami and b.allocated_amount > 0
            )
            warnings_count = len(alloc_res.warnings)
            syn_odds = alloc_res.synthetic_odds
            is_bankrupt = alloc_res.is_bankrupt

            # 合成オッズ不足幅
            syn_shortfall = max(0.0, config.synthetic_odds_threshold - syn_odds)

            # 買い目のバランス判定 (守り枠と攻め枠が両方存在し、3点以上あること)
            has_defense = any(b.role == BetRole.DEFENSE for b in candidate_bets)
            has_offense = any(b.role == BetRole.OFFENSE for b in candidate_bets)

            # 完全クリア判定（警告ゼロ、トリガミゼロ、合成オッズ閾値以上、破綻なし、適切な点数バランス）
            if (
                not is_bankrupt
                and syn_shortfall <= 0.001
                and torigami_count == 0
                and warnings_count == 0
                and len(candidate_bets) >= 3
                and has_defense
                and has_offense
            ):
                # 警告ゼロを達成！即座に採用して探索終了
                return candidate_bets, None, iteration

            # ペナルティスコア計算（最善手探索用）
            balance_penalty = 0.0
            if len(candidate_bets) < 3:
                balance_penalty += 1000.0
            if not has_defense:
                balance_penalty += 500.0
            if not has_offense:
                balance_penalty += 500.0

            penalty = (
                (100000.0 if is_bankrupt else 0.0)
                + (torigami_count * 200.0)
                + (syn_shortfall * 150.0)
                + (warnings_count * 50.0)
                + balance_penalty
            )

            if penalty < best_penalty:
                best_penalty = penalty
                best_bets = candidate_bets
                best_res = alloc_res
                best_iter = iteration

        if iteration >= max_iterations:
            break

    # 100回試行しても完全クリア（警告ゼロ）が見つからなかった場合
    # 最善手を返し、改善提案（必要追加予算 or 合成オッズ閾値引下げ）を生成
    if not best_bets or len(best_bets) < 2:
        # フォールバック (守りワイド2点 + 三連複フォーメーション)
        w_min, w_max = estimate_composite_odds(TicketType.WIDE, [honmei, taikou], race.entries)
        w2_min, w2_max = estimate_composite_odds(TicketType.WIDE, [honmei, tanana], race.entries)
        best_bets = [
            SingleBet(TicketType.WIDE, f"{min(honmei, taikou)}-{max(honmei, taikou)}", w_min, w_max, BetRole.DEFENSE),
            SingleBet(TicketType.WIDE, f"{min(honmei, tanana)}-{max(honmei, tanana)}", w2_min, w2_max, BetRole.DEFENSE),
        ]
        trio_cands = generate_formation_bets(TicketType.TRIO, [honmei], [taikou, tanana], [taikou, tanana] + osae_cands[:2])
        for tc in trio_cands:
            h = parse_combination_horses(tc)
            t_min, _ = estimate_composite_odds(TicketType.TRIO, h, race.entries)
            best_bets.append(SingleBet(TicketType.TRIO, tc, t_min, None, BetRole.OFFENSE))
        best_res = allocate_funds(best_bets, config)

    # 提案数値の精密計算
    # 1. 必要追加予算の算出 (シミュレーション)
    needed_budget = config.total_budget
    found_budget = False
    for add_b in range(1000, 50001, 1000):
        test_config = copy.deepcopy(config)
        test_config.total_budget = config.total_budget + add_b
        res_test = allocate_funds(best_bets, test_config)
        test_torigami = sum(1 for b in res_test.bets if b.is_torigami and b.allocated_amount > 0)
        if not res_test.is_bankrupt and test_torigami == 0:
            needed_budget = test_config.total_budget
            found_budget = True
            break

    if not found_budget:
        # 最低オッズから理論値を算出
        min_odds = min(b.odds_min for b in best_bets if b.odds_min > 0)
        needed_budget = max(config.total_budget + 2000, int(min_odds * len(best_bets) * config.min_bet_unit * 1.5))
        # 1000円単位に切り上げ
        needed_budget = ((needed_budget + 999) // 1000) * 1000

    additional_budget = max(1000, needed_budget - config.total_budget)

    # 2. 推奨合成オッズ閾値の算出
    current_syn = best_res.synthetic_odds if best_res else 1.0
    suggested_threshold = max(1.2, round(current_syn - 0.1, 1))

    proposal = {
        "additional_budget": additional_budget,
        "suggested_threshold": suggested_threshold,
        "iterations": max_iterations,
        "best_iteration": best_iter,
        "current_synthetic_odds": current_syn,
        "message": f"予算を {additional_budget:,} 円追加 もしくは 合成オッズ閾値を {suggested_threshold:.1f} に下げる必要があります。",
    }

    return best_bets, proposal, max_iterations
