"""奖励函数：综合战斗效率、流派发展、路线质量、药水使用时机。

设计哲学：
- 少受伤、少回合打赢 = 好战斗
- 拿到核心卡/删掉垃圾卡 = 好决策
- 灵活路线(有选择余地) = 好路线
- 精英前后有篝火 = 好规划
- 药水在正确时机用 = 好操作
"""
from __future__ import annotations
from sts_env.game_state import GameState, RoomType
from sts_env.archetypes import (
    REMOVE_ALWAYS,
    card_pick_score,
    card_remove_score,
    deck_quality_score,
    get_character_strategy,
    upgrade_priority_score,
)


class RewardConfig:
    # --- 大事件 ---
    WIN_RUN = 1000.0
    DEATH = -1000.0

    # --- 战斗胜利 ---
    BEAT_BOSS = 120.0
    BEAT_ELITE = 30.0
    BEAT_MONSTER = 8.0

    # --- 战斗效率 ---
    HP_LOSS_PENALTY = -0.15       # 每点HP损失（精英/Boss战加倍）
    TURN_PENALTY = -0.05          # 每多一回合
    ONE_TURN_KILL_BONUS = 5.0     # 一回合秒杀
    LOW_HP_FINISH_BONUS = 3.0     # 高HP存活结束战斗（剩余>80%）

    # --- 早期避战精英 ---
    AVOID_ELITE_FLOOR1_2_BONUS = 5.0   # 前两层成功绕开精英

    # --- 牌组发展 ---
    CORE_CARD_PICKUP = 15.0       # 拿到核心卡
    SYNERGY_CARD_PICKUP = 5.0     # 拿到协同卡
    JUNK_CARD_PICKUP = -8.0       # 拿了垃圾卡
    SKIP_REWARD_BONUS = 2.0       # 理智跳过（牌组已够好时）
    REMOVE_JUNK_BONUS = 10.0      # 删掉垃圾卡
    REMOVE_CURSE_BONUS = 15.0     # 删掉诅咒
    REMOVE_GOOD_CARD_PENALTY = -5.0   # 删掉好卡
    SHOP_CARD_PRICE_PRESSURE = -1.0   # 商店购买的金币机会成本

    # --- 路线质量 ---
    FLOOR_ADVANCE = 0.5           # 每层前进
    FLEXIBLE_ROUTE_BONUS = 1.0    # 选择了有2+个后继节点的路线
    CAMPFIRE_BEFORE_ELITE = 3.0   # 精英前有篝火
    CAMPFIRE_AFTER_ELITE = 2.0    # 精英后有篝火
    ELITE_READY_BONUS = 3.0
    ELITE_UNREADY_PENALTY = -3.5
    MONSTER_GROWTH_BONUS = 1.5
    SHOP_REMOVE_SETUP_BONUS = 3.0
    SHOP_RICH_BONUS = 1.25
    REST_LOW_HP_BONUS = 3.0
    REST_UPGRADE_WINDOW_BONUS = 2.0
    TREASURE_ROUTE_BONUS = 2.0
    EVENT_SAFE_BONUS = 0.4
    EVENT_LOW_HP_PENALTY = -0.4

    # --- 休息站（REST）---
    # 休息站动作：rest(回血30%)、upgrade(升级牌)、dig(需铲子遗物)
    # 注意：休息站【不能删牌】，删牌只能通过商店或特定事件
    REST_HP_GAIN_FACTOR = 0.08    # 恢复HP时 * 恢复量
    UPGRADE_CORE_CARD = 5.0       # 升级核心卡
    UPGRADE_SYNERGY_CARD = 2.0    # 升级协同卡
    DIG_REWARD = 2.0
    LIFT_REWARD = 1.25

    # --- 商店/事件删牌 ---
    SHOP_REMOVE_JUNK = 10.0       # 商店删垃圾卡（值得花金币）
    SHOP_REMOVE_CURSE = 15.0      # 商店删诅咒
    SHOP_REMOVE_GOOD_PENALTY = -5.0  # 商店删好卡
    SHOP_REMOVE_GOLD_PRESSURE = -2.5
    SHOP_PREMIUM_CARD_BONUS = 1.0
    SHOP_BLOAT_PENALTY = -2.0
    SHOP_RELIC_COMMON = 1.0
    SHOP_RELIC_UNCOMMON = 2.0
    SHOP_RELIC_RARE = 3.0
    SHOP_RELIC_ANCIENT = 4.0

    # --- 药水 ---
    POTION_USED_SAVE_LIFE = 20.0  # 药水救命（HP < 20%）
    POTION_USED_ELITE = 5.0       # 精英战用药水
    POTION_WASTED = -3.0          # 满背包时无法捡药水

    # --- 事件 ---
    EVENT_HEAL_FACTOR = 0.08
    EVENT_DAMAGE_FACTOR = -0.08
    EVENT_GOLD_FACTOR = 0.01
    EVENT_MAX_HP_FACTOR = 0.2


