"""コース分析およびレース条件別 有利馬診断モジュール (STEP 0 用)

デジタル庁デザインシステム (DADS) の見やすいUIに適合する、
競馬場・コース・距離・馬場状態の分析と、有利な出走馬の自動診断ロジックを提供します。
"""
from typing import Dict, Any, List, Optional
from .models import RaceInfo, HorseEntry


# ==============================================================================
# JRA主要コース特徴・展開傾向データベース
# ==============================================================================

COURSE_PROFILES: Dict[tuple, Dict[str, str]] = {
    # 京都
    ("京都", "芝", 2000): {
        "layout": "内回り・直線328m (平坦)",
        "description": "正面スタンド前直線半ばから発走。1コーナーまでの直線が約300mと短いため内枠（1〜4枠）がロスなく先行ポジションを取りやすい。3コーナー手前から上り坂、4コーナーにかけて名物「淀の坂」を下る構造。下りから各馬がスパートするためロングスパート適性が問われ、直線が平坦で短いため先行馬・好位差し馬が圧倒的に有利。",
        "advantage_frame": "内枠（1〜4枠）有利",
        "advantage_style": "逃げ・先行・好位抜け出し",
    },
    ("京都", "芝", 2200): {
        "layout": "外回り・直線404m (平坦)",
        "description": "外回りコース使用。向正面が長く、淀の坂の下りから一気にペースアップ。直線は平坦で400m超と長いため、末脚の持続力を持つ差し・先行馬が活躍しやすい。",
        "advantage_frame": "中枠〜外枠",
        "advantage_style": "先行・持続力型差し",
    },
    ("京都", "芝", 1600): {
        "layout": "外回り・直線404m (平坦)",
        "description": "2コーナー奥の引き込み線スタート。淀の坂を登り切ってからの下りを利用して直線へ。直線が長く平坦なため、瞬発力と切れ味勝負になりやすい。",
        "advantage_frame": "フラット",
        "advantage_style": "先行・差し",
    },
    # 東京
    ("東京", "芝", 2400): {
        "layout": "本馬場1周・直線525.9m (高低差2.1m上り坂)",
        "description": "日本ダービー・ジャパンCで使用される日本屈指のチャンピオンコース。正面スタンド前からスタートし広大な馬場を1周。直線の長さと最後に待ち受ける急坂のため、真のスタミナ・瞬発力・地力が問われる。枠順の有利不利は少なく実力通り決まりやすい。",
        "advantage_frame": "枠順不問（フラット）",
        "advantage_style": "差し・長く良い脚を使える馬",
    },
    ("東京", "芝", 2000): {
        "layout": "直線525.9m (スタート直後にカーブ)",
        "description": "天皇賞（秋）の舞台。2コーナー奥のポケットからスタートし、発走後わずか100m余りで本線に合流するため外枠は強烈な距離ロスを被る「日本一外枠不利」なコース。1〜3枠を引いた先行・好位馬が絶好の恩恵を受ける。",
        "advantage_frame": "1〜3枠が絶対的有利",
        "advantage_style": "先行・好位差し",
    },
    ("東京", "芝", 1600): {
        "layout": "直線525.9m (安田記念・NHKマイルC)",
        "description": "向正面の奥からスタート。スタート直後に緩やかな下り坂がありペースが淀みなく流れやすい。直線が長く坂もあるため、単なるスピードだけでなくタフな末脚の持続力が必須。",
        "advantage_frame": "中枠〜外枠やや有利",
        "advantage_style": "差し・好位差し",
    },
    # 中山
    ("中山", "芝", 2500): {
        "layout": "内回り・直線310m (高低差2.2m急坂)",
        "description": "有馬記念の舞台。外回り3コーナー手前からスタートし、コーナーを6回通過するトリッキーなコース。直線の急坂を2回越えるためスタミナと立ち回りの器用さが必須。外枠は距離ロスが響くため内枠の先行・好位勢が有利。",
        "advantage_frame": "内枠有利（1〜4枠）",
        "advantage_style": "先行・好位抜け出し・機動力差し",
    },
    ("中山", "芝", 2000): {
        "layout": "内回り・直線310m (皐月賞)",
        "description": "正面スタンド前直線の急坂下からスタート。スタート後すぐに坂を登るため前半のペースは落ち着きやすい。小回りかつ直線が短いため、先行力と小回り適性が直結する。",
        "advantage_frame": "内枠〜中枠有利",
        "advantage_style": "先行・好位差し",
    },
    ("中山", "芝", 1800): {
        "layout": "内回り・スタンド前発走",
        "description": "スタート直後に急坂を登り、すぐに1コーナーを迎えるため外枠は外を回されやすい。内枠先行馬の信頼度が高い。",
        "advantage_frame": "内枠有利",
        "advantage_style": "逃げ・先行",
    },
    # 阪神
    ("阪神", "芝", 1600): {
        "layout": "外回り・直線473.6m (急坂あり)",
        "description": "桜花賞・朝日杯FS・阪神JFの舞台。向正面が長く3〜4コーナーがゆったりした大回りコース。直線が長く急坂もあるため、上がり最速の瞬発力を持つ差し馬が鋭く伸びる。",
        "advantage_frame": "フラット〜外枠",
        "advantage_style": "瞬発力型差し・好位",
    },
    ("阪神", "芝", 2000): {
        "layout": "内回り・直線356.5m (大阪杯)",
        "description": "内回りコース使用。直線が短くゴール前に急坂。コーナリングの器用さと4コーナーでの位置取りが勝敗を分けるため、先行・好位勢が優勢。",
        "advantage_frame": "内枠〜中枠",
        "advantage_style": "先行・好位",
    },
    ("阪神", "芝", 2200): {
        "layout": "内回り・直線356.5m (宝塚記念)",
        "description": "スタンド前発走で内回りを1周強。前半下り坂からハイペースになりやすく、タフなスタミナ勝負になる。直線が短いため早めに動ける機動力のある馬が有利。",
        "advantage_frame": "フラット",
        "advantage_style": "好位差し・捲り",
    },
}


