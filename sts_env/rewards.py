"""奖励函数：综合战斗效率、牌组发展、路线质量、药水使用时机。"""
from __future__ import annotations

from sts_env.game_state import GameState, RoomType
from sts_env.archetypes import (
    REMOVE_ALWAYS,
    card_pick_score,
    combat_exhaust_play_score,
    card_remove_score,
    deck_quality_score,
    elite_readiness_score,
    get_character_strategy,
    strategic_role_scores,
    upgrade_priority_score,
)


class RewardConfig:
    # --- 大事件 ---
    WIN_RUN = 1000.0
    DEATH = -1000.0

    # --- 战斗胜利 ---
    BEAT_BOSS = 120.0
    BEAT_ELITE = 30.0
    BEAT_MONSTER = 15.0             # 从8.0提升到15.0，让模型更快学会打赢小怪

    # --- 战斗效率 ---
    HP_LOSS_PENALTY = -0.15       # 每点HP损失（精英/Boss战加倍）
    AVOIDABLE_HP_LOSS_PENALTY = -0.3
    TURN_PENALTY_LINEAR = -0.1
    TURN_PENALTY_QUADRATIC = -0.08
    NORMAL_TURN_THRESHOLD = 3
    ELITE_TURN_THRESHOLD = 4
    BOSS_TURN_THRESHOLD = 6
    ONE_TURN_KILL_BONUS = 5.0     # 一回合秒杀
    TWO_TURN_KILL_BONUS = 2.5
    LOW_HP_FINISH_BONUS = 3.0     # 高HP存活结束战斗（剩余>80%）
    RUN_FLOOR_FACTOR = 1.0
    RUN_WIN_BONUS = 1000.0
    RUN_REMAINING_HP_FACTOR = 20.0

    # --- 战术层面密集奖励（新增）---
    KILL_ENEMY = 25.0             # 从15.0提升到25.0，击杀奖励加倍
    KILL_ELITE = 25.0             # 击杀精英怪
    DEAL_DAMAGE = 0.2             # 从0.1提升到0.2，伤害效率翻倍
    BLOCK_DAMAGE = 0.08           # 格挡伤害（按量）
    APPLY_VULNERABLE = 8.0        # 给敌人挂易伤
    APPLY_WEAK = 5.0              # 给敌人挂虚弱
    APPLY_POISON = 6.0            # 给敌人挂毒
    APPLY_ARTIFACT_BREAK = 10.0   # 破除Artifact
    PLAY_POWER_CARD = 3.0         # 出Power牌
    DRAW_CARDS = 0.5              # 过牌（每张）
    ENERGY_EFFICIENCY = 1.0       # 能量利用效率
    COMBO_PLAY = 2.0              # 连击奖励（同回合多次出牌）
    OPTIMAL_TARGET = 3.0          # 选择最优目标（如收头、破绽目标）

    # --- 牌组发展 ---
    CORE_CARD_PICKUP = 15.0
    SYNERGY_CARD_PICKUP = 5.0
    JUNK_CARD_PICKUP = -8.0
    SKIP_REWARD_BONUS = 2.0
    REMOVE_JUNK_BONUS = 10.0
    REMOVE_CURSE_BONUS = 15.0
    REMOVE_GOOD_CARD_PENALTY = -5.0
    SHOP_CARD_PRICE_PRESSURE = -0.8

    # --- 路线质量 ---
    FLOOR_ADVANCE = 0.5
    FLEXIBLE_ROUTE_BONUS = 1.0
    CAMPFIRE_BEFORE_ELITE = 3.0
    CAMPFIRE_AFTER_ELITE = 2.0
    ELITE_READY_BONUS = 3.5
    ELITE_UNREADY_PENALTY = -2.25
    MONSTER_GROWTH_BONUS = 1.5
    SHOP_REMOVE_SETUP_BONUS = 2.0
    SHOP_RICH_BONUS = 1.5
    REST_LOW_HP_BONUS = 3.0
    REST_UPGRADE_WINDOW_BONUS = 2.0
    TREASURE_ROUTE_BONUS = 2.0
    EVENT_SAFE_BONUS = 0.4
    EVENT_LOW_HP_PENALTY = -0.4

    # --- 休息站（REST）---
    REST_HP_GAIN_FACTOR = 0.08
    UPGRADE_CORE_CARD = 5.0
    UPGRADE_SYNERGY_CARD = 2.0
    DIG_REWARD = 2.0
    LIFT_REWARD = 1.25

    # --- 商店/事件删牌 ---
    SHOP_REMOVE_JUNK = 10.0
    SHOP_REMOVE_CURSE = 15.0
    SHOP_REMOVE_GOOD_PENALTY = -5.0
    SHOP_REMOVE_GOLD_PRESSURE = -1.2
    SHOP_PREMIUM_CARD_BONUS = 1.25
    SHOP_BLOAT_PENALTY = -2.0
    SHOP_RELIC_COMMON = 1.0
    SHOP_RELIC_UNCOMMON = 2.0
    SHOP_RELIC_RARE = 3.0
    SHOP_RELIC_ANCIENT = 4.0

    # --- 药水 ---
    POTION_USED_SAVE_LIFE = 20.0
    POTION_USED_ELITE = 5.0
    POTION_WASTED = -3.0

    # --- 事件 ---
    EVENT_HEAL_FACTOR = 0.08
    EVENT_DAMAGE_FACTOR = -0.08
    EVENT_GOLD_FACTOR = 0.01
    EVENT_MAX_HP_FACTOR = 0.2
    EXHAUST_PLAY_STRONG = 1.0
    EXHAUST_PLAY_WEAK = 0.35
    EXHAUST_PLAY_BAD = -0.6


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