def _deck_ids(gs: GameState) -> list[str]:
    return [c.card_id for c in gs.deck]


def _hp_ratio(gs: GameState, *, hp: int | None = None) -> float:
    current_hp = gs.player.hp if hp is None else hp
    return current_hp / max(1, gs.player.max_hp)


def _best_remove_score(gs: GameState, deck_ids: list[str] | None = None) -> float:
    ids = deck_ids if deck_ids is not None else _deck_ids(gs)
    return max(
        (card_remove_score(gs.character, ids, card_id, floor=gs.floor, act=gs.act) for card_id in ids),
        default=0.0,
    )


def _best_upgrade_score(gs: GameState, deck_ids: list[str] | None = None) -> float:
    ids = deck_ids if deck_ids is not None else _deck_ids(gs)
    return max((upgrade_priority_score(gs.character, ids, card_id) for card_id in ids), default=0.0)


def compute_combat_reward(
    gs: GameState,
    room_type: RoomType,
    won: bool,
    hp_before: int,
    hp_after: int,
    turns: int,
    max_hp: int,
) -> float:
    reward = 0.0
    if not won:
        reward += RewardConfig.DEATH
        return reward

    # 基础胜利奖励
    if room_type == RoomType.BOSS:
        reward += RewardConfig.BEAT_BOSS
    elif room_type == RoomType.ELITE:
        reward += RewardConfig.BEAT_ELITE
    else:
        reward += RewardConfig.BEAT_MONSTER

    # HP损失惩罚（精英/Boss加倍）
    hp_lost = max(0, hp_before - hp_after)
    multiplier = 2.0 if room_type in (RoomType.ELITE, RoomType.BOSS) else 1.0
    reward += hp_lost * RewardConfig.HP_LOSS_PENALTY * multiplier

    # 回合惩罚
    reward += turns * RewardConfig.TURN_PENALTY

    # 一回合秒杀奖励
    if turns <= 1:
        reward += RewardConfig.ONE_TURN_KILL_BONUS

    # 高HP存活奖励
    if hp_after > 0 and max_hp > 0 and hp_after / max_hp > 0.8:
        reward += RewardConfig.LOW_HP_FINISH_BONUS

    return reward


def compute_card_reward(
    gs: GameState,
    picked_card_id: str | None,
    skipped: bool,
    offered_card_ids: list[str] | None = None,
) -> float:
    """选卡/跳过时的奖励。"""
    deck_ids = _deck_ids(gs)
    strategy = get_character_strategy(gs.character)

    if skipped:
        best_offer = max((card_pick_score(gs.character, deck_ids, card_id) for card_id in (offered_card_ids or [])), default=0.0)
        q = deck_quality_score(gs.character, deck_ids)
        if len(deck_ids) >= strategy.workable_deck_max and best_offer < 6.0:
            return RewardConfig.SKIP_REWARD_BONUS + 1.0
        if best_offer <= 0.0:
            return RewardConfig.SKIP_REWARD_BONUS
        if q > 0.6 and best_offer < 4.0:
            return RewardConfig.SKIP_REWARD_BONUS
        if best_offer >= 8.0 and len(deck_ids) < strategy.workable_deck_max:
            return -1.0
        return 0.0

    if picked_card_id is None:
        return 0.0

    score = card_pick_score(gs.character, deck_ids, picked_card_id)
    return max(RewardConfig.JUNK_CARD_PICKUP, min(RewardConfig.CORE_CARD_PICKUP, score))


def compute_remove_card_reward(
    gs: GameState,
    removed_card_id: str,
) -> float:
    """删牌时的奖励。"""
    if removed_card_id in REMOVE_ALWAYS:
        return RewardConfig.REMOVE_CURSE_BONUS

    deck_ids = _deck_ids(gs)
    remove_score = card_remove_score(gs.character, deck_ids, removed_card_id, floor=gs.floor, act=gs.act)
    if remove_score >= 28.0:
        return RewardConfig.REMOVE_JUNK_BONUS
    if remove_score >= 12.0:
        return RewardConfig.REMOVE_JUNK_BONUS * 0.6
    if remove_score <= -16.0:
        return RewardConfig.REMOVE_GOOD_CARD_PENALTY - 2.0
    if remove_score <= -8.0:
        return RewardConfig.REMOVE_GOOD_CARD_PENALTY
    return 0.0


def compute_remove_at_shop_reward(
    gs: GameState,
    removed_card_id: str,
) -> float:
    """商店/事件删牌时的奖励（不是休息站，休息站不能删牌）。"""
    return compute_remove_card_reward(gs, removed_card_id)


