"""Power（增益/减益）系统实现。"""
from __future__ import annotations
import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sts_env.combat import Card, Creature, Player


class PowerType(Enum):
    BUFF = "buff"
    DEBUFF = "debuff"


class StackType(Enum):
    COUNTER = "counter"
    DURATION = "duration"


@dataclass
class Power:
    power_id: str
    amount: int = 0
    power_type: PowerType = PowerType.BUFF
    stack_type: StackType = StackType.COUNTER
    owner: Creature | None = None

    def stack(self, amt: int):
        self.amount += amt
        if self.amount > 9999:
            self.amount = 9999

    def tick_duration(self):
        if self.stack_type == StackType.DURATION:
            self.amount -= 1

    @property
    def should_remove(self) -> bool:
        if self.stack_type == StackType.DURATION:
            return self.amount <= 0
        return False


# ---------------------------------------------------------------------------
# 具体 Power 定义
# ---------------------------------------------------------------------------

POWER_REGISTRY: dict[str, type[Power]] = {}


def register_power(cls: type[Power]) -> type[Power]:
    POWER_REGISTRY[cls.__name__] = cls
    return cls


@register_power
class StrengthPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("StrengthPower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def modify_damage(self, base_damage: int) -> int:
        return base_damage + self.amount


@register_power
class DexterityPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("DexterityPower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def modify_block(self, base_block: int) -> int:
        return base_block + self.amount


@register_power
class VulnerablePower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("VulnerablePower", amount, PowerType.DEBUFF, StackType.COUNTER, owner)

    def modify_damage_received(self, damage: int) -> int:
        return int(damage * 1.5)


@register_power
class WeakPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("WeakPower", amount, PowerType.DEBUFF, StackType.COUNTER, owner)

    def modify_damage_dealt(self, damage: int) -> int:
        return int(damage * 0.75)


@register_power
class FrailPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("FrailPower", amount, PowerType.DEBUFF, StackType.COUNTER, owner)

    def modify_block_gained(self, block: int) -> int:
        return int(block * 0.75)


@register_power
class PoisonPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("PoisonPower", amount, PowerType.DEBUFF, StackType.COUNTER, owner)

    def on_turn_start(self) -> int:
        """返回毒伤害值，然后递减。"""
        dmg = self.amount
        self.amount -= 1
        return dmg


@register_power
class RitualPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("RitualPower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def on_turn_end(self, creature: Creature):
        str_power = creature.get_power("StrengthPower")
        if str_power:
            str_power.stack(self.amount)
        else:
            creature.add_power(StrengthPower(self.amount, creature))


@register_power
class ArtifactPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("ArtifactPower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def try_block_debuff(self) -> bool:
        if self.amount > 0:
            self.amount -= 1
            return True
        return False


@register_power
class BarricadePower(Power):
    def __init__(self, amount: int = 1, owner: Creature | None = None):
        super().__init__("BarricadePower", amount, PowerType.BUFF, StackType.COUNTER, owner)


@register_power
class MetallicizePower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("MetallicizePower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def on_turn_end_block(self) -> int:
        return self.amount


@register_power
class ThornsPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("ThornsPower", amount, PowerType.BUFF, StackType.COUNTER, owner)


@register_power
class RegenPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("RegenPower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def on_turn_start_heal(self) -> int:
        heal = self.amount
        self.amount -= 1
        return heal


@register_power
class PlatedArmorPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("PlatedArmorPower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def on_turn_end_block(self) -> int:
        return self.amount

    def on_attacked(self):
        self.amount = max(0, self.amount - 1)


@register_power
class CorruptionPower(Power):
    def __init__(self, amount: int = 1, owner: Creature | None = None):
        super().__init__("CorruptionPower", amount, PowerType.BUFF, StackType.COUNTER, owner)


@register_power
class FeelNoPainPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("FeelNoPainPower", amount, PowerType.BUFF, StackType.COUNTER, owner)


@register_power
class DemonFormPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("DemonFormPower", amount, PowerType.BUFF, StackType.COUNTER, owner)

    def on_turn_start_buff(self, creature: Creature):
        str_power = creature.get_power("StrengthPower")
        if str_power:
            str_power.stack(self.amount)
        else:
            creature.add_power(StrengthPower(self.amount, creature))


@register_power
class NoxiousFumesPower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("NoxiousFumesPower", amount, PowerType.BUFF, StackType.COUNTER, owner)


@register_power
class IntangiblePower(Power):
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("IntangiblePower", amount, PowerType.BUFF, StackType.DURATION, owner)

    def modify_damage_received(self, damage: int) -> int:
        return 1 if damage > 0 else 0


@register_power
class DrawCardPower(Power):
    """每回合额外抽牌。"""
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("DrawCardPower", amount, PowerType.BUFF, StackType.COUNTER, owner)


@register_power
class EnergizedPower(Power):
    """下回合额外能量。"""
    def __init__(self, amount: int = 0, owner: Creature | None = None):
        super().__init__("EnergizedPower", amount, PowerType.BUFF, StackType.COUNTER, owner)


@register_power
class NightmarePower(Power):
    """下回合抽牌前将选中的牌复制到手牌。"""

    def __init__(
        self,
        amount: int = 3,
        owner: Creature | None = None,
        selected_card: Card | None = None,
    ):
        super().__init__("NightmarePower", amount, PowerType.BUFF, StackType.COUNTER, owner)
        self.selected_card = copy.deepcopy(selected_card) if selected_card is not None else None

    def set_selected_card(self, card: Card):
        self.selected_card = copy.deepcopy(card)

    def before_hand_draw(self, player: Player):
        if self.selected_card is None:
            return
        for _ in range(max(0, self.amount)):
            if len(player.hand) >= 10:
                break
            clone = copy.deepcopy(self.selected_card)
            clone.single_turn_sly = False
            player.hand.append(clone)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def create_power(power_id: str, amount: int, owner: Creature | None = None) -> Power:
    cls = POWER_REGISTRY.get(power_id)
    if cls:
        return cls(amount, owner)
    return Power(power_id, amount, owner=owner)
