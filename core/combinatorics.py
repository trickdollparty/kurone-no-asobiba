"""フォーメーション・ボックス・ながし等の買い目組み合わせ展開モジュール
"""
from itertools import permutations, combinations
from typing import List, Optional, Set
from .models import TicketType


def format_combination(ticket_type: TicketType, horses: tuple[int, ...]) -> str:
    """券種に応じた正規化組み合わせ文字列を返す

    馬連・ワイド・三連複は昇順ソートしてハイフン連結 (例: '1-4', '1-2-4')
    単勝・複勝は単一馬番 (例: '1')
    馬単・三連単は順序を保持してハイフン連結 (例: '4-1', '3-1-2')
    """
    if ticket_type in (TicketType.WIN, TicketType.PLACE):
        return str(horses[0])
    elif ticket_type in (TicketType.QUINELLA, TicketType.WIDE, TicketType.TRIO):
        sorted_h = sorted(horses)
        return "-".join(map(str, sorted_h))
    else:  # EXACTA, TRIFECTA
        return "-".join(map(str, horses))


def generate_formation_bets(
    ticket_type: TicketType,
    first: List[int],
    second: Optional[List[int]] = None,
    third: Optional[List[int]] = None,
) -> List[str]:
    """フォーメーション展開関数

    Args:
        ticket_type: 券種
        first: 1頭目/1着候補の馬番リスト
        second: 2頭目/2着候補の馬番リスト (2頭以上券種で必要)
        third: 3頭目/3着候補の馬番リスト (3頭券種で必要)

    Returns:
        重複排除・正規化済みの買い目リスト (例: ['1-2', '1-3'])
    """
    first_set = [h for h in first if isinstance(h, int)]
    second_set = [h for h in (second or []) if isinstance(h, int)]
    third_set = [h for h in (third or []) if isinstance(h, int)]

    results: Set[str] = set()

    if ticket_type in (TicketType.WIN, TicketType.PLACE):
        for h in first_set:
            results.add(format_combination(ticket_type, (h,)))

    elif ticket_type in (TicketType.QUINELLA, TicketType.WIDE):
        # 順不同2頭
        for h1 in first_set:
            for h2 in second_set:
                if h1 != h2:
                    comb = tuple(sorted((h1, h2)))
                    results.add(format_combination(ticket_type, comb))

    elif ticket_type == TicketType.EXACTA:
        # 順序あり2頭 (1着-2着)
        for h1 in first_set:
            for h2 in second_set:
                if h1 != h2:
                    results.add(format_combination(ticket_type, (h1, h2)))

    elif ticket_type == TicketType.TRIO:
        # 順不同3頭
        for h1 in first_set:
            for h2 in second_set:
                if h1 == h2:
                    continue
                for h3 in third_set:
                    if h3 != h1 and h3 != h2:
                        comb = tuple(sorted((h1, h2, h3)))
                        results.add(format_combination(ticket_type, comb))

    elif ticket_type == TicketType.TRIFECTA:
        # 順序あり3頭 (1着-2着-3着)
        for h1 in first_set:
            for h2 in second_set:
                if h1 == h2:
                    continue
                for h3 in third_set:
                    if h3 != h1 and h3 != h2:
                        results.add(format_combination(ticket_type, (h1, h2, h3)))

    # 昇順ソートして安定した順序で返す
    def sort_key(item: str):
        parts = [int(p) for p in item.split("-") if p.isdigit()]
        return parts

    return sorted(list(results), key=sort_key)


def generate_box_bets(ticket_type: TicketType, horses: List[int]) -> List[str]:
    """ボックス展開関数

    選択した馬番群から指定券種のすべての組み合わせを生成
    """
    unique_horses = sorted(list(set(horses)))
    results: List[str] = []

    if ticket_type in (TicketType.WIN, TicketType.PLACE):
        results = [str(h) for h in unique_horses]

    elif ticket_type in (TicketType.QUINELLA, TicketType.WIDE):
        if len(unique_horses) >= 2:
            for comb in combinations(unique_horses, 2):
                results.append(format_combination(ticket_type, comb))

    elif ticket_type == TicketType.EXACTA:
        if len(unique_horses) >= 2:
            for comb in permutations(unique_horses, 2):
                results.append(format_combination(ticket_type, comb))

    elif ticket_type == TicketType.TRIO:
        if len(unique_horses) >= 3:
            for comb in combinations(unique_horses, 3):
                results.append(format_combination(ticket_type, comb))

    elif ticket_type == TicketType.TRIFECTA:
        if len(unique_horses) >= 3:
            for comb in permutations(unique_horses, 3):
                results.append(format_combination(ticket_type, comb))

    return results


def parse_combination_horses(combination: str) -> List[int]:
    """買い目文字列 ('1-4' や '1-2-4') を馬番リスト [1, 4] に変換"""
    try:
        return [int(x.strip()) for x in combination.split("-") if x.strip()]
    except ValueError:
        return []
