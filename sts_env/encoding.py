"""状态编码：将游戏状态转换为 numpy 观测向量。

v2: 增加流派匹配度、地图前瞻、药水槽、商店信息、牌组质量等战略特征。
"""
from __future__ import annotations
import json
import numpy as np
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sts_env.game_state import GameState

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------------------
# 卡牌 ID 映射
# ---------------------------------------------------------------------------

_CARD_IDS: list[str] = []
_CARD_ID_MAP: dict[str, int] = {}


def _load_card_ids():
    global _CARD_IDS, _CARD_ID_MAP
    if _CARD_IDS:
        return
    p = DATA_DIR / "cards.json"
    if p.exists():
        cards = json.loads(p.read_text("utf-8"))
        _CARD_IDS = [c["id"] for c in cards]
    else:
        _CARD_IDS = ["Strike", "Defend", "Bash"]
    _CARD_ID_MAP = {cid: i for i, cid in enumerate(_CARD_IDS)}


def card_id_to_idx(card_id: str) -> int:
    _load_card_ids()
    return _CARD_ID_MAP.get(card_id, len(_CARD_IDS))


def num_cards() -> int:
    _load_card_ids()
    return len(_CARD_IDS) + 1


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_HAND = 10
MAX_ENEMIES = 5
MAX_POTIONS = 3
MAX_CARD_REWARDS = 3
MAX_MAP_LOOKAHEAD = 5   # 前瞻5层
MAX_MAP_NODES_PER_FLOOR = 4
NUM_ROOM_TYPES = 8       # Monster/Elite/Boss/Rest/Shop/Event/Treasure/Ancient
NUM_INTENT_TYPES = 8
NUM_POWER_FEATURES = 20
NUM_ARCHETYPE_FEATURES = 4  # 最多4个流派的匹配度
CARD_FEATURE_DIM = 8
ENEMY_FEATURE_DIM = 6 + NUM_INTENT_TYPES
COMBAT_UI_GLOBAL_DIM = 15
COMBAT_RUNTIME_GLOBAL_DIM = 6
COMBAT_RUNTIME_CARD_DIM = 8

TRACKED_POWERS = [
    "StrengthPower", "DexterityPower", "VulnerablePower", "WeakPower",
    "FrailPower", "PoisonPower", "ArtifactPower", "BarricadePower",
    "MetallicizePower", "ThornsPower", "RitualPower", "PlatedArmorPower",
    "IntangiblePower", "RegenPower", "CorruptionPower", "DemonFormPower",
    "NoxiousFumesPower", "DrawCardPower", "EnergizedPower", "FeelNoPainPower",
]

# 药水特征维度：[存在, 稀有度, 战斗用/随时用]
POTION_FEATURE_DIM = 3


def get_obs_dim() -> int:
    global_dim = 10  # hp, max_hp, block, energy, floor, act, gold, deck_size, phase, has_meat_cleaver
    hand_dim = MAX_HAND * CARD_FEATURE_DIM
    pile_dim = 3  # draw, discard, exhaust
    enemy_dim = MAX_ENEMIES * ENEMY_FEATURE_DIM
    player_power_dim = NUM_POWER_FEATURES
    deck_dim = num_cards()
    archetype_dim = NUM_ARCHETYPE_FEATURES  # 各流派匹配度
    strategic_dim = 6  # deck_quality, junk_ratio, avg_cost, atk_ratio, skill_ratio, power_ratio
    potion_dim = MAX_POTIONS * POTION_FEATURE_DIM
    map_dim = MAX_MAP_LOOKAHEAD * NUM_ROOM_TYPES  # 前瞻地图节点类型分布
    card_reward_dim = MAX_CARD_REWARDS * CARD_FEATURE_DIM  # 当前可选卡牌
    combat_ui_dim = COMBAT_UI_GLOBAL_DIM + MAX_HAND * 3
    combat_runtime_dim = COMBAT_RUNTIME_GLOBAL_DIM + MAX_HAND * COMBAT_RUNTIME_CARD_DIM
    return (global_dim + hand_dim + pile_dim + enemy_dim + player_power_dim
             + deck_dim + archetype_dim + strategic_dim + potion_dim
             + map_dim + card_reward_dim + combat_ui_dim + combat_runtime_dim)


# ---------------------------------------------------------------------------
# 编码函数
# ---------------------------------------------------------------------------