def compute_rest_reward(
    gs: GameState,
    action: str,  # 'rest' | 'upgrade' | 'dig' | 'cook'
    # 注意：篝火删牌只有持有 MeatCleaver 遗物时才能执行 COOK 动作
    # COOK = 删2张牌 + 获得9点最大HP
    hp_gained: int = 0,
    hp_before: int | None = None,
    upgraded_card_id: str | None = None,
    removed_card_ids: list[str] | None = None,
) -> float:
    """休息站决策的奖励。
    可选动作：
    - rest: 回复30%最大HP
    - upgrade: 升级1张牌
    - dig: 获得遗物（需 Shovel 遗物）
    - cook: 删2张牌 + 9最大HP（需 MeatCleaver 遗物）
    """
    reward = 0.0
    deck_ids = _deck_ids(gs)
    hp_ratio_before = _hp_ratio(gs, hp=hp_before)
    best_upgrade = _best_upgrade_score(gs, deck_ids)
    if action == "rest":
        reward += hp_gained * RewardConfig.REST_HP_GAIN_FACTOR
        if hp_ratio_before < 0.45:
            reward += RewardConfig.REST_LOW_HP_BONUS
        elif hp_ratio_before < 0.6:
            reward += 1.0
        elif best_upgrade >= 12.0:
            reward -= 0.75
    elif action == "upgrade" and upgraded_card_id:
        priority = upgrade_priority_score(gs.character, deck_ids, upgraded_card_id)
        if priority >= 18.0:
            reward += RewardConfig.UPGRADE_CORE_CARD + 1.0
        elif priority >= 10.0:
            reward += RewardConfig.UPGRADE_CORE_CARD
        elif priority >= 5.0:
            reward += RewardConfig.UPGRADE_SYNERGY_CARD
        else:
            reward += 0.25
        if hp_ratio_before >= 0.55:
            reward += 0.75
        elif hp_ratio_before < 0.3:
            reward -= 0.75
    elif action == "dig":
        reward += RewardConfig.DIG_REWARD if hp_ratio_before >= 0.45 else 0.5
    elif action == "cook" and removed_card_ids:
        # MeatCleaver：删2张牌 + 获得9点最大HP
        for rid in removed_card_ids:
            reward += compute_remove_card_reward(gs, rid)
        reward += hp_gained * RewardConfig.REST_HP_GAIN_FACTOR  # +9最大HP的价值
        if hp_ratio_before >= 0.5:
            reward += 1.5
    elif action == "lift":
        reward += RewardConfig.LIFT_REWARD if hp_ratio_before >= 0.6 else 0.25
    return reward


def compute_route_reward(
    gs: GameState,
    chosen_node_type: RoomType,
    n_alternatives: int,
    floor: int,
    next_nodes_preview: list[RoomType],
) -> float:
    """选路时的奖励。"""
    deck_ids = _deck_ids(gs)
    hp_ratio = _hp_ratio(gs)
    deck_quality = deck_quality_score(gs.character, deck_ids)
    strategy = get_character_strategy(gs.character)
    best_remove = _best_remove_score(gs, deck_ids)
    best_upgrade = _best_upgrade_score(gs, deck_ids)
    upcoming_floor = floor + 1
    act_floor = ((upcoming_floor - 1) % 17) + 1
    has_rest_next = RoomType.REST in next_nodes_preview
    has_elite_next = RoomType.ELITE in next_nodes_preview
    wants_shop = (gs.player.gold >= gs.shop_remove_cost and best_remove >= 12.0) or gs.player.gold >= 150

    reward = RewardConfig.FLOOR_ADVANCE

    # 灵活路线奖励
    if n_alternatives >= 2:
        reward += RewardConfig.FLEXIBLE_ROUTE_BONUS

    # 前两层默认优先稳定成长
    if act_floor <= 2:
        if chosen_node_type == RoomType.ELITE and not (hp_ratio >= 0.85 and deck_quality >= 0.65):
            reward -= RewardConfig.AVOID_ELITE_FLOOR1_2_BONUS
        elif chosen_node_type == RoomType.MONSTER:
            reward += RewardConfig.AVOID_ELITE_FLOOR1_2_BONUS * 0.25

    if chosen_node_type == RoomType.MONSTER:
        if len(deck_ids) < strategy.ideal_deck_min or deck_quality < 0.58:
            reward += RewardConfig.MONSTER_GROWTH_BONUS
        if hp_ratio < 0.35:
            reward -= 0.75
    elif chosen_node_type == RoomType.ELITE:
        if hp_ratio >= 0.68 and deck_quality >= 0.55:
            reward += RewardConfig.ELITE_READY_BONUS
        else:
            reward += RewardConfig.ELITE_UNREADY_PENALTY
        if has_rest_next:
            reward += RewardConfig.CAMPFIRE_AFTER_ELITE
    elif chosen_node_type == RoomType.REST:
        if hp_ratio < 0.45:
            reward += RewardConfig.REST_LOW_HP_BONUS
        elif best_upgrade >= 12.0 and hp_ratio >= 0.55:
            reward += RewardConfig.REST_UPGRADE_WINDOW_BONUS
        else:
            reward -= 0.25
        if has_elite_next and hp_ratio >= 0.55:
            reward += RewardConfig.CAMPFIRE_BEFORE_ELITE
    elif chosen_node_type == RoomType.SHOP:
        if wants_shop:
            reward += RewardConfig.SHOP_REMOVE_SETUP_BONUS
        else:
            reward += RewardConfig.SHOP_RICH_BONUS if gs.player.gold >= 125 else -0.5
    elif chosen_node_type == RoomType.EVENT:
        reward += RewardConfig.EVENT_SAFE_BONUS if hp_ratio >= 0.6 else RewardConfig.EVENT_LOW_HP_PENALTY
    elif chosen_node_type == RoomType.TREASURE:
        reward += RewardConfig.TREASURE_ROUTE_BONUS

    return reward


