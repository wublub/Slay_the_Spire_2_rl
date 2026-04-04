"""特殊卡牌效果实现。

对于大部分卡牌，combat.py 的通用逻辑（伤害+格挡+施加Power+抽牌）已经足够。
本模块只处理需要额外逻辑的卡牌。返回 True 表示已完全处理，False 表示回退到通用逻辑。
"""
from __future__ import annotations
import copy
import json
import random
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sts_env.combat import Card, Player, Monster, Combat

from sts_env.archetypes import card_pick_score, card_remove_score
from sts_env.powers import create_power, StrengthPower, PoisonPower, NightmarePower


# 特殊卡牌处理器注册表
_HANDLERS: dict[str, callable] = {}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_RAW_CARDS: list[dict] = []


def _register(card_id: str):
    def decorator(fn):
        _HANDLERS[card_id] = fn
        return fn
    return decorator


def _load_raw_cards() -> list[dict]:
    global _RAW_CARDS
    if not _RAW_CARDS:
        path = DATA_DIR / "cards.json"
        if path.exists():
            _RAW_CARDS = json.loads(path.read_text(encoding="utf-8"))
    return _RAW_CARDS


def execute_card_effect(card: Card, player: Player, target: Monster | None, combat: Combat) -> bool:
    handler = _HANDLERS.get(card.card_id)
    if handler:
        handler(card, player, target, combat)
        return True
    return False


def _player_character(player: Player) -> str:
    return str(getattr(player, "name", ""))


def _all_deck_ids(player: Player) -> list[str]:
    deck_ids: list[str] = []
    for pile in (player.hand, player.draw_pile, player.discard_pile, player.exhaust_pile):
        deck_ids.extend(card.card_id for card in pile)
    return deck_ids


def _card_value_score(player: Player, card: Card, *, immediate: bool = False) -> float:
    deck_ids = _all_deck_ids(player)
    score = card_pick_score(_player_character(player), deck_ids, card.card_id)
    if immediate and card.can_play(player.energy):
        score += 2.5
    if immediate and card.cost >= 0 and card.cost <= player.energy:
        score += 0.5
    if card.upgraded:
        score += 0.5
    score += card.draw * 0.75
    score += getattr(card, "replay_count", 0) * 0.75
    if "Retain" in getattr(card, "keywords", []):
        score += 0.25
    if card.card_type.value in {"Status", "Curse"} or "Unplayable" in getattr(card, "keywords", []):
        score -= 50.0
    return score


def _card_prune_score(player: Player, card: Card) -> float:
    deck_ids = _all_deck_ids(player)
    score = card_remove_score(_player_character(player), deck_ids, card.card_id)
    if card.upgraded:
        score -= 4.0
    if card.card_type.value in {"Status", "Curse"} or "Unplayable" in getattr(card, "keywords", []):
        score += 40.0
    return score


