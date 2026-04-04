"""地图生成与事件系统。"""
from __future__ import annotations
import json
import random
from pathlib import Path
from typing import Any

from sts_env.game_state import MapNode, RoomType, GameState
from sts_env.combat import make_card, Card

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# 地图生成
# ---------------------------------------------------------------------------

# 每个 Act 的楼层数
FLOORS_PER_ACT = 17
NUM_PATHS = 6  # 每层节点数

# 节点类型分布权重（简化版）
_ROOM_WEIGHTS = {
    RoomType.MONSTER: 0.45,
    RoomType.EVENT: 0.22,
    RoomType.ELITE: 0.08,
    RoomType.REST: 0.12,
    RoomType.SHOP: 0.05,
    RoomType.TREASURE: 0.08,
}


def generate_act_map(act: int, rng: random.Random) -> list[list[MapNode]]:
    """生成一个 Act 的地图。返回 floors × nodes 的二维列表。"""
    floors: list[list[MapNode]] = []
    num_floors = FLOORS_PER_ACT

    for f in range(num_floors):
        num_nodes = rng.randint(2, 4)
        layer: list[MapNode] = []
        for i in range(num_nodes):
            if f == 0:
                room = RoomType.MONSTER
            elif f == num_floors - 1:
                room = RoomType.BOSS
            elif f == num_floors - 2:
                room = RoomType.REST
            elif f < 4:
                room = rng.choices(
                    [RoomType.MONSTER, RoomType.EVENT],
                    weights=[0.7, 0.3],
                )[0]
            else:
                types = list(_ROOM_WEIGHTS.keys())
                weights = list(_ROOM_WEIGHTS.values())
                room = rng.choices(types, weights=weights)[0]
            layer.append(MapNode(floor=f, index=i, room_type=room))
        floors.append(layer)

    # 连接节点
    for f in range(len(floors) - 1):
        current_layer = floors[f]
        next_layer = floors[f + 1]
        for node in current_layer:
            # 每个节点连接1-2个下层节点
            n_children = min(rng.randint(1, 2), len(next_layer))
            start = min(node.index, len(next_layer) - 1)
            children = set()
            children.add(start % len(next_layer))
            while len(children) < n_children:
                children.add(rng.randint(0, len(next_layer) - 1))
            node.children = sorted(children)

    return floors


# ---------------------------------------------------------------------------
# 遭遇战生成
# ---------------------------------------------------------------------------

_ENCOUNTER_DB: list[dict] = []


def _load_encounters():
    global _ENCOUNTER_DB
    if _ENCOUNTER_DB:
        return
    p = DATA_DIR / "encounters.json"
    if p.exists():
        _ENCOUNTER_DB = json.loads(p.read_text("utf-8"))


def pick_encounter(room_type: RoomType, act: int, rng: random.Random) -> list[str]:
    """根据房间类型和Act选择一个遭遇战，返回怪物ID列表。"""
    _load_encounters()
    rt_map = {
        RoomType.MONSTER: "Monster",
        RoomType.ELITE: "Elite",
        RoomType.BOSS: "Boss",
    }
    rt_str = rt_map.get(room_type, "Monster")
    candidates = [e for e in _ENCOUNTER_DB if e.get("room_type") == rt_str and e.get("monsters")]
    if not candidates:
        return ["JawWorm"]
    enc = rng.choice(candidates)
    return enc.get("monsters", ["JawWorm"])


# ---------------------------------------------------------------------------
# 卡牌奖励生成
# ---------------------------------------------------------------------------

_CARDS_BY_POOL: dict[str, list[dict]] = {}
_RELIC_DB: list[dict] = []
_POTION_DB: list[dict] = []


def _load_cards_by_pool():
    global _CARDS_BY_POOL
    if _CARDS_BY_POOL:
        return
    p = DATA_DIR / "cards.json"
    if p.exists():
        for c in json.loads(p.read_text("utf-8")):
            pool = c.get("pool", "Unknown")
            _CARDS_BY_POOL.setdefault(pool, []).append(c)