def encode_card(card) -> np.ndarray:
    feat = np.zeros(CARD_FEATURE_DIM, dtype=np.float32)
    effective_cost = 0 if bool(getattr(card, "single_turn_free", False)) else card.cost
    feat[0] = effective_cost / 5.0
    feat[1] = card.damage / 50.0
    feat[2] = card.block / 50.0
    feat[3] = 1.0 if card.card_type.value == "Attack" else 0.0
    feat[4] = 1.0 if card.card_type.value == "Skill" else 0.0
    feat[5] = 1.0 if card.card_type.value == "Power" else 0.0
    feat[6] = card.draw / 5.0
    feat[7] = 1.0 if card.upgraded else 0.0
    return feat


def encode_enemy(enemy) -> np.ndarray:
    feat = np.zeros(ENEMY_FEATURE_DIM, dtype=np.float32)
    if enemy is None or enemy.is_dead:
        return feat
    feat[0] = enemy.hp / max(enemy.max_hp, 1)
    feat[1] = enemy.hp / 200.0
    feat[2] = enemy.block / 50.0
    feat[3] = enemy.intent.damage / 50.0
    feat[4] = enemy.intent.hits / 5.0
    feat[5] = enemy.intent.block / 30.0
    intent_idx = enemy.intent.intent_type.value - 1
    if 0 <= intent_idx < NUM_INTENT_TYPES:
        feat[6 + intent_idx] = 1.0
    return feat


def encode_powers(creature) -> np.ndarray:
    feat = np.zeros(NUM_POWER_FEATURES, dtype=np.float32)
    for i, pid in enumerate(TRACKED_POWERS):
        p = creature.get_power(pid)
        if p:
            feat[i] = p.amount / 20.0
    return feat


def encode_deck(deck) -> np.ndarray:
    vec = np.zeros(num_cards(), dtype=np.float32)
    for card in deck:
        idx = card_id_to_idx(card.card_id)
        if idx < len(vec):
            vec[idx] += 1.0
    return vec / max(len(deck), 1)


def encode_archetypes(gs) -> np.ndarray:
    """编码各流派的匹配度。"""
    from sts_env.archetypes import get_archetypes
    feat = np.zeros(NUM_ARCHETYPE_FEATURES, dtype=np.float32)
    archetypes = get_archetypes(gs.character)
    deck_ids = [c.card_id for c in gs.deck]
    for i, arch in enumerate(archetypes[:NUM_ARCHETYPE_FEATURES]):
        feat[i] = arch.score(deck_ids)
    return feat


def encode_strategic(gs) -> np.ndarray:
    """编码牌组战略特征。"""
    from sts_env.archetypes import deck_quality_score, JUNK_CARDS
    feat = np.zeros(6, dtype=np.float32)
    deck = gs.deck
    deck_ids = [c.card_id for c in deck]
    n = max(len(deck), 1)

    feat[0] = deck_quality_score(gs.character, deck_ids)
    junks = JUNK_CARDS.get(gs.character, [])
    feat[1] = sum(1 for c in deck_ids if c in junks) / n  # junk ratio
    feat[2] = sum(c.cost for c in deck) / n / 3.0  # avg cost normalized
    feat[3] = sum(1 for c in deck if c.card_type.value == "Attack") / n
    feat[4] = sum(1 for c in deck if c.card_type.value == "Skill") / n
    feat[5] = sum(1 for c in deck if c.card_type.value == "Power") / n
    return feat


def encode_potions(gs) -> np.ndarray:
    """编码药水槽。"""
    feat = np.zeros(MAX_POTIONS * POTION_FEATURE_DIM, dtype=np.float32)
    for i, pot in enumerate(gs.player.potions[:MAX_POTIONS]):
        base = i * POTION_FEATURE_DIM
        feat[base] = 1.0  # 有药水
        if isinstance(pot, str):
            pot = {"id": pot}
        rarity_map = {"Common": 0.3, "Uncommon": 0.6, "Rare": 1.0}
        feat[base + 1] = rarity_map.get(pot.get("rarity", "Common"), 0.3)
        feat[base + 2] = 1.0 if pot.get("usage") == "CombatOnly" else 0.5
    return feat