def analyze_course(venue: str, surface: str, distance: int, track_condition: str = "良") -> Dict[str, str]:
    """競馬場・サーフェス・距離・馬場状態からコース特性・展開傾向を分析"""
    key = (venue, surface, distance)
    if key in COURSE_PROFILES:
        prof = COURSE_PROFILES[key].copy()
    else:
        # 汎用フォールバック
        is_turf = "芝" in surface
        dist_cat = "短距離" if distance <= 1400 else ("マイル" if distance <= 1600 else ("中距離" if distance <= 2200 else "長距離"))
        
        if is_turf:
            if dist_cat == "短距離":
                prof = {
                    "layout": f"{venue} {surface}{distance}m",
                    "description": f"{venue}の{surface}{distance}m戦。スタート直後からのスピード争いが激しく、先行力が直結します。基本は前残り傾向が強いレイアウトです。",
                    "advantage_frame": "内枠〜中枠有利",
                    "advantage_style": "逃げ・先行",
                }
            elif dist_cat == "長距離":
                prof = {
                    "layout": f"{venue} {surface}{distance}m (長距離)",
                    "description": f"{venue}の{distance}m長距離戦。ペース配分とスタミナ、折り合いの良さが最重要。距離ロスを最小限に抑えられる内枠や経済コースを通れる馬が有利です。",
                    "advantage_frame": "内枠有利",
                    "advantage_style": "好位差し・先行",
                }
            else:
                prof = {
                    "layout": f"{venue} {surface}{distance}m ({dist_cat})",
                    "description": f"{venue}競馬場の{surface}{distance}mコース。実力馬が力を発揮しやすいスタンダードな設計で、直線でのポジショニングと末脚の持続力が勝負の分かれ目となります。",
                    "advantage_frame": "枠順フラット",
                    "advantage_style": "先行・好位差し",
                }
        else:
            # ダート
            prof = {
                "layout": f"{venue} ダート{distance}m",
                "description": f"{venue}ダート{distance}m。砂のキックバックを受けにくい先行馬、および外をスムーズに回せる外枠が有利になりやすいコース構造です。",
                "advantage_frame": "中枠〜外枠有利",
                "advantage_style": "先行・好位抜け出し",
            }

    # 馬場状態の補足を追記
    cond = track_condition or "良"
    if cond in ("重", "不良"):
        prof["description"] += f" 【馬場注意: {cond}】水分を含んだ馬場のため時計が速くなり、前に行った馬が止まりにくい前残りバイアスが強まる傾向にあります。"
    elif cond == "稍重":
        prof["description"] += f" 【馬場注意: {cond}】やや時計の出やすいコンディションで、スピードの持続力とパワーが求められます。"

    return prof


