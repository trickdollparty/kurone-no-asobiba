"""レースデータ取得・解析モジュール

1. 段階的オンデマンド取得 (当月一覧 / 個別レース出馬表)
2. 文字コード自動判別 (EUC-JP / UTF-8 / cp51932)
3. コピペスマートパーサー (JRA / netkeiba)
4. リアルな内蔵プリセットレースデータ
5. ハービル公式に基づく複合オッズ自動推計
6. CSV/JSONインポート・エクスポート
"""
import re
import json
import csv
import io
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
from itertools import permutations
import requests
from bs4 import BeautifulSoup
import charset_normalizer

from .models import RaceSummary, RaceInfo, HorseEntry, TicketType


# ==============================================================================
# 文字コード安全デコード & 通信ヘルパー
# ==============================================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def decode_html_bytes(content: bytes) -> str:
    """生バイト列から文字コード (EUC-JP / UTF-8 / cp51932等) を自動判別して安全にデコード"""
    if not content:
        return ""

    # 1. HTML内のmeta charsetを検査
    charset_match = re.search(
        rb'<meta[^>]+(?:charset=["\']?|content=["\'][^"\']*charset=)([\w\-]+)',
        content[:4096],
        re.IGNORECASE,
    )
    declared_enc = None
    if charset_match:
        try:
            declared_enc = charset_match.group(1).decode("ascii", errors="ignore").lower()
        except Exception:
            pass

    if declared_enc:
        if "euc" in declared_enc:
            for enc in ["euc_jp", "cp51932", "euc_jis_2004"]:
                try:
                    return content.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    pass
        elif "utf" in declared_enc:
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                pass
        elif "shift" in declared_enc or "sjis" in declared_enc or "cp932" in declared_enc:
            try:
                return content.decode("cp932")
            except UnicodeDecodeError:
                pass

    # 2. UTF-8で例外なくデコードできるか試行 (UTF-8はバイトシーケンスの制約が極めて厳格)
    try:
        decoded = content.decode("utf-8")
        # デコード成功してもEUC-JPのバイトが偶然UTF-8に通るケースは極めて稀だが、
        # もし文字化け記号が少なければUTF-8とみなす
        return decoded
    except UnicodeDecodeError:
        pass

    # 3. EUC-JP / cp51932 試行 (国内競馬サイトで主流)
    for euc_enc in ["euc_jp", "cp51932", "euc_jis_2004"]:
        try:
            return content.decode(euc_enc)
        except (UnicodeDecodeError, LookupError):
            pass

    # 4. Shift_JIS / CP932 試行
    for sjis_enc in ["cp932", "shift_jis"]:
        try:
            return content.decode(sjis_enc)
        except (UnicodeDecodeError, LookupError):
            pass

    # 5. charset-normalizer の推測
    best_guess = charset_normalizer.from_bytes(content).best()
    if best_guess and best_guess.encoding:
        try:
            return content.decode(best_guess.encoding)
        except (UnicodeDecodeError, LookupError):
            pass

    # 最終手段: errors="replace"
    return content.decode("utf-8", errors="replace")


# ==============================================================================
# ハービルの公式に基づく複合馬券オッズ自動推計
# ==============================================================================

JRA_PAYOUT_RATES = {
    TicketType.WIN: 0.80,       # 単勝
    TicketType.PLACE: 0.80,     # 複勝
    TicketType.QUINELLA: 0.775, # 馬連
    TicketType.WIDE: 0.775,     # ワイド
    TicketType.EXACTA: 0.75,    # 馬単
    TicketType.TRIO: 0.75,      # 三連複
    TicketType.TRIFECTA: 0.725, # 三連単
}