def encode_map_lookahead(gs) -> np.ndarray:
    """编码前方5层的节点类型分布（让模型看到前方有没有精英/篝火/商店）。"""
    from sts_env.game_state import RoomType
    feat = np.zeros(MAX_MAP_LOOKAHEAD * NUM_ROOM_TYPES, dtype=np.float32)
    room_type_idx = {
        RoomType.MONSTER: 0, RoomType.ELITE: 1, RoomType.BOSS: 2,
        RoomType.REST: 3, RoomType.SHOP: 4, RoomType.EVENT: 5,
        RoomType.TREASURE: 6,
    }
    current_floor = gs.floor
    for look in range(MAX_MAP_LOOKAHEAD):
        f = current_floor + look
        if f < len(gs.map_nodes):
            layer = gs.map_nodes[f]
            for node in layer:
                rt_idx = room_type_idx.get(node.room_type, 7)
                feat[look * NUM_ROOM_TYPES + rt_idx] += 1.0 / max(len(layer), 1)
    return feat


def encode_card_rewards(gs) -> np.ndarray:
    """编码当前可选的卡牌奖励。"""
    feat = np.zeros(MAX_CARD_REWARDS * CARD_FEATURE_DIM, dtype=np.float32)
    for i, card in enumerate(gs.card_rewards[:MAX_CARD_REWARDS]):
        feat[i * CARD_FEATURE_DIM:(i + 1) * CARD_FEATURE_DIM] = encode_card(card)
    return feat


def _selection_mode_bucket(mode: str | None) -> str:
    if not mode:
        return "none"
    key = str(mode).lower().replace("_", "").replace("-", "")
    if "discard" in key:
        return "discard"
    if "exhaust" in key:
        return "exhaust"
    if "upgrade" in key or "smith" in key:
        return "upgrade"
    if "transform" in key:
        return "transform"
    if "putback" in key or "topdeck" in key:
        return "put_back"
    return "generic"


def encode_combat_ui(gs) -> np.ndarray:
    feat = np.zeros(COMBAT_UI_GLOBAL_DIM + MAX_HAND * 3, dtype=np.float32)
    combat = gs.combat
    if combat is None:
        return feat

    feat[0] = 1.0
    state = getattr(combat, "hand_selection", None)
    if state is not None:
        feat[1] = 1.0
        bucket = _selection_mode_bucket(state.mode)
        bucket_index = {
            "discard": 2,
            "exhaust": 3,
            "upgrade": 4,
            "transform": 5,
            "put_back": 6,
            "generic": 7,
        }.get(bucket)
        if bucket_index is not None:
            feat[bucket_index] = 1.0
        feat[8] = 1.0 if state.confirm_enabled else 0.0
        feat[9] = 1.0 if state.manual_confirm else 0.0
        feat[10] = state.selected_count / 3.0
        feat[11] = state.min_select / 3.0
        feat[12] = state.max_select / 3.0
        feat[13] = sum(1 for flag in state.selectable_cards[:MAX_HAND] if flag) / max(MAX_HAND, 1)
        feat[14] = 1.0 if state.confirm_enabled else 0.0
    else:
        end_turn_enabled = getattr(combat, "end_turn_enabled_override", None)
        feat[14] = 1.0 if end_turn_enabled is not False else 0.0

    playable_base = COMBAT_UI_GLOBAL_DIM
    selectable_base = playable_base + MAX_HAND
    selected_base = selectable_base + MAX_HAND

    playable_cards = getattr(combat, "playable_cards_override", None)
    if playable_cards is None:
        playable_cards = [card.can_play(gs.player.energy) for card in gs.player.hand[:MAX_HAND]]
    for idx, enabled in enumerate(playable_cards[:MAX_HAND]):
        feat[playable_base + idx] = 1.0 if enabled else 0.0

    if state is not None:
        for idx, enabled in enumerate(state.selectable_cards[:MAX_HAND]):
            feat[selectable_base + idx] = 1.0 if enabled else 0.0
        for idx, selected in enumerate(state.selected_cards[:MAX_HAND]):
            feat[selected_base + idx] = 1.0 if selected else 0.0

    return feat