def compute_floor_reward() -> float:
    return RewardConfig.FLOOR_ADVANCE


def compute_win_reward() -> float:
    return RewardConfig.WIN_RUN


def compute_potion_reward(
    gs: GameState,
    room_type: RoomType,
    hp_ratio: float,  # 当前HP/最大HP
) -> float:
    """使用药水时的奖励（战斗中）。"""
    reward = 0.0
    if hp_ratio < 0.2:
        reward += RewardConfig.POTION_USED_SAVE_LIFE
    if room_type == RoomType.ELITE:
        reward += RewardConfig.POTION_USED_ELITE
    return reward


def compute_shop_card_reward(
    gs: GameState,
    card_id: str,
    price: int,
) -> float:
    """商店买卡奖励，需要明确扣掉金币机会成本，避免乱买中性牌。"""
    deck_ids = _deck_ids(gs)
    strategy = get_character_strategy(gs.character)
    base_score = card_pick_score(gs.character, deck_ids, card_id)
    reward = base_score * 0.7 + RewardConfig.SHOP_CARD_PRICE_PRESSURE * (price / 75.0)
    remaining_gold = gs.player.gold - price
    best_remove = _best_remove_score(gs, deck_ids)

    if base_score >= 10.0:
        reward += RewardConfig.SHOP_PREMIUM_CARD_BONUS
    if len(deck_ids) >= strategy.workable_deck_max and base_score < 8.0:
        reward += RewardConfig.SHOP_BLOAT_PENALTY
    if remaining_gold < gs.shop_remove_cost and best_remove >= 12.0:
        reward += RewardConfig.SHOP_REMOVE_GOLD_PRESSURE
    return reward


def compute_shop_relic_reward(
    gs: GameState,
    relic_id: str,
    rarity: str,
    price: int,
) -> float:
    """商店买遗物奖励。"""
    reward = {
        "Common": RewardConfig.SHOP_RELIC_COMMON,
        "Uncommon": RewardConfig.SHOP_RELIC_UNCOMMON,
        "Rare": RewardConfig.SHOP_RELIC_RARE,
        "Ancient": RewardConfig.SHOP_RELIC_ANCIENT,
    }.get(rarity, RewardConfig.SHOP_RELIC_UNCOMMON)

    remaining_gold = gs.player.gold - price
    if remaining_gold < gs.shop_remove_cost and _best_remove_score(gs) >= 12.0:
        reward += RewardConfig.SHOP_REMOVE_GOLD_PRESSURE
    if relic_id in gs.player.relics:
        reward -= 5.0
    return reward


def compute_event_reward(
    gs: GameState,
    effect: dict,
    *,
    hp_before: int,
    remove_reward: float = 0.0,
) -> float:
    """事件结果奖励。"""
    reward = remove_reward
    missing_hp = max(0, gs.player.max_hp - hp_before)
    hp_ratio_before = _hp_ratio(gs, hp=hp_before)

    if "heal" in effect:
        reward += min(effect["heal"], missing_hp) * RewardConfig.EVENT_HEAL_FACTOR
    if "gold" in effect:
        reward += min(effect["gold"] * RewardConfig.EVENT_GOLD_FACTOR, 1.5)
    if "damage" in effect:
        reward += effect["damage"] * RewardConfig.EVENT_DAMAGE_FACTOR
        if hp_ratio_before < 0.4:
            reward -= 1.0
    if "max_hp" in effect:
        reward += effect["max_hp"] * RewardConfig.EVENT_MAX_HP_FACTOR
    return reward