def _pick_best_cards(cards: list[Card], count: int, score_fn) -> list[Card]:
    scored = [(float(score_fn(card)), idx, card) for idx, card in enumerate(cards)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [card for _score, _idx, card in scored[:max(0, count)]]


def _move_cards_to_hand(player: Player, source_pile: list[Card], cards: list[Card]) -> list[Card]:
    moved: list[Card] = []
    for card in cards:
        if len(player.hand) >= 10:
            break
        if card in source_pile:
            source_pile.remove(card)
            player.hand.append(card)
            moved.append(card)
    return moved


def _move_cards_to_draw_top(player: Player, source_pile: list[Card], cards: list[Card]) -> list[Card]:
    moved: list[Card] = []
    for card in reversed(cards):
        if card in source_pile:
            source_pile.remove(card)
            player.draw_pile.append(card)
            moved.append(card)
    return list(reversed(moved))


def _exhaust_cards_in_pile(player: Player, source_pile: list[Card], cards: list[Card]) -> list[Card]:
    exhausted: list[Card] = []
    for card in cards:
        if card in source_pile:
            source_pile.remove(card)
            player.exhaust_pile.append(card)
            exhausted.append(card)
    return exhausted


def _transform_cards_in_pile(source_pile: list[Card], cards: list[Card], new_card_id: str, *, upgraded: bool = False):
    from sts_env.combat import make_card

    for card in cards:
        if card in source_pile:
            idx = source_pile.index(card)
            source_pile[idx] = make_card(new_card_id, upgraded=upgraded)


def _best_cards_from_pile(
    player: Player,
    pile: list[Card],
    *,
    count: int = 1,
    filter_fn=None,
    purpose: str = "recover",
) -> list[Card]:
    candidates = [card for card in pile if filter_fn is None or filter_fn(card)]
    if not candidates or count <= 0:
        return []

    if purpose == "prune":
        return _pick_best_cards(candidates, count, lambda card: _card_prune_score(player, card))
    if purpose == "recover_to_hand":
        return _pick_best_cards(candidates, count, lambda card: _card_value_score(player, card, immediate=True))
    return _pick_best_cards(candidates, count, lambda card: _card_value_score(player, card))


def _eligible_generated_cards(
    *,
    pools: tuple[str, ...],
    filter_fn=None,
    exclude_pools: tuple[str, ...] = (),
) -> list[dict]:
    cards = []
    for entry in _load_raw_cards():
        pool = str(entry.get("pool", ""))
        if pool not in pools or pool in exclude_pools:
            continue
        if entry.get("rarity") in {"Basic", "Token", "Curse"}:
            continue
        if entry.get("type") in {"Status", "Curse"}:
            continue
        if filter_fn is not None and not filter_fn(entry):
            continue
        cards.append(entry)
    return cards


def _generate_choice_cards(
    *,
    pools: tuple[str, ...],
    count: int,
    upgraded: bool = False,
    filter_fn=None,
    exclude_pools: tuple[str, ...] = (),
) -> list[Card]:
    from sts_env.combat import make_card

    eligible = _eligible_generated_cards(pools=pools, filter_fn=filter_fn, exclude_pools=exclude_pools)
    if not eligible:
        return []
    random.shuffle(eligible)
    selected: list[dict] = []
    seen: set[str] = set()
    for entry in eligible:
        card_id = str(entry.get("id", ""))
        if not card_id or card_id in seen:
            continue
        selected.append(entry)
        seen.add(card_id)
        if len(selected) >= count:
            break
    return [make_card(str(entry["id"]), upgraded=upgraded) for entry in selected]


def _choose_generated_card(player: Player, options: list[Card]) -> Card | None:
    if not options:
        return None
    return _pick_best_cards(options, 1, lambda card: _card_value_score(player, card, immediate=True))[0]


def _add_generated_card_to_hand(player: Player, card: Card | None, *, free_this_turn: bool = False) -> bool:
    if card is None or len(player.hand) >= 10:
        return False
    if free_this_turn:
        card.single_turn_free = True
    player.hand.append(card)
    return True


def _summon_osty(player: Player):
    player.is_osty_missing = False


def _queue_hand_discard(
    combat: Combat,
    *,
    mode: str,
    count: int,
    on_resolve=None,
):
    target_count = min(max(int(count), 0), len(combat.player.hand))
    if target_count <= 0:
        if on_resolve is not None:
            on_resolve([])
        return

    def resolve(selected_indices: list[int]):
        combat.discard_cards_from_hand(selected_indices)
        if on_resolve is not None:
            on_resolve(selected_indices)

    combat.begin_hand_selection(
        mode=mode,
        min_select=target_count,
        max_select=target_count,
        manual_confirm=False,
        on_resolve=resolve,
    )


def _queue_hand_exhaust(
    combat: Combat,
    *,
    mode: str,
    count: int = 1,
    filter_fn=None,
    manual_confirm: bool = False,
    on_resolve=None,
):
    available = [idx for idx, card in enumerate(combat.player.hand) if filter_fn is None or filter_fn(card)]
    target_count = min(max(int(count), 0), len(available))
    if target_count <= 0:
        if on_resolve is not None:
            on_resolve([])
        return

    def resolve(selected_indices: list[int]):
        combat.exhaust_cards_from_hand(selected_indices)
        if on_resolve is not None:
            on_resolve(selected_indices)

    combat.begin_hand_selection(
        mode=mode,
        min_select=target_count,
        max_select=target_count,
        manual_confirm=manual_confirm,
        filter_fn=filter_fn,
        on_resolve=resolve,
    )


def _queue_hand_upgrade(combat: Combat, *, mode: str, count: int = 1, filter_fn=None):
    available = [idx for idx, card in enumerate(combat.player.hand) if filter_fn is None or filter_fn(card)]
    target_count = min(max(int(count), 0), len(available))
    if target_count <= 0:
        return

    combat.begin_hand_selection(
        mode=mode,
        min_select=target_count,
        max_select=target_count,
        manual_confirm=False,
        filter_fn=filter_fn,
        on_resolve=combat.upgrade_cards_in_hand,
    )


def _queue_hand_selection(
    combat: Combat,
    *,
    mode: str,
    min_select: int,
    max_select: int,
    manual_confirm: bool = False,
    filter_fn=None,
    on_resolve=None,
):
    available = [idx for idx, hand_card in enumerate(combat.player.hand) if filter_fn is None or filter_fn(hand_card)]
    target_max = min(max(int(max_select), 0), len(available))
    target_min = min(max(int(min_select), 0), target_max)
    if target_max <= 0 and target_min <= 0:
        if on_resolve is not None:
            on_resolve([])
        return

    combat.begin_hand_selection(
        mode=mode,
        min_select=target_min,
        max_select=target_max,
        manual_confirm=manual_confirm,
        filter_fn=filter_fn,
        on_resolve=on_resolve,
    )


def _replace_cards_in_hand(combat: Combat, hand_indices: list[int], build_replacement):
    replacements: list[tuple[int, Card]] = []
    for idx in sorted(set(hand_indices)):
        if 0 <= idx < len(combat.player.hand):
            replacement = build_replacement(combat.player.hand[idx], idx)
            if replacement is not None:
                replacements.append((idx, replacement))
    for idx, replacement in replacements:
        if 0 <= idx < len(combat.player.hand):
            combat.player.hand[idx] = replacement


def _ensure_keyword(card: Card, keyword: str):
    if keyword not in card.keywords:
        card.keywords.append(keyword)


def _remove_keyword(card: Card, keyword: str):
    if keyword in card.keywords:
        card.keywords = [existing for existing in card.keywords if existing != keyword]


# ---------------------------------------------------------------------------
# Ironclad 特殊卡牌
# ---------------------------------------------------------------------------

@_register("Bash")
def _bash(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)
        vuln_amt = card.powers.get("VulnerablePower", 2)
        target.add_power(create_power("VulnerablePower", vuln_amt, target))


@_register("BodySlam")
def _body_slam(card, player, target, combat):
    dmg = player.block
    str_p = player.get_power("StrengthPower")
    if str_p and isinstance(str_p, StrengthPower):
        dmg = str_p.modify_damage(dmg)
    if target:
        target.take_damage(max(0, dmg), attacker=player)


@_register("Whirlwind")
def _whirlwind(card, player, target, combat):
    hits = player.energy + card.effective_cost(player.energy)
    dmg = combat._calc_player_damage(card.damage)
    for _ in range(hits):
        for m in combat.alive_monsters:
            m.take_damage(dmg, attacker=player)


@_register("Rampage")
def _rampage(card, player, target, combat):
    if not hasattr(card, '_rampage_bonus'):
        card._rampage_bonus = 0
    dmg = combat._calc_player_damage(card.damage + card._rampage_bonus)
    if target:
        target.take_damage(dmg, attacker=player)
    card._rampage_bonus += card.magic if card.magic > 0 else 5


@_register("TwinStrike")
def _twin_strike(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)
        target.take_damage(dmg, attacker=player)


@_register("SwordBoomerang")
def _sword_boomerang(card, player, target, combat):
    import random
    dmg = combat._calc_player_damage(card.damage)
    hits = card.magic if card.magic > 0 else 3
    for _ in range(hits):
        alive = combat.alive_monsters
        if not alive:
            break
        t = random.choice(alive)
        t.take_damage(dmg, attacker=player)


@_register("PommelStrike")
def _pommel_strike(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)
    player.draw_cards(card.draw if card.draw > 0 else 1)


@_register("ShrugItOff")
def _shrug_it_off(card, player, target, combat):
    player.gain_block(card.block)
    player.draw_cards(card.draw if card.draw > 0 else 1)


@_register("Armaments")
def _armaments(card, player, target, combat):
    player.gain_block(card.block if card.block > 0 else 5)
    if card.upgraded:
        for hand_card in player.hand:
            hand_card.apply_upgrade()
        return

    _queue_hand_upgrade(
        combat,
        mode="UpgradeSelect",
        count=1,
        filter_fn=lambda hand_card: not hand_card.upgraded,
    )


@_register("BurningPact")
def _burning_pact(card, player, target, combat):
    def resolve(_selected_indices: list[int]):
        player.draw_cards(card.draw if card.draw > 0 else 2)

    _queue_hand_exhaust(
        combat,
        mode="ExhaustSelect",
        count=1,
        on_resolve=resolve,
    )


@_register("Begone")
def _begone(card, player, target, combat):
    from sts_env.combat import make_card

    def resolve(selected_indices: list[int]):
        _replace_cards_in_hand(
            combat,
            selected_indices,
            lambda _selected_card, _idx: make_card("MinionStrike", upgraded=card.upgraded),
        )

    _queue_hand_selection(
        combat,
        mode="TransformSelect",
        min_select=1,
        max_select=1,
        on_resolve=resolve,
    )


@_register("Brand")
def _brand(card, player, target, combat):
    player.take_unblockable_damage(card.magic if card.magic > 0 else 1)

    def resolve(_selected_indices: list[int]):
        player.add_power(create_power("StrengthPower", card.powers.get("StrengthPower", 1), player))

    _queue_hand_exhaust(
        combat,
        mode="ExhaustSelect",
        count=1,
        on_resolve=resolve,
    )


@_register("TrueGrit")
def _true_grit(card, player, target, combat):
    player.gain_block(card.block if card.block > 0 else 7)
    if card.upgraded:
        _queue_hand_exhaust(combat, mode="ExhaustSelect", count=1)
        return

    import random
    if player.hand:
        chosen_idx = random.randrange(len(player.hand))
        combat.exhaust_cards_from_hand([chosen_idx])


@_register("Guards")
def _guards(card, player, target, combat):
    from sts_env.combat import make_card

    def resolve(selected_indices: list[int]):
        _replace_cards_in_hand(
            combat,
            selected_indices,
            lambda _selected_card, _idx: make_card("MinionSacrifice", upgraded=card.upgraded),
        )

    _queue_hand_selection(
        combat,
        mode="TransformSelect",
        min_select=0,
        max_select=len(player.hand),
        on_resolve=resolve,
    )


@_register("Hemokinesis")
def _hemokinesis(card, player, target, combat):
    player.take_unblockable_damage(card.magic if card.magic > 0 else 2)
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)