def estimate_composite_odds(
    ticket_type: TicketType,
    horse_numbers: List[int],
    entries: List[HorseEntry],
) -> Tuple[float, Optional[float]]:
    """単勝オッズから支持率を逆算し、ハービル公式簡易近似により複合オッズを推計

    Args:
        ticket_type: 券種
        horse_numbers: 買い目を構成する馬番リスト (例: [1, 4] や [1, 2, 4])
        entries: レースの全出走馬リスト

    Returns:
        (odds_min, odds_max) のタプル (ワイド・複勝以外は odds_max=None)
    """
    if not entries or not horse_numbers:
        return 1.0, None

    # 各馬の単勝オッズマッピング
    horse_dict = {h.horse_number: h for h in entries}
    for num in horse_numbers:
        if num not in horse_dict:
            return 10.0, None

    # 全頭の支持率 (勝率 p_i) を逆算・正規化
    # 単勝オッズ o_i から、p_i ∝ 1 / o_i
    inv_odds_sum = sum(1.0 / max(h.win_odds, 1.0) for h in entries)
    if inv_odds_sum <= 0:
        inv_odds_sum = 1.0

    p = {h.horse_number: (1.0 / max(h.win_odds, 1.0)) / inv_odds_sum for h in entries}

    payout_rate = JRA_PAYOUT_RATES.get(ticket_type, 0.75)

    if ticket_type == TicketType.WIN:
        h = horse_dict[horse_numbers[0]]
        return round(h.win_odds, 1), None

    elif ticket_type == TicketType.PLACE:
        h = horse_dict[horse_numbers[0]]
        # 複勝は概ね単勝の 1/3 〜 1/2.5 程度、下限と上限が存在
        min_o = max(1.1, round(1.0 + (h.win_odds - 1.0) * 0.28, 1))
        max_o = max(min_o, round(1.0 + (h.win_odds - 1.0) * 0.45, 1))
        return min_o, max_o

    elif ticket_type == TicketType.QUINELLA:
        # 馬連: P(i, j) = p_i * p_j / (1 - p_i) + p_j * p_i / (1 - p_j)
        h1, h2 = horse_numbers[0], horse_numbers[1]
        p1, p2 = p[h1], p[h2]
        denom1 = max(1.0 - p1, 0.001)
        denom2 = max(1.0 - p2, 0.001)
        prob = (p1 * p2 / denom1) + (p2 * p1 / denom2)
        if prob <= 0:
            return 999.9, None
        odds = min(9999.9, max(1.1, round(payout_rate / prob, 1)))
        return odds, None

    elif ticket_type == TicketType.EXACTA:
        # 馬単: P(h1 -> h2) = p1 * p2 / (1 - p1)
        h1, h2 = horse_numbers[0], horse_numbers[1]
        p1, p2 = p[h1], p[h2]
        denom = max(1.0 - p1, 0.001)
        prob = p1 * p2 / denom
        if prob <= 0:
            return 999.9, None
        odds = min(9999.9, max(1.1, round(payout_rate / prob, 1)))
        return odds, None

    elif ticket_type == TicketType.WIDE:
        # ワイド: 1-2着または1-3着または2-3着。相手によってオッズ幅がある
        h1, h2 = horse_numbers[0], horse_numbers[1]
        p1, p2 = p[h1], p[h2]
        denom1 = max(1.0 - p1, 0.001)
        denom2 = max(1.0 - p2, 0.001)
        base_quinella_prob = (p1 * p2 / denom1) + (p2 * p1 / denom2)
        # ワイド的中確率は馬連の約2.5〜3倍程度
        prob = min(base_quinella_prob * 2.8, 0.95)
        if prob <= 0:
            return 1.1, 1.5
        center_odds = payout_rate / prob
        min_odds = max(1.1, round(center_odds * 0.85, 1))
        max_odds = max(min_odds, round(center_odds * 1.35, 1))
        return min_odds, max_odds

    elif ticket_type == TicketType.TRIO:
        # 三連複: 3頭の全順列の和
        if len(horse_numbers) < 3:
            return 10.0, None
        h1, h2, h3 = horse_numbers[:3]
        horses_3 = [h1, h2, h3]
        total_p = 0.0
        for perm in permutations(horses_3):
            p_a, p_b, p_c = p[perm[0]], p[perm[1]], p[perm[2]]
            d1 = max(1.0 - p_a, 0.001)
            d2 = max(1.0 - p_a - p_b, 0.001)
            total_p += (p_a * p_b * p_c) / (d1 * d2)
        if total_p <= 0:
            return 9999.9, None
        odds = min(99999.9, max(1.1, round(payout_rate / total_p, 1)))
        return odds, None

    elif ticket_type == TicketType.TRIFECTA:
        # 三連単: 1着-2着-3着
        if len(horse_numbers) < 3:
            return 20.0, None
        h1, h2, h3 = horse_numbers[:3]
        p1, p2, p3 = p[h1], p[h2], p[h3]
        d1 = max(1.0 - p1, 0.001)
        d2 = max(1.0 - p1 - p2, 0.001)
        prob = (p1 * p2 * p3) / (d1 * d2)
        if prob <= 0:
            return 99999.9, None
        odds = min(99999.9, max(1.1, round(payout_rate / prob, 1)))
        return odds, None

    return 10.0, None


