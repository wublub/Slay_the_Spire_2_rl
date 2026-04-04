"""游戏状态管理：完整的一局 Run 状态。"""
from __future__ import annotations
import json
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from sts_env.combat import Card, Player, make_card

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class RoomType(Enum):
    MONSTER = auto()
    ELITE = auto()
    BOSS = auto()
    REST = auto()
    SHOP = auto()
    EVENT = auto()
    TREASURE = auto()


class GamePhase(Enum):
    MAP = auto()           # 选择路径
    COMBAT = auto()        # 战斗中
    CARD_REWARD = auto()   # 选卡奖励（含skip）
    EVENT = auto()         # 事件选项
    REST = auto()          # 休息站（rest/upgrade/dig/cook/lift）
    SHOP = auto()          # 商店（买卡/买遗物/买药水/删牌/离开）
    TREASURE = auto()      # 宝箱房（选遗物）
    BOSS_RELIC = auto()    # Boss遗物3选1
    NEOW = auto()          # 开局Neow事件
    GAME_OVER = auto()     # 游戏结束


@dataclass
class MapNode:
    floor: int
    index: int
    room_type: RoomType
    children: list[int] = field(default_factory=list)  # 子节点 index


# ---------------------------------------------------------------------------
# 角色数据
# ---------------------------------------------------------------------------

_CHAR_DB: list[dict] = []


def _load_char_db():
    global _CHAR_DB
    if _CHAR_DB:
        return
    p = DATA_DIR / "characters.json"
    if p.exists():
        _CHAR_DB = json.loads(p.read_text("utf-8"))


def get_character_data(char_id: str) -> dict:
    _load_char_db()
    for c in _CHAR_DB:
        if c["id"] == char_id:
            return c
    return {}


PLAYABLE_CHARACTERS = ["Ironclad", "Silent", "Defect", "Necrobinder", "Regent"]


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

class GameState:
    def __init__(self, character: str = "Ironclad", ascension: int = 0, seed: int | None = None):
        self.character = character
        self.ascension = ascension
        self.rng = random.Random(seed)
        self.phase = GamePhase.MAP
        self.act = 1
        self.floor = 0
        self.max_floor = 50  # 3 acts × ~17 floors
        self.won = False

        # 初始化玩家
        char_data = get_character_data(character)
        hp = char_data.get("starting_hp", 80)
        self.player = Player(character, hp, hp)
        self.player.gold = char_data.get("starting_gold", 99)
        self.player.relics = list(char_data.get("starting_relics", []))

        # 初始牌组
        deck_ids = char_data.get("starting_deck", ["StrikeIronclad"] * 5 + ["DefendIronclad"] * 4 + ["Bash"])
        self.deck: list[Card] = [make_card(cid) for cid in deck_ids]

        # 地图
        self.map_nodes: list[list[MapNode]] = []
        self.current_node: MapNode | None = None
        self.available_next: list[int] = []

        # 战斗
        self.combat = None

        # 卡牌奖励
        self.card_rewards: list[Card] = []

        # 事件
        self.event_options: list[dict] = []

        # 商店
        self.shop_cards: list[Card] = []       # 可购买的卡
        self.shop_relics: list[dict] = []      # 可购买的遗物
        self.shop_potions: list[dict] = []     # 可购买的药水
        self.shop_remove_cost: int = 75        # 删牌费用（每次+25）
        self.shop_removes_done: int = 0        # 本局已删牌次数

        # Boss遗物选择
        self.boss_relic_choices: list[str] = []

        # 统计
        self.monsters_killed = 0
        self.elites_killed = 0
        self.bosses_killed = 0

    def get_deck_copy(self) -> list[Card]:
        return [make_card(c.card_id, c.upgraded) for c in self.deck]

    def add_card_to_deck(self, card: Card):
        self.deck.append(card)

    def remove_card_from_deck(self, idx: int):
        if 0 <= idx < len(self.deck):
            self.deck.pop(idx)
