from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from sts_env.combat import IntentType, Player, player_can_play_card, player_card_cost


@dataclass(slots=True)
class SearchAction:
    label: str
    kind: str
    cost: int = 0
    block: int = 0
    draw: int = 0
    damage_reduction: int = 0
    energy_gain: int = 0
    frees_hand_space: bool = False
    reduces_incoming_damage: bool = False
    draws: list["SearchAction"] = field(default_factory=list)
    consume_on_use: bool = True


@dataclass(slots=True)
class TurnSearchContext:
    actual_hp_loss: int
    incoming_damage: int
    energy: int
    hand: list[SearchAction] = field(default_factory=list)
    draw_pile: list[SearchAction] = field(default_factory=list)
    potions: list[SearchAction] = field(default_factory=list)
    hand_limit: int = 10


@dataclass(slots=True)
class TurnSearchResult:
    actual_hp_loss: int
    optimal_min_hp_loss: int
    avoidable_hp_loss: int
    best_line_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _SearchState:
    energy: int
    block: int
    incoming_damage: int
    hand: list[SearchAction]
    draw_pile: list[SearchAction]
    potions: list[SearchAction]
    ended_turn: bool = False
    lineage: list[SearchAction] = field(default_factory=list)

    @property
    def hp_loss(self) -> int:
        return max(0, self.incoming_damage - self.block)

    @property
    def enemy_threat(self) -> int:
        return max(0, self.incoming_damage)

    @property
    def resource_quality(self) -> int:
        return self.energy + len(self.hand) + len(self.potions)


def _normalize_action(raw: Any) -> SearchAction:
    if isinstance(raw, SearchAction):
        return raw
    payload = dict(raw or {})
    draws = [_normalize_action(item) for item in payload.get("draws", [])]
    block = int(payload.get("block", 0) or 0)
    damage_reduction = int(payload.get("damage_reduction", 0) or 0)
    return SearchAction(
        label=str(payload.get("label", payload.get("card_id", payload.get("id", "action")))),
        kind=str(payload.get("kind", "card")),
        cost=int(payload.get("cost", 0) or 0),
        block=block,
        draw=int(payload.get("draw", 0) or 0),
        damage_reduction=damage_reduction,
        energy_gain=int(payload.get("energy_gain", 0) or 0),
        frees_hand_space=bool(payload.get("frees_hand_space", False)),
        reduces_incoming_damage=bool(payload.get("reduces_incoming_damage", False) or block > 0 or damage_reduction > 0),
        draws=draws,
        consume_on_use=bool(payload.get("consume_on_use", True)),
    )


def _normalize_context(raw: Any) -> TurnSearchContext:
    if isinstance(raw, TurnSearchContext):
        return raw
    payload = dict(raw or {})
    return TurnSearchContext(
        actual_hp_loss=int(payload.get("actual_hp_loss", 0) or 0),
        incoming_damage=int(payload.get("incoming_damage", 0) or 0),
        energy=int(payload.get("energy", 0) or 0),
        hand=[_normalize_action(item) for item in payload.get("hand", [])],
        draw_pile=[_normalize_action(item) for item in payload.get("draw_pile", [])],
        potions=[_normalize_action(item) for item in payload.get("potions", [])],
        hand_limit=int(payload.get("hand_limit", 10) or 10),
    )


def _seed_state(turn_context: TurnSearchContext) -> _SearchState:
    return _SearchState(
        energy=turn_context.energy,
        block=0,
        incoming_damage=turn_context.incoming_damage,
        hand=list(turn_context.hand),
        draw_pile=list(turn_context.draw_pile),
        potions=list(turn_context.potions),
    )


def _state_rank(state: _SearchState) -> tuple[int, int, int]:
    return (
        int(state.hp_loss),
        int(state.enemy_threat),
        -int(state.resource_quality),
    )


def _action_priority(action: SearchAction) -> tuple[int, int, str]:
    if action.kind == "card" and action.cost == 0 and action.draw > 0:
        return (0, 0, action.label)
    if action.kind == "card" and action.frees_hand_space:
        return (1, 0, action.label)
    if action.kind == "card" and action.reduces_incoming_damage:
        return (2, 0, action.label)
    if action.kind == "potion" and action.reduces_incoming_damage:
        return (3, 0, action.label)
    if action.kind == "end_turn":
        return (9, 0, action.label)
    return (5, 0, action.label)


def _with_drawn_cards(
    hand: list[SearchAction],
    draw_pile: list[SearchAction],
    action: SearchAction,
    hand_limit: int,
) -> tuple[list[SearchAction], list[SearchAction]]:
    next_hand = list(hand)
    next_draw_pile = list(draw_pile)

    for generated in action.draws:
        if len(next_hand) >= hand_limit:
            break
        next_hand.append(generated)

    draw_count = max(0, action.draw - len(action.draws))
    while draw_count > 0 and next_draw_pile and len(next_hand) < hand_limit:
        next_hand.append(next_draw_pile.pop(0))
        draw_count -= 1
    return next_hand, next_draw_pile


