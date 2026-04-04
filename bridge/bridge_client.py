"""游戏桥接客户端：通过 WebSocket 暴露桥接协议，并将原始游戏状态转换为策略输入。"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bridge_server import BridgeServer, PROTOCOL_VERSION
from agent.model_paths import CHARACTERS
from bridge.control_state import BridgeControlStateStore
from sts_env.combat import (
    Card,
    CardType,
    Combat,
    Intent,
    IntentType,
    Monster,
    Player,
    TargetType,
    make_card,
)
from sts_env.encoding import encode_observation
from sts_env.env import (
    A_BOSS_END,
    A_BOSS_START,
    A_COOK,
    A_DIG,
    A_END_TURN,
    A_EVENT_END,
    A_EVENT_START,
    A_LIFT,
    A_MAP_END,
    A_MAP_START,
    A_PICK_END,
    A_PICK_START,
    A_PLAY_END,
    A_PLAY_START,
    A_POTION_END,
    A_POTION_START,
    A_REST,
    A_SHOP_CARD_END,
    A_SHOP_CARD_START,
    A_SHOP_LEAVE,
    A_SHOP_RELIC_END,
    A_SHOP_RELIC_START,
    A_SHOP_REMOVE,
    A_SKIP,
    A_UPGRADE,
    TOTAL_ACTIONS,
    StsEnv,
)
from sts_env.game_state import GamePhase, GameState, MapNode, RoomType
from sts_env.powers import create_power


def _enum_key(value: Any) -> str:
    return str(value).strip().replace("-", "_").replace(" ", "_").lower()


_PHASE_MAP = {
    "map": GamePhase.MAP,
    "combat": GamePhase.COMBAT,
    "card_reward": GamePhase.CARD_REWARD,
    "cardreward": GamePhase.CARD_REWARD,
    "event": GamePhase.EVENT,
    "rest": GamePhase.REST,
    "shop": GamePhase.SHOP,
    "treasure": GamePhase.TREASURE,
    "boss_relic": GamePhase.BOSS_RELIC,
    "bossrelic": GamePhase.BOSS_RELIC,
    "neow": GamePhase.NEOW,
    "game_over": GamePhase.GAME_OVER,
    "gameover": GamePhase.GAME_OVER,
}

_ROOM_TYPE_MAP = {
    "monster": RoomType.MONSTER,
    "elite": RoomType.ELITE,
    "boss": RoomType.BOSS,
    "rest": RoomType.REST,
    "shop": RoomType.SHOP,
    "event": RoomType.EVENT,
    "treasure": RoomType.TREASURE,
}

_CARD_TYPE_MAP = {
    "attack": CardType.ATTACK,
    "skill": CardType.SKILL,
    "power": CardType.POWER,
    "status": CardType.STATUS,
    "curse": CardType.CURSE,
}

_TARGET_TYPE_MAP = {
    "self": TargetType.SELF,
    "anyenemy": TargetType.ANY_ENEMY,
    "any_enemy": TargetType.ANY_ENEMY,
    "allenemies": TargetType.ALL_ENEMIES,
    "all_enemies": TargetType.ALL_ENEMIES,
    "none": TargetType.NONE,
}

_INTENT_TYPE_MAP = {
    "attack": IntentType.ATTACK,
    "attack_buff": IntentType.ATTACK_BUFF,
    "attackbuff": IntentType.ATTACK_BUFF,
    "attack_debuff": IntentType.ATTACK_DEBUFF,
    "attackdebuff": IntentType.ATTACK_DEBUFF,
    "buff": IntentType.BUFF,
    "debuff": IntentType.DEBUFF,
    "defend": IntentType.DEFEND,
    "heal": IntentType.HEAL,
    "unknown": IntentType.UNKNOWN,
}

_CHARACTER_MAP = {_enum_key(character): character for character in CHARACTERS}


def _coerce_phase(value: Any) -> GamePhase:
    if isinstance(value, GamePhase):
        return value
    phase = _PHASE_MAP.get(_enum_key(value))
    if phase is None:
        raise ValueError(f"不支持的 phase: {value}")
    return phase


def _coerce_room_type(value: Any) -> RoomType | str:
    if isinstance(value, RoomType):
        return value
    room_type = _ROOM_TYPE_MAP.get(_enum_key(value))
    if room_type is not None:
        return room_type
    return str(value).upper()


def _coerce_card_type(value: Any) -> CardType:
    if isinstance(value, CardType):
        return value
    return _CARD_TYPE_MAP.get(_enum_key(value), CardType.ATTACK)


def _coerce_target_type(value: Any) -> TargetType:
    if isinstance(value, TargetType):
        return value
    return _TARGET_TYPE_MAP.get(_enum_key(value), TargetType.ANY_ENEMY)


def _coerce_intent_type(value: Any) -> IntentType:
    if isinstance(value, IntentType):
        return value
    return _INTENT_TYPE_MAP.get(_enum_key(value), IntentType.UNKNOWN)


def _canonicalize_character(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _CHARACTER_MAP.get(_enum_key(text), text)


def _power_amount_map(payload: Any) -> dict[str, int]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return {str(key): int(value) for key, value in payload.items()}

    result: dict[str, int] = {}
    for item in payload:
        if isinstance(item, str):
            result[item] = result.get(item, 0) + 1
            continue
        power_id = str(item.get("id") or item.get("power_id"))
        result[power_id] = result.get(power_id, 0) + int(item.get("amount", 1))
    return result


def _apply_powers(creature: Player | Monster, payload: Any):
    creature.powers = []
    for power_id, amount in _power_amount_map(payload).items():
        creature.add_power(create_power(power_id, amount, creature))


def _build_card(payload: Any) -> Card:
    if isinstance(payload, str):
        return make_card(payload)

    if not isinstance(payload, dict):
        raise ValueError(f"卡牌格式错误: {payload!r}")

    card_id = payload.get("card_id") or payload.get("id")
    if not card_id:
        raise ValueError(f"卡牌缺少 id: {payload!r}")

    upgraded = bool(payload.get("upgraded", False))
    card = make_card(str(card_id), upgraded=upgraded)

    if "cost" in payload:
        card.cost = int(payload["cost"])
    if "type" in payload:
        card.card_type = _coerce_card_type(payload["type"])
    if "target" in payload:
        card.target = _coerce_target_type(payload["target"])
    if "damage" in payload:
        card.damage = int(payload["damage"])
    if "block" in payload:
        card.block = int(payload["block"])
    if "draw" in payload:
        card.draw = int(payload["draw"])
    if "magic" in payload:
        card.magic = int(payload["magic"])
    if "powers" in payload:
        card.powers = _power_amount_map(payload["powers"])
    if "keywords" in payload:
        card.keywords = [str(item) for item in payload["keywords"]]
    if "tags" in payload:
        card.tags = [str(item) for item in payload["tags"]]
    if "pool" in payload:
        card.pool = str(payload["pool"])
    if "vars" in payload and isinstance(payload["vars"], dict):
        card.vars = {str(key): int(value) for key, value in payload["vars"].items()}
    if "replay_count" in payload:
        card.replay_count = int(payload["replay_count"])
    if "retain_this_turn" in payload:
        card.single_turn_retain = bool(payload["retain_this_turn"])
    if "sly_this_turn" in payload:
        card.single_turn_sly = bool(payload["sly_this_turn"])
    if "affliction_id" in payload:
        card.affliction_id = str(payload["affliction_id"])
    if "affliction_amount" in payload:
        card.affliction_amount = int(payload["affliction_amount"])

    return card


def _build_cards(payload: Any) -> list[Card]:
    return [_build_card(item) for item in (payload or [])]


def _build_player(payload: dict[str, Any], *, character: str) -> Player:
    max_hp = int(payload.get("max_hp", payload.get("hp", 80)))
    hp = int(payload.get("hp", max_hp))
    player = Player(
        str(payload.get("name", character)),
        hp,
        max_hp,
        energy_per_turn=int(payload.get("energy_per_turn", 3)),
        draw_per_turn=int(payload.get("draw_per_turn", 5)),
    )
    player.block = int(payload.get("block", 0))
    player.energy = int(payload.get("energy", payload.get("energy_per_turn", 0)))
    player.gold = int(payload.get("gold", 0))
    player.relics = [str(item) for item in payload.get("relics", [])]
    player.potions = list(payload.get("potions", []))
    player.orb_slots = int(payload.get("orb_slots", 0))
    player.orbs = [str(item) for item in payload.get("orbs", [])]
    player.is_osty_missing = bool(payload.get("is_osty_missing", False))
    player.hand = _build_cards(payload.get("hand", []))
    player.draw_pile = _build_cards(payload.get("draw_pile", []))
    player.discard_pile = _build_cards(payload.get("discard_pile", []))
    player.exhaust_pile = _build_cards(payload.get("exhaust_pile", []))
    _apply_powers(player, payload.get("powers", []))
    return player


def _build_intent(payload: Any) -> Intent:
    if payload is None:
        return Intent()
    if isinstance(payload, str):
        return Intent(intent_type=_coerce_intent_type(payload))
    if not isinstance(payload, dict):
        raise ValueError(f"intent 格式错误: {payload!r}")
    return Intent(
        intent_type=_coerce_intent_type(payload.get("type", payload.get("intent_type", "unknown"))),
        damage=int(payload.get("damage", 0)),
        hits=int(payload.get("hits", 1)),
        block=int(payload.get("block", 0)),
    )


def _build_monster(payload: dict[str, Any], *, index: int) -> Monster:
    max_hp = int(payload.get("max_hp", payload.get("hp", 1)))
    hp = int(payload.get("hp", max_hp))
    monster = Monster(str(payload.get("name", f"Monster{index}")), hp, max_hp)
    monster.block = int(payload.get("block", 0))
    monster.intent = _build_intent(payload.get("intent"))
    monster.move_history = [str(item) for item in payload.get("move_history", [])]
    monster.turn_count = int(payload.get("turn_count", 0))
    if bool(payload.get("is_dead", False)) or monster.hp <= 0:
        monster.hp = 0
        monster.is_dead = True
    _apply_powers(monster, payload.get("powers", []))
    return monster


def _build_combat(player: Player, payload: dict[str, Any], *, ui_payload: dict[str, Any] | None = None) -> Combat:
    monsters = [_build_monster(item, index=index) for index, item in enumerate(payload.get("monsters", []))]
    combat = Combat(player, monsters)
    combat.turn_count = int(payload.get("turn_count", 0))
    combat.round_number = int(payload.get("round_number", 1))

    ui_payload = ui_payload or {}
    playable_cards = ui_payload.get("combat_playable_cards", payload.get("playable_cards"))
    if playable_cards is not None:
        combat.playable_cards_override = [bool(item) for item in playable_cards]

    if "combat_end_turn_enabled" in ui_payload:
        combat.end_turn_enabled_override = bool(ui_payload.get("combat_end_turn_enabled"))
    elif "end_turn_enabled" in payload:
        combat.end_turn_enabled_override = bool(payload.get("end_turn_enabled"))

    selection_mode = ui_payload.get("combat_selection_mode", payload.get("selection_mode"))
    selectable_cards = ui_payload.get("combat_selectable_cards", payload.get("selectable_cards"))
    selected_cards = ui_payload.get("combat_selected_cards", payload.get("selected_cards"))
    selection_min = ui_payload.get("combat_selection_min", payload.get("selection_min"))
    selection_max = ui_payload.get("combat_selection_max", payload.get("selection_max"))
    selection_manual_confirm = ui_payload.get(
        "combat_selection_manual_confirm",
        payload.get("selection_manual_confirm"),
    )
    selection_selected_count = ui_payload.get(
        "combat_selection_selected_count",
        payload.get("selection_selected_count"),
    )
    selection_confirm_enabled = ui_payload.get(
        "combat_selection_confirm_enabled",
        payload.get("selection_confirm_enabled"),
    )

    if any(
        value is not None
        for value in (
            selection_mode,
            selectable_cards,
            selected_cards,
            selection_confirm_enabled,
            selection_min,
            selection_max,
        )
    ):
        selected_flags = [bool(item) for item in _list_payload(selected_cards)]
        if not selected_flags and selection_selected_count:
            selected_flags = [False] * len(player.hand)
        max_select_default = max(
            sum(1 for item in _list_payload(selectable_cards) if item),
            sum(1 for item in selected_flags if item),
            1,
        )
        combat.begin_hand_selection(
            mode=str(selection_mode or "CardSelect"),
            min_select=int(selection_min if selection_min is not None else 1),
            max_select=int(selection_max if selection_max is not None else max_select_default),
            manual_confirm=bool(selection_manual_confirm) if selection_manual_confirm is not None else False,
            preset_selected_cards=selected_flags,
            preset_selectable_cards=[bool(item) for item in _list_payload(selectable_cards)],
        )
        if combat.hand_selection is not None and selection_confirm_enabled is not None:
            combat.hand_selection.confirm_enabled = bool(selection_confirm_enabled)
        if combat.hand_selection is not None and selection_selected_count is not None:
            combat.hand_selection.selected_count = int(selection_selected_count)
    return combat


def _derive_deck_payload(player_payload: dict[str, Any]) -> list[Any]:
    deck_payload: list[Any] = []
    for zone_name in ("hand", "draw_pile", "discard_pile", "exhaust_pile"):
        deck_payload.extend(player_payload.get(zone_name, []))
    return deck_payload


def _build_map_nodes(payload: dict[str, Any], *, floor: int) -> list[list[MapNode]]:
    map_payload = payload.get("map", {}) if isinstance(payload.get("map"), dict) else {}
    raw_layers = map_payload.get("lookahead") or payload.get("map_lookahead") or []
    nodes: list[list[MapNode]] = [[] for _ in range(max(floor, 0))]

    for layer_offset, layer in enumerate(raw_layers):
        absolute_floor = floor + layer_offset
        while len(nodes) <= absolute_floor:
            nodes.append([])
        built_layer: list[MapNode] = []
        for node_index, node_payload in enumerate(layer):
            if isinstance(node_payload, dict):
                room_type = _coerce_room_type(
                    node_payload.get("room_type")
                    or node_payload.get("type")
                    or node_payload.get("room")
                    or "monster"
                )
                children = [int(item) for item in node_payload.get("children", [])]
            else:
                room_type = _coerce_room_type(node_payload)
                children = []
            built_layer.append(
                MapNode(
                    floor=absolute_floor,
                    index=node_index,
                    room_type=room_type,
                    children=children,
                )
            )
        nodes[absolute_floor] = built_layer

    return nodes


def _build_available_next(payload: dict[str, Any], *, floor: int, map_nodes: list[list[MapNode]]) -> list[int]:
    map_payload = payload.get("map", {}) if isinstance(payload.get("map"), dict) else {}
    raw_available = map_payload.get("available_next")
    if raw_available is None:
        raw_available = payload.get("available_next")

    if raw_available is not None:
        if all(isinstance(item, int) for item in raw_available):
            return [int(item) for item in raw_available]
        return list(range(len(raw_available)))

    if 0 <= floor < len(map_nodes):
        return list(range(len(map_nodes[floor])))
    return [0]


def _phase_schema_key(value: Any) -> str:
    return {
        "cardreward": "card_reward",
        "bossrelic": "boss_relic",
        "gameover": "game_over",
    }.get(_enum_key(value), _enum_key(value))



def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}



def _list_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return []



def _item_enabled(value: Any, *, default: bool = True) -> bool:
    if isinstance(value, dict) and "enabled" in value:
        return bool(value["enabled"])
    return default



def _phase_section(state_payload: dict[str, Any], phase: Any) -> dict[str, Any]:
    target = _phase_schema_key(phase)
    for key, value in state_payload.items():
        if _phase_schema_key(key) == target:
            return _dict_payload(value)
    return {}



def _legacy_phase_payload(message: dict[str, Any], phase: Any) -> dict[str, Any]:
    phase_key = _phase_schema_key(phase)

    if phase_key == "map":
        legacy = _dict_payload(message.get("map"))
        if "available_next" in message and "available_next" not in legacy:
            legacy["available_next"] = message.get("available_next")
        if "map_lookahead" in message and "lookahead" not in legacy:
            legacy["lookahead"] = message.get("map_lookahead")
        return legacy

    if phase_key == "combat":
        legacy = _dict_payload(message.get("combat"))
        if "enemies" in legacy and "monsters" not in legacy:
            legacy["monsters"] = legacy.get("enemies")
        return legacy

    if phase_key == "card_reward":
        cards = message.get("card_rewards")
        if cards is not None:
            legacy = {"cards": cards}
            if "can_skip" in message:
                legacy["can_skip"] = message.get("can_skip")
            return legacy
        return _dict_payload(message.get("card_reward"))

    if phase_key == "event":
        event_payload = _dict_payload(message.get("event"))
        if "options" not in event_payload and "event_options" in message:
            event_payload["options"] = message.get("event_options")
        return event_payload

    if phase_key == "rest":
        return _dict_payload(message.get("rest"))

    if phase_key == "shop":
        shop_payload = _dict_payload(message.get("shop"))
        if "cards" not in shop_payload and "shop_cards" in message:
            shop_payload["cards"] = message.get("shop_cards")
        if "relics" not in shop_payload and "shop_relics" in message:
            shop_payload["relics"] = message.get("shop_relics")
        if "potions" not in shop_payload and "shop_potions" in message:
            shop_payload["potions"] = message.get("shop_potions")
        if "remove_cost" not in shop_payload and "shop_remove_cost" in message:
            shop_payload["remove_cost"] = message.get("shop_remove_cost")
        return shop_payload

    if phase_key == "boss_relic":
        choices = message.get("boss_relic_choices")
        if choices is not None:
            return {"choices": choices}
        return _dict_payload(message.get("boss_relic"))

    if phase_key == "treasure":
        return _dict_payload(message.get("treasure"))

    return {}



def _normalize_map_state(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    map_payload: dict[str, Any] = {}
    ui_payload: dict[str, Any] = {}
    choices = _list_payload(payload.get("choices"))

    if choices:
        map_payload["available_next"] = list(range(len(choices)))
        ui_payload["map_choices_enabled"] = [_item_enabled(choice) for choice in choices]
        if "lookahead" not in payload:
            map_payload["lookahead"] = [[
                {
                    "room_type": (
                        _dict_payload(choice.get("node")).get("room_type")
                        or _dict_payload(choice.get("node")).get("type")
                        or choice.get("room_type")
                        or choice.get("type")
                        or choice.get("room")
                        or "monster"
                    ),
                    "children": _dict_payload(choice.get("node")).get("children", choice.get("children", [])),
                }
                if isinstance(choice, dict)
                else choice
                for choice in choices
            ]]

    if "available_next" in payload and "available_next" not in map_payload:
        map_payload["available_next"] = payload.get("available_next")
    if "lookahead" in payload:
        map_payload["lookahead"] = payload.get("lookahead")

    if map_payload:
        out["map"] = map_payload
    if ui_payload:
        out["_bridge_ui"] = ui_payload
    return out



def _normalize_combat_state(payload: dict[str, Any]) -> dict[str, Any]:
    combat_payload = dict(payload)
    if "enemies" in combat_payload and "monsters" not in combat_payload:
        combat_payload["monsters"] = combat_payload.get("enemies")
    if not combat_payload:
        return {}
    out: dict[str, Any] = {"combat": combat_payload}
    ui_payload: dict[str, Any] = {}

    if "playable_cards" in payload:
        ui_payload["combat_playable_cards"] = [
            bool(item) for item in _list_payload(payload.get("playable_cards"))
        ]
    if "selectable_cards" in payload:
        ui_payload["combat_selectable_cards"] = [
            bool(item) for item in _list_payload(payload.get("selectable_cards"))
        ]
    if "selected_cards" in payload:
        ui_payload["combat_selected_cards"] = [
            bool(item) for item in _list_payload(payload.get("selected_cards"))
        ]
    if "end_turn_enabled" in payload:
        ui_payload["combat_end_turn_enabled"] = bool(payload.get("end_turn_enabled"))
    if "selection_mode" in payload:
        ui_payload["combat_selection_mode"] = str(payload.get("selection_mode"))
    if "selection_confirm_enabled" in payload:
        ui_payload["combat_selection_confirm_enabled"] = bool(payload.get("selection_confirm_enabled"))
    if "selection_min" in payload:
        ui_payload["combat_selection_min"] = int(payload.get("selection_min"))
    if "selection_max" in payload:
        ui_payload["combat_selection_max"] = int(payload.get("selection_max"))
    if "selection_manual_confirm" in payload:
        ui_payload["combat_selection_manual_confirm"] = bool(payload.get("selection_manual_confirm"))
    if "selection_selected_count" in payload:
        ui_payload["combat_selection_selected_count"] = int(payload.get("selection_selected_count"))

    if ui_payload:
        out["_bridge_ui"] = ui_payload
    return out



def _normalize_card_reward_state(payload: dict[str, Any]) -> dict[str, Any]:
    cards = _list_payload(payload.get("cards", payload.get("card_rewards")))
    out: dict[str, Any] = {}
    if cards:
        out["card_rewards"] = cards
        out["_bridge_ui"] = {"card_reward_enabled": [_item_enabled(card) for card in cards]}
    if "can_skip" in payload:
        out.setdefault("_bridge_ui", {})["card_reward_can_skip"] = bool(payload.get("can_skip"))
    return out



def _normalize_event_state(payload: dict[str, Any]) -> dict[str, Any]:
    options = _list_payload(payload.get("options", payload.get("event_options")))
    if not options:
        return {}
    return {
        "event_options": options,
        "_bridge_ui": {"event_enabled": [_item_enabled(option) for option in options]},
    }



def _normalize_rest_state(payload: dict[str, Any]) -> dict[str, Any]:
    options = _list_payload(payload.get("options"))
    if not options:
        return {}

    enabled_by_id: dict[str, bool] = {}
    for option in options:
        if isinstance(option, str):
            enabled_by_id[_phase_schema_key(option)] = True
            continue
        option_payload = _dict_payload(option)
        option_id = option_payload.get("id") or option_payload.get("type")
        if option_id is None:
            continue
        enabled_by_id[_phase_schema_key(option_id)] = _item_enabled(option)

    if not enabled_by_id:
        return {}
    return {"_bridge_ui": {"rest_enabled_by_id": enabled_by_id}}



def _normalize_shop_state(payload: dict[str, Any]) -> dict[str, Any]:
    cards = _list_payload(payload.get("cards", payload.get("shop_cards")))
    relics = _list_payload(payload.get("relics", payload.get("shop_relics")))
    potions = _list_payload(payload.get("potions", payload.get("shop_potions")))
    out: dict[str, Any] = {}
    shop_payload: dict[str, Any] = {}
    ui_payload: dict[str, Any] = {}

    if cards:
        shop_payload["cards"] = cards
        ui_payload["shop_cards_enabled"] = [_item_enabled(card) for card in cards]
    if relics:
        shop_payload["relics"] = relics
        ui_payload["shop_relics_enabled"] = [_item_enabled(relic) for relic in relics]
    if potions:
        shop_payload["potions"] = potions
    if "remove_cost" in payload:
        shop_payload["remove_cost"] = payload.get("remove_cost")
    elif "shop_remove_cost" in payload:
        shop_payload["remove_cost"] = payload.get("shop_remove_cost")

    remove_payload = _dict_payload(payload.get("remove"))
    if remove_payload and "cost" in remove_payload and "remove_cost" not in shop_payload:
        shop_payload["remove_cost"] = remove_payload.get("cost")

    if "remove_enabled" in payload:
        ui_payload["shop_remove_enabled"] = bool(payload.get("remove_enabled"))
    elif "enabled" in remove_payload:
        ui_payload["shop_remove_enabled"] = bool(remove_payload.get("enabled"))

    if "leave_enabled" in payload:
        ui_payload["shop_leave_enabled"] = bool(payload.get("leave_enabled"))

    if shop_payload:
        out["shop"] = shop_payload
    if ui_payload:
        out["_bridge_ui"] = ui_payload
    return out



def _normalize_boss_relic_state(payload: dict[str, Any]) -> dict[str, Any]:
    choices = _list_payload(payload.get("choices", payload.get("boss_relic_choices")))
    if not choices:
        return {}

    choice_ids: list[str] = []
    enabled_flags: list[bool] = []
    for choice in choices:
        if isinstance(choice, dict):
            choice_id = choice.get("id") or choice.get("relic_id")
            if not choice_id:
                raise ValueError(f"Boss 遗物选项缺少 id: {choice!r}")
            choice_ids.append(str(choice_id))
            enabled_flags.append(_item_enabled(choice))
        else:
            choice_ids.append(str(choice))
            enabled_flags.append(True)

    return {
        "boss_relic_choices": choice_ids,
        "_bridge_ui": {"boss_relic_enabled": enabled_flags},
    }



def _normalize_treasure_state(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "can_proceed" in payload:
        out["_bridge_ui"] = {"treasure_proceed_enabled": bool(payload.get("can_proceed"))}
    return out



def _normalize_phase_payload(phase: Any, payload: dict[str, Any]) -> dict[str, Any]:
    phase_key = _phase_schema_key(phase)

    if phase_key == "map":
        return _normalize_map_state(payload)
    if phase_key == "combat":
        return _normalize_combat_state(payload)
    if phase_key == "card_reward":
        return _normalize_card_reward_state(payload)
    if phase_key == "event":
        return _normalize_event_state(payload)
    if phase_key == "rest":
        return _normalize_rest_state(payload)
    if phase_key == "shop":
        return _normalize_shop_state(payload)
    if phase_key == "boss_relic":
        return _normalize_boss_relic_state(payload)
    if phase_key == "treasure":
        return _normalize_treasure_state(payload)
    return {}



def _resolve_controlled_model_path(
    payload: dict[str, Any],
    *,
    default_character: str | None,
    default_model_path: str | Path | None,
    control_state_store: BridgeControlStateStore | None,
) -> str | Path | None:
    if payload.get("model_path") is not None:
        return payload.get("model_path")

    character = payload.get("character") or default_character
    if character is None:
        return default_model_path

    if control_state_store is not None:
        state = control_state_store.load()
        override = state.effective_model_path(str(character))
        if override is not None:
            return override

    return default_model_path



def _control_response(
    response_type: str,
    *,
    request_id: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    response = {
        "ok": True,
        "type": response_type,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
    }
    response.update(payload)
    return response



def _apply_bridge_control(
    payload: dict[str, Any],
    *,
    default_character: str | None = None,
    control_state_store: BridgeControlStateStore | None = None,
) -> dict[str, Any] | None:
    if control_state_store is None:
        return None

    message_type = payload.get("type")
    if message_type in {"ping", "describe", "load", "shutdown"}:
        return None

    state = control_state_store.load()
    request_id = payload.get("request_id")

    if state.paused:
        return _control_response("idle", request_id=request_id, reason="paused")

    desired_character = _canonicalize_character(state.desired_character)
    current_character = _canonicalize_character(payload.get("character") or default_character)
    if desired_character and current_character and str(current_character) != desired_character:
        return _control_response(
            "restart_required",
            request_id=request_id,
            target_character=desired_character,
            current_character=str(current_character),
        )

    return None


def normalize_state_envelope(
    message: dict[str, Any],
    *,
    default_character: str | None = None,
) -> dict[str, Any]:
    phase = message.get("phase")
    if phase is None:
        raise ValueError("缺少 phase")

    normalized: dict[str, Any] = {"phase": phase}
    character = _canonicalize_character(message.get("character") or default_character)
    if character is not None:
        normalized["character"] = str(character)

    run_payload = _dict_payload(message.get("run"))
    if "act" in run_payload or "act" in message:
        normalized["act"] = int(run_payload.get("act", message.get("act", 1)))
    if "floor" in run_payload or "floor" in message:
        normalized["floor"] = int(run_payload.get("floor", message.get("floor", 0)))
    if "won" in run_payload or "won" in message:
        normalized["won"] = bool(run_payload.get("won", message.get("won", False)))

    if "player" in message:
        normalized["player"] = _dict_payload(message.get("player"))
    if "deck" in message:
        normalized["deck"] = message.get("deck")

    for key in ("request_id", "model_path", "schema_version", "meta", "deterministic"):
        if key in message:
            normalized[key] = message.get(key)

    state_payload = _dict_payload(message.get("state"))
    phase_payload = _phase_section(state_payload, phase)
    if not phase_payload:
        phase_payload = _legacy_phase_payload(message, phase)

    phase_fields = _normalize_phase_payload(phase, phase_payload)
    ui_payload = _dict_payload(phase_fields.pop("_bridge_ui", None))

    if _phase_schema_key(phase) != "combat":
        combat_payload = _phase_section(state_payload, "combat")
        if not combat_payload:
            combat_payload = _legacy_phase_payload(message, "combat")
        if combat_payload:
            combat_fields = _normalize_combat_state(combat_payload)
            ui_payload.update(_dict_payload(combat_fields.pop("_bridge_ui", None)))
            normalized.update(combat_fields)

    normalized.update(phase_fields)
    if ui_payload:
        normalized["_bridge_ui"] = ui_payload

    return normalized



def _apply_ui_action_mask_overrides(mask: Any, payload: dict[str, Any]):
    ui_payload = _dict_payload(payload.get("_bridge_ui"))
    if not ui_payload:
        return mask

    if "combat_selectable_cards" in ui_payload:
        for card_idx in range(10):
            for target_idx in range(5):
                mask[A_PLAY_START + card_idx * 5 + target_idx] = False

        for potion_action in range(A_POTION_START, A_POTION_END + 1):
            mask[potion_action] = False

        for card_idx, enabled in enumerate(_list_payload(ui_payload.get("combat_selectable_cards"))[:10]):
            if enabled:
                mask[A_PLAY_START + card_idx * 5] = True

        mask[A_END_TURN] = bool(ui_payload.get("combat_selection_confirm_enabled", False))
        if not mask.any():
            mask[A_END_TURN] = True

    elif "combat_playable_cards" in ui_payload:
        for card_idx, enabled in enumerate(_list_payload(ui_payload.get("combat_playable_cards"))[:10]):
            if enabled:
                continue
            for target_idx in range(5):
                mask[A_PLAY_START + card_idx * 5 + target_idx] = False

    if (
        "combat_end_turn_enabled" in ui_payload
        and not bool(ui_payload.get("combat_end_turn_enabled"))
        and not bool(ui_payload.get("combat_selection_confirm_enabled", False))
    ):
        tightened = mask.copy()
        tightened[A_END_TURN] = False
        if tightened.any():
            mask[A_END_TURN] = False

    if "map_choices_enabled" in ui_payload:
        for idx, enabled in enumerate(_list_payload(ui_payload.get("map_choices_enabled"))[:4]):
            if not enabled:
                mask[A_MAP_START + idx] = False

    if "card_reward_enabled" in ui_payload:
        for idx, enabled in enumerate(_list_payload(ui_payload.get("card_reward_enabled"))[:3]):
            if not enabled:
                mask[A_PICK_START + idx] = False
    if "card_reward_can_skip" in ui_payload and not bool(ui_payload.get("card_reward_can_skip")):
        mask[A_SKIP] = False

    if "event_enabled" in ui_payload:
        for idx, enabled in enumerate(_list_payload(ui_payload.get("event_enabled"))[:4]):
            if not enabled:
                mask[A_EVENT_START + idx] = False

    rest_enabled_by_id = _dict_payload(ui_payload.get("rest_enabled_by_id"))
    if rest_enabled_by_id:
        rest_action_map = {
            "rest": A_REST,
            "upgrade": A_UPGRADE,
            "dig": A_DIG,
            "cook": A_COOK,
            "lift": A_LIFT,
        }
        for option_id, action in rest_action_map.items():
            if not bool(rest_enabled_by_id.get(option_id, False)):
                mask[action] = False

    if "shop_cards_enabled" in ui_payload:
        for idx, enabled in enumerate(_list_payload(ui_payload.get("shop_cards_enabled"))[:3]):
            if not enabled:
                mask[A_SHOP_CARD_START + idx] = False
    if "shop_relics_enabled" in ui_payload:
        for idx, enabled in enumerate(_list_payload(ui_payload.get("shop_relics_enabled"))[:3]):
            if not enabled:
                mask[A_SHOP_RELIC_START + idx] = False
    if "shop_remove_enabled" in ui_payload and not bool(ui_payload.get("shop_remove_enabled")):
        mask[A_SHOP_REMOVE] = False
    if "shop_leave_enabled" in ui_payload and not bool(ui_payload.get("shop_leave_enabled")):
        mask[A_SHOP_LEAVE] = False

    if "boss_relic_enabled" in ui_payload:
        for idx, enabled in enumerate(_list_payload(ui_payload.get("boss_relic_enabled"))[:3]):
            if not enabled:
                mask[A_BOSS_START + idx] = False

    if "treasure_proceed_enabled" in ui_payload and not bool(ui_payload.get("treasure_proceed_enabled")):
        mask[A_SKIP] = False

    return mask


def build_game_state_from_payload(payload: dict[str, Any], *, character: str) -> GameState:
    """将外部状态 payload 重建为可编码的 GameState。"""
    phase = _coerce_phase(payload["phase"])
    gs = GameState(character=character)
    gs.phase = phase
    gs.act = int(payload.get("act", 1))
    gs.floor = int(payload.get("floor", 0))
    gs.won = bool(payload.get("won", False))

    player_payload = payload.get("player", {})
    player = _build_player(player_payload, character=character)
    gs.player = player

    deck_payload = payload.get("deck")
    if deck_payload is None:
        deck_payload = _derive_deck_payload(player_payload)
    if deck_payload:
        gs.deck = _build_cards(deck_payload)

    gs.map_nodes = _build_map_nodes(payload, floor=gs.floor)
    gs.available_next = _build_available_next(payload, floor=gs.floor, map_nodes=gs.map_nodes)
    gs.current_node = None

    gs.card_rewards = _build_cards(payload.get("card_rewards", []))
    gs.event_options = list(payload.get("event_options", []))

    shop_payload = payload.get("shop", {}) if isinstance(payload.get("shop"), dict) else {}
    gs.shop_cards = _build_cards(shop_payload.get("cards", payload.get("shop_cards", [])))
    gs.shop_relics = list(shop_payload.get("relics", payload.get("shop_relics", [])))
    gs.shop_potions = list(shop_payload.get("potions", payload.get("shop_potions", [])))
    gs.shop_remove_cost = int(shop_payload.get("remove_cost", payload.get("shop_remove_cost", gs.shop_remove_cost)))
    gs.boss_relic_choices = [str(item) for item in payload.get("boss_relic_choices", [])]

    if phase == GamePhase.COMBAT or "combat" in payload:
        gs.combat = _build_combat(
            player,
            payload.get("combat", {}),
            ui_payload=_dict_payload(payload.get("_bridge_ui")),
        )
    else:
        gs.combat = None

    return gs


def raw_state_to_act_message(
    message: dict[str, Any],
    *,
    default_character: str | None = None,
    default_model_path: str | Path | None = None,
) -> dict[str, Any]:
    """将原始游戏状态消息转换为桥接 act 请求。"""
    character = _canonicalize_character(message.get("character") or default_character)
    if not character:
        raise ValueError("缺少 character")

    game_state = build_game_state_from_payload(message, character=str(character))
    env = StsEnv(character=str(character))
    env.gs = game_state
    action_mask = _apply_ui_action_mask_overrides(env.action_masks(), message).astype(bool).tolist()

    normalized = {
        "type": "act",
        "character": str(character),
        "observation": encode_observation(game_state).astype(float).tolist(),
        "action_mask": action_mask,
    }

    if message.get("request_id") is not None:
        normalized["request_id"] = message.get("request_id")

    normalized["deterministic"] = bool(message.get("deterministic", False))

    model_path = message.get("model_path") or default_model_path
    if model_path is not None:
        normalized["model_path"] = str(model_path)

    return normalized


def decode_action(action: int) -> dict[str, Any]:
    """将环境离散动作 ID 转换为游戏侧更易消费的结构化决策。"""
    if not 0 <= action < TOTAL_ACTIONS:
        raise ValueError(f"动作越界: {action}")

    if A_PLAY_START <= action <= A_PLAY_END:
        rel = action - A_PLAY_START
        return {"type": "play_card", "card_index": rel // 5, "target_index": rel % 5}

    if action == A_END_TURN:
        return {"type": "end_turn"}

    if A_POTION_START <= action <= A_POTION_END:
        rel = action - A_POTION_START
        return {"type": "use_potion", "potion_index": rel // 5, "target_index": rel % 5}

    if A_MAP_START <= action <= A_MAP_END:
        return {"type": "choose_path", "index": action - A_MAP_START}

    if A_PICK_START <= action <= A_PICK_END:
        return {"type": "pick_card", "index": action - A_PICK_START}

    if action == A_SKIP:
        return {"type": "skip"}

    if action == A_REST:
        return {"type": "rest"}

    if action == A_UPGRADE:
        return {"type": "upgrade"}

    if action == A_DIG:
        return {"type": "dig"}

    if action == A_COOK:
        return {"type": "cook"}

    if action == A_LIFT:
        return {"type": "lift"}

    if A_SHOP_CARD_START <= action <= A_SHOP_CARD_END:
        return {"type": "buy_card", "index": action - A_SHOP_CARD_START}

    if A_SHOP_RELIC_START <= action <= A_SHOP_RELIC_END:
        return {"type": "buy_relic", "index": action - A_SHOP_RELIC_START}

    if action == A_SHOP_REMOVE:
        return {"type": "remove_card"}

    if action == A_SHOP_LEAVE:
        return {"type": "leave_shop"}

    if A_EVENT_START <= action <= A_EVENT_END:
        return {"type": "choose_option", "index": action - A_EVENT_START}

    if A_BOSS_START <= action <= A_BOSS_END:
        return {"type": "choose_boss_relic", "index": action - A_BOSS_START}

    raise ValueError(f"未识别动作: {action}")


def normalize_bridge_message(
    message: dict[str, Any],
    *,
    default_character: str | None = None,
    default_model_path: str | Path | None = None,
    control_state_store: BridgeControlStateStore | None = None,
) -> dict[str, Any]:
    """将 WebSocket 侧消息补齐为桥接服务可接受的协议格式。"""
    normalized = dict(message)
    message_type = normalized.get("type")

    if message_type == "state":
        state_message = normalize_state_envelope(normalized, default_character=default_character)
        controlled_model_path = _resolve_controlled_model_path(
            state_message,
            default_character=default_character,
            default_model_path=default_model_path,
            control_state_store=control_state_store,
        )
        return raw_state_to_act_message(
            state_message,
            default_character=default_character,
            default_model_path=controlled_model_path,
        )

    if message_type == "raw_state":
        controlled_model_path = _resolve_controlled_model_path(
            normalized,
            default_character=default_character,
            default_model_path=default_model_path,
            control_state_store=control_state_store,
        )
        return raw_state_to_act_message(
            normalized,
            default_character=default_character,
            default_model_path=controlled_model_path,
        )

    if "type" not in normalized:
        if "observation" in normalized and "action_mask" in normalized:
            normalized["type"] = "act"
        elif "phase" in normalized:
            state_message = normalize_state_envelope(normalized, default_character=default_character)
            controlled_model_path = _resolve_controlled_model_path(
                state_message,
                default_character=default_character,
                default_model_path=default_model_path,
                control_state_store=control_state_store,
            )
            return raw_state_to_act_message(
                state_message,
                default_character=default_character,
                default_model_path=controlled_model_path,
            )
        else:
            raise ValueError("缺少 type；请发送桥接协议消息，或直接提供 observation 与 action_mask")

    if normalized["type"] == "act" and "phase" in normalized and (
        "observation" not in normalized or "action_mask" not in normalized
    ):
        state_message = normalize_state_envelope(normalized, default_character=default_character)
        controlled_model_path = _resolve_controlled_model_path(
            state_message,
            default_character=default_character,
            default_model_path=default_model_path,
            control_state_store=control_state_store,
        )
        return raw_state_to_act_message(
            state_message,
            default_character=default_character,
            default_model_path=controlled_model_path,
        )

    if normalized["type"] in {"act", "load"}:
        if "character" not in normalized:
            if default_character is None:
                raise ValueError("缺少 character")
            normalized["character"] = default_character
        normalized["character"] = _canonicalize_character(normalized.get("character"))
        controlled_model_path = _resolve_controlled_model_path(
            normalized,
            default_character=default_character,
            default_model_path=default_model_path,
            control_state_store=control_state_store,
        )
        if controlled_model_path is not None and "model_path" not in normalized:
            normalized["model_path"] = str(controlled_model_path)

    return normalized


def websocket_error(
    error: str,
    *,
    request_id: str | None = None,
    code: str = "bad_message",
) -> dict[str, Any]:
    return {
        "ok": False,
        "type": "error",
        "protocol_version": PROTOCOL_VERSION,
        "code": code,
        "error": error,
        "request_id": request_id,
    }


def adapt_response_for_websocket(response: dict[str, Any]) -> dict[str, Any]:
    """为游戏侧补充结构化动作描述。"""
    adapted = dict(response)
    if adapted.get("ok") and adapted.get("type") == "action" and "action" in adapted:
        try:
            adapted["decision"] = decode_action(int(adapted["action"]))
        except Exception as exc:
            return websocket_error(
                f"动作解码失败: {exc}",
                request_id=adapted.get("request_id"),
                code="action_decode_error",
            )
    return adapted


def process_websocket_message(
    server: BridgeServer,
    raw_message: str,
    *,
    default_character: str | None = None,
    default_model_path: str | Path | None = None,
    control_state_store: BridgeControlStateStore | None = None,
) -> dict[str, Any]:
    """处理单条 WebSocket 文本消息。"""
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        response = websocket_error(f"JSON 解析失败: {exc}", code="json_error")
        if control_state_store is not None:
            control_state_store.record_bridge_result(response)
        return response

    if not isinstance(payload, dict):
        response = websocket_error("消息必须是 JSON 对象", code="bad_message")
        if control_state_store is not None:
            control_state_store.record_bridge_result(response)
        return response

    request_id = payload.get("request_id")
    control_response = _apply_bridge_control(
        payload,
        default_character=default_character,
        control_state_store=control_state_store,
    )
    if control_response is not None:
        if control_state_store is not None:
            control_state_store.record_bridge_result(control_response)
        return control_response

    try:
        message = normalize_bridge_message(
            payload,
            default_character=default_character,
            default_model_path=default_model_path,
            control_state_store=control_state_store,
        )
    except Exception as exc:
        response = websocket_error(str(exc), request_id=request_id, code="bad_message")
        if control_state_store is not None:
            control_state_store.record_bridge_result(response)
        return response

    response = adapt_response_for_websocket(server.handle_message(message))
    if control_state_store is not None:
        control_state_store.record_bridge_result(response)
    return response


async def handle_connection(
    websocket,
    server: BridgeServer,
    *,
    default_character: str | None = None,
    default_model_path: str | Path | None = None,
    control_state_store: BridgeControlStateStore | None = None,
):
    """处理单个游戏连接。"""
    async for raw_message in websocket:
        response = process_websocket_message(
            server,
            raw_message,
            default_character=default_character,
            default_model_path=default_model_path,
            control_state_store=control_state_store,
        )
        await websocket.send(json.dumps(response, ensure_ascii=False))
        if response.get("type") == "shutdown" and response.get("ok"):
            break


async def run_bridge(
    host: str = "localhost",
    port: int = 8765,
    *,
    default_character: str | None = None,
    default_model_path: str | Path | None = None,
    preload: list[str] | None = None,
    preload_all: bool = False,
    control_state_path: str | Path | None = None,
):
    import websockets

    server = BridgeServer()
    control_state_store = BridgeControlStateStore(control_state_path) if control_state_path is not None else None
    if control_state_store is not None:
        control_state_store.ensure_initialized(desired_character=default_character)
    if default_character is not None and default_model_path is not None:
        server.registry.get_runtime(default_character, default_model_path)

    preload_characters: list[str] = []
    if preload_all:
        preload_characters = list(CHARACTERS)
    elif preload:
        preload_characters = list(preload)

    if preload_characters:
        server.preload(preload_characters)

    async def _handler(websocket, *_args):
        await handle_connection(
            websocket,
            server,
            default_character=default_character,
            default_model_path=default_model_path,
            control_state_store=control_state_store,
        )

    print(f"等待游戏连接 ws://{host}:{port} ...")
    async with websockets.serve(_handler, host, port):
        await asyncio.Future()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 STS Agent WebSocket 桥接服务")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--character", choices=CHARACTERS, default=None, help="默认角色")
    parser.add_argument("--model", default=None, help="默认模型路径")
    parser.add_argument("--preload-all", action="store_true", help="启动时预加载全部角色默认模型")
    parser.add_argument("--preload", nargs="*", choices=CHARACTERS, default=None, help="预加载指定角色默认模型")
    parser.add_argument("--control-state", default=None, help="UI 与 bridge 共享的控制状态 JSON 路径")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.model and not args.character:
        parser.error("--model 需要配合 --character 一起使用")

    asyncio.run(
        run_bridge(
            args.host,
            args.port,
            default_character=args.character,
            default_model_path=args.model,
            preload=args.preload,
            preload_all=args.preload_all,
            control_state_path=args.control_state,
        )
    )


if __name__ == "__main__":
    main()
