from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any


class TicketType(str, Enum):
    """馬券の券種定義"""
    WIN = "単勝"
    PLACE = "複勝"
    QUINELLA = "馬連"
    EXACTA = "馬単"
    WIDE = "ワイド"
    TRIO = "三連複"
    TRIFECTA = "三連単"


class BetRole(str, Enum):
    """買い目の役割定義"""
    DEFENSE = "defense"  # 守り（保険・元金回収）
    OFFENSE = "offense"  # 攻め（高配当・万馬券狙い）


class StrategyMode(str, Enum):
    """資金配分戦略モード"""
    HYBRID = "hybrid"              # ハイブリッド戦略（守り元金回収＋攻め万馬券狙い）
    EQUAL_PAYOUT = "equal_payout"  # 均等払戻（傾斜配分）戦略
    INSURANCE = "insurance"        # 元返し保険（1点集中ヘッジ＋残金均等/傾斜）戦略


@dataclass
class RaceSummary:
    """当月レース一覧表示用の軽量データクラス（詳細ページアクセス前）"""
    race_id: str                     # レースID (例: "202608050611")
    date: str                        # 開催日 (例: "2026-10-18")
    venue: str                       # 開催場 (例: "京都", "東京", "阪神", "中山")
    race_number: int                 # レース番号 (例: 11)
    race_name: str                   # レース名 (例: "秋華賞")
    grade: str = ""                  # グレード ("G1", "G2", "G3", "OP", "条件" 等)
    departure_time: str = ""         # 発走時刻 (例: "15:40")
    detail_url: Optional[str] = None # 詳細（出馬表）ページのURL

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RaceSummary":
        return cls(**data)


@dataclass
class HorseEntry:
    """出走馬データクラス"""
    frame_number: int                # 枠番 (1〜8)
    horse_number: int                # 馬番 (1〜18)
    horse_name: str                  # 馬名
    jockey: str                      # 騎手名
    win_odds: float                  # 単勝オッズ
    popularity: int                  # 人気順
    mark: str = "無"                 # 予想印（◎, ○, ▲, △, ☆, 無）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HorseEntry":
        return cls(**data)


@dataclass
class RaceInfo:
    """個別レース選択時に取得される詳細情報＆出馬表データクラス"""
    race_id: str                     # レースID (例: "202608050611" または内部ID)
    date: str                        # 開催日 (例: "2026-10-18")
    venue: str                       # 開催場 (例: "京都", "東京", "阪神", "中山")
    race_number: int                 # レース番号 (例: 11)
    race_name: str                   # レース名 (例: "秋華賞", "東京ハイジャンプ", "富士ステークス" など)
    grade: str                       # グレード ("G1", "G2", "G3", "OP", "特別", "条件")
    surface: str                     # トラック ("芝", "ダート", "障害")
    distance: int                    # 距離 (m)
    weather: str = "晴"              # 天候
    track_condition: str = "良"      # 馬場状態
    departure_time: str = "15:40"    # 発走時刻
    source_url: Optional[str] = None # 取得元URL
    fetched_at: Optional[str] = None # 最終取得日時タイムスタンプ
    entries: List[HorseEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RaceInfo":
        entries_data = data.pop("entries", [])
        entries = [HorseEntry.from_dict(e) if isinstance(e, dict) else e for e in entries_data]
        return cls(entries=entries, **data)


@dataclass
class SingleBet:
    """個別の買い目データクラス"""
    ticket_type: TicketType
    combination: str                 # 例: '1-4', '1-2-4'（馬連・ワイド・三連複は昇順正規化）
    odds_min: float                  # 想定オッズ（ワイド・複勝の場合は下限オッズ）
    odds_max: Optional[float] = None # ワイド・複勝の上限オッズ（未指定時は odds_min と同値）
    role: BetRole = BetRole.OFFENSE
    allocated_amount: int = 0        # 配分金額（円・100の倍数）
    expected_payout_min: float = 0.0 # 的中時の最低想定払戻金
    expected_payout_max: float = 0.0 # 的中時の最高想定払戻金
    is_torigami: bool = False        # トリガミ判定フラグ
    shortfall_amount: int = 0        # トリガミ時の不足金額（円）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_type": self.ticket_type.value if isinstance(self.ticket_type, TicketType) else self.ticket_type,
            "combination": self.combination,
            "odds_min": self.odds_min,
            "odds_max": self.odds_max,
            "role": self.role.value if isinstance(self.role, BetRole) else self.role,
            "allocated_amount": self.allocated_amount,
            "expected_payout_min": self.expected_payout_min,
            "expected_payout_max": self.expected_payout_max,
            "is_torigami": self.is_torigami,
            "shortfall_amount": self.shortfall_amount,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SingleBet":
        ticket_type = TicketType(data["ticket_type"]) if isinstance(data["ticket_type"], str) else data["ticket_type"]
        role = BetRole(data["role"]) if isinstance(data["role"], str) else data["role"]
        return cls(
            ticket_type=ticket_type,
            combination=data["combination"],
            odds_min=float(data["odds_min"]),
            odds_max=float(data["odds_max"]) if data.get("odds_max") is not None else None,
            role=role,
            allocated_amount=int(data.get("allocated_amount", 0)),
            expected_payout_min=float(data.get("expected_payout_min", 0.0)),
            expected_payout_max=float(data.get("expected_payout_max", 0.0)),
            is_torigami=bool(data.get("is_torigami", False)),
            shortfall_amount=int(data.get("shortfall_amount", 0)),
        )


@dataclass
class SimulationConfig:
    """シミュレーション実行設定"""
    total_budget: int = 10000               # 総予算（円・100の倍数）
    strategy_mode: StrategyMode = StrategyMode.HYBRID
    target_recovery_rate: float = 1.0       # 守り枠の目標回収率 (1.0 = 100%元返し)
    min_bet_unit: int = 100                 # 最小購入単位（100円）
    synthetic_odds_threshold: float = 2.0   # 合成オッズ警告閾値
    surplus_rule: str = "boost_min_payout"  # 余剰金配分ルール: 'boost_min_payout' (最低払戻底上げ) or 'max_odds' (最大オッズ優先)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_budget": self.total_budget,
            "strategy_mode": self.strategy_mode.value if isinstance(self.strategy_mode, StrategyMode) else self.strategy_mode,
            "target_recovery_rate": self.target_recovery_rate,
            "min_bet_unit": self.min_bet_unit,
            "synthetic_odds_threshold": self.synthetic_odds_threshold,
            "surplus_rule": self.surplus_rule,
        }


@dataclass
class AllocationResult:
    """資金配分結果データクラス"""
    bets: List[SingleBet]
    total_invested: int
    remaining_budget: int
    synthetic_odds: float
    min_defense_payout: float
    is_bankrupt: bool = False               # 守り枠だけで予算超過した場合 True
    error_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bets": [b.to_dict() for b in self.bets],
            "total_invested": self.total_invested,
            "remaining_budget": self.remaining_budget,
            "synthetic_odds": self.synthetic_odds,
            "min_defense_payout": self.min_defense_payout,
            "is_bankrupt": self.is_bankrupt,
            "error_message": self.error_message,
            "warnings": self.warnings,
        }
