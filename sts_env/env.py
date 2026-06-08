"""STS2 Gymnasium 环境 v2：完整一局 Run，覆盖所有决策场景。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from sts_env.game_state import GameState, GamePhase, RoomType, PLAYABLE_CHARACTERS
from sts_env.combat import Combat, CombatResult, Player, make_card, TargetType, player_can_play_card
from sts_env.monster_ai import create_monster
from sts_env.map_gen import (
    generate_act_map,
    pick_encounter,
    generate_card_rewards,
    generate_transformed_card,
    generate_shop_inventory,
    choose_event,
    materialize_event,
    pick_event,
    apply_event_effect,
    FLOORS_PER_ACT,
)
from sts_env.encoding import encode_observation, get_obs_dim
from sts_env.encoding import MAX_GENERIC_SELECTION
from sts_env.rewards import (
    compute_combat_reward,
    compute_combat_play_reward,
    compute_run_score,
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
from sts_env.combat_scoring import analyze_turn_avoidable_hp_loss, build_turn_search_context

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------------------
# 动作空间（共 97 个离散动作）
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
A_SHOP_POTION_START = 82
A_SHOP_POTION_END = 84
A_SHOP_RELIC_START = 85
A_SHOP_RELIC_END = 87
A_SHOP_REMOVE = 88
A_SHOP_LEAVE = 89
A_EVENT_START = 90
A_EVENT_END = 93
A_BOSS_START = 94
A_BOSS_END = 96
A_SELECT_START = 97
A_SELECT_END = 136
TOTAL_ACTIONS = 137


def _option_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return {"label": value, "effect": {}}
    return {}


def _shop_item_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return {"id": value}
    return {}


def _shop_items_payload(values: Any) -> list[dict[str, Any]]:
    return [item for item in (_shop_item_payload(value) for value in (values or [])) if item]


def _event_options_payload(values: Any) -> list[dict[str, Any]]:
    return [item for item in (_option_payload(value) for value in (values or [])) if item]


_SHOP_CARD_PRICE = {"Common": 45, "Uncommon": 75, "Rare": 150}
_SHOP_RELIC_PRICE = {"Common": 150, "Uncommon": 250, "Rare": 300, "Ancient": 300}
_POTION_PRICE = {"Common": 50, "Uncommon": 75, "Rare": 100}
_SELF_TARGET_POTIONS = {
    "Ashwater",
    "AttackPotion",
    "BlessingOfTheForge",
    "BlockPotion",
    "BloodPotion",
    "BoneBrew",
    "BottledPotential",
    "Clarity",
    "ColorlessPotion",
    "CosmicConcoction",
    "CunningPotion",
    "CureAll",
    "DexterityPotion",
    "DistilledChaos",
    "DropletOfPrecognition",
    "Duplicator",
    "EnergyPotion",
    "EntropicBrew",
    "EssenceOfDarkness",
    "FairyInABottle",
    "FlexPotion",
    "FocusPotion",
    "Fortifier",
    "FruitJuice",
    "FyshOil",
    "GamblersBrew",
    "GhostInAJar",
    "GigantificationPotion",
    "GlowwaterPotion",
    "HeartOfIron",
    "KingsCourage",
    "LiquidBronze",
    "LiquidMemories",
    "LuckyTonic",
    "MazalethsGift",
    "OrobicAcid",
    "PotionOfCapacity",
    "PowerPotion",
    "RadiantTincture",
    "RegenPotion",
    "ShipInABottle",
    "SkillPotion",
    "SneckoOil",
    "SoldiersStew",
    "SpeedPotion",
    "StableSerum",
    "StarPotion",
    "StrengthPotion",
    "SwiftPotion",
    "TouchOfInsanity",
}
_ALL_ENEMY_POTIONS = {"ExplosiveAmpoule", "PotionOfBinding", "FoulPotion"}

_RELIC_DB: list[dict] = []
_POTION_DB: list[dict] = []
_POTION_ID_ALIAS: dict[str, str] = {}


def _enum_key(value: Any) -> str:
    return str(value).strip().replace("-", "_").replace(" ", "_").lower()


def _id_key(value: Any) -> str:
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def _load_relic_db() -> list[dict]:
    global _RELIC_DB
    if not _RELIC_DB:
        p = DATA_DIR / "relics.json"
        if p.exists():
            _RELIC_DB = json.loads(p.read_text("utf-8"))
    return _RELIC_DB


def _load_potion_db() -> list[dict]:
    global _POTION_DB, _POTION_ID_ALIAS
    if not _POTION_DB:
        p = DATA_DIR / "potions.json"
        if p.exists():
            _POTION_DB = json.loads(p.read_text("utf-8"))
            _POTION_ID_ALIAS = {
                _id_key(str(item.get("id"))): str(item.get("id"))
                for item in _POTION_DB
                if isinstance(item, dict) and item.get("id")
            }
    return _POTION_DB


def _canonicalize_potion_id(value: Any) -> str:
    _load_potion_db()
    text = str(value or "").strip()
    if not text:
        return ""
    return _POTION_ID_ALIAS.get(_id_key(text), text)


def _potion_id_from_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return _canonicalize_potion_id(payload)
    if isinstance(payload, dict):
        return _canonicalize_potion_id(payload.get("id", ""))
    return ""


def _potion_target_slots(potion_id: str, enemy_count: int) -> list[int]:
    if potion_id in _SELF_TARGET_POTIONS or enemy_count <= 0:
        return [0]
    if potion_id in _ALL_ENEMY_POTIONS:
        return [0]
    return list(range(enemy_count))


class StsEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, character: str = "Ironclad", ascension: int = 0, seed: int | None = None):
        super().__init__()
        assert character in PLAYABLE_CHARACTERS, f"未知角色: {character}"
        self.character = character
        self.ascension = ascension
        self._seed = seed
        self.gs: GameState | None = None
        self._selection_choice_map: list[int] = []
        self._selection_resolve_callback = None
        self._selection_skip_callback = None

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

        self.gs = GameState(character=self.character, ascension=self.ascension, seed=self._seed)
        self.gs.map_nodes = generate_act_map(self.gs.act, self.gs.rngs.map)
        self.gs.available_next = list(range(len(self.gs.map_nodes[0]))) if self.gs.map_nodes else [0]
        self.gs.current_node = None
        self.gs.phase = GamePhase.MAP
        self.gs.combat = None
        self.gs.card_rewards = []
        self.gs.selection_kind = None
        self.gs.selection_cards = []
        self.gs.selection_can_skip = False
        self.gs.selection_total_count = 0
        self.gs.selection_truncated = False
        self.gs.current_event_id = None
        self.gs.event_options = []
        self.gs.shop_cards = []
        self.gs.shop_relics = []
        self.gs.shop_potions = []
        self.gs.boss_relic_choices = []
        self._run_combat_score_total = 0.0
        self._run_combat_count = 0
        self._run_hp_lost_total = 0
        self._run_avoidable_hp_lost_total = 0
        self._run_turns_total = 0
        self._combat_actual_hp_lost = 0
        self._combat_avoidable_hp_lost = 0
        self._combat_turn_damage_samples = []
        self._combat_hp_before = self.gs.player.hp

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
        elif gs.phase == GamePhase.CARD_SELECT:
            reward += self._step_card_select(action)
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
        info = self._build_terminal_info(won=bool(gs.won)) if (terminated or truncated) else self._info()
        return obs, float(reward), terminated, truncated, info

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
        elif gs.phase == GamePhase.CARD_SELECT:
            self._mask_card_select(mask)
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
        elif gs.phase == GamePhase.GAME_OVER:
            return mask
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

    def _build_terminal_info(self, *, won: bool) -> dict[str, Any]:
        info = self._info()
        info["won"] = bool(won)
        info["combat_score_total"] = float(getattr(self, "_run_combat_score_total", 0.0))
        info["combat_count"] = int(getattr(self, "_run_combat_count", 0))
        combat_count = info["combat_count"]
        info["avg_turns_per_combat"] = (
            float(getattr(self, "_run_turns_total", 0)) / combat_count if combat_count else 0.0
        )
        info["avg_hp_lost_per_combat"] = (
            float(getattr(self, "_run_hp_lost_total", 0)) / combat_count if combat_count else 0.0
        )
        info["avg_avoidable_hp_lost_per_combat"] = (
            float(getattr(self, "_run_avoidable_hp_lost_total", 0)) / combat_count if combat_count else 0.0
        )
        info["run_score"] = compute_run_score(
            won=bool(won),
            floor=int(info.get("floor", 0)),
            hp=int(info.get("hp", 0)),
            max_hp=int(info.get("max_hp", 0)),
            combat_score_total=float(info["combat_score_total"]),
        )
        return info

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
                    gs.map_nodes = generate_act_map(gs.act, gs.rngs.map)
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
        scored: list[tuple[float, int]] = []
        for idx, card in enumerate(gs.deck):
            score = float(len(priority) - priority.index(card.card_id)) if card.card_id in priority else 0.0
            scored.append((score, idx))

        groups: dict[float, list[int]] = {}
        for score, idx in scored:
            groups.setdefault(score, []).append(idx)

        ordered_indices: list[int] = []
        for score in sorted(groups.keys(), reverse=True):
            tied = list(groups[score])
            gs.rngs.niche.shuffle(tied)
            ordered_indices.extend(tied)
        return ordered_indices[:max(0, count)]

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
        best_score = max(score for score, _idx in candidates)
        tied = [idx for score, idx in candidates if score == best_score]
        gs.rngs.niche.shuffle(tied)
        return tied[0]

    def _begin_card_selection(
        self,
        *,
        kind: str,
        cards: list,
        choice_map: list[int],
        can_skip: bool,
        on_resolve,
        on_skip=None,
    ):
        gs = self.gs
        gs.selection_kind = kind
        gs.selection_cards = list(cards)
        gs.selection_can_skip = bool(can_skip)
        gs.selection_total_count = len(choice_map)
        gs.selection_truncated = len(choice_map) > len(cards)
        gs.phase = GamePhase.CARD_SELECT
        self._selection_choice_map = list(choice_map)
        self._selection_resolve_callback = on_resolve
        self._selection_skip_callback = on_skip

    def _clear_card_selection(self):
        gs = self.gs
        gs.selection_kind = None
        gs.selection_cards = []
        gs.selection_can_skip = False
        gs.selection_total_count = 0
        gs.selection_truncated = False
        self._selection_choice_map = []
        self._selection_resolve_callback = None
        self._selection_skip_callback = None

    def _truncate_selection_candidates(self, candidate_indices: list[int]) -> tuple[list[int], list]:
        gs = self.gs
        choice_map = list(candidate_indices[:MAX_GENERIC_SELECTION])
        selection_cards = [gs.deck[idx] for idx in choice_map]
        return choice_map, selection_cards

    def _eligible_selection_indices(
        self,
        *,
        kind: str,
        selector_tags: list[str] | None = None,
        enchantment_id: str | None = None,
    ) -> list[int]:
        gs = self.gs
        indices = list(range(len(gs.deck)))
        if kind == "upgrade":
            indices = [idx for idx in indices if not gs.deck[idx].upgraded]
        if selector_tags:
            required = set(selector_tags)
            indices = [idx for idx in indices if required.intersection(set(gs.deck[idx].tags))]
        if kind == "enchant" and enchantment_id:
            indices = [idx for idx in indices if self._can_apply_enchantment(gs.deck[idx], enchantment_id)]
        return indices

    def _can_apply_enchantment(self, card, enchantment_id: str) -> bool:
        if any(str(enchantment.get("id", "")) == enchantment_id for enchantment in getattr(card, "enchantments", [])):
            return False
        if enchantment_id == "Corrupted":
            return card.card_type.value == "Attack"
        if enchantment_id == "Vigorous":
            return card.card_type.value == "Attack"
        if enchantment_id == "SoulsPower":
            return "Exhaust" in card.keywords
        if enchantment_id == "Slither":
            return "Unplayable" not in card.keywords and card.cost >= 0
        if enchantment_id == "Spiral":
            return card.rarity == "Basic" and ("Strike" in card.tags or "Defend" in card.tags)
        return True

    def _apply_enchantment_to_card(self, card, enchantment_id: str, amount: int):
        card.add_enchantment(enchantment_id, amount=max(1, int(amount)))

    def _apply_event_card_additions(self, effect: dict):
        gs = self.gs
        for card_id in effect.get("add_cards", []):
            gs.add_card_to_deck(make_card(str(card_id)))
        for card_id in effect.get("add_curses", []):
            gs.add_card_to_deck(make_card(str(card_id)))

    def _finish_event_with_effect(self, effect: dict, *, hp_before: int, remove_reward: float = 0.0) -> float:
        gs = self.gs
        self._apply_event_card_additions(effect)
        reward = compute_event_reward(gs, effect, hp_before=hp_before, remove_reward=remove_reward)
        gs.current_event_id = None
        gs.event_options = []
        reward += self._advance_after_room()
        return reward

    def _finalize_post_combat_rewards(self, room_type: RoomType):
        gs = self.gs
        if room_type == RoomType.MONSTER:
            gs.monsters_killed += 1
            gs.card_rewards = generate_card_rewards(gs.character, gs.player.player_rngs.rewards, count=3)
            gs.phase = GamePhase.CARD_REWARD
        elif room_type == RoomType.ELITE:
            gs.elites_killed += 1
            gs.card_rewards = generate_card_rewards(gs.character, gs.player.player_rngs.rewards, count=3)
            self._gain_random_relic("elite")
            gs.phase = GamePhase.CARD_REWARD
        else:
            gs.bosses_killed += 1
            gs.boss_relic_choices = self._generate_boss_relic_choices()
            gs.phase = GamePhase.BOSS_RELIC

    def _begin_post_combat_grimoire_selection(self, room_type: RoomType, remaining: int) -> float:
        gs = self.gs
        candidates = list(range(len(gs.deck)))
        if remaining <= 0 or not candidates:
            self._finalize_post_combat_rewards(room_type)
            return 0.0
        choice_map, selection_cards = self._truncate_selection_candidates(candidates)

        def _resolve_grimoire_selection(selected_idx: int) -> float:
            selected_card_id = gs.deck[selected_idx].card_id
            remove_reward = compute_remove_card_reward(gs, selected_card_id)
            gs.remove_card_from_deck(selected_idx)
            return remove_reward + self._begin_post_combat_grimoire_selection(room_type, remaining - 1)

        self._begin_card_selection(
            kind="remove",
            cards=selection_cards,
            choice_map=choice_map,
            can_skip=False,
            on_resolve=_resolve_grimoire_selection,
        )
        return 0.0

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
        monster_ids = pick_encounter(room_type, gs.act, gs.rngs.encounter)
        monsters = []
        encounter_floor = gs.current_node.floor if gs.current_node is not None else gs.floor
        encounter_node = gs.current_node.index if gs.current_node is not None else -1
        for monster_idx, monster_id in enumerate(monster_ids):
            monster_rng = gs.rngs.derive_local(
                "monster",
                gs.act,
                encounter_floor,
                encounter_node,
                room_type.name,
                monster_idx,
                monster_id,
                name=f"monster:{monster_id}:{monster_idx}",
            )
            monster = create_monster(monster_id, rng=monster_rng)
            monsters.append(monster)
            from sts_env.monster_ai import ensure_unique_monster_hp
            ensure_unique_monster_hp(monster, monsters[:-1], gs.rngs.niche)

        p = Player(gs.player.name, gs.player.hp, gs.player.max_hp)
        p.rng = gs.rngs.shuffle
        p.player_rngs = gs.player.player_rngs
        p.gold = gs.player.gold
        p.relics = list(gs.player.relics)
        p.potions = copy.deepcopy(gs.player.potions)
        p.powers = copy.deepcopy(gs.player.powers)
        p.init_deck(gs.get_deck_copy())

        gs.combat = Combat(p, monsters, rngs=gs.rngs)
        gs.combat.start_combat()
        gs.phase = GamePhase.COMBAT
        gs._combat_room_type = room_type
        gs._combat_hp_before = gs.player.hp
        self._combat_hp_before = gs.player.hp
        self._combat_actual_hp_lost = 0
        self._combat_avoidable_hp_lost = 0
        self._combat_turn_damage_samples = []

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

        if combat.card_selection is not None:
            for idx in range(min(A_SELECT_END - A_SELECT_START + 1, len(combat.card_selection.cards))):
                mask[A_SELECT_START + idx] = True
            if combat.card_selection.can_skip:
                mask[A_SKIP] = True
            return

        playable_cards = combat.playable_cards_override
        if playable_cards is None:
            playable_cards = [player_can_play_card(card, p) for card in p.hand[:10]]

        for card_idx, card in enumerate(p.hand[:10]):
            enabled = player_can_play_card(card, p)
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
            # 修复：确保 _pot 是字典类型，兼容字符串情况
            if isinstance(_pot, str):
                _pot = {"id": _pot}
                p.potions[pot_idx] = _pot
            pot_id = _potion_id_from_payload(_pot)
            for target_idx in _potion_target_slots(pot_id, target_count):
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
        pot_id = _potion_id_from_payload(pot)
        hp_ratio = p.hp / max(1, p.max_hp)
        reward = compute_potion_reward(gs, getattr(gs, "_combat_room_type", RoomType.MONSTER), hp_ratio)

        from sts_env.powers import create_power as _cp

        if pot_id == "BloodPotion":
            p.heal(max(8, p.max_hp // 5))
        elif pot_id in {"FruitJuice", "FairyInABottle", "BoneBrew"}:
            p.heal(max(8, p.max_hp // 5))
        elif pot_id == "BlockPotion":
            p.gain_block(12)
        elif pot_id == "DexterityPotion":
            p.add_power(_cp("DexterityPower", 2, p))
        elif pot_id == "StrengthPotion":
            p.add_power(_cp("StrengthPower", 2, p))
        elif pot_id == "FirePotion":
            alive = combat.alive_monsters
            if alive:
                t = alive[min(target_idx, len(alive) - 1)]
                t.take_unblockable_damage(20)
        elif pot_id == "PoisonPotion":
            alive = combat.alive_monsters
            if alive:
                t = alive[min(target_idx, len(alive) - 1)]
                t.add_power(_cp("PoisonPower", 6, t))
        elif pot_id == "WeakPotion":
            alive = combat.alive_monsters
            if alive:
                t = alive[min(target_idx, len(alive) - 1)]
                t.add_power(_cp("WeakPower", 3, t))
        elif pot_id == "VulnerablePotion":
            alive = combat.alive_monsters
            if alive:
                t = alive[min(target_idx, len(alive) - 1)]
                t.add_power(_cp("VulnerablePower", 3, t))
        elif pot_id == "EnergyPotion":
            p.energy += 2
        elif pot_id == "RegenPotion":
            p.add_power(_cp("RegenPower", 5, p))
        elif pot_id in {"AttackPotion", "SkillPotion", "PowerPotion", "ColorlessPotion"}:
            p.draw_cards(1)
        elif pot_id == "FearPotion":
            alive = combat.alive_monsters
            if alive:
                t = alive[min(target_idx, len(alive) - 1)]
                t.add_power(_cp("VulnerablePower", 3, t))
        else:
            if combat.alive_monsters:
                t = combat.alive_monsters[min(target_idx, len(combat.alive_monsters) - 1)]
                t.take_unblockable_damage(12)
        combat._check_combat_end()
        return reward

    def _finish_combat(self) -> float:
        gs = self.gs
        combat = gs.combat
        self._sync_from_combat()
        room_type = getattr(gs, "_combat_room_type", RoomType.MONSTER)
        reward = compute_combat_reward(
            gs=gs,
            room_type=room_type,
            won=True,
            hp_before=getattr(gs, "_combat_hp_before", gs.player.hp),
            hp_after=gs.player.hp,
            turns=max(1, combat.turn_count + 1),
            max_hp=gs.player.max_hp,
            avoidable_hp_loss=self._combat_avoidable_hp_lost,
        )
        self._run_combat_score_total += reward
        self._run_combat_count += 1
        self._run_hp_lost_total += self._combat_actual_hp_lost
        self._run_avoidable_hp_lost_total += self._combat_avoidable_hp_lost
        self._run_turns_total += max(1, combat.turn_count + 1)
        grimoire_removals = 0
        grimoire = gs.player.get_power("ForbiddenGrimoirePower")
        if grimoire is not None:
            grimoire_removals = max(0, int(getattr(grimoire, "amount", 0)))
            gs.player.remove_power("ForbiddenGrimoirePower")

        if grimoire_removals > 0 and len(gs.deck) > 0:
            self._begin_post_combat_grimoire_selection(room_type, grimoire_removals)
        else:
            self._finalize_post_combat_rewards(room_type)
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
        elif combat.card_selection is not None:
            if action == A_SKIP:
                if not combat.skip_card_selection():
                    reward -= 0.05
            elif A_SELECT_START <= action <= A_SELECT_END:
                idx = action - A_SELECT_START
                if not combat.select_card_option(idx):
                    reward -= 0.05
            else:
                reward -= 0.05
        else:
            if A_PLAY_START <= action <= A_PLAY_END:
                rel = action - A_PLAY_START
                card_idx = rel // 5
                target_idx = rel % 5
                played_card = combat.player.hand[card_idx] if 0 <= card_idx < len(combat.player.hand) else None
                
                # 记录出牌前的block和incoming damage
                block_before = combat.player.block
                total_incoming = sum(
                    m.intent.damage * m.intent.hits 
                    for m in combat.monsters 
                    if not m.is_dead and m.intent.intent_type.value <= 2  # attack类型
                )
                
                ok = combat.play_card(card_idx, target_idx)
                if not ok:
                    reward -= 0.05
                elif played_card is not None:
                    combat_deck_ids = [
                        card.card_id
                        for pile in (
                            combat.player.hand,
                            combat.player.draw_pile,
                            combat.player.discard_pile,
                            combat.player.exhaust_pile,
                        )
                        for card in pile
                    ] + [played_card.card_id]
                    reward += compute_combat_play_reward(
                        gs,
                        combat_deck_ids,
                        played_card.card_id,
                        exhausts_on_play=("Exhaust" in played_card.keywords),
                    )
                    
                    # 新增：防御奖励 - 当怪物有攻击意图时打出防御牌
                    if total_incoming > 0 and played_card.block > 0:
                        block_gained = combat.player.block - block_before
                        if block_gained > 0:
                            # 基础格挡奖励
                            reward += block_gained * RewardConfig.BLOCK_DAMAGE
                            
                            # 额外奖励：防御牌正好覆盖incoming damage
                            coverage_ratio = combat.player.block / total_incoming
                            if coverage_ratio >= 1.0:
                                reward += 5.0  # 完全覆盖攻击的额外奖励
                            elif coverage_ratio >= 0.5:
                                reward += 2.0  # 部分覆盖奖励
                            
                            # 低HP时防御奖励加倍
                            hp_ratio = combat.player.hp / max(1, combat.player.max_hp)
                            if hp_ratio < 0.3:
                                reward *= 2.0
                            elif hp_ratio < 0.5:
                                reward *= 1.5
            elif action == A_END_TURN:
                player_hp_before_enemy_phase = combat.player.hp
                search_context = build_turn_search_context(combat, combat.player, actual_hp_loss=0)
                combat.end_player_turn()
                actual_hp_loss = max(0, player_hp_before_enemy_phase - combat.player.hp)
                if actual_hp_loss > 0:
                    search_context.actual_hp_loss = actual_hp_loss
                    search_result = analyze_turn_avoidable_hp_loss(search_context)
                    self._combat_actual_hp_lost += search_result.actual_hp_loss
                    self._combat_avoidable_hp_lost += search_result.avoidable_hp_loss
                    self._combat_turn_damage_samples.append(search_result)
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

    def _mask_card_select(self, mask: np.ndarray):
        gs = self.gs
        for i in range(min(A_SELECT_END - A_SELECT_START + 1, len(gs.selection_cards))):
            mask[A_SELECT_START + i] = True
        if gs.selection_can_skip:
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

    def _step_card_select(self, action: int) -> float:
        gs = self.gs
        if action == A_SKIP and gs.selection_can_skip:
            callback = self._selection_skip_callback
            self._clear_card_selection()
            return float(callback() if callback is not None else 0.0)

        if not (A_SELECT_START <= action <= A_SELECT_END):
            return -0.05

        idx = action - A_SELECT_START
        if idx < 0 or idx >= len(self._selection_choice_map):
            return -0.05

        actual_idx = self._selection_choice_map[idx]
        callback = self._selection_resolve_callback
        self._clear_card_selection()
        if callback is None:
            return -0.05
        return float(callback(actual_idx))

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
            candidates = [idx for idx, card in enumerate(gs.deck) if not card.upgraded]
            if candidates:
                choice_map, selection_cards = self._truncate_selection_candidates(candidates)

                def _resolve_upgrade_selection(selected_idx: int) -> float:
                    upgraded_card = gs.deck[selected_idx]
                    upgraded_card_id = upgraded_card.card_id
                    upgraded_card.apply_upgrade()
                    return compute_rest_reward(
                        gs,
                        action="upgrade",
                        hp_before=hp_before,
                        upgraded_card_id=upgraded_card_id,
                    ) + self._advance_after_room()

                self._begin_card_selection(
                    kind="upgrade",
                    cards=selection_cards,
                    choice_map=choice_map,
                    can_skip=False,
                    on_resolve=_resolve_upgrade_selection,
                )
                return reward
            else:
                reward -= 0.05
        elif action == A_DIG and "Shovel" in gs.player.relics:
            self._gain_random_relic("rest")
            reward += compute_rest_reward(gs, action="dig", hp_before=hp_before)
        elif action == A_COOK and "MeatCleaver" in gs.player.relics and len(gs.deck) >= 2:
            def _start_cook_selection(remaining: int, removed_ids: list[str]) -> float:
                candidates = list(range(len(gs.deck)))
                if remaining <= 0 or not candidates:
                    gs.player.max_hp += 9
                    gs.player.hp = min(gs.player.max_hp, gs.player.hp + 9)
                    return compute_rest_reward(gs, action="cook", hp_gained=9, hp_before=hp_before, removed_card_ids=removed_ids) + self._advance_after_room()

                choice_map, selection_cards = self._truncate_selection_candidates(candidates)

                def _resolve_cook_selection(selected_idx: int) -> float:
                    removed_card_id = gs.deck[selected_idx].card_id
                    removed_ids.append(removed_card_id)
                    gs.remove_card_from_deck(selected_idx)
                    return _start_cook_selection(remaining - 1, removed_ids)

                self._begin_card_selection(
                    kind="remove",
                    cards=selection_cards,
                    choice_map=choice_map,
                    can_skip=False,
                    on_resolve=_resolve_cook_selection,
                )
                return 0.0

            return _start_cook_selection(2, [])
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
        inv = generate_shop_inventory(gs.character, gs.player.player_rngs.shops)
        gs.shop_cards = list(inv.get("cards", []))
        gs.shop_relics = [
            relic for relic in _shop_items_payload(inv.get("relics", []))
            if relic and str(relic.get("id", "")) not in gs.player.relics
        ][:3]
        gs.shop_potions = _shop_items_payload(inv.get("potions", []))[:3]

    def _mask_shop(self, mask: np.ndarray):
        gs = self.gs
        for i, card in enumerate(gs.shop_cards[:3]):
            rarity = getattr(card, "rarity", None) or getattr(card, "pool", None) or "Common"
            price = _SHOP_CARD_PRICE.get(rarity, 75)
            if gs.player.gold >= price:
                mask[A_SHOP_CARD_START + i] = True

        for i, potion in enumerate(_shop_items_payload(gs.shop_potions)[:3]):
            price = _POTION_PRICE.get(str(potion.get("rarity", "Common")), 75)
            if gs.player.gold >= price and len(gs.player.potions) < 3:
                mask[A_SHOP_POTION_START + i] = True

        for i, relic in enumerate(_shop_items_payload(gs.shop_relics)[:3]):
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

        normalized_shop_potions = _shop_items_payload(gs.shop_potions)
        if A_SHOP_POTION_START <= action <= A_SHOP_POTION_END and normalized_shop_potions:
            idx = min(action - A_SHOP_POTION_START, len(normalized_shop_potions) - 1)
            potion = normalized_shop_potions[idx]
            price = _POTION_PRICE.get(str(potion.get("rarity", "Common")), 75)
            if gs.player.gold >= price and len(gs.player.potions) < 3:
                reward += compute_potion_reward(
                    gs,
                    room_type=RoomType.SHOP,
                    hp_ratio=gs.player.hp / max(1, gs.player.max_hp),
                )
                gs.player.gold -= price
                gs.player.potions.append(copy.deepcopy(potion))
                gs.shop_potions.pop(idx)
            else:
                reward -= 0.05
            return reward

        normalized_shop_relics = _shop_items_payload(gs.shop_relics)
        if A_SHOP_RELIC_START <= action <= A_SHOP_RELIC_END and normalized_shop_relics:
            idx = min(action - A_SHOP_RELIC_START, len(normalized_shop_relics) - 1)
            relic = normalized_shop_relics[idx]
            if "id" not in relic:
                return -0.05
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
                candidates = list(range(len(gs.deck)))
                choice_map, selection_cards = self._truncate_selection_candidates(candidates)

                def _resolve_shop_remove_selection(selected_idx: int) -> float:
                    removed_card_id = gs.deck[selected_idx].card_id
                    remove_reward = compute_remove_at_shop_reward(gs, removed_card_id)
                    gs.remove_card_from_deck(selected_idx)
                    gs.player.gold -= gs.shop_remove_cost
                    gs.shop_removes_done += 1
                    gs.shop_remove_cost += 50 if gs.ascension >= 6 else 25
                    return remove_reward

                self._begin_card_selection(
                    kind="remove",
                    cards=selection_cards,
                    choice_map=choice_map,
                    can_skip=False,
                    on_resolve=_resolve_shop_remove_selection,
                )
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
        chosen_event = choose_event(gs.rngs.events)
        gs.current_event_id = str(chosen_event.get("id", "GenericEvent"))
        event_rng = gs.rngs.derive_local(
            "event",
            gs.current_event_id,
            gs.act,
            gs.floor,
            name=f"event:{gs.current_event_id}",
        )
        ev = materialize_event(chosen_event, event_rng)
        gs.event_options = _event_options_payload(ev.get("options", []))[:4]
        if not gs.event_options:
            gs.event_options = [{"label": "离开", "effect": {}}]

    def _mask_event(self, mask: np.ndarray):
        gs = self.gs
        for i, option in enumerate(_event_options_payload(gs.event_options)[:4]):
            if not option.get("disabled", False):
                mask[A_EVENT_START + i] = True

    def _step_event(self, action: int) -> float:
        gs = self.gs
        idx = action - A_EVENT_START
        normalized_options = _event_options_payload(gs.event_options)
        if not normalized_options:
            return -0.05
        if idx < 0 or idx >= len(normalized_options):
            idx = 0
        option = normalized_options[idx]
        if option.get("disabled", False):
            return -0.05
        effect = option.get("effect", {})
        if not isinstance(effect, dict):
            effect = {}
        hp_before = gs.player.hp
        apply_event_effect(gs, effect)

        selector_tags = [str(tag) for tag in effect.get("selector_tags", [])]
        remove_count = int(effect.get("remove", 0) or 0)
        upgrade_count = int(effect.get("upgrade", 0) or 0)
        transform_count = int(effect.get("transform", 0) or 0)
        enchant_count = int(effect.get("enchant", 0) or 0)
        enchantment_id = str(effect.get("enchantment_id", "") or "")
        enchant_amount = int(effect.get("enchant_amount", 1) or 1)
        upgrade_random_count = int(effect.get("upgrade_random", 0) or 0)

        if remove_count > 0:
            def _start_remove_selection(remaining: int, reward_accum: float) -> float:
                candidates = self._eligible_selection_indices(kind="remove", selector_tags=selector_tags)
                if remaining <= 0 or not candidates:
                    return self._finish_event_with_effect(effect, hp_before=hp_before, remove_reward=reward_accum)

                choice_map, selection_cards = self._truncate_selection_candidates(candidates)

                def _resolve_event_remove_selection(selected_idx: int) -> float:
                    removed_card_id = gs.deck[selected_idx].card_id
                    remove_reward = compute_remove_card_reward(gs, removed_card_id)
                    gs.remove_card_from_deck(selected_idx)
                    return _start_remove_selection(remaining - 1, reward_accum + remove_reward)

                self._begin_card_selection(
                    kind="remove",
                    cards=selection_cards,
                    choice_map=choice_map,
                    can_skip=False,
                    on_resolve=_resolve_event_remove_selection,
                )
                return 0.0

            return _start_remove_selection(remove_count, 0.0)

        if upgrade_count > 0:
            def _start_upgrade_selection(remaining: int) -> float:
                candidates = self._eligible_selection_indices(kind="upgrade", selector_tags=selector_tags)
                if remaining <= 0 or not candidates:
                    return self._finish_event_with_effect(effect, hp_before=hp_before)

                choice_map, selection_cards = self._truncate_selection_candidates(candidates)

                def _resolve_event_upgrade_selection(selected_idx: int) -> float:
                    gs.deck[selected_idx].apply_upgrade()
                    return _start_upgrade_selection(remaining - 1)

                self._begin_card_selection(
                    kind="upgrade",
                    cards=selection_cards,
                    choice_map=choice_map,
                    can_skip=False,
                    on_resolve=_resolve_event_upgrade_selection,
                )
                return 0.0

            return _start_upgrade_selection(upgrade_count)

        if transform_count > 0:
            def _start_transform_selection(remaining: int) -> float:
                candidates = self._eligible_selection_indices(kind="remove", selector_tags=selector_tags)
                if remaining <= 0 or not candidates:
                    return self._finish_event_with_effect(effect, hp_before=hp_before)

                choice_map, selection_cards = self._truncate_selection_candidates(candidates)

                def _resolve_event_transform_selection(selected_idx: int) -> float:
                    selected_card = gs.deck[selected_idx]
                    transformed_card = generate_transformed_card(
                        gs.character,
                        gs.player.player_rngs.transformations,
                        exclude_card_id=selected_card.card_id,
                        rarity=selected_card.rarity,
                        upgraded=selected_card.upgraded,
                        target_card_id=effect.get("transform_target"),
                    )
                    gs.deck = gs.deck[:selected_idx] + [transformed_card] + gs.deck[selected_idx + 1:]
                    return _start_transform_selection(remaining - 1)

                self._begin_card_selection(
                    kind="transform",
                    cards=selection_cards,
                    choice_map=choice_map,
                    can_skip=False,
                    on_resolve=_resolve_event_transform_selection,
                )
                return 0.0

            return _start_transform_selection(transform_count)

        if enchant_count > 0 and enchantment_id:
            def _start_enchant_selection(remaining: int) -> float:
                candidates = self._eligible_selection_indices(
                    kind="enchant",
                    selector_tags=selector_tags,
                    enchantment_id=enchantment_id,
                )
                if remaining <= 0 or not candidates:
                    return self._finish_event_with_effect(effect, hp_before=hp_before)

                choice_map, selection_cards = self._truncate_selection_candidates(candidates)

                def _resolve_event_enchant_selection(selected_idx: int) -> float:
                    self._apply_enchantment_to_card(gs.deck[selected_idx], enchantment_id, enchant_amount)
                    return _start_enchant_selection(remaining - 1)

                self._begin_card_selection(
                    kind="enchant",
                    cards=selection_cards,
                    choice_map=choice_map,
                    can_skip=False,
                    on_resolve=_resolve_event_enchant_selection,
                )
                return 0.0

            return _start_enchant_selection(enchant_count)

        if upgrade_random_count > 0:
            candidates = [idx for idx, card in enumerate(gs.deck) if not card.upgraded]
            gs.rngs.niche.shuffle(candidates)
            for selected_idx in candidates[:upgrade_random_count]:
                gs.deck[selected_idx].apply_upgrade()

        return self._finish_event_with_effect(effect, hp_before=hp_before)

    # ------------------------------------------------------------------
    # TREASURE / BOSS RELIC
    # ------------------------------------------------------------------

    def _gain_random_relic(self, source: str = "reward") -> str | None:
        gs = self.gs
        relics = [r for r in _load_relic_db() if r.get("id") not in gs.player.relics]
        if source == "elite":
            pool = [r for r in relics if r.get("rarity") in {"Uncommon", "Rare", "Ancient"}]
            rng = gs.player.player_rngs.rewards
        elif source == "treasure":
            pool = [r for r in relics if r.get("rarity") in {"Common", "Uncommon", "Rare"}]
            rng = gs.rngs.treasure
        elif source == "boss":
            pool = [r for r in relics if r.get("rarity") in {"Rare", "Ancient"}]
            rng = gs.rngs.treasure
        else:
            pool = [r for r in relics if r.get("rarity") in {"Common", "Uncommon", "Rare"}]
            rng = gs.player.player_rngs.rewards
        if not pool:
            return None
        relic = rng.choice(pool)
        gs.player.relics.append(relic["id"])
        return relic["id"]

    def _init_treasure(self):
        self.gs.boss_relic_choices = []

    def _step_treasure(self, action: int) -> float:
        self._gain_random_relic("treasure")
        return self._advance_after_room()

    def _generate_boss_relic_choices(self) -> list[str]:
        gs = self.gs
        pool = [r for r in _load_relic_db() if r.get("id") not in gs.player.relics and r.get("rarity") in {"Rare", "Ancient"}]
        gs.rngs.treasure.shuffle(pool)
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