def _apply_action(
    state: _SearchState,
    action: SearchAction,
    *,
    hand_limit: int,
    hand_index: int | None = None,
    potion_index: int | None = None,
) -> _SearchState:
    if action.kind == "end_turn":
        return _SearchState(
            energy=state.energy,
            block=state.block,
            incoming_damage=state.incoming_damage,
            hand=list(state.hand),
            draw_pile=list(state.draw_pile),
            potions=list(state.potions),
            ended_turn=True,
            lineage=[*state.lineage, action],
        )

    next_hand = list(state.hand)
    next_potions = list(state.potions)
    if hand_index is not None and 0 <= hand_index < len(next_hand):
        next_hand.pop(hand_index)
    if potion_index is not None and 0 <= potion_index < len(next_potions) and action.consume_on_use:
        next_potions.pop(potion_index)

    drawn_hand, next_draw_pile = _with_drawn_cards(next_hand, state.draw_pile, action, hand_limit)
    return _SearchState(
        energy=max(0, state.energy - action.cost + action.energy_gain),
        block=max(0, state.block + action.block),
        incoming_damage=max(0, state.incoming_damage - action.damage_reduction),
        hand=drawn_hand,
        draw_pile=next_draw_pile,
        potions=next_potions,
        ended_turn=False,
        lineage=[*state.lineage, action],
    )


def _expand_legal_actions(state: _SearchState, *, hand_limit: int):
    candidates: list[tuple[SearchAction, int | None, int | None]] = []
    for idx, action in enumerate(state.hand):
        if action.cost <= state.energy:
            candidates.append((action, idx, None))
    for idx, action in enumerate(state.potions):
        candidates.append((action, None, idx))
    candidates.append((SearchAction(label="end_turn", kind="end_turn"), None, None))
    candidates.sort(key=lambda item: _action_priority(item[0]))
    for action, hand_index, potion_index in candidates:
        yield _apply_action(
            state,
            action,
            hand_limit=hand_limit,
            hand_index=hand_index,
            potion_index=potion_index,
        )


def _dedupe_states(states: list[_SearchState]) -> list[_SearchState]:
    deduped: dict[tuple[Any, ...], _SearchState] = {}
    for state in states:
        key = (
            state.energy,
            state.block,
            state.incoming_damage,
            tuple(action.label for action in state.hand),
            tuple(action.label for action in state.draw_pile),
            tuple(action.label for action in state.potions),
            state.ended_turn,
        )
        incumbent = deduped.get(key)
        if incumbent is None or _state_rank(state) < _state_rank(incumbent):
            deduped[key] = state
    return list(deduped.values())


def _beam_search_turn(turn_context: TurnSearchContext, *, beam_width: int, max_depth: int) -> _SearchState:
    frontier = [_seed_state(turn_context)]
    best_terminal = _apply_action(frontier[0], SearchAction(label="end_turn", kind="end_turn"), hand_limit=turn_context.hand_limit)
    for _ in range(max_depth):
        candidates: list[_SearchState] = []
        for state in frontier:
            if state.ended_turn:
                candidates.append(state)
                continue
            candidates.extend(_expand_legal_actions(state, hand_limit=turn_context.hand_limit))
        if not candidates:
            break
        deduped = _dedupe_states(candidates)
        deduped.sort(key=_state_rank)
        frontier = deduped[:beam_width]
        maybe_terminal = min(frontier, key=_state_rank)
        if _state_rank(maybe_terminal) < _state_rank(best_terminal):
            best_terminal = maybe_terminal
        if all(state.ended_turn for state in frontier):
            break
    return best_terminal


def analyze_turn_avoidable_hp_loss(
    turn_context: TurnSearchContext | dict[str, Any],
    *,
    beam_width: int = 48,
    max_depth: int = 10,
) -> TurnSearchResult:
    context = _normalize_context(turn_context)
    best_state = _beam_search_turn(context, beam_width=beam_width, max_depth=max_depth)
    actual_hp_loss = int(context.actual_hp_loss)
    optimal_min_hp_loss = int(best_state.hp_loss)
    return TurnSearchResult(
        actual_hp_loss=actual_hp_loss,
        optimal_min_hp_loss=optimal_min_hp_loss,
        avoidable_hp_loss=max(0, actual_hp_loss - optimal_min_hp_loss),
        best_line_labels=[step.label for step in best_state.lineage if step.kind != "end_turn"],
    )


def _estimate_enemy_damage_reduction_from_weak(base_damage: int, hits: int) -> int:
    if base_damage <= 0 or hits <= 0:
        return 0
    weakened = max(0, int(base_damage * 0.75))
    return max(0, (base_damage - weakened) * hits)