def _default_turn_threshold(room_type: RoomType) -> int:
    return {
        RoomType.MONSTER: RewardConfig.NORMAL_TURN_THRESHOLD,
        RoomType.ELITE: RewardConfig.ELITE_TURN_THRESHOLD,
        RoomType.BOSS: RewardConfig.BOSS_TURN_THRESHOLD,
    }.get(room_type, RewardConfig.NORMAL_TURN_THRESHOLD)


def _turn_penalty(room_type: RoomType, turns: int, *, turn_threshold: int | None = None) -> float:
    threshold = max(1, int(turn_threshold if turn_threshold is not None else _default_turn_threshold(room_type)))
    overflow = max(0, int(turns) - threshold)
    return (
        int(turns) * RewardConfig.TURN_PENALTY_LINEAR
        + (overflow ** 2) * RewardConfig.TURN_PENALTY_QUADRATIC
    )


def compute_combat_reward(
    gs: GameState,
    room_type: RoomType,
    won: bool,
    hp_before: int,
    hp_after: int,
    turns: int,
    max_hp: int,
    avoidable_hp_loss: int = 0,
    turn_threshold: int | None = None,
) -> float:
    reward = 0.0
    if not won:
        reward += RewardConfig.DEATH
        return reward

    if room_type == RoomType.BOSS:
        reward += RewardConfig.BEAT_BOSS
    elif room_type == RoomType.ELITE:
        reward += RewardConfig.BEAT_ELITE
    else:
        reward += RewardConfig.BEAT_MONSTER

    hp_lost = max(0, hp_before - hp_after)
    multiplier = 2.0 if room_type in (RoomType.ELITE, RoomType.BOSS) else 1.0
    reward += hp_lost * RewardConfig.HP_LOSS_PENALTY * multiplier
    reward += max(0, avoidable_hp_loss) * RewardConfig.AVOIDABLE_HP_LOSS_PENALTY
    reward += _turn_penalty(room_type, max(1, turns), turn_threshold=turn_threshold)

    if turns <= 1:
        reward += RewardConfig.ONE_TURN_KILL_BONUS
    elif turns <= 2:
        reward += RewardConfig.TWO_TURN_KILL_BONUS
    if hp_after > 0 and max_hp > 0 and hp_after / max_hp > 0.8:
        reward += RewardConfig.LOW_HP_FINISH_BONUS

    return reward


