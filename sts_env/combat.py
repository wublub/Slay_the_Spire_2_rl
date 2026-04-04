"""战斗模拟器核心：Creature、Combat 回合流程。"""
from __future__ import annotations
import copy
import json
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

from sts_env.powers import (
    Power, PowerType, create_power,
    StrengthPower, VulnerablePower, WeakPower, FrailPower,
    PoisonPower, ArtifactPower, BarricadePower, MetallicizePower,
    IntangiblePower, DemonFormPower, RitualPower, PlatedArmorPower,
    RegenPower, DrawCardPower, EnergizedPower, FeelNoPainPower,
    CorruptionPower, NoxiousFumesPower, ThornsPower,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# 卡牌
# ---------------------------------------------------------------------------

class CardType(Enum):
    ATTACK = "Attack"
    SKILL = "Skill"
    POWER = "Power"
    STATUS = "Status"
    CURSE = "Curse"

class TargetType(Enum):
    SELF = "Self"
    ANY_ENEMY = "AnyEnemy"
    ALL_ENEMIES = "AllEnemies"
    NONE = "None"


@dataclass
class Card:
    card_id: str
    cost: int = 1
    card_type: CardType = CardType.ATTACK
    target: TargetType = TargetType.ANY_ENEMY
    damage: int = 0
    block: int = 0
    draw: int = 0
    magic: int = 0
    powers: dict[str, int] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    upgraded: bool = False
    upgrade_data: dict = field(default_factory=dict)
    vars: dict[str, int] = field(default_factory=dict)
    pool: str = ""
    replay_count: int = 0
    single_turn_retain: bool = False
    single_turn_sly: bool = False
    single_turn_free: bool = False
    affliction_id: str = ""
    affliction_amount: int = 0

    def can_play(self, energy: int) -> bool:
        if self.card_type == CardType.STATUS:
            return False
        if self.card_type == CardType.CURSE:
            return False
        if self.single_turn_free:
            return True
        if self.cost < 0:  # X-cost 卡
            return energy >= 0
        return energy >= self.cost

    def effective_cost(self, energy: int) -> int:
        if self.single_turn_free:
            return 0
        if self.cost < 0:
            return energy
        return self.cost

    def apply_upgrade(self):
        if self.upgraded:
            return

        self.upgraded = True
        upg = self.upgrade_data
        self.damage += int(upg.get("damage", 0))
        self.block += int(upg.get("block", 0))
        self.cost += int(upg.get("cost", 0))
        self.draw += int(upg.get("draw", 0))
        self.magic += int(upg.get("magic", 0))

        for power_id, amount in upg.get("powers", {}).items():
            self.powers[power_id] = self.powers.get(power_id, 0) + int(amount)

        for keyword in upg.get("keywords", []):
            if keyword not in self.keywords:
                self.keywords.append(keyword)


# ---------------------------------------------------------------------------
# 卡牌数据库
# ---------------------------------------------------------------------------

_CARD_DB: dict[str, dict] = {}


def _load_card_db():
    global _CARD_DB
    if _CARD_DB:
        return
    cards_path = DATA_DIR / "cards.json"
    if cards_path.exists():
        for c in json.loads(cards_path.read_text("utf-8")):
            _CARD_DB[c["id"]] = c


def make_card(card_id: str, upgraded: bool = False) -> Card:
    _load_card_db()
    data = _CARD_DB.get(card_id, {})
    tt_map = {
        "AnyEnemy": TargetType.ANY_ENEMY,
        "AllEnemies": TargetType.ALL_ENEMIES,
        "Self": TargetType.SELF,
        "None": TargetType.NONE,
    }
    ct_map = {
        "Attack": CardType.ATTACK,
        "Skill": CardType.SKILL,
        "Power": CardType.POWER,
        "Status": CardType.STATUS,
        "Curse": CardType.CURSE,
    }
    card = Card(
        card_id=card_id,
        cost=data.get("cost", 1),
        card_type=ct_map.get(data.get("type", "Attack"), CardType.ATTACK),
        target=tt_map.get(data.get("target", "AnyEnemy"), TargetType.ANY_ENEMY),
        damage=data.get("damage", 0),
        block=data.get("block", 0),
        draw=data.get("draw", 0),
        magic=data.get("magic", 0),
        powers=dict(data.get("powers", {})),
        keywords=list(data.get("keywords", [])),
        tags=list(data.get("tags", [])),
        upgrade_data=dict(data.get("upgrade", {})),
        vars={str(k): int(v) for k, v in dict(data.get("vars", {})).items()},
        pool=data.get("pool", ""),
    )
    if upgraded:
        card.apply_upgrade()
    return card


# ---------------------------------------------------------------------------
# Creature（生物：玩家和怪物的基类）
# ---------------------------------------------------------------------------

class Creature:
    def __init__(self, name: str, hp: int, max_hp: int | None = None):
        self.name = name
        self.max_hp = max_hp or hp
        self.hp = hp
        self.block = 0
        self.powers: list[Power] = []
        self.is_dead = False

    def get_power(self, power_id: str) -> Power | None:
        for p in self.powers:
            if p.power_id == power_id:
                return p
        return None

    def add_power(self, power: Power):
        # Artifact 挡 debuff
        if power.power_type == PowerType.DEBUFF:
            art = self.get_power("ArtifactPower")
            if art and isinstance(art, ArtifactPower) and art.try_block_debuff():
                if art.amount <= 0:
                    self.powers.remove(art)
                return
        existing = self.get_power(power.power_id)
        if existing:
            existing.stack(power.amount)
        else:
            power.owner = self
            self.powers.append(power)

    def remove_power(self, power_id: str):
        self.powers = [p for p in self.powers if p.power_id != power_id]

    def take_damage(self, damage: int, attacker: Creature | None = None) -> int:
        """处理伤害，返回实际HP损失。"""
        if damage <= 0:
            return 0
        # Intangible
        intang = self.get_power("IntangiblePower")
        if intang and isinstance(intang, IntangiblePower):
            damage = intang.modify_damage_received(damage)
        # Vulnerable
        vuln = self.get_power("VulnerablePower")
        if vuln and isinstance(vuln, VulnerablePower):
            damage = vuln.modify_damage_received(damage)
        # Block 吸收
        blocked = min(self.block, damage)
        self.block -= blocked
        hp_loss = damage - blocked
        self.hp -= hp_loss
        # Thorns 反伤
        if attacker and hp_loss > 0:
            thorns = self.get_power("ThornsPower")
            if thorns and isinstance(thorns, ThornsPower) and thorns.amount > 0:
                attacker.hp -= thorns.amount
                if attacker.hp <= 0:
                    attacker.is_dead = True
        # Plated Armor 受击减层
        pa = self.get_power("PlatedArmorPower")
        if pa and isinstance(pa, PlatedArmorPower) and hp_loss > 0:
            pa.on_attacked()
            if pa.amount <= 0:
                self.powers.remove(pa)
        if self.hp <= 0:
            self.hp = 0
            self.is_dead = True
        return hp_loss

    def take_unblockable_damage(self, damage: int) -> int:
        if damage <= 0:
            return 0
        self.hp -= damage
        if self.hp <= 0:
            self.hp = 0
            self.is_dead = True
        return damage

    def gain_block(self, amount: int):
        # Frail
        frail = self.get_power("FrailPower")
        if frail and isinstance(frail, FrailPower):
            amount = frail.modify_block_gained(amount)
        # Dexterity
        from sts_env.powers import DexterityPower
        dex = self.get_power("DexterityPower")
        if dex and isinstance(dex, DexterityPower):
            amount = dex.modify_block(amount)
        self.block += max(0, amount)

    def heal(self, amount: int):
        self.hp = min(self.hp + amount, self.max_hp)

    def start_turn(self):
        """回合开始：清除 block（除非 Barricade），触发 Power 效果。"""
        if not self.get_power("BarricadePower"):
            self.block = 0
        # Regen
        regen = self.get_power("RegenPower")
        if regen and isinstance(regen, RegenPower):
            heal_amt = regen.on_turn_start_heal()
            self.heal(heal_amt)
            if regen.amount <= 0:
                self.powers.remove(regen)
        # Poison
        poison = self.get_power("PoisonPower")
        if poison and isinstance(poison, PoisonPower):
            dmg = poison.on_turn_start()
            self.take_unblockable_damage(dmg)
            if poison.amount <= 0:
                self.powers.remove(poison)
        # DemonForm
        demon = self.get_power("DemonFormPower")
        if demon and isinstance(demon, DemonFormPower):
            demon.on_turn_start_buff(self)

    def end_turn(self):
        """回合结束：Metallicize/PlatedArmor 加 block，Power duration tick。"""
        met = self.get_power("MetallicizePower")
        if met and isinstance(met, MetallicizePower):
            self.block += met.on_turn_end_block()
        pa = self.get_power("PlatedArmorPower")
        if pa and isinstance(pa, PlatedArmorPower):
            self.block += pa.on_turn_end_block()
        # Ritual
        rit = self.get_power("RitualPower")
        if rit and isinstance(rit, RitualPower):
            rit.on_turn_end(self)
        # Tick durations
        to_remove = []
        for p in self.powers:
            if p.power_id in ("VulnerablePower", "WeakPower", "FrailPower"):
                p.amount -= 1
                if p.amount <= 0:
                    to_remove.append(p)
        for p in to_remove:
            self.powers.remove(p)


# ---------------------------------------------------------------------------
# 怪物意图
# ---------------------------------------------------------------------------

class IntentType(Enum):
    ATTACK = auto()
    ATTACK_BUFF = auto()
    ATTACK_DEBUFF = auto()
    BUFF = auto()
    DEBUFF = auto()
    DEFEND = auto()
    HEAL = auto()
    UNKNOWN = auto()


@dataclass
class Intent:
    intent_type: IntentType = IntentType.UNKNOWN
    damage: int = 0
    hits: int = 1
    block: int = 0


# ---------------------------------------------------------------------------
# Monster
# ---------------------------------------------------------------------------

class Monster(Creature):
    def __init__(self, name: str, hp: int, max_hp: int | None = None):
        super().__init__(name, hp, max_hp)
        self.intent = Intent()
        self.move_history: list[str] = []
        self.turn_count = 0

    def roll_move(self, combat: Combat):
        """由子类或 monster_ai 覆盖。"""
        pass

    def perform_move(self, combat: Combat):
        """执行当前意图。"""
        player = combat.player
        if self.is_dead:
            return
        intent = self.intent
        if intent.intent_type in (IntentType.ATTACK, IntentType.ATTACK_BUFF, IntentType.ATTACK_DEBUFF):
            dmg = self._calc_attack_damage(intent.damage)
            for _ in range(intent.hits):
                if player.is_dead:
                    break
                player.take_damage(dmg, attacker=self)
        if intent.block > 0:
            self.gain_block(intent.block)
        self.turn_count += 1

    def _calc_attack_damage(self, base: int) -> int:
        dmg = base
        str_p = self.get_power("StrengthPower")
        if str_p and isinstance(str_p, StrengthPower):
            dmg = str_p.modify_damage(dmg)
        weak_p = self.get_power("WeakPower")
        if weak_p and isinstance(weak_p, WeakPower):
            dmg = weak_p.modify_damage_dealt(dmg)
        return max(0, dmg)


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class Player(Creature):
    def __init__(self, name: str, hp: int, max_hp: int | None = None,
                 energy_per_turn: int = 3, draw_per_turn: int = 5):
        super().__init__(name, hp, max_hp)
        self.energy = 0
        self.energy_per_turn = energy_per_turn
        self.draw_per_turn = draw_per_turn
        self.draw_pile: list[Card] = []
        self.hand: list[Card] = []
        self.discard_pile: list[Card] = []
        self.exhaust_pile: list[Card] = []
        self.gold: int = 99
        self.relics: list[str] = []
        self.potions: list[dict] = []
        self.orb_slots: int = 0
        self.orbs: list[str] = []
        self.is_osty_missing: bool = False

    def init_deck(self, cards: list[Card]):
        self.draw_pile = list(cards)
        random.shuffle(self.draw_pile)

    def draw_cards(self, n: int):
        for _ in range(n):
            if len(self.hand) >= 10:
                break
            if not self.draw_pile:
                if not self.discard_pile:
                    break
                self.draw_pile = self.discard_pile[:]
                self.discard_pile.clear()
                random.shuffle(self.draw_pile)
            if self.draw_pile:
                self.hand.append(self.draw_pile.pop())

    def discard_hand(self):
        for card in self.hand:
            if "Retain" in card.keywords or card.single_turn_retain:
                continue
            if "Ethereal" in card.keywords:
                self.exhaust_pile.append(card)
            else:
                self.discard_pile.append(card)
        self.hand = [c for c in self.hand if "Retain" in c.keywords or c.single_turn_retain]

    def start_turn(self):
        super().start_turn()
        self.energy = self.energy_per_turn
        for pile in (self.hand, self.draw_pile, self.discard_pile, self.exhaust_pile):
            for card in pile:
                card.single_turn_retain = False
                card.single_turn_sly = False
                card.single_turn_free = False
        nightmare_powers = [p for p in self.powers if p.power_id == "NightmarePower"]
        for nightmare in nightmare_powers:
            if hasattr(nightmare, "before_hand_draw"):
                nightmare.before_hand_draw(self)
            if nightmare in self.powers:
                self.powers.remove(nightmare)
        # Energized
        enrg = self.get_power("EnergizedPower")
        if enrg and isinstance(enrg, EnergizedPower):
            self.energy += enrg.amount
            self.remove_power("EnergizedPower")
        draw_count = self.draw_per_turn
        draw_p = self.get_power("DrawCardPower")
        if draw_p and isinstance(draw_p, DrawCardPower):
            draw_count += draw_p.amount
        self.draw_cards(draw_count)


# ---------------------------------------------------------------------------
# Combat 战斗管理器
# ---------------------------------------------------------------------------

class CombatPhase(Enum):
    PLAYER_TURN = auto()
    ENEMY_TURN = auto()
    COMBAT_END = auto()


class CombatResult(Enum):
    IN_PROGRESS = auto()
    WIN = auto()
    LOSE = auto()


SelectionFilter = Callable[[Card], bool]
SelectionResolver = Callable[[list[int]], None]


@dataclass
class HandSelectionState:
    mode: str
    min_select: int = 1
    max_select: int = 1
    manual_confirm: bool = False
    source_card_id: str | None = None
    selectable_cards: list[bool] = field(default_factory=list)
    selected_cards: list[bool] = field(default_factory=list)
    confirm_enabled: bool = False
    selected_count: int = 0
    selectable_override: list[bool] | None = field(default=None, repr=False)
    filter_fn: SelectionFilter | None = field(default=None, repr=False)
    on_resolve: SelectionResolver | None = field(default=None, repr=False)


class Combat:
    def __init__(self, player: Player, monsters: list[Monster]):
        self.player = player
        self.monsters = monsters
        self.phase = CombatPhase.PLAYER_TURN
        self.result = CombatResult.IN_PROGRESS
        self.round_number = 0
        self.turn_count = 0
        self.hand_selection: HandSelectionState | None = None
        self.playable_cards_override: list[bool] | None = None
        self.end_turn_enabled_override: bool | None = None

    def start_combat(self):
        random.shuffle(self.player.draw_pile)
        self.round_number = 1
        # 怪物 roll 第一个意图
        for m in self.monsters:
            m.roll_move(self)
        self.player.start_turn()

    def begin_hand_selection(
        self,
        *,
        mode: str,
        min_select: int = 1,
        max_select: int = 1,
        manual_confirm: bool = False,
        filter_fn: SelectionFilter | None = None,
        on_resolve: SelectionResolver | None = None,
        source_card_id: str | None = None,
        preset_selected_cards: list[bool] | None = None,
        preset_selectable_cards: list[bool] | None = None,
    ) -> bool:
        selected_cards = list(preset_selected_cards or [])
        if len(selected_cards) < len(self.player.hand):
            selected_cards.extend([False] * (len(self.player.hand) - len(selected_cards)))
        else:
            selected_cards = selected_cards[:len(self.player.hand)]

        self.hand_selection = HandSelectionState(
            mode=mode,
            min_select=max(0, int(min_select)),
            max_select=max(0, int(max_select)),
            manual_confirm=bool(manual_confirm),
            source_card_id=source_card_id,
            selected_cards=selected_cards,
            selectable_override=list(preset_selectable_cards) if preset_selectable_cards is not None else None,
            filter_fn=filter_fn,
            on_resolve=on_resolve,
        )
        self._refresh_hand_selection()
        if self.hand_selection is None:
            return False
        return any(self.hand_selection.selectable_cards) or self.hand_selection.confirm_enabled

    def _refresh_hand_selection(self):
        state = self.hand_selection
        if state is None:
            return

        if len(state.selected_cards) < len(self.player.hand):
            state.selected_cards.extend([False] * (len(self.player.hand) - len(state.selected_cards)))
        else:
            state.selected_cards = state.selected_cards[:len(self.player.hand)]

        state.selected_count = sum(1 for flag in state.selected_cards if flag)
        max_reached = state.max_select > 0 and state.selected_count >= state.max_select
        selectable_cards: list[bool] = []
        selectable_override = state.selectable_override

        for idx, card in enumerate(self.player.hand):
            selectable = not state.selected_cards[idx] and not max_reached
            if selectable and selectable_override is not None:
                selectable = idx < len(selectable_override) and bool(selectable_override[idx])
            elif selectable and state.filter_fn is not None:
                try:
                    selectable = bool(state.filter_fn(card))
                except Exception:
                    selectable = False
            selectable_cards.append(selectable)

        state.selectable_cards = selectable_cards
        can_confirm = state.selected_count >= state.min_select
        allow_partial_confirm = state.manual_confirm or state.min_select == 0 or state.min_select != state.max_select
        state.confirm_enabled = can_confirm and (allow_partial_confirm or not any(selectable_cards))

        if not any(selectable_cards) and not state.confirm_enabled:
            self.hand_selection = None

    def select_hand_card(self, hand_idx: int) -> bool:
        state = self.hand_selection
        if state is None:
            return False
        if hand_idx < 0 or hand_idx >= len(self.player.hand):
            return False
        if hand_idx >= len(state.selectable_cards) or not state.selectable_cards[hand_idx]:
            return False

        state.selected_cards[hand_idx] = True
        self._refresh_hand_selection()
        state = self.hand_selection
        if state is None:
            return False

        exact_count = state.max_select > 0 and state.min_select == state.max_select
        if exact_count and not state.manual_confirm and state.selected_count >= state.max_select:
            return self.confirm_hand_selection()
        return True

    def confirm_hand_selection(self) -> bool:
        state = self.hand_selection
        if state is None or not state.confirm_enabled:
            return False

        selected_indices = [idx for idx, flag in enumerate(state.selected_cards) if flag]
        on_resolve = state.on_resolve
        self.hand_selection = None
        if on_resolve is not None:
            on_resolve(selected_indices)
        self._check_combat_end()
        return True

    def discard_cards_from_hand(self, hand_indices: list[int]):
        sly_cards: list[Card] = []
        for idx in sorted(set(hand_indices), reverse=True):
            if 0 <= idx < len(self.player.hand):
                card = self.player.hand.pop(idx)
                self.player.discard_pile.append(card)
                if "Sly" in card.keywords or card.single_turn_sly:
                    sly_cards.append(card)
        for card in sly_cards:
            if card in self.player.discard_pile:
                self.player.discard_pile.remove(card)
            card.single_turn_sly = False
            self.auto_play_card(card)

    def exhaust_cards_from_hand(self, hand_indices: list[int]):
        fnp = self.player.get_power("FeelNoPainPower")
        for idx in sorted(set(hand_indices), reverse=True):
            if 0 <= idx < len(self.player.hand):
                self.player.exhaust_pile.append(self.player.hand.pop(idx))
                if fnp and isinstance(fnp, FeelNoPainPower):
                    self.player.gain_block(fnp.amount)

    def upgrade_cards_in_hand(self, hand_indices: list[int]):
        for idx in hand_indices:
            if 0 <= idx < len(self.player.hand):
                self.player.hand[idx].apply_upgrade()

    def _execute_card_repeated(self, card: Card, target_idx: int = 0):
        target = self._resolve_target(card, target_idx)
        total_plays = max(1, 1 + max(0, int(getattr(card, "replay_count", 0))))
        for play_idx in range(total_plays):
            current_card = card if play_idx == 0 else copy.deepcopy(card)
            if current_card.target == TargetType.ANY_ENEMY and (target is None or target.is_dead):
                target = self._resolve_target(current_card, target_idx)
            self._execute_card(current_card, target)
            self._check_combat_end()
            if self.result != CombatResult.IN_PROGRESS or self.hand_selection is not None:
                break

    def _move_card_to_result_pile(self, card: Card):
        if "Exhaust" in card.keywords:
            self.player.exhaust_pile.append(card)
        elif card.card_type != CardType.POWER:
            self.player.discard_pile.append(card)
        corr = self.player.get_power("CorruptionPower")
        if corr and card.card_type == CardType.SKILL and card not in self.player.exhaust_pile:
            if card in self.player.discard_pile:
                self.player.discard_pile.remove(card)
            self.player.exhaust_pile.append(card)
        if card in self.player.exhaust_pile:
            fnp = self.player.get_power("FeelNoPainPower")
            if fnp and isinstance(fnp, FeelNoPainPower):
                self.player.gain_block(fnp.amount)

    def auto_play_card(self, card: Card, target_idx: int = 0) -> bool:
        if self.result != CombatResult.IN_PROGRESS:
            return False
        self._execute_card_repeated(card, target_idx)
        self._move_card_to_result_pile(card)
        self._check_combat_end()
        return True

    def play_card(self, hand_idx: int, target_idx: int = 0) -> bool:
        """玩家打出手牌。返回是否成功。"""
        if self.phase != CombatPhase.PLAYER_TURN:
            return False
        if self.hand_selection is not None:
            return False
        if hand_idx < 0 or hand_idx >= len(self.player.hand):
            return False
        card = self.player.hand[hand_idx]
        if not card.can_play(self.player.energy):
            return False
        # 扣能量
        cost = card.effective_cost(self.player.energy)
        self.player.energy -= cost
        # 从手牌移除
        self.player.hand.pop(hand_idx)
        self.auto_play_card(card, target_idx)
        return True

    def end_player_turn(self):
        if self.phase != CombatPhase.PLAYER_TURN:
            return
        if self.hand_selection is not None:
            return
        self.player.discard_hand()
        self.player.end_turn()
        # NoxiousFumes
        nf = self.player.get_power("NoxiousFumesPower")
        if nf and isinstance(nf, NoxiousFumesPower):
            for m in self.alive_monsters:
                m.add_power(PoisonPower(nf.amount, m))
        self._enemy_turn()

    def _enemy_turn(self):
        self.phase = CombatPhase.ENEMY_TURN
        for m in self.alive_monsters:
            m.start_turn()
            if m.is_dead:
                continue
            m.perform_move(self)
            m.end_turn()
            if self.player.is_dead:
                break
        self._check_combat_end()
        if self.result == CombatResult.IN_PROGRESS:
            # 下一轮
            self.round_number += 1
            for m in self.alive_monsters:
                m.roll_move(self)
            self.phase = CombatPhase.PLAYER_TURN
            self.player.start_turn()
            self.turn_count += 1

    def _resolve_target(self, card: Card, target_idx: int) -> Monster | None:
        alive = self.alive_monsters
        if card.target == TargetType.ANY_ENEMY:
            if 0 <= target_idx < len(alive):
                return alive[target_idx]
            return alive[0] if alive else None
        return None

    def _execute_card(self, card: Card, target: Monster | None):
        """执行卡牌效果（通用逻辑，复杂卡牌由 card_effects 处理）。"""
        from sts_env.card_effects import execute_card_effect
        if execute_card_effect(card, self.player, target, self):
            return
        # 默认通用逻辑
        if card.damage > 0 and target:
            dmg = self._calc_player_damage(card.damage)
            if card.target == TargetType.ALL_ENEMIES:
                for m in self.alive_monsters:
                    m.take_damage(dmg, attacker=self.player)
            else:
                target.take_damage(dmg, attacker=self.player)
        if card.block > 0:
            self.player.gain_block(card.block)
        if card.draw > 0:
            self.player.draw_cards(card.draw)
        # 施加 Power
        for pid, amt in card.powers.items():
            if target and pid in ("VulnerablePower", "WeakPower", "FrailPower", "PoisonPower"):
                target.add_power(create_power(pid, amt, target))
            else:
                self.player.add_power(create_power(pid, amt, self.player))

    def _calc_player_damage(self, base: int) -> int:
        dmg = base
        str_p = self.player.get_power("StrengthPower")
        if str_p and isinstance(str_p, StrengthPower):
            dmg = str_p.modify_damage(dmg)
        weak_p = self.player.get_power("WeakPower")
        if weak_p and isinstance(weak_p, WeakPower):
            dmg = weak_p.modify_damage_dealt(dmg)
        return max(0, dmg)

    def _check_combat_end(self):
        if self.player.is_dead:
            self.result = CombatResult.LOSE
            self.phase = CombatPhase.COMBAT_END
        elif all(m.is_dead for m in self.monsters):
            self.result = CombatResult.WIN
            self.phase = CombatPhase.COMBAT_END

    @property
    def alive_monsters(self) -> list[Monster]:
        return [m for m in self.monsters if not m.is_dead]

    @property
    def is_over(self) -> bool:
        return self.result != CombatResult.IN_PROGRESS