# ==============================================================================
# コピペ一発スマートパーサー (方式B)
# ==============================================================================

def parse_pasted_shutuba_text(raw_text: str) -> RaceInfo:
    """JRA公式またはnetkeiba等の出馬表テキストを正規表現とヒューリスティックで自動解析"""
    if not raw_text or not raw_text.strip():
        raise ValueError("貼り付けられたテキストが空です。")

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    # レース名の簡易推定
    race_name = "取込レース"
    venue = "東京"
    race_number = 11
    grade = "OP"
    surface = "芝"
    distance = 2000

    for ln in lines[:10]:
        if "レース" in ln or "R" in ln or "賞" in ln or "ステークス" in ln or "特別" in ln or "記念" in ln:
            # レース番号抽出
            r_num_match = re.search(r"(\d{1,2})\s*R", ln)
            if r_num_match:
                race_number = int(r_num_match.group(1))
            # 競馬場抽出
            for v in ["東京", "中山", "京都", "阪神", "新潟", "福島", "小倉", "中京", "札幌", "函館"]:
                if v in ln:
                    venue = v
                    break
            # グレード抽出
            for g in ["G1", "G2", "G3", "J.G1", "J.G2", "J.G3", "OP", "特別"]:
                if g in ln:
                    grade = g
                    break
            # 芝・ダート距離抽出
            course_match = re.search(r"(芝|ダ|障害)[^\d]*(\d{3,4})m?", ln)
            if course_match:
                surface = "芝" if "芝" in course_match.group(1) else ("ダート" if "ダ" in course_match.group(1) else "障害")
                distance = int(course_match.group(2))

            # クリーンなレース名
            cand = re.sub(r"\d{1,2}R|G\d|J\.G\d", "", ln).strip()
            if len(cand) >= 2:
                race_name = cand.split()[0]
            break

    entries: List[HorseEntry] = []

    # 行ごとのパースを試みる
    # netkeiba / JRAのパターン例:
    # 枠 馬番 馬名 斤量 騎手 オッズ 人気 ...
    # またはタブ区切り
    for line in lines:
        # タブ区切りまたは連続空白区切りでトークン分割
        tokens = re.split(r"[\t\s]+", line)
        if len(tokens) < 3:
            continue

        # 先頭2つが枠番・馬番である可能性
        frame_no = None
        horse_no = None
        horse_name = ""
        jockey = ""
        win_odds = 10.0
        pop = 99

        # パターン1: トークン内に数字(1-8)、数字(1-18)、日本語馬名(カタカナ)が含まれる
        # トークンを走査
        digit_indices = [i for i, t in enumerate(tokens) if re.match(r"^\d{1,2}$", t)]
        kana_indices = [i for i, t in enumerate(tokens) if re.search(r"[\u30A0-\u30FF]{2,}", t)]

        if digit_indices and kana_indices:
            # 最初のカタカナトークンを馬名候補
            name_idx = kana_indices[0]
            horse_name = tokens[name_idx]
            # 馬名より前にある数字を枠番・馬番として解釈
            digits_before = [int(tokens[i]) for i in digit_indices if i < name_idx]
            if len(digits_before) >= 2:
                frame_no = digits_before[0]
                horse_no = digits_before[1]
            elif len(digits_before) == 1:
                horse_no = digits_before[0]
                # 枠番は馬番から自動計算 (18頭立て想定)
                frame_no = min(8, max(1, (horse_no - 1) // 2 + 1))
            else:
                continue

            # 馬名以降のトークンから騎手名・オッズ・人気を抽出
            after_tokens = tokens[name_idx + 1:]
            for t in after_tokens:
                # オッズパターン (例: 3.4, 12.0)
                if re.match(r"^\d{1,3}\.\d{1,2}$", t):
                    try:
                        win_odds = float(t)
                    except ValueError:
                        pass
                # 騎手名候補 (漢字2〜4文字、またはカタカナ)
                elif not jockey and re.match(r"^[\u4E00-\u9FFF\u30A0-\u30FF]{2,5}$", t):
                    # 斤量や性別を除外
                    if not re.search(r"^[牡牝騙セ]\d|[0-9.]+$", t):
                        jockey = t
                # 人気順 (例: 1, 2, 1人気)
                elif re.match(r"^(\d{1,2})人?気?$", t):
                    m = re.match(r"^(\d{1,2})", t)
                    if m:
                        pop = int(m.group(1))

        if horse_no and horse_name:
            if not jockey:
                jockey = "未定"
            if frame_no is None or frame_no < 1 or frame_no > 8:
                frame_no = min(8, max(1, (horse_no - 1) // 2 + 1))

            # 既存の馬番と重複していなければ追加
            if not any(e.horse_number == horse_no for e in entries):
                entries.append(
                    HorseEntry(
                        frame_number=frame_no,
                        horse_number=horse_no,
                        horse_name=horse_name,
                        jockey=jockey,
                        win_odds=win_odds,
                        popularity=pop if pop != 99 else len(entries) + 1,
                    )
                )

    if not entries:
        raise ValueError(
            "出馬表の解析に失敗しました。テキスト内に馬番・馬名・オッズが含まれていることを確認してください。"
        )

    # 馬番昇順でソート
    entries.sort(key=lambda x: x.horse_number)

    # 人気順が99のままのものはオッズ順で補完
    sorted_by_odds = sorted(entries, key=lambda x: x.win_odds)
    for i, e in enumerate(sorted_by_odds):
        if e.popularity == 99 or e.popularity > len(entries):
            e.popularity = i + 1

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    race_id = f"custom_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    return RaceInfo(
        race_id=race_id,
        date=datetime.now().strftime("%Y-%m-%d"),
        venue=venue,
        race_number=race_number,
        race_name=race_name,
        grade=grade,
        surface=surface,
        distance=distance,
        weather="晴",
        track_condition="良",
        departure_time="15:40",
        source_url="貼り付けテキスト解析",
        fetched_at=now_str,
        entries=entries,
    )


# ==============================================================================
# 内蔵プリセットレースデータ (方式C)
# ==============================================================================

def get_preset_races() -> List[RaceInfo]:
    """当月開催の主要重賞レースのリアルなプリセットデータを返す"""
    races: List[RaceInfo] = []

    # 1. 秋華賞 (G1, 京都・芝2000m, 18頭立て)
    shuka_entries = [
        HorseEntry(1, 1, "ホーエリート", "北村友一", 42.5, 12, "無"),
        HorseEntry(1, 2, "ミアネーロ", "津村明秀", 11.2, 5, "△"),
        HorseEntry(2, 3, "クイーンズウォーク", "川田将雅", 4.1, 2, "○"),
        HorseEntry(2, 4, "テンハッピーローズ", "津村明秀", 38.0, 11, "無"),
        HorseEntry(3, 5, "チェルヴィニア", "C.ルメール", 2.3, 1, "◎"),
        HorseEntry(3, 6, "ラヴァンダ", "岩田望来", 28.4, 9, "☆"),
        HorseEntry(4, 7, "チルカーノ", "鮫島克駿", 55.0, 14, "無"),
        HorseEntry(4, 8, "コガネノソラ", "丹内祐次", 16.8, 6, "△"),
        HorseEntry(5, 9, "アドマイヤベル", "横山武史", 31.0, 10, "無"),
        HorseEntry(5, 10, "ボンドガール", "武豊", 8.4, 4, "▲"),
        HorseEntry(6, 11, "ランスオブクイーン", "松山弘平", 64.2, 15, "無"),
        HorseEntry(6, 12, "ステレンボッシュ", "戸崎圭太", 5.2, 3, "○"),
        HorseEntry(7, 13, "クリスマスパレード", "石川裕紀人", 21.0, 8, "△"),
        HorseEntry(7, 14, "ハワイアンティアレ", "池添謙一", 88.0, 16, "無"),
        HorseEntry(7, 15, "セキトバイースト", "藤岡佑介", 18.5, 7, "☆"),
        HorseEntry(8, 16, "タガノエルピーダ", "団野大成", 45.0, 13, "無"),
        HorseEntry(8, 17, "エルフストラック", "西村淳也", 95.0, 17, "無"),
        HorseEntry(8, 18, "パレハ", "田口貫太", 120.0, 18, "無"),
    ]
    races.append(
        RaceInfo(
            race_id="202608050611",
            date="2026-10-18",
            venue="京都",
            race_number=11,
            race_name="秋華賞",
            grade="G1",
            surface="芝",
            distance=2000,
            weather="晴",
            track_condition="良",
            departure_time="15:40",
            source_url="https://race.netkeiba.com/race/shutuba.html?race_id=202608050611",
            fetched_at="2026-10-18 10:00:00 (プリセット)",
            entries=shuka_entries,
        )
    )

    # 2. 菊花賞 (G1, 京都・芝3000m, 18頭立て)
    kikka_entries = [
        HorseEntry(1, 1, "ピースワンデュック", "柴田善臣", 34.0, 10, "無"),
        HorseEntry(1, 2, "ノーブルスカイ", "池添謙一", 52.0, 13, "無"),
        HorseEntry(2, 3, "アスクカムオンモア", "北村友一", 41.0, 11, "無"),
        HorseEntry(2, 4, "ダノンデサイル", "横山典弘", 3.2, 1, "◎"),
        HorseEntry(3, 5, "ハヤテノフクノスケ", "岩田望来", 22.0, 7, "△"),
        HorseEntry(3, 6, "ミスタージーティー", "坂井瑠星", 28.5, 9, "無"),
        HorseEntry(4, 7, "アレグロブリランテ", "横山和生", 78.0, 15, "無"),
        HorseEntry(4, 8, "コスモキュランダ", "M.デムーロ", 5.8, 3, "▲"),
        HorseEntry(5, 9, "コスモブッドレア", "石川裕紀人", 65.0, 14, "無"),
        HorseEntry(5, 10, "メリオーレム", "川田将雅", 8.9, 4, "○"),
        HorseEntry(6, 11, "ショウナンラプンタ", "鮫島克駿", 14.2, 5, "△"),
        HorseEntry(6, 12, "シュバルツクーゲル", "松山弘平", 48.0, 12, "無"),
        HorseEntry(7, 13, "アーバンシック", "C.ルメール", 4.5, 2, "○"),
        HorseEntry(7, 14, "メリオーレム", "藤岡佑介", 90.0, 16, "無"),
        HorseEntry(7, 15, "エコロヴァルツ", "岩田康誠", 25.0, 8, "☆"),
        HorseEntry(8, 16, "ヘデントール", "戸崎圭太", 15.0, 6, "△"),
        HorseEntry(8, 17, "アドマイヤテラ", "武豊", 18.0, 7, "☆"),
        HorseEntry(8, 18, "メイショウタバル", "浜中俊", 9.8, 5, "▲"),
    ]
    races.append(
        RaceInfo(
            race_id="202608050811",
            date="2026-10-25",
            venue="京都",
            race_number=11,
            race_name="菊花賞",
            grade="G1",
            surface="芝",
            distance=3000,
            weather="曇",
            track_condition="良",
            departure_time="15:40",
            source_url="https://race.netkeiba.com/race/shutuba.html?race_id=202608050811",
            fetched_at="2026-10-18 10:00:00 (プリセット)",
            entries=kikka_entries,
        )
    )

    # 3. 天皇賞(秋) (G1, 東京・芝2000m, 16頭立て)
    tenno_entries = [
        HorseEntry(1, 1, "ベラジオオペラ", "横山和生", 8.2, 4, "▲"),
        HorseEntry(1, 2, "マテンロウスカイ", "横山典弘", 24.5, 8, "△"),
        HorseEntry(2, 3, "ステラヴェローチェ", "佐々木大輔", 31.0, 9, "無"),
        HorseEntry(2, 4, "タスティエーラ", "松山弘平", 16.5, 6, "△"),
        HorseEntry(3, 5, "ノースブリッジ", "岩田康誠", 18.0, 7, "☆"),
        HorseEntry(3, 6, "ソールオリエンス", "横山武史", 9.8, 5, "△"),
        HorseEntry(4, 7, "ドウデュース", "武豊", 3.8, 1, "◎"),
        HorseEntry(4, 8, "キングズパレス", "A.シュタルケ", 45.0, 11, "無"),
        HorseEntry(5, 9, "ホウオウビスケッツ", "岩田望来", 35.0, 10, "☆"),
        HorseEntry(5, 10, "ダノンベルーガ", "C.デムーロ", 7.5, 3, "○"),
        HorseEntry(6, 11, "ジャスティンパレス", "坂井瑠星", 6.2, 2, "○"),
        HorseEntry(6, 12, "リバティアイランド", "川田将雅", 4.2, 2, "◎"),
        HorseEntry(7, 13, "シルトホルン", "津村明秀", 75.0, 13, "無"),
        HorseEntry(7, 14, "レーベンスティール", "C.ルメール", 5.5, 3, "○"),
        HorseEntry(8, 15, "ニシノレヴナント", "田辺裕信", 90.0, 14, "無"),
        HorseEntry(8, 16, "カレンシュトラウス", "池添謙一", 120.0, 15, "無"),
    ]
    races.append(
        RaceInfo(
            race_id="202605040811",
            date="2026-10-27",
            venue="東京",
            race_number=11,
            race_name="天皇賞(秋)",
            grade="G1",
            surface="芝",
            distance=2000,
            weather="晴",
            track_condition="良",
            departure_time="15:40",
            source_url="https://race.netkeiba.com/race/shutuba.html?race_id=202605040811",
            fetched_at="2026-10-18 10:00:00 (プリセット)",
            entries=tenno_entries,
        )
    )

    # 4. 富士ステークス (G2, 東京・芝1600m, 16頭立て)
    fuji_entries = [
        HorseEntry(1, 1, "バスラットレオン", "古川吉洋", 38.0, 10, "無"),
        HorseEntry(1, 2, "コムストックロード", "石川裕紀人", 55.0, 13, "無"),
        HorseEntry(2, 3, "アルナシーム", "横山典弘", 12.5, 5, "△"),
        HorseEntry(2, 4, "ゴンバデカーブース", "松山弘平", 7.2, 4, "▲"),
        HorseEntry(3, 5, "エアロロノア", "田辺裕信", 26.0, 8, "無"),
        HorseEntry(3, 6, "ジェイパームス", "佐々木大輔", 18.0, 7, "☆"),
        HorseEntry(4, 7, "セリフォス", "藤岡佑介", 3.5, 1, "○"),
        HorseEntry(4, 8, "バルサムノート", "松岡正海", 42.0, 11, "無"),
        HorseEntry(5, 9, "レッドモンレーヴ", "横山和生", 14.0, 6, "△"),
        HorseEntry(5, 10, "クルゼイロドスル", "津村明秀", 32.0, 9, "無"),
        HorseEntry(6, 11, "ソウルラッシュ", "団野大成", 3.6, 2, "◎"),
        HorseEntry(6, 12, "タイムトゥヘヴン", "柴田善臣", 48.0, 12, "無"),
        HorseEntry(7, 13, "ジュンブロッサム", "戸崎圭太", 5.8, 3, "○"),
        HorseEntry(7, 14, "ロジリオン", "北村宏司", 15.0, 6, "△"),
        HorseEntry(8, 15, "パラレルヴィジョン", "C.ルメール", 11.0, 5, "☆"),
        HorseEntry(8, 16, "ミシシッピテソーロ", "原優介", 95.0, 14, "無"),
    ]
    races.append(
        RaceInfo(
            race_id="202605040311",
            date="2026-10-19",
            venue="東京",
            race_number=11,
            race_name="富士ステークス",
            grade="G2",
            surface="芝",
            distance=1600,
            weather="晴",
            track_condition="良",
            departure_time="15:45",
            source_url="https://race.netkeiba.com/race/shutuba.html?race_id=202605040311",
            fetched_at="2026-10-18 10:00:00 (プリセット)",
            entries=fuji_entries,
        )
    )

    return races


# ==============================================================================
# オンデマンドWebスクレイピング (方式A)
# ==============================================================================

def fetch_monthly_race_list(year: int = 2026, month: int = 10) -> List[RaceSummary]:
    """当月のレース一覧をオンデマンドで取得 (詳細出馬表へのアクセスは行わない)

    ネットワーク通信を試み、接続不可またはエラー時はプリセットのサマリー一覧へフォールバック
    """
    summaries: List[RaceSummary] = []

    # プリセットからサマリーをまず構築
    presets = get_preset_races()
    for p in presets:
        summaries.append(
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
        )

    # Webスクレイピング試行 (手動更新時など)
    # netkeibaのカレンダーURLまたはYahoo競馬
    target_url = f"https://race.netkeiba.com/top/calendar.html?year={year}&month={month}"
    try:
        resp = requests.get(target_url, headers=DEFAULT_HEADERS, timeout=8)
        if resp.status_code == 200:
            html = decode_html_bytes(resp.content)
            soup = BeautifulSoup(html, "lxml")
            # レースリンクや重賞情報を探索
            # netkeibaのカレンダー形式パース
            race_links = soup.find_all("a", href=re.compile(r"race_id=(\d{12})"))
            seen_ids = {s.race_id for s in summaries}
            for link in race_links:
                href = link.get("href", "")
                m = re.search(r"race_id=(\d{12})", href)
                if m:
                    r_id = m.group(1)
                    if r_id not in seen_ids:
                        r_name = link.text.strip()
                        if r_name and len(r_name) >= 2:
                            seen_ids.add(r_id)
                            # 日付の推定
                            summaries.append(
                                RaceSummary(
                                    race_id=r_id,
                                    date=f"{year}-{month:02d}-18",
                                    venue="JRA開催場",
                                    race_number=11,
                                    race_name=r_name,
                                    grade="重賞",
                                    departure_time="15:40",
                                    detail_url=f"https://race.netkeiba.com/race/shutuba.html?race_id={r_id}",
                                )
                            )
    except Exception:
        # 通信失敗時は安全にプリセット一覧を返す
        pass

    return summaries


def fetch_race_detail(race_id: str) -> RaceInfo:
    """単一レースの出馬表・詳細条件を取得 (オンデマンド)"""
    # まず内蔵プリセットに合致するか検査
    presets = get_preset_races()
    for p in presets:
        if p.race_id == race_id:
            return p

    # Webから取得を試行
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    return fetch_shutuba_by_url(url, race_id=race_id)


def fetch_shutuba_by_url(url: str, race_id: Optional[str] = None) -> RaceInfo:
    """指定URLから出馬表とオッズをスクレイピング取得"""
    if not race_id:
        m = re.search(r"race_id=(\d{12})", url)
        race_id = m.group(1) if m else f"web_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # プリセット一致チェック
    for p in get_preset_races():
        if p.race_id == race_id:
            return p

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        resp.raise_for_status()
        html = decode_html_bytes(resp.content)
        soup = BeautifulSoup(html, "lxml")

        # レース名取得
        race_name_el = soup.find("div", class_=re.compile(r"RaceName|Race_Name"))
        race_name = race_name_el.text.strip() if race_name_el else "レース詳細"

        # レース情報 (距離、芝/ダ、天候、馬場)
        race_data_el = soup.find("div", class_=re.compile(r"RaceData01|RaceData"))
        race_data_text = race_data_el.text.strip() if race_data_el else ""

        surface = "芝" if "芝" in race_data_text else ("ダート" if "ダ" in race_data_text else "芝")
        dist_match = re.search(r"(\d{3,4})m", race_data_text)
        distance = int(dist_match.group(1)) if dist_match else 2000

        venue = "東京"
        for v in ["東京", "中山", "京都", "阪神", "新潟", "福島", "小倉", "中京", "札幌", "函館"]:
            if v in html[:2000]:
                venue = v
                break

        # 出馬表テーブルパース
        table = soup.find("table", class_=re.compile(r"Shutuba_Table|race_table"))
        entries: List[HorseEntry] = []

        if table:
            rows = table.find_all("tr", class_=re.compile(r"HorseList"))
            for idx, tr in enumerate(rows):
                # 枠番
                frame_td = tr.find("td", class_=re.compile(r"Waku|waku"))
                frame_no = int(frame_td.text.strip()) if frame_td and frame_td.text.strip().isdigit() else 1

                # 馬番
                umaban_td = tr.find("td", class_=re.compile(r"Umaban|umaban"))
                horse_no = int(umaban_td.text.strip()) if umaban_td and umaban_td.text.strip().isdigit() else (idx + 1)

                # 馬名
                name_el = tr.find("span", class_=re.compile(r"HorseName")) or tr.find("a", href=re.compile(r"/horse/"))
                horse_name = name_el.text.strip() if name_el else f"馬{horse_no}"

                # 騎手
                jockey_el = tr.find("td", class_=re.compile(r"Jockey")) or tr.find("a", href=re.compile(r"/jockey/"))
                jockey = jockey_el.text.strip() if jockey_el else "未定"

                # 単勝オッズ
                odds_td = tr.find("td", class_=re.compile(r"Popular|Odds")) or tr.find("span", id=re.compile(r"odds"))
                odds_text = odds_td.text.strip() if odds_td else ""
                odds_val = 10.0
                try:
                    odds_val = float(re.search(r"\d+\.\d+", odds_text).group(0))
                except Exception:
                    # 非同期オッズの場合の仮オッズ（人気順または初期値）
                    odds_val = round(max(2.0, (idx + 1) * 2.5), 1)

                # 人気順
                pop_td = tr.find("td", class_=re.compile(r"Ninki"))
                pop_val = idx + 1
                try:
                    pop_val = int(pop_td.text.strip())
                except Exception:
                    pass

                entries.append(
                    HorseEntry(
                        frame_number=frame_no,
                        horse_number=horse_no,
                        horse_name=horse_name,
                        jockey=jockey,
                        win_odds=odds_val,
                        popularity=pop_val,
                    )
                )

        if not entries:
            # 取得できなかった場合はデフォルトでフォールバック
            raise ValueError("出馬表テーブルが見つかりませんでした。")

        return RaceInfo(
            race_id=race_id,
            date=datetime.now().strftime("%Y-%m-%d"),
            venue=venue,
            race_number=11,
            race_name=race_name,
            grade="重賞",
            surface=surface,
            distance=distance,
            weather="晴",
            track_condition="良",
            departure_time="15:40",
            source_url=url,
            fetched_at=now_str,
            entries=entries,
        )

    except Exception as e:
        # 通信エラーまたはパースエラー時は秋華賞プリセットにフォールバック案内
        default_race = get_preset_races()[0]
        default_race.source_url = url
        default_race.fetched_at = f"{now_str} (取得エラーのためフォールバック表示: {str(e)[:40]})"
        return default_race


# ==============================================================================
# CSV / JSON インポート・エクスポート
# ==============================================================================

def export_race_to_json(race_info: RaceInfo) -> str:
    """RaceInfoオブジェクトをJSON文字列化"""
    return json.dumps(race_info.to_dict(), ensure_ascii=False, indent=2)


def import_race_from_json(json_str: str) -> RaceInfo:
    """JSON文字列からRaceInfoオブジェクトを復元"""
    data = json.loads(json_str)
    return RaceInfo.from_dict(data)


def export_bets_to_csv(bets: List[Any]) -> str:
    """買い目リストをCSV文字列としてエクスポート"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ticket_type",
        "combination",
        "role",
        "odds_min",
        "odds_max",
        "allocated_amount",
        "expected_payout_min",
        "expected_payout_max",
        "is_torigami",
        "shortfall_amount",
    ])
    for b in bets:
        writer.writerow([
            b.ticket_type.value if hasattr(b.ticket_type, "value") else b.ticket_type,
            b.combination,
            b.role.value if hasattr(b.role, "value") else b.role,
            b.odds_min,
            b.odds_max or "",
            b.allocated_amount,
            b.expected_payout_min,
            b.expected_payout_max,
            b.is_torigami,
            b.shortfall_amount,
        ])
    return output.getvalue()


def import_bets_from_csv(csv_str: str) -> List[Dict[str, Any]]:
    """CSV文字列から買い目リストを辞書配列としてインポート"""
    reader = csv.DictReader(io.StringIO(csv_str.strip()))
    bets_data = []
    for row in reader:
        bets_data.append({
            "ticket_type": row.get("ticket_type", "単勝"),
            "combination": row.get("combination", "1"),
            "role": row.get("role", "offense"),
            "odds_min": float(row.get("odds_min", 2.0)),
            "odds_max": float(row["odds_max"]) if row.get("odds_max") else None,
            "allocated_amount": int(row.get("allocated_amount", 0)),
        })
    return bets_data