def _load_relics():
    global _RELIC_DB
    if _RELIC_DB:
        return
    p = DATA_DIR / "relics.json"
    if p.exists():
        _RELIC_DB = json.loads(p.read_text("utf-8"))


def _load_potions():
    global _POTION_DB
    if _POTION_DB:
        return
    p = DATA_DIR / "potions.json"
    if p.exists():
        _POTION_DB = json.loads(p.read_text("utf-8"))


def generate_card_rewards(character: str, rng: random.Random, count: int = 3) -> list[Card]:
    _load_cards_by_pool()
    pool = _CARDS_BY_POOL.get(character, [])
    # 过滤掉 Basic 卡
    eligible = [c for c in pool if c.get("rarity") not in ("Basic",)]
    if not eligible:
        eligible = pool
    chosen = rng.sample(eligible, min(count, len(eligible)))
    return [make_card(c["id"]) for c in chosen]


def generate_shop_inventory(character: str, rng: random.Random) -> dict[str, list]:
    """生成商店库存。返回 cards/relics/potions 三类商品。"""
    _load_cards_by_pool()
    _load_relics()
    _load_potions()

    cards = generate_card_rewards(character, rng, count=3)

    relic_pool = [r for r in _RELIC_DB if r.get("rarity") in ("Common", "Uncommon", "Rare", "Ancient")]
    potion_pool = [p for p in _POTION_DB if p.get("rarity") in ("Common", "Uncommon", "Rare")]

    relic_count = min(3, len(relic_pool))
    potion_count = min(3, len(potion_pool))

    relics = rng.sample(relic_pool, relic_count) if relic_count > 0 else []
    potions = rng.sample(potion_pool, potion_count) if potion_count > 0 else []

    return {
        "cards": cards,
        "relics": relics,
        "potions": potions,
    }


# ---------------------------------------------------------------------------
# 事件系统（简化版）
# ---------------------------------------------------------------------------

_EVENT_DB: list[dict] = []


def _load_events():
    global _EVENT_DB
    if _EVENT_DB:
        return
    p = DATA_DIR / "events.json"
    if p.exists():
        _EVENT_DB = json.loads(p.read_text("utf-8"))


def pick_event(rng: random.Random) -> dict:
    _load_events()
    if not _EVENT_DB:
        return _default_event()
    ev = rng.choice(_EVENT_DB)
    return _make_event_options(ev, rng)


def _default_event() -> dict:
    return {
        "id": "GenericEvent",
        "options": [
            {"label": "获得金币", "effect": {"gold": 50}},
            {"label": "离开", "effect": {}},
        ],
    }


def _make_event_options(ev_data: dict, rng: random.Random) -> dict:
    """将事件数据转换为可选选项。"""
    ev_vars = ev_data.get("vars", {})
    options = []
    if ev_vars.get("gold"):
        options.append({"label": "获得金币", "effect": {"gold": ev_vars["gold"]}})
    if ev_vars.get("heal"):
        options.append({"label": "恢复生命", "effect": {"heal": ev_vars["heal"]}})
    if ev_vars.get("damage"):
        options.append({"label": "承受伤害获得奖励", "effect": {"damage": ev_vars["damage"], "gold": 50}})
    if not options:
        options.append({"label": "获得少量金币", "effect": {"gold": rng.randint(20, 50)}})
    options.append({"label": "离开", "effect": {}})
    return {"id": ev_data["id"], "options": options}


def apply_event_effect(gs: GameState, effect: dict):
    if "gold" in effect:
        gs.player.gold += effect["gold"]
    if "heal" in effect:
        gs.player.heal(effect["heal"])
    if "damage" in effect:
        gs.player.take_unblockable_damage(effect["damage"])
    if "max_hp" in effect:
        gs.player.max_hp += effect["max_hp"]
        gs.player.hp += effect["max_hp"]