def _enemy_incoming_damage(enemy: Any) -> int:
    intent = getattr(enemy, "intent", None)
    if intent is None:
        return 0
    if getattr(intent, "intent_type", IntentType.UNKNOWN) not in (
        IntentType.ATTACK,
        IntentType.ATTACK_BUFF,
        IntentType.ATTACK_DEBUFF,
    ):
        return 0
    return int(getattr(intent, "damage", 0) or 0) * int(getattr(intent, "hits", 1) or 1)


def _estimate_card_damage_reduction(card: Any, combat: Any) -> int:
    alive_monsters = [monster for monster in getattr(combat, "alive_monsters", []) if not getattr(monster, "is_dead", False)]
    reduction = 0

    card_damage = int(getattr(card, "damage", 0) or 0)
    powers = dict(getattr(card, "powers", {}) or {})
    target = getattr(card, "target", None)
    if card_damage > 0 and alive_monsters:
        if str(getattr(target, "value", target)) == "AllEnemies":
            reduction += sum(_enemy_incoming_damage(monster) for monster in alive_monsters if card_damage >= monster.hp)
        else:
            reduction += max((_enemy_incoming_damage(monster) for monster in alive_monsters if card_damage >= monster.hp), default=0)

    weak_amount = int(powers.get("WeakPower", 0) or 0)
    if weak_amount > 0 and alive_monsters:
        if str(getattr(target, "value", target)) == "AllEnemies":
            reduction += sum(
                _estimate_enemy_damage_reduction_from_weak(
                    int(getattr(monster.intent, "damage", 0) or 0),
                    int(getattr(monster.intent, "hits", 1) or 1),
                )
                for monster in alive_monsters
            )
        else:
            reduction += max(
                (
                    _estimate_enemy_damage_reduction_from_weak(
                        int(getattr(monster.intent, "damage", 0) or 0),
                        int(getattr(monster.intent, "hits", 1) or 1),
                    )
                    for monster in alive_monsters
                ),
                default=0,
            )
    return reduction


def _card_to_action(card: Any, player: Player, combat: Any) -> SearchAction | None:
    if not player_can_play_card(card, player):
        return None
    return SearchAction(
        label=str(getattr(card, "card_id", "card")),
        kind="card",
        cost=int(player_card_cost(card, player)),
        block=int(getattr(card, "block", 0) or 0),
        draw=int(getattr(card, "draw", 0) or 0),
        damage_reduction=_estimate_card_damage_reduction(card, combat),
        frees_hand_space=int(getattr(card, "draw", 0) or 0) > 0,
        reduces_incoming_damage=(
            int(getattr(card, "block", 0) or 0) > 0 or _estimate_card_damage_reduction(card, combat) > 0
        ),
    )


def _potion_to_action(potion: Any, combat: Any) -> SearchAction | None:
    potion_id = ""
    if isinstance(potion, dict):
        potion_id = str(potion.get("id", ""))
    elif isinstance(potion, str):
        potion_id = potion
    if not potion_id:
        return None

    alive_monsters = [monster for monster in getattr(combat, "alive_monsters", []) if not getattr(monster, "is_dead", False)]
    damage_reduction = 0
    if potion_id in {"BlockPotion", "HeartOfIron"}:
        return SearchAction(label=potion_id, kind="potion", block=12, reduces_incoming_damage=True)
    if potion_id in {"WeakPotion", "PotionOfBinding"} and alive_monsters:
        damage_reduction = max(
            (
                _estimate_enemy_damage_reduction_from_weak(
                    int(getattr(monster.intent, "damage", 0) or 0),
                    int(getattr(monster.intent, "hits", 1) or 1),
                )
                for monster in alive_monsters
            ),
            default=0,
        )
    elif potion_id in {"FearPotion", "ExplosiveAmpoule", "FirePotion"} and alive_monsters:
        damage_reduction = max((_enemy_incoming_damage(monster) for monster in alive_monsters if monster.hp <= 20), default=0)
    return SearchAction(
        label=potion_id,
        kind="potion",
        damage_reduction=damage_reduction,
        reduces_incoming_damage=damage_reduction > 0,
    )


def build_turn_search_context(combat: Any, player: Player, *, actual_hp_loss: int) -> TurnSearchContext:
    incoming_damage = sum(_enemy_incoming_damage(monster) for monster in getattr(combat, "alive_monsters", []))
    hand = [action for action in (_card_to_action(card, player, combat) for card in player.hand) if action is not None]
    draw_pile = [action for action in (_card_to_action(card, player, combat) for card in player.draw_pile) if action is not None]
    potions = [action for action in (_potion_to_action(potion, combat) for potion in player.potions) if action is not None]
    return TurnSearchContext(
        actual_hp_loss=int(actual_hp_loss),
        incoming_damage=incoming_damage,
        energy=int(player.energy),
        hand=hand,
        draw_pile=draw_pile,
        potions=potions,
        hand_limit=10,
    )