def compute_run_score(
    *,
    progress: float | None = None,
    combat_score: float | None = None,
    remaining_hp: int | float | None = None,
    won: bool | None = None,
    floor: int | None = None,
    hp: int | None = None,
    max_hp: int | None = None,
    combat_score_total: float | None = None,
) -> float:
    if progress is not None or combat_score is not None or remaining_hp is not None:
        return float(progress or 0.0) + float(combat_score or 0.0) + float(remaining_hp or 0.0)

    remaining_hp_bonus = 0.0
    if max_hp and max_hp > 0:
        remaining_hp_bonus = max(0, int(hp or 0)) / max_hp * RewardConfig.RUN_REMAINING_HP_FACTOR
    return (
        int(floor or 0) * RewardConfig.RUN_FLOOR_FACTOR
        + float(combat_score_total or 0.0)
        + remaining_hp_bonus
        + (RewardConfig.RUN_WIN_BONUS if won else 0.0)
    )


def compute_card_reward(
    gs: GameState,
    picked_card_id: str | None,
    skipped: bool,
    offered_card_ids: list[str] | None = None,
) -> float:
    """选卡/跳过时的奖励。
    
    改进版：
    1. 考虑当前牌组大小和质量，给出更合理的选牌/跳过建议
    2. 对选择流派匹配牌给予高奖励
    3. 对牌组臃肿时跳过给予奖励
    4. 对缺少核心能力时选择对应牌给予额外奖励
    """
    deck_ids = _deck_ids(gs)
    strategy = get_character_strategy(gs.character)
    profile = strategic_role_scores(gs.character, deck_ids, act=gs.act, floor=gs.floor)

    if skipped:
        offered_ids = list(offered_card_ids or [])
        best_offer = max((card_pick_score(gs.character, deck_ids, card_id) for card_id in offered_ids), default=0.0)
        premium_offer_present = any(
            card_id in strategy.engine_cards
            or card_id in strategy.resource_cards
            or card_id in strategy.payoff_cards
            or card_id in strategy.premium_shop_cards
            for card_id in offered_ids
        )
        major_gap = max(
            1.0 - profile["frontload_damage"],
            1.0 - profile["frontload_defend"],
            1.0 - profile["engine"],
            1.0 - profile["scaling_damage"],
        )
        if best_offer <= 0.0:
            return RewardConfig.SKIP_REWARD_BONUS
        deck_is_bloated = len(deck_ids) >= max(strategy.ideal_deck_min + 8, strategy.ideal_deck_max - 2)
        deck_is_underdeveloped = deck_quality_score(gs.character, deck_ids) < 0.45
        if deck_is_bloated and deck_is_underdeveloped and not premium_offer_present and best_offer < 10.0:
            return RewardConfig.SKIP_REWARD_BONUS + 0.5
        if major_gap >= 0.45 and best_offer >= 4.0:
            return -1.5
        if best_offer >= 8.0:
            return -1.0
        deck_is_large_and_stable = (
            len(deck_ids) >= strategy.workable_deck_max
            and profile["engine"] >= 0.75
            and profile["scaling_damage"] >= 0.7
            and profile["frontload_damage"] >= 0.7
        )
        if deck_is_large_and_stable and best_offer < 6.0:
            return RewardConfig.SKIP_REWARD_BONUS + 0.5
        if deck_quality_score(gs.character, deck_ids) > 0.72 and best_offer < 5.0:
            return RewardConfig.SKIP_REWARD_BONUS
        return 0.0

    if picked_card_id is None:
        return 0.0

    # 计算选牌的分数
    score = card_pick_score(gs.character, deck_ids, picked_card_id)
    
    # 新增：额外奖励机制
    reward = max(RewardConfig.JUNK_CARD_PICKUP, min(RewardConfig.CORE_CARD_PICKUP, score))

    # 1. 如果当前牌组缺少某能力，选择对应牌给予额外奖励
    if picked_card_id in strategy.engine_cards and profile["engine"] < 0.5:
        reward += 5.0
    if picked_card_id in strategy.payoff_cards and profile["scaling_damage"] < 0.5:
        reward += 4.0
    # 修复：frontload_cards 应该是 frontload_damage_cards 和 frontload_aoe_cards 的合并
    frontload_cards = set(strategy.frontload_damage_cards) | set(strategy.frontload_aoe_cards)
    if picked_card_id in frontload_cards and profile["frontload_damage"] < 0.5:
        reward += 3.5
    
    # 2. 如果选择稀有/强力的牌
    if picked_card_id in strategy.premium_shop_cards:
        reward += 3.0
    
    # 3. 如果牌组已经很完善，但仍然选牌
    if deck_quality_score(gs.character, deck_ids) > 0.8 and score < 5.0:
        reward -= 2.0  # 惩罚低质量选择
    
    return reward


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
    """商店/事件删牌时的奖励。"""
    return compute_remove_card_reward(gs, removed_card_id)


