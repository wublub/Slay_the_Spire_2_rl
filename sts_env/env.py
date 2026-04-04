"""STS2 Gymnasium 环境 v2：完整一局 Run，覆盖所有决策场景。"""
from __future__ import annotations

import copy
import json
import random as stdlib_random
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from sts_env.game_state import GameState, GamePhase, RoomType, PLAYABLE_CHARACTERS
from sts_env.combat import Combat, CombatResult, Player, make_card, TargetType
from sts_env.monster_ai import create_monster
from sts_env.map_gen import (
    generate_act_map,
    pick_encounter,
    generate_card_rewards,
    generate_shop_inventory,
    pick_event,
    apply_event_effect,
    FLOORS_PER_ACT,
)
from sts_env.encoding import encode_observation, get_obs_dim
from sts_env.rewards import (
    compute_combat_reward,
    compute_card_reward,
    compute_event_reward,
    compute_rest_reward,
    compute_route_reward,
    compute_potion_reward,
    compute_remove_at_shop_reward,
    compute_remove_card_reward,
    compute_shop_card_reward,
    compute_shop_relic_reward,
    compute_floor_reward,
    compute_win_reward,
    RewardConfig,
)
from sts_env.archetypes import removable_priority, upgrade_priority_score

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------------------
# 动作空间（共 94 个离散动作）
# ---------------------------------------------------------------------------
A_PLAY_START = 0
A_PLAY_END = 49
A_END_TURN = 50
A_POTION_START = 51
A_POTION_END = 65
A_MAP_START = 66
A_MAP_END = 69
A_PICK_START = 70
A_PICK_END = 72
A_SKIP = 73
A_REST = 74
A_UPGRADE = 75
A_DIG = 76
A_COOK = 77
A_LIFT = 78
A_SHOP_CARD_START = 79
A_SHOP_CARD_END = 81
A_SHOP_RELIC_START = 82
A_SHOP_RELIC_END = 84
A_SHOP_REMOVE = 85
A_SHOP_LEAVE = 86
A_EVENT_START = 87
A_EVENT_END = 90
A_BOSS_START = 91
A_BOSS_END = 93
TOTAL_ACTIONS = 94

_SHOP_CARD_PRICE = {"Common": 45, "Uncommon": 75, "Rare": 150}
_SHOP_RELIC_PRICE = {"Common": 150, "Uncommon": 250, "Rare": 300, "Ancient": 300}
_POTION_PRICE = {"Common": 50, "Uncommon": 75, "Rare": 100}

_RELIC_DB: list[dict] = []
_POTION_DB: list[dict] = []


def _load_relic_db() -> list[dict]:
    global _RELIC_DB
    if not _RELIC_DB:
        p = DATA_DIR / "relics.json"
        if p.exists():
            _RELIC_DB = json.loads(p.read_text("utf-8"))
    return _RELIC_DB


def _load_potion_db() -> list[dict]:
    global _POTION_DB
    if not _POTION_DB:
        p = DATA_DIR / "potions.json"
        if p.exists():
            _POTION_DB = json.loads(p.read_text("utf-8"))
    return _POTION_DB


class StsEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, character: str = "Ironclad", ascension: int = 0, seed: int | None = None):
        super().__init__()
        assert character in PLAYABLE_CHARACTERS, f"未知角色: {character}"
        self.character = character
        self.ascension = ascension
        self._seed = seed
        self._rng = stdlib_random.Random(seed)
        self.gs: GameState | None = None

        self.observation_space = spaces.Box(
            low=-10.0,
            high=10.0,
            shape=(get_obs_dim(),),
            dtype=np.float32,
        )
        self.action_space = spaces.Discrete(TOTAL_ACTIONS)

    # ------------------------------------------------------------------
    # Gym 接口
    # ------------------------------------------------------------------

    def reset(self, *, seed: int | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
        if seed is not None:
            self._seed = seed
            self._rng = stdlib_random.Random(seed)

        self.gs = GameState(character=self.character, ascension=self.ascension, seed=self._seed)
        self.gs.map_nodes = generate_act_map(self.gs.act, self.gs.rng)
        self.gs.available_next = list(range(len(self.gs.map_nodes[0]))) if self.gs.map_nodes else [0]
        self.gs.current_node = None
        self.gs.phase = GamePhase.MAP
        self.gs.combat = None
        self.gs.card_rewards = []
        self.gs.event_options = []
        self.gs.shop_cards = []
        self.gs.shop_relics = []
        self.gs.shop_potions = []
        self.gs.boss_relic_choices = []

        obs = encode_observation(self.gs)
        return obs, self._info()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
        assert self.gs is not None, "请先调用 reset()"
        gs = self.gs

        if gs.phase == GamePhase.GAME_OVER:
            return encode_observation(gs), 0.0, True, False, self._info()

        reward = 0.0
        terminated = False
        truncated = False

        if gs.phase == GamePhase.MAP:
            reward += self._step_map(action)
        elif gs.phase == GamePhase.COMBAT:
            reward += self._step_combat(action)
        elif gs.phase == GamePhase.CARD_REWARD:
            reward += self._step_card_reward(action)
        elif gs.phase == GamePhase.REST:
            reward += self._step_rest(action)
        elif gs.phase == GamePhase.SHOP:
            reward += self._step_shop(action)
        elif gs.phase == GamePhase.EVENT:
            reward += self._step_event(action)
        elif gs.phase == GamePhase.TREASURE:
            reward += self._step_treasure(action)
        elif gs.phase == GamePhase.BOSS_RELIC:
            reward += self._step_boss_relic(action)
        elif gs.phase == GamePhase.NEOW:
            gs.phase = GamePhase.MAP

        if gs.player.hp <= 0:
            gs.player.hp = 0
            gs.phase = GamePhase.GAME_OVER
            reward += RewardConfig.DEATH
            terminated = True

        if gs.won:
            gs.phase = GamePhase.GAME_OVER
            reward += compute_win_reward()
            terminated = True

        obs = encode_observation(gs)
        return obs, float(reward), terminated, truncated, self._info()

    def render(self):
        return None

    def close(self):
        return None

    # 兼容测试与 MaskablePPO
    def action_mask(self) -> np.ndarray:
        return self.action_masks()

    def action_masks(self) -> np.ndarray:
        mask = np.zeros(TOTAL_ACTIONS, dtype=bool)
        gs = self.gs
        if gs is None:
            mask[A_MAP_START] = True
            return mask

        if gs.phase == GamePhase.MAP:
            self._mask_map(mask)
        elif gs.phase == GamePhase.COMBAT:
            self._mask_combat(mask)
        elif gs.phase == GamePhase.CARD_REWARD:
            self._mask_card_reward(mask)
        elif gs.phase == GamePhase.REST:
            self._mask_rest(mask)
        elif gs.phase == GamePhase.SHOP:
            self._mask_shop(mask)
        elif gs.phase == GamePhase.EVENT:
            self._mask_event(mask)
        elif gs.phase == GamePhase.TREASURE:
            mask[A_SKIP] = True
        elif gs.phase == GamePhase.BOSS_RELIC:
            self._mask_boss_relic(mask)
        else:
            mask[A_END_TURN] = True

        if not mask.any():
            mask[A_END_TURN] = True
        return mask

    # ------------------------------------------------------------------
    # 信息与辅助函数
    # ------------------------------------------------------------------

    def _info(self) -> dict[str, Any]:
        gs = self.gs
        return {
            "phase": gs.phase.name,
            "floor": gs.floor,
            "act": gs.act,
            "hp": gs.player.hp,
            "max_hp": gs.player.max_hp,
            "gold": gs.player.gold,
            "won": gs.won,
        }

    def _next_room_options(self) -> list[RoomType]:
        gs = self.gs
        if gs.current_node is None:
            floor_idx = gs.floor
            if 0 <= floor_idx < len(gs.map_nodes):
                return [n.room_type for n in gs.map_nodes[floor_idx]]
            return []
        next_floor = gs.current_node.floor + 1
        if next_floor >= len(gs.map_nodes):
            return []
        out: list[RoomType] = []
        for child_idx in gs.current_node.children:
            if 0 <= child_idx < len(gs.map_nodes[next_floor]):
                out.append(gs.map_nodes[next_floor][child_idx].room_type)
        return out

    def _advance_after_room(self):
        gs = self.gs
        reward = compute_floor_reward()

        if gs.current_node is None:
            gs.phase = GamePhase.GAME_OVER
            return reward

        next_floor = gs.current_node.floor + 1
        if next_floor >= len(gs.map_nodes):
            if gs.current_node.room_type == RoomType.BOSS:
                if gs.act >= 3:
                    gs.won = True
                    gs.phase = GamePhase.GAME_OVER
                else:
                    gs.act += 1
                    gs.map_nodes = generate_act_map(gs.act, gs.rng)
                    gs.available_next = list(range(len(gs.map_nodes[0]))) if gs.map_nodes else [0]
                    gs.current_node = None
                    gs.floor = (gs.act - 1) * FLOORS_PER_ACT
                    gs.phase = GamePhase.MAP
            else:
                gs.phase = GamePhase.GAME_OVER
            return reward

        if gs.current_node.children:
            gs.available_next = list(gs.current_node.children)
        else:
            gs.available_next = list(range(len(gs.map_nodes[next_floor])))
        gs.phase = GamePhase.MAP
        return reward

    def _start_room(self, room_type: RoomType):
        gs = self.gs
        if room_type in (RoomType.MONSTER, RoomType.ELITE, RoomType.BOSS):
            self._start_combat(room_type)
        elif room_type == RoomType.REST:
            gs.phase = GamePhase.REST
        elif room_type == RoomType.SHOP:
            self._init_shop()
            gs.phase = GamePhase.SHOP
        elif room_type == RoomType.EVENT:
            self._init_event()
            gs.phase = GamePhase.EVENT
        elif room_type == RoomType.TREASURE:
            self._init_treasure()
            gs.phase = GamePhase.TREASURE
        else:
            gs.phase = GamePhase.MAP

    def _choose_remove_index(self, count: int = 1) -> list[int]:
        gs = self.gs
        deck_ids = [c.card_id for c in gs.deck]
        priority = removable_priority(gs.character, deck_ids, floor=gs.floor, act=gs.act)
        chosen: list[int] = []
        used: set[int] = set()
        for cid in priority:
            for idx, card in enumerate(gs.deck):
                if idx in used:
                    continue
                if card.card_id == cid:
                    chosen.append(idx)
                    used.add(idx)
                    break
            if len(chosen) >= count:
                return chosen
        for idx in range(len(gs.deck)):
            if idx not in used:
                chosen.append(idx)
            if len(chosen) >= count:
                break
        return chosen

    def _best_upgrade_index(self) -> int | None:
        gs = self.gs
        deck_ids = [c.card_id for c in gs.deck]
        candidates: list[tuple[float, int]] = []
        for idx, card in enumerate(gs.deck):
            if card.upgraded:
                continue
            score = upgrade_priority_score(gs.character, deck_ids, card.card_id)
            candidates.append((score, idx))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]

    # ------------------------------------------------------------------
    # MAP
    # ------------------------------------------------------------------

    def _mask_map(self, mask: np.ndarray):
        gs = self.gs
        for i in range(min(4, len(gs.available_next))):
            mask[A_MAP_START + i] = True

    def _step_map(self, action: int) -> float:
        gs = self.gs
        if not (A_MAP_START <= action <= A_MAP_END):
            action = A_MAP_START
        choice = action - A_MAP_START
        if not gs.available_next:
            gs.phase = GamePhase.GAME_OVER
            return 0.0
        if choice >= len(gs.available_next):
            choice = 0

        node_idx = gs.available_next[choice]
        floor_idx = gs.floor % FLOORS_PER_ACT
        if floor_idx >= len(gs.map_nodes):
            floor_idx = min(gs.floor, len(gs.map_nodes) - 1)
        layer = gs.map_nodes[floor_idx]
        node_idx = max(0, min(node_idx, len(layer) - 1))
        node = layer[node_idx]

        next_preview = []
        if node.floor + 1 < len(gs.map_nodes):
            for child_idx in node.children:
                if 0 <= child_idx < len(gs.map_nodes[node.floor + 1]):
                    next_preview.append(gs.map_nodes[node.floor + 1][child_idx].room_type)

        reward = compute_route_reward(
            gs=gs,
            chosen_node_type=node.room_type,
            n_alternatives=len(node.children),
            floor=gs.floor,
            next_nodes_preview=next_preview,
        )

        gs.current_node = node
        gs.floor += 1
        self._start_room(node.room_type)
        return reward

    # ------------------------------------------------------------------
    # COMBAT
    # ------------------------------------------------------------------

    def _start_combat(self, room_type: RoomType):
        gs = self.gs
        monster_ids = pick_encounter(room_type, gs.act, gs.rng)
        monsters = [create_monster(mid) for mid in monster_ids]

        p = Player(gs.player.name, gs.player.hp, gs.player.max_hp)
        p.gold = gs.player.gold
        p.relics = list(gs.player.relics)
        p.potions = copy.deepcopy(gs.player.potions)
        p.powers = copy.deepcopy(gs.player.powers)
        p.init_deck(gs.get_deck_copy())

        gs.combat = Combat(p, monsters)
        gs.combat.start_combat()
        gs.phase = GamePhase.COMBAT
        gs._combat_room_type = room_type
        gs._combat_hp_before = gs.player.hp

    def _sync_from_combat(self):
        gs = self.gs
        cp = gs.combat.player
        gs.player.hp = cp.hp
        gs.player.max_hp = cp.max_hp
        gs.player.gold = cp.gold
        gs.player.relics = list(cp.relics)
        gs.player.potions = copy.deepcopy(cp.potions)
        gs.player.powers = copy.deepcopy(cp.powers)

    def _mask_combat(self, mask: np.ndarray):
        gs = self.gs
        combat = gs.combat
        if combat is None or combat.is_over:
            mask[A_END_TURN] = True
            return

        p = combat.player
        enemies = combat.alive_monsters
        target_count = max(1, min(5, len(enemies)))

        if combat.hand_selection is not None:
            selection = combat.hand_selection
            for card_idx, enabled in enumerate(selection.selectable_cards[:10]):
                if enabled:
                    mask[A_PLAY_START + card_idx * 5] = True
            if selection.confirm_enabled:
                mask[A_END_TURN] = True
            return

        playable_cards = combat.playable_cards_override
        if playable_cards is None:
            playable_cards = [card.can_play(p.energy) for card in p.hand[:10]]

        for card_idx, card in enumerate(p.hand[:10]):
            enabled = card.can_play(p.energy)
            if card_idx < len(playable_cards):
                enabled = bool(playable_cards[card_idx])
            if not enabled:
                continue
            if card.target == TargetType.ANY_ENEMY:
                for target_idx in range(target_count):
                    mask[A_PLAY_START + card_idx * 5 + target_idx] = True
            else:
                mask[A_PLAY_START + card_idx * 5] = True

        for pot_idx, _pot in enumerate(p.potions[:3]):
            for target_idx in range(target_count):
                mask[A_POTION_START + pot_idx * 5 + target_idx] = True

        if combat.end_turn_enabled_override is not False:
            mask[A_END_TURN] = True

    def _use_potion(self, potion_idx: int, target_idx: int) -> float:
        gs = self.gs
        combat = gs.combat
        p = combat.player
        if potion_idx < 0 or potion_idx >= len(p.potions):
            return -0.05

        pot = p.potions.pop(potion_idx)
        pot_id = pot.get("id", "")
        hp_ratio = p.hp / max(1, p.max_hp)
        reward = compute_potion_reward(gs, getattr(gs, "_combat_room_type", RoomType.MONSTER), hp_ratio)

        if pot_id in {"BloodPotion", "FruitJuice", "FairyInABottle", "BoneBrew"}:
            p.heal(max(8, p.max_hp // 5))
        elif pot_id in {"BlockPotion", "DexterityPotion"}:
            p.gain_block(12)
        elif pot_id in {"AttackPotion", "StrengthPotion", "FirePotion", "FearPotion", "PoisonPotion"}:
            alive = combat.alive_monsters
            if alive:
                t = alive[min(target_idx, len(alive) - 1)]
                if pot_id == "PoisonPotion":
                    t.take_unblockable_damage(10)
                else:
                    t.take_unblockable_damage(20)
        else:
            if combat.alive_monsters:
                t = combat.alive_monsters[min(target_idx, len(combat.alive_monsters) - 1)]
                t.take_unblockable_damage(12)
        combat._check_combat_end()
        return reward

    def _finish_combat(self) -> float:
        gs = self.gs
        self._sync_from_combat()
        room_type = getattr(gs, "_combat_room_type", RoomType.MONSTER)
        reward = compute_combat_reward(
            gs=gs,
            room_type=room_type,
            won=True,
            hp_before=getattr(gs, "_combat_hp_before", gs.player.hp),
            hp_after=gs.player.hp,
            turns=max(1, gs.combat.turn_count + 1),
            max_hp=gs.player.max_hp,
        )

        if room_type == RoomType.MONSTER:
            gs.monsters_killed += 1
            gs.card_rewards = generate_card_rewards(gs.character, gs.rng, count=3)
            gs.phase = GamePhase.CARD_REWARD
        elif room_type == RoomType.ELITE:
            gs.elites_killed += 1
            gs.card_rewards = generate_card_rewards(gs.character, gs.rng, count=3)
            self._gain_random_relic("elite")
            gs.phase = GamePhase.CARD_REWARD
        else:
            gs.bosses_killed += 1
            gs.boss_relic_choices = self._generate_boss_relic_choices()
            gs.phase = GamePhase.BOSS_RELIC
        gs.combat = None
        return reward

    def _step_combat(self, action: int) -> float:
        gs = self.gs
        combat = gs.combat
        if combat is None:
            gs.phase = GamePhase.MAP
            return 0.0

        reward = 0.0
        if combat.hand_selection is not None:
            if A_PLAY_START <= action <= A_PLAY_END:
                rel = action - A_PLAY_START
                card_idx = rel // 5
                target_idx = rel % 5
                ok = target_idx == 0 and combat.select_hand_card(card_idx)
                if not ok:
                    reward -= 0.05
            elif action == A_END_TURN:
                if not combat.confirm_hand_selection():
                    reward -= 0.05
            else:
                reward -= 0.05
        else:
            if A_PLAY_START <= action <= A_PLAY_END:
                rel = action - A_PLAY_START
                card_idx = rel // 5
                target_idx = rel % 5
                ok = combat.play_card(card_idx, target_idx)
                if not ok:
                    reward -= 0.05
            elif action == A_END_TURN:
                combat.end_player_turn()
            elif A_POTION_START <= action <= A_POTION_END:
                rel = action - A_POTION_START
                potion_idx = rel // 5
                target_idx = rel % 5
                reward += self._use_potion(potion_idx, target_idx)
            else:
                reward -= 0.05

        if combat.result == CombatResult.WIN:
            reward += self._finish_combat()
        elif combat.result == CombatResult.LOSE:
            self._sync_from_combat()
            gs.player.hp = 0
            gs.phase = GamePhase.GAME_OVER
        return reward

    # ------------------------------------------------------------------
    # CARD REWARD
    # ------------------------------------------------------------------

    def _mask_card_reward(self, mask: np.ndarray):
        gs = self.gs
        for i in range(min(3, len(gs.card_rewards))):
            mask[A_PICK_START + i] = True
        mask[A_SKIP] = True

    def _step_card_reward(self, action: int) -> float:
        gs = self.gs
        offered_card_ids = [card.card_id for card in gs.card_rewards]
        if A_PICK_START <= action <= A_PICK_END and gs.card_rewards:
            idx = min(action - A_PICK_START, len(gs.card_rewards) - 1)
            card = gs.card_rewards[idx]
            reward = compute_card_reward(gs, card.card_id, skipped=False, offered_card_ids=offered_card_ids)
            gs.add_card_to_deck(make_card(card.card_id, card.upgraded))
        else:
            reward = compute_card_reward(gs, None, skipped=True, offered_card_ids=offered_card_ids)
        gs.card_rewards = []
        reward += self._advance_after_room()
        return reward

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------

    def _mask_rest(self, mask: np.ndarray):
        gs = self.gs
        mask[A_REST] = True
        if any(not c.upgraded for c in gs.deck):
            mask[A_UPGRADE] = True
        if "Shovel" in gs.player.relics:
            mask[A_DIG] = True
        if "MeatCleaver" in gs.player.relics and len(gs.deck) >= 2:
            mask[A_COOK] = True
        if "Girya" in gs.player.relics:
            mask[A_LIFT] = True

    def _step_rest(self, action: int) -> float:
        gs = self.gs
        reward = 0.0
        hp_before = gs.player.hp
        if action == A_REST:
            heal_amt = max(1, int(gs.player.max_hp * 0.3))
            gs.player.heal(heal_amt)
            reward += compute_rest_reward(gs, action="rest", hp_gained=gs.player.hp - hp_before, hp_before=hp_before)
        elif action == A_UPGRADE:
            idx = self._best_upgrade_index()
            if idx is not None:
                gs.deck[idx].apply_upgrade()
                reward += compute_rest_reward(gs, action="upgrade", hp_before=hp_before, upgraded_card_id=gs.deck[idx].card_id)
            else:
                reward -= 0.05
        elif action == A_DIG and "Shovel" in gs.player.relics:
            self._gain_random_relic("rest")
            reward += compute_rest_reward(gs, action="dig", hp_before=hp_before)
        elif action == A_COOK and "MeatCleaver" in gs.player.relics and len(gs.deck) >= 2:
            idxs = sorted(self._choose_remove_index(count=2), reverse=True)
            removed_ids: list[str] = []
            for idx in idxs:
                if 0 <= idx < len(gs.deck):
                    removed_ids.append(gs.deck[idx].card_id)
                    gs.remove_card_from_deck(idx)
            gs.player.max_hp += 9
            gs.player.hp = min(gs.player.max_hp, gs.player.hp + 9)
            reward += compute_rest_reward(gs, action="cook", hp_gained=9, hp_before=hp_before, removed_card_ids=removed_ids)
        elif action == A_LIFT and "Girya" in gs.player.relics:
            reward += compute_rest_reward(gs, action="lift", hp_before=hp_before)
        else:
            reward -= 0.05

        reward += self._advance_after_room()
        return reward

    # ------------------------------------------------------------------
    # SHOP
    # ------------------------------------------------------------------

    def _init_shop(self):
        gs = self.gs
        inv = generate_shop_inventory(gs.character, gs.rng)
        gs.shop_cards = list(inv.get("cards", []))
        gs.shop_relics = [
            r for r in inv.get("relics", [])
            if r.get("id") not in gs.player.relics
        ][:3]
        gs.shop_potions = list(inv.get("potions", []))[:3]

    def _mask_shop(self, mask: np.ndarray):
        gs = self.gs
        for i, card in enumerate(gs.shop_cards[:3]):
            rarity = getattr(card, "rarity", None) or getattr(card, "pool", None) or "Common"
            price = _SHOP_CARD_PRICE.get(rarity, 75)
            if gs.player.gold >= price:
                mask[A_SHOP_CARD_START + i] = True

        for i, relic in enumerate(gs.shop_relics[:3]):
            price = _SHOP_RELIC_PRICE.get(relic.get("rarity", "Common"), 250)
            if gs.player.gold >= price:
                mask[A_SHOP_RELIC_START + i] = True

        if gs.player.gold >= gs.shop_remove_cost and len(gs.deck) > 0:
            mask[A_SHOP_REMOVE] = True
        mask[A_SHOP_LEAVE] = True

    def _step_shop(self, action: int) -> float:
        gs = self.gs
        reward = 0.0

        if A_SHOP_CARD_START <= action <= A_SHOP_CARD_END and gs.shop_cards:
            idx = min(action - A_SHOP_CARD_START, len(gs.shop_cards) - 1)
            card = gs.shop_cards[idx]
            rarity = getattr(card, "rarity", None) or "Common"
            price = _SHOP_CARD_PRICE.get(rarity, 75)
            if gs.player.gold >= price:
                reward += compute_shop_card_reward(gs, card.card_id, price)
                gs.player.gold -= price
                gs.add_card_to_deck(make_card(card.card_id, card.upgraded))
                gs.shop_cards.pop(idx)
            else:
                reward -= 0.05
            return reward

        if A_SHOP_RELIC_START <= action <= A_SHOP_RELIC_END and gs.shop_relics:
            idx = min(action - A_SHOP_RELIC_START, len(gs.shop_relics) - 1)
            relic = gs.shop_relics[idx]
            price = _SHOP_RELIC_PRICE.get(relic.get("rarity", "Common"), 250)
            if gs.player.gold >= price:
                reward += compute_shop_relic_reward(gs, relic["id"], relic.get("rarity", "Common"), price)
                gs.player.gold -= price
                gs.player.relics.append(relic["id"])
                gs.shop_relics.pop(idx)
            else:
                reward -= 0.05
            return reward

        if action == A_SHOP_REMOVE:
            if gs.player.gold >= gs.shop_remove_cost and len(gs.deck) > 0:
                idx = self._choose_remove_index(count=1)[0]
                removed_id = gs.deck[idx].card_id
                reward += compute_remove_at_shop_reward(gs, removed_id)
                gs.remove_card_from_deck(idx)
                gs.player.gold -= gs.shop_remove_cost
                gs.shop_removes_done += 1
                gs.shop_remove_cost += 25
            else:
                reward -= 0.05
            return reward

        if action == A_SHOP_LEAVE:
            reward += self._advance_after_room()
            return reward

        return -0.05

    # ------------------------------------------------------------------
    # EVENT
    # ------------------------------------------------------------------

    def _init_event(self):
        gs = self.gs
        ev = pick_event(gs.rng)
        gs.event_options = list(ev.get("options", []))[:4]
        if not gs.event_options:
            gs.event_options = [{"label": "离开", "effect": {}}]

    def _mask_event(self, mask: np.ndarray):
        gs = self.gs
        for i in range(min(4, len(gs.event_options))):
            mask[A_EVENT_START + i] = True

    def _step_event(self, action: int) -> float:
        gs = self.gs
        idx = action - A_EVENT_START
        if idx < 0 or idx >= len(gs.event_options):
            idx = 0
        option = gs.event_options[idx]
        effect = option.get("effect", {})
        hp_before = gs.player.hp
        apply_event_effect(gs, effect)

        remove_reward = 0.0
        if effect.get("remove", False) and len(gs.deck) > 0:
            idx_remove = self._choose_remove_index(count=1)[0]
            removed_id = gs.deck[idx_remove].card_id
            remove_reward += compute_remove_card_reward(gs, removed_id)
            gs.remove_card_from_deck(idx_remove)

        reward = compute_event_reward(gs, effect, hp_before=hp_before, remove_reward=remove_reward)
        gs.event_options = []
        reward += self._advance_after_room()
        return reward

    # ------------------------------------------------------------------
    # TREASURE / BOSS RELIC
    # ------------------------------------------------------------------

    def _gain_random_relic(self, source: str = "common") -> str | None:
        gs = self.gs
        relics = [r for r in _load_relic_db() if r.get("id") not in gs.player.relics]
        if source == "elite":
            pool = [r for r in relics if r.get("rarity") in {"Uncommon", "Rare", "Ancient"}]
        elif source == "boss":
            pool = [r for r in relics if r.get("rarity") in {"Rare", "Ancient"}]
        else:
            pool = [r for r in relics if r.get("rarity") in {"Common", "Uncommon", "Rare"}]
        if not pool:
            return None
        relic = gs.rng.choice(pool)
        gs.player.relics.append(relic["id"])
        return relic["id"]

    def _init_treasure(self):
        self.gs.boss_relic_choices = []

    def _step_treasure(self, action: int) -> float:
        self._gain_random_relic("common")
        return self._advance_after_room()

    def _generate_boss_relic_choices(self) -> list[str]:
        gs = self.gs
        pool = [r for r in _load_relic_db() if r.get("id") not in gs.player.relics and r.get("rarity") in {"Rare", "Ancient"}]
        gs.rng.shuffle(pool)
        return [r["id"] for r in pool[:3]]

    def _mask_boss_relic(self, mask: np.ndarray):
        gs = self.gs
        for i in range(min(3, len(gs.boss_relic_choices))):
            mask[A_BOSS_START + i] = True

    def _step_boss_relic(self, action: int) -> float:
        gs = self.gs
        if not gs.boss_relic_choices:
            return self._advance_after_room()
        idx = min(max(0, action - A_BOSS_START), len(gs.boss_relic_choices) - 1)
        gs.player.relics.append(gs.boss_relic_choices[idx])
        gs.boss_relic_choices = []
        return self._advance_after_room()