def encode_combat_runtime(gs) -> np.ndarray:
    feat = np.zeros(COMBAT_RUNTIME_GLOBAL_DIM + MAX_HAND * COMBAT_RUNTIME_CARD_DIM, dtype=np.float32)
    player = gs.player
    feat[0] = 1.0 if getattr(player, "is_osty_missing", False) else 0.0
    orb_slots = int(getattr(player, "orb_slots", 0))
    orbs = [str(item) for item in getattr(player, "orbs", [])]
    feat[1] = orb_slots / 10.0
    feat[2] = len(orbs) / 10.0
    feat[3] = sum(1 for orb in orbs if orb == "LightningOrb") / 10.0
    feat[4] = sum(1 for orb in orbs if orb == "FrostOrb") / 10.0
    feat[5] = sum(1 for orb in orbs if orb == "DarkOrb") / 10.0

    base = COMBAT_RUNTIME_GLOBAL_DIM
    for idx, card in enumerate(player.hand[:MAX_HAND]):
        offset = base + idx * COMBAT_RUNTIME_CARD_DIM
        keywords = {str(keyword) for keyword in getattr(card, "keywords", [])}
        tags = {str(tag) for tag in getattr(card, "tags", [])}
        retain_this_turn = "Retain" in keywords or bool(getattr(card, "single_turn_retain", False))
        sly_this_turn = "Sly" in keywords or bool(getattr(card, "single_turn_sly", False))
        affliction_id = str(getattr(card, "affliction_id", ""))
        affliction_amount = int(getattr(card, "affliction_amount", 0))

        feat[offset] = min(max(int(getattr(card, "replay_count", 0)), 0), 3) / 3.0
        feat[offset + 1] = 1.0 if retain_this_turn else 0.0
        feat[offset + 2] = 1.0 if sly_this_turn else 0.0
        feat[offset + 3] = 1.0 if affliction_id else 0.0
        feat[offset + 4] = min(max(affliction_amount, 0), 10) / 10.0
        feat[offset + 5] = 1.0 if "OstyAttack" in tags else 0.0
        feat[offset + 6] = 1.0 if "Exhaust" in keywords else 0.0
        feat[offset + 7] = 1.0 if "Ethereal" in keywords else 0.0

    return feat


# ---------------------------------------------------------------------------
# 主编码函数
# ---------------------------------------------------------------------------

def encode_observation(gs) -> np.ndarray:
    from sts_env.game_state import GamePhase
    parts = []
    p = gs.player

    # 全局状态 (10)
    phase_map = {
        GamePhase.MAP: 0.0, GamePhase.COMBAT: 0.2, GamePhase.CARD_REWARD: 0.4,
        GamePhase.EVENT: 0.5, GamePhase.REST: 0.6, GamePhase.SHOP: 0.7,
        GamePhase.GAME_OVER: 1.0,
    }
    has_cleaver = 1.0 if "MeatCleaver" in p.relics else 0.0
    parts.append(np.array([
        p.hp / max(p.max_hp, 1),
        p.max_hp / 200.0,
        p.block / 100.0,
        p.energy / 5.0 if hasattr(p, 'energy') else 0.0,
        gs.floor / 50.0,
        gs.act / 3.0,
        p.gold / 500.0,
        len(gs.deck) / 40.0,
        phase_map.get(gs.phase, 0.0),
        has_cleaver,
    ], dtype=np.float32))

    # 手牌 (MAX_HAND * 8)
    hand_feats = np.zeros((MAX_HAND, CARD_FEATURE_DIM), dtype=np.float32)
    for i, card in enumerate(p.hand[:MAX_HAND]):
        hand_feats[i] = encode_card(card)
    parts.append(hand_feats.flatten())

    # 牌堆大小 (3)
    parts.append(np.array([
        len(p.draw_pile) / 30.0,
        len(p.discard_pile) / 30.0,
        len(p.exhaust_pile) / 20.0,
    ], dtype=np.float32))

    # 敌人 (MAX_ENEMIES * 14)
    enemy_feats = np.zeros((MAX_ENEMIES, ENEMY_FEATURE_DIM), dtype=np.float32)
    if gs.combat:
        for i, m in enumerate(gs.combat.monsters[:MAX_ENEMIES]):
            enemy_feats[i] = encode_enemy(m)
    parts.append(enemy_feats.flatten())

    # 玩家 Power (20)
    parts.append(encode_powers(p))

    # 牌组组成 (num_cards)
    parts.append(encode_deck(gs.deck))

    # 流派匹配度 (4)
    parts.append(encode_archetypes(gs))

    # 战略特征 (6)
    parts.append(encode_strategic(gs))

    # 药水槽 (9)
    parts.append(encode_potions(gs))

    # 地图前瞻 (40)
    parts.append(encode_map_lookahead(gs))

    # 卡牌奖励 (24)
    parts.append(encode_card_rewards(gs))

    # 战斗 UI 子状态与手牌交互掩码
    parts.append(encode_combat_ui(gs))

    # 运行时手牌状态尾部，供新模型学习 retain/replay/affliction/orb/osty 语义。
    parts.append(encode_combat_runtime(gs))

    return np.concatenate(parts)