def compute_rest_reward(
    gs: GameState,
    action: str,
    hp_gained: int = 0,
    hp_before: int | None = None,
    upgraded_card_id: str | None = None,
    removed_card_ids: list[str] | None = None,
) -> float:
    """休息站决策的奖励。"""
    reward = 0.0
    deck_ids = _deck_ids(gs)
    hp_ratio_before = _hp_ratio(gs, hp=hp_before)
    best_upgrade = _best_upgrade_score(gs, deck_ids)
    elite_ready = elite_readiness_score(
        gs.character,
        deck_ids,
        hp_ratio=hp_ratio_before,
        act=gs.act,
        floor=gs.floor,
    )

    if action == "rest":
        reward += hp_gained * RewardConfig.REST_HP_GAIN_FACTOR
        if hp_ratio_before < 0.45:
            reward += RewardConfig.REST_LOW_HP_BONUS
        elif hp_ratio_before < 0.6:
            reward += 1.2
        elif elite_ready < 0.58 and hp_ratio_before < 0.72:
            reward += 0.9
        elif best_upgrade >= 14.0:
            reward -= 0.5
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
        if hp_ratio_before >= 0.62 and elite_ready >= 0.56:
            reward += 0.75
        elif hp_ratio_before < 0.35 or (elite_ready < 0.48 and hp_ratio_before < 0.58):
            reward -= 0.75
    elif action == "dig":
        reward += RewardConfig.DIG_REWARD if hp_ratio_before >= 0.45 else 0.5
    elif action == "cook" and removed_card_ids:
        for rid in removed_card_ids:
            reward += compute_remove_card_reward(gs, rid)
        reward += hp_gained * RewardConfig.REST_HP_GAIN_FACTOR
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
    profile = strategic_role_scores(gs.character, deck_ids, act=gs.act, floor=floor)
    elite_ready = elite_readiness_score(gs.character, deck_ids, hp_ratio=hp_ratio, act=gs.act, floor=floor)
    upcoming_floor = floor + 1
    act_floor = ((upcoming_floor - 1) % 17) + 1
    has_rest_next = RoomType.REST in next_nodes_preview
    has_elite_next = RoomType.ELITE in next_nodes_preview
    needs_shop_power = (
        gs.player.gold >= gs.shop_remove_cost and best_remove >= 16.0
    ) or gs.player.gold >= 150 or profile["gap_total"] >= 0.32

    reward = RewardConfig.FLOOR_ADVANCE

    early_game = act_floor <= 6
    mid_game = 7 <= act_floor <= 11
    late_game = act_floor >= 12

    if n_alternatives >= 2:
        reward += RewardConfig.FLEXIBLE_ROUTE_BONUS

    if chosen_node_type == RoomType.MONSTER:
        if len(deck_ids) < strategy.ideal_deck_min or deck_quality < 0.58:
            reward += RewardConfig.MONSTER_GROWTH_BONUS
        if early_game and elite_ready < 0.68:
            reward += 1.1
        elif early_game and elite_ready >= 0.78:
            reward -= 0.2
        elif mid_game and elite_ready < 0.6:
            reward += 0.6
        if hp_ratio < 0.35:
            reward -= 0.75
        if profile["frontload_damage"] >= 0.85 and profile["frontload_aoe"] >= 0.7 and early_game:
            reward -= 0.5
    elif chosen_node_type == RoomType.ELITE:
        reward += (elite_ready - 0.5) * 7.0
        if early_game:
            reward += RewardConfig.ELITE_READY_BONUS if elite_ready >= 0.72 else RewardConfig.ELITE_UNREADY_PENALTY
        elif mid_game:
            reward += 2.2 if elite_ready >= 0.62 else -0.9
        else:
            reward += 1.5 if elite_ready >= 0.48 else -0.35
        if early_game:
            if elite_ready >= 0.7:
                reward += 1.6
            else:
                reward -= 1.5
        elif mid_game:
            reward += 1.0 if elite_ready >= 0.6 else -1.0
            reward += (deck_quality - 0.55) * 1.6
        elif late_game:
            reward += 1.0
            if hp_ratio < 0.4:
                reward -= 1.0
        if has_rest_next:
            reward += RewardConfig.CAMPFIRE_AFTER_ELITE
        if profile["frontload_aoe"] >= 0.7:
            reward += 0.4
    elif chosen_node_type == RoomType.REST:
        if hp_ratio < 0.45:
            reward += RewardConfig.REST_LOW_HP_BONUS
        elif best_upgrade >= 12.0 and hp_ratio >= 0.6 and elite_ready >= 0.55:
            reward += RewardConfig.REST_UPGRADE_WINDOW_BONUS
        elif elite_ready < 0.5 and hp_ratio < 0.68:
            reward += 1.0
        else:
            reward -= 0.2
        if has_elite_next:
            reward += 0.8
            if elite_ready >= 0.52:
                reward += RewardConfig.CAMPFIRE_BEFORE_ELITE
    elif chosen_node_type == RoomType.SHOP:
        if needs_shop_power:
            reward += RewardConfig.SHOP_REMOVE_SETUP_BONUS
        if gs.player.gold >= 125:
            reward += RewardConfig.SHOP_RICH_BONUS
        if best_remove >= 18.0:
            reward += 0.8
        if profile["gap_total"] >= 0.28:
            reward += 0.9
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
    hp_ratio: float,
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
    """商店买卡奖励。"""
    deck_ids = _deck_ids(gs)
    strategy = get_character_strategy(gs.character)
    profile = strategic_role_scores(gs.character, deck_ids, act=gs.act, floor=gs.floor)
    base_score = card_pick_score(gs.character, deck_ids, card_id)
    reward = base_score * 0.78 + RewardConfig.SHOP_CARD_PRICE_PRESSURE * (price / 75.0)
    remaining_gold = gs.player.gold - price
    best_remove = _best_remove_score(gs, deck_ids)

    if card_id in strategy.premium_shop_cards or base_score >= 10.0:
        reward += RewardConfig.SHOP_PREMIUM_CARD_BONUS
    if len(deck_ids) >= strategy.workable_deck_max and base_score < 8.0 and profile["engine"] < 0.72:
        reward += RewardConfig.SHOP_BLOAT_PENALTY
    if remaining_gold < gs.shop_remove_cost and best_remove >= 18.0 and base_score < 10.0:
        reward += RewardConfig.SHOP_REMOVE_GOLD_PRESSURE
    elif remaining_gold < gs.shop_remove_cost and base_score >= 10.0:
        reward += 0.4
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
    best_remove = _best_remove_score(gs)
    if remaining_gold < gs.shop_remove_cost and best_remove >= 20.0:
        reward += RewardConfig.SHOP_REMOVE_GOLD_PRESSURE * 0.75
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


def compute_combat_play_reward(
    gs: GameState,
    deck_ids: list[str],
    card_id: str,
    *,
    exhausts_on_play: bool,
) -> float:
    if not exhausts_on_play:
        return 0.0
    score = combat_exhaust_play_score(gs.character, deck_ids, card_id)
    if score >= 2.0:
        return RewardConfig.EXHAUST_PLAY_STRONG
    if score > 0.0:
        return RewardConfig.EXHAUST_PLAY_WEAK
    if score <= -1.0:
        return RewardConfig.EXHAUST_PLAY_BAD
    return 0.0