def diagnose_advantageous_horses(race: RaceInfo) -> Dict[str, Any]:
    """出走馬データとレース条件から、本レースで有利な馬（最有力、展開利、穴注目）を自動診断"""
    if not race.entries:
        return {"honmei": None, "tactical": None, "upset": None}

    course_info = analyze_course(race.venue, race.surface, race.distance, race.track_condition)

    # 有力馬候補の選定
    entries_sorted_by_odds = sorted(race.entries, key=lambda x: x.win_odds)
    
    # 1. 最有力馬 (本命推奨)
    # ◎印を持つ馬、いなければ単勝1番人気
    honmei_candidates = [e for e in race.entries if e.mark == "◎"]
    honmei = honmei_candidates[0] if honmei_candidates else entries_sorted_by_odds[0]

    # 2. 展開利・コース適性馬 (特注馬)
    # コースが内枠有利なら1〜4枠の上位人気馬、外枠有利なら5〜8枠、それ以外は2〜4番人気の実力馬
    is_inner_adv = "内枠" in course_info["advantage_frame"]
    tactical = None

    for e in entries_sorted_by_odds:
        if e.horse_number == honmei.horse_number:
            continue
        if is_inner_adv and e.frame_number <= 4 and e.win_odds <= 15.0:
            tactical = e
            break
        elif not is_inner_adv and e.mark in ("○", "▲"):
            tactical = e
            break

    if not tactical:
        tactical = entries_sorted_by_odds[1] if len(entries_sorted_by_odds) > 1 else honmei

    # 3. 穴注目馬 (高配当・妙味狙い)
    # 単勝8.0倍〜45.0倍の中から、印（▲・△・☆）がある馬、または上位騎手騎乗の馬
    elite_jockeys = ["ルメール", "川田", "武豊", "戸崎", "坂井", "松山", "横山武", "モレイラ", "レーン", "デムーロ"]
    upset = None

    for e in entries_sorted_by_odds:
        if e.horse_number in (honmei.horse_number, tactical.horse_number):
            continue
        if 8.0 <= e.win_odds <= 45.0:
            if e.mark in ("▲", "△", "☆") or any(j in (e.jockey or "") for j in elite_jockeys):
                upset = e
                break

    if not upset:
        # 中穴ゾーンから選出
        candidates_mid = [e for e in entries_sorted_by_odds if 8.0 <= e.win_odds <= 50.0 and e.horse_number not in (honmei.horse_number, tactical.horse_number)]
        if candidates_mid:
            upset = candidates_mid[0]
        elif len(entries_sorted_by_odds) > 2:
            upset = entries_sorted_by_odds[2]
        else:
            upset = honmei

    # 推奨理由テキストの構築
    honmei_reason = (
        f"単勝{honmei.popularity}番人気・オッズ{honmei.win_odds:.1f}倍。メンバー屈指の能力と安定度を誇り、"
        f"{race.venue}{race.distance}mの舞台設定でも軸馬として最も信頼度が高い中心馬です。"
    )

    tactical_reason = (
        f"枠番{tactical.frame_number}枠{tactical.horse_number}番（{tactical.jockey}騎手）。"
        f"当コースの「{course_info['advantage_frame']}・{course_info['advantage_style']}」傾向に最も合致し、"
        f"展開・位置取りの恩恵を最大限に受けて上位争いに食い込む好配当の要です。"
    )

    upset_reason = (
        f"単勝{upset.popularity}番人気・オッズ{upset.win_odds:.1f}倍。妙味十分のオッズ帯でありながら、"
        f"舞台適性や展開次第での一発を秘めており、高配当・万馬券を狙う上で見逃せない特注穴馬です。"
    )

    return {
        "course_info": course_info,
        "honmei": {"entry": honmei, "reason": honmei_reason},
        "tactical": {"entry": tactical, "reason": tactical_reason},
        "upset": {"entry": upset, "reason": upset_reason},
    }