@_register("Offering")
def _offering(card, player, target, combat):
    player.take_unblockable_damage(6)
    player.energy += 2
    player.draw_cards(card.draw if card.draw > 0 else 3)


@_register("Bloodletting")
def _bloodletting(card, player, target, combat):
    player.take_unblockable_damage(card.magic if card.magic > 0 else 3)
    player.energy += 2


@_register("Feed")
def _feed(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        killed_before = target.is_dead
        target.take_damage(dmg, attacker=player)
        if target.is_dead and not killed_before:
            player.max_hp += card.magic if card.magic > 0 else 3
            player.hp += card.magic if card.magic > 0 else 3


@_register("Impervious")
def _impervious(card, player, target, combat):
    player.gain_block(card.block if card.block > 0 else 30)


@_register("FiendFire")
def _fiend_fire(card, player, target, combat):
    hand_count = len(player.hand)
    for c in player.hand[:]:
        player.hand.remove(c)
        player.exhaust_pile.append(c)
    dmg = combat._calc_player_damage(card.damage)
    if target:
        for _ in range(hand_count):
            target.take_damage(dmg, attacker=player)


@_register("Headbutt")
def _headbutt(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)
    selected = _best_cards_from_pile(player, player.discard_pile, count=1, purpose="recover")
    _move_cards_to_draw_top(player, player.discard_pile, selected)


@_register("SecondWind")
def _second_wind(card, player, target, combat):
    non_attacks = [c for c in player.hand if c.card_type.value != "Attack"]
    block_per = card.block if card.block > 0 else 5
    for c in non_attacks:
        player.hand.remove(c)
        player.exhaust_pile.append(c)
        player.gain_block(block_per)


@_register("Thunderclap")
def _thunderclap(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    for m in combat.alive_monsters:
        m.take_damage(dmg, attacker=player)
        m.add_power(create_power("VulnerablePower", card.powers.get("VulnerablePower", 1), m))


# ---------------------------------------------------------------------------
# Silent 特殊卡牌
# ---------------------------------------------------------------------------

@_register("Neutralize")
def _neutralize(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)
        target.add_power(create_power("WeakPower", card.powers.get("WeakPower", 1), target))


@_register("Backflip")
def _backflip(card, player, target, combat):
    player.gain_block(card.block)
    player.draw_cards(card.draw if card.draw > 0 else 2)


@_register("Acrobatics")
def _acrobatics(card, player, target, combat):
    player.draw_cards(card.draw if card.draw > 0 else 3)
    _queue_hand_discard(combat, mode="DiscardSelect", count=1)


@_register("Survivor")
def _survivor(card, player, target, combat):
    player.gain_block(card.block if card.block > 0 else 8)
    _queue_hand_discard(combat, mode="DiscardSelect", count=1)


@_register("Prepared")
def _prepared(card, player, target, combat):
    discard_count = card.draw if card.draw > 0 else 1
    player.draw_cards(discard_count)
    _queue_hand_discard(combat, mode="DiscardSelect", count=discard_count)


@_register("HandTrick")
def _hand_trick(card, player, target, combat):
    player.gain_block(card.block if card.block > 0 else 7)

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if 0 <= idx < len(player.hand):
            player.hand[idx].single_turn_sly = True

    _queue_hand_selection(
        combat,
        mode="CardSelect",
        min_select=1,
        max_select=1,
        filter_fn=lambda hand_card: (
            hand_card.card_type.value == "Skill"
            and "Sly" not in hand_card.keywords
            and not getattr(hand_card, "single_turn_sly", False)
        ),
        on_resolve=resolve,
    )


@_register("DaggerThrow")
def _dagger_throw(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)
    player.draw_cards(card.draw if card.draw > 0 else 1)
    _queue_hand_discard(combat, mode="DiscardSelect", count=1)


@_register("HiddenDaggers")
def _hidden_daggers(card, player, target, combat):
    discard_count = card.draw if card.draw > 0 else 2

    def resolve(_selected_indices: list[int]):
        from sts_env.combat import make_card

        shiv_count = card.magic if card.magic > 0 else 2
        for _ in range(shiv_count):
            if len(player.hand) >= 10:
                break
            shiv = make_card("Shiv")
            if card.upgraded:
                shiv.apply_upgrade()
            player.hand.append(shiv)

    _queue_hand_discard(
        combat,
        mode="DiscardSelect",
        count=discard_count,
        on_resolve=resolve,
    )


@_register("Scavenge")
def _scavenge(card, player, target, combat):
    def resolve(_selected_indices: list[int]):
        player.add_power(create_power("EnergizedPower", card.magic if card.magic > 0 else 2, player))

    _queue_hand_exhaust(
        combat,
        mode="ExhaustSelect",
        count=1,
        on_resolve=resolve,
    )


@_register("DualWield")
def _dual_wield(card, player, target, combat):
    copy_count = card.draw if card.draw > 0 else max(card.magic, 1) if card.magic > 0 else 1

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        selected_card = player.hand[selected_indices[0]]
        for _ in range(copy_count):
            if len(player.hand) >= 10:
                break
            player.hand.append(copy.deepcopy(selected_card))

    combat.begin_hand_selection(
        mode="CardSelect",
        min_select=1,
        max_select=1,
        manual_confirm=False,
        filter_fn=lambda hand_card: hand_card.card_type.value in {"Attack", "Power"},
        on_resolve=resolve,
    )


@_register("HeirloomHammer")
def _heirloom_hammer(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage if card.damage > 0 else 20)
    if target:
        target.take_damage(dmg, attacker=player)

    repeat_count = max(1, int(card.vars.get("Repeat", 1)))

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if 0 <= idx < len(player.hand):
            selected_card = player.hand[idx]
            for _ in range(repeat_count):
                if len(player.hand) >= 10:
                    break
                player.hand.append(copy.deepcopy(selected_card))

    _queue_hand_selection(
        combat,
        mode="CardSelect",
        min_select=1,
        max_select=1,
        filter_fn=lambda hand_card: hand_card.pool.lower() == "colorless",
        on_resolve=resolve,
    )


@_register("Nightmare")
def _nightmare(card, player, target, combat):
    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if 0 <= idx < len(player.hand):
            nightmare = NightmarePower(3, player)
            nightmare.set_selected_card(player.hand[idx])
            player.add_power(nightmare)

    _queue_hand_selection(
        combat,
        mode="CardSelect",
        min_select=1,
        max_select=1,
        on_resolve=resolve,
    )


@_register("DecisionsDecisions")
def _decisions_decisions(card, player, target, combat):
    player.draw_cards(card.draw if card.draw > 0 else (5 if card.upgraded else 3))

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if idx < 0 or idx >= len(player.hand):
            return

        selected_card = player.hand.pop(idx)
        for play_idx in range(3):
            play_card = selected_card if play_idx == 0 else copy.deepcopy(selected_card)
            combat._execute_card_repeated(play_card, 0)
            if combat.hand_selection is not None or combat.is_over:
                break
        combat._move_card_to_result_pile(selected_card)
        combat._check_combat_end()

    _queue_hand_selection(
        combat,
        mode="CardSelect",
        min_select=1,
        max_select=1,
        filter_fn=lambda hand_card: (
            hand_card.card_type.value == "Skill"
            and "Unplayable" not in hand_card.keywords
        ),
        on_resolve=resolve,
    )


@_register("Glimmer")
def _glimmer(card, player, target, combat):
    player.draw_cards(card.draw if card.draw > 0 else 3)
    put_back = card.magic if card.magic > 0 else 1

    def resolve(selected_indices: list[int]):
        for idx in sorted(set(selected_indices), reverse=True):
            if 0 <= idx < len(player.hand):
                player.draw_pile.append(player.hand.pop(idx))

    selectable = min(put_back, len(player.hand))
    if selectable <= 0:
        return
    combat.begin_hand_selection(
        mode="PutBackSelect",
        min_select=selectable,
        max_select=selectable,
        manual_confirm=False,
        on_resolve=resolve,
    )


@_register("ThinkingAhead")
def _thinking_ahead(card, player, target, combat):
    player.draw_cards(card.draw if card.draw > 0 else 2)

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if 0 <= idx < len(player.hand):
            player.draw_pile.append(player.hand.pop(idx))

    combat.begin_hand_selection(
        mode="PutBackSelect",
        min_select=1,
        max_select=1,
        manual_confirm=False,
        on_resolve=resolve,
    )


@_register("PhotonCut")
def _photon_cut(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage if card.damage > 0 else 10)
    if target:
        target.take_damage(dmg, attacker=player)

    player.draw_cards(card.draw if card.draw > 0 else (2 if card.upgraded else 1))
    put_back = max(0, int(card.vars.get("PutBack", 1)))
    selectable = min(put_back, len(player.hand))
    if selectable <= 0:
        return

    def resolve(selected_indices: list[int]):
        for idx in sorted(set(selected_indices), reverse=True):
            if 0 <= idx < len(player.hand):
                player.draw_pile.append(player.hand.pop(idx))

    combat.begin_hand_selection(
        mode="PutBackSelect",
        min_select=selectable,
        max_select=selectable,
        manual_confirm=False,
        on_resolve=resolve,
    )


@_register("Purity")
def _purity(card, player, target, combat):
    _queue_hand_selection(
        combat,
        mode="ExhaustSelect",
        min_select=0,
        max_select=5 if card.upgraded else 3,
        on_resolve=combat.exhaust_cards_from_hand,
    )


@_register("SculptingStrike")
def _sculpting_strike(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage if card.damage > 0 else 9)
    if target:
        target.take_damage(dmg, attacker=player)

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if 0 <= idx < len(player.hand):
            _ensure_keyword(player.hand[idx], "Ethereal")

    _queue_hand_selection(
        combat,
        mode="CardSelect",
        min_select=1,
        max_select=1,
        filter_fn=lambda hand_card: "Ethereal" not in hand_card.keywords,
        on_resolve=resolve,
    )


@_register("Snap")
def _snap(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage if card.damage > 0 else (10 if card.upgraded else 7))
    if target and not getattr(player, "is_osty_missing", False):
        target.take_damage(dmg, attacker=player)

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if 0 <= idx < len(player.hand):
            _ensure_keyword(player.hand[idx], "Retain")

    _queue_hand_selection(
        combat,
        mode="CardSelect",
        min_select=1,
        max_select=1,
        filter_fn=lambda hand_card: "Retain" not in hand_card.keywords,
        on_resolve=resolve,
    )


@_register("Transfigure")
def _transfigure(card, player, target, combat):
    if card.upgraded and "Exhaust" in card.keywords:
        card.keywords = [keyword for keyword in card.keywords if keyword != "Exhaust"]

    def resolve(selected_indices: list[int]):
        if not selected_indices:
            return
        idx = selected_indices[0]
        if 0 <= idx < len(player.hand):
            selected_card = player.hand[idx]
            if selected_card.cost >= 0:
                selected_card.cost += 1
            selected_card.replay_count += 1

    _queue_hand_selection(
        combat,
        mode="CardSelect",
        min_select=1,
        max_select=1,
        on_resolve=resolve,
    )


@_register("Backstab")
def _backstab(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)


@_register("Adrenaline")
def _adrenaline(card, player, target, combat):
    player.energy += 1
    player.draw_cards(card.draw if card.draw > 0 else 2)


@_register("BladeDance")
def _blade_dance(card, player, target, combat):
    from sts_env.combat import make_card
    shiv_count = card.magic if card.magic > 0 else 3
    for _ in range(shiv_count):
        if len(player.hand) < 10:
            player.hand.append(make_card("Shiv"))


@_register("DaggerSpray")
def _dagger_spray(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    hits = 2
    for _ in range(hits):
        for m in combat.alive_monsters:
            m.take_damage(dmg, attacker=player)


@_register("PiercingWail")
def _piercing_wail(card, player, target, combat):
    from sts_env.powers import StrengthPower as SP
    amt = -(card.magic if card.magic > 0 else 6)
    for m in combat.alive_monsters:
        m.add_power(SP(amt, m))


# ---------------------------------------------------------------------------
# Defect 特殊卡牌
# ---------------------------------------------------------------------------

@_register("BallLightning")
def _ball_lightning(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage)
    if target:
        target.take_damage(dmg, attacker=player)
    # 简化：充能球效果暂不实现


@_register("Zap")
def _zap(card, player, target, combat):
    pass  # 充能球系统暂不实现


@_register("Dualcast")
def _dualcast(card, player, target, combat):
    pass  # 充能球系统暂不实现


# Hologram / pile selection helpers
@_register("Hologram")
def _hologram(card, player, target, combat):
    if card.upgraded:
        _remove_keyword(card, "Exhaust")
    player.gain_block(card.block if card.block > 0 else 3)
    selected = _best_cards_from_pile(
        player,
        player.discard_pile,
        count=1,
        purpose="recover_to_hand",
    )
    _move_cards_to_hand(player, player.discard_pile, selected)


# ---------------------------------------------------------------------------
# Necrobinder / Regent / Colorless pile-selection cards
# ---------------------------------------------------------------------------

@_register("Charge")
def _charge(card, player, target, combat):
    selected = _best_cards_from_pile(
        player,
        player.draw_pile,
        count=card.draw if card.draw > 0 else 2,
        purpose="prune",
    )
    _transform_cards_in_pile(player.draw_pile, selected, "MinionDiveBomb", upgraded=card.upgraded)


@_register("Cleanse")
def _cleanse(card, player, target, combat):
    _summon_osty(player)
    selected = _best_cards_from_pile(player, player.draw_pile, count=1, purpose="prune")
    _exhaust_cards_in_pile(player, player.draw_pile, selected)


@_register("CosmicIndifference")
def _cosmic_indifference(card, player, target, combat):
    player.gain_block(card.block if card.block > 0 else 6)
    selected = _best_cards_from_pile(player, player.discard_pile, count=1, purpose="recover")
    _move_cards_to_draw_top(player, player.discard_pile, selected)


@_register("Discovery")
def _discovery(card, player, target, combat):
    if card.upgraded:
        _remove_keyword(card, "Exhaust")
    options = _generate_choice_cards(
        pools=(_player_character(player),),
        count=3,
    )
    selected = _choose_generated_card(player, options)
    _add_generated_card_to_hand(player, selected, free_this_turn=True)


@_register("Dredge")
def _dredge(card, player, target, combat):
    retrieve_count = min(card.draw if card.draw > 0 else 3, max(0, 10 - len(player.hand)))
    selected = _best_cards_from_pile(
        player,
        player.discard_pile,
        count=retrieve_count,
        purpose="recover_to_hand",
    )
    _move_cards_to_hand(player, player.discard_pile, selected)


@_register("Graveblast")
def _graveblast(card, player, target, combat):
    if card.upgraded:
        _remove_keyword(card, "Exhaust")
    dmg = combat._calc_player_damage(card.damage if card.damage > 0 else 4)
    if target:
        target.take_damage(dmg, attacker=player)
    selected = _best_cards_from_pile(
        player,
        player.discard_pile,
        count=1,
        purpose="recover_to_hand",
    )
    _move_cards_to_hand(player, player.discard_pile, selected)


@_register("Quasar")
def _quasar(card, player, target, combat):
    options = _generate_choice_cards(
        pools=("Colorless",),
        count=3,
        upgraded=card.upgraded,
    )
    selected = _choose_generated_card(player, options)
    _add_generated_card_to_hand(player, selected)


@_register("Seance")
def _seance(card, player, target, combat):
    selected = _best_cards_from_pile(player, player.draw_pile, count=1, purpose="prune")
    _transform_cards_in_pile(player.draw_pile, selected, "Soul")


@_register("SecretTechnique")
def _secret_technique(card, player, target, combat):
    if card.upgraded:
        _remove_keyword(card, "Exhaust")
    selected = _best_cards_from_pile(
        player,
        player.draw_pile,
        count=1,
        filter_fn=lambda pile_card: pile_card.card_type.value == "Skill",
        purpose="recover_to_hand",
    )
    _move_cards_to_hand(player, player.draw_pile, selected)


@_register("SecretWeapon")
def _secret_weapon(card, player, target, combat):
    if card.upgraded:
        _remove_keyword(card, "Exhaust")
    selected = _best_cards_from_pile(
        player,
        player.draw_pile,
        count=1,
        filter_fn=lambda pile_card: pile_card.card_type.value == "Attack",
        purpose="recover_to_hand",
    )
    _move_cards_to_hand(player, player.draw_pile, selected)


@_register("SeekerStrike")
def _seeker_strike(card, player, target, combat):
    dmg = combat._calc_player_damage(card.damage if card.damage > 0 else 9)
    if target:
        target.take_damage(dmg, attacker=player)
    if not player.draw_pile or len(player.hand) >= 10:
        return
    sample_size = min(card.draw if card.draw > 0 else 3, len(player.draw_pile))
    sampled = random.sample(player.draw_pile, sample_size)
    selected = _best_cards_from_pile(player, sampled, count=1, purpose="recover_to_hand")
    _move_cards_to_hand(player, player.draw_pile, selected)


@_register("Splash")
def _splash(card, player, target, combat):
    character_pools = ("Ironclad", "Silent", "Defect", "Necrobinder", "Regent")
    options = _generate_choice_cards(
        pools=character_pools,
        count=3,
        upgraded=card.upgraded,
        filter_fn=lambda entry: entry.get("type") == "Attack",
        exclude_pools=(_player_character(player),),
    )
    selected = _choose_generated_card(player, options)
    _add_generated_card_to_hand(player, selected, free_this_turn=True)


@_register("Wish")
def _wish(card, player, target, combat):
    selected = _best_cards_from_pile(
        player,
        player.draw_pile,
        count=1,
        purpose="recover_to_hand",
    )
    _move_cards_to_hand(player, player.draw_pile, selected)


# ---------------------------------------------------------------------------
# 通用卡牌
# ---------------------------------------------------------------------------

@_register("Apparition")
def _apparition(card, player, target, combat):
    from sts_env.powers import IntangiblePower
    player.add_power(IntangiblePower(card.magic if card.magic > 0 else 1, player))
