"""怪物AI状态机：基于反编译数据实现怪物行为模式。"""
from __future__ import annotations
import json
import random
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sts_env.combat import Combat

from sts_env.combat import Monster, Intent, IntentType
from sts_env.powers import create_power

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_MONSTER_DB: dict[str, dict] = {}


def _load_monster_db():
    global _MONSTER_DB
    if _MONSTER_DB:
        return
    p = DATA_DIR / "monsters.json"
    if p.exists():
        for m in json.loads(p.read_text("utf-8")):
            _MONSTER_DB[m["id"]] = m


# ---------------------------------------------------------------------------
# 通用数据驱动怪物
# ---------------------------------------------------------------------------

class DataDrivenMonster(Monster):
    """从 monsters.json 数据驱动的通用怪物。"""

    def __init__(self, monster_id: str, hp_override: int | None = None):
        _load_monster_db()
        data = _MONSTER_DB.get(monster_id, {})
        hp_min = data.get("hp_min", 20)
        hp_max = data.get("hp_max", hp_min + 5)
        hp = hp_override or random.randint(hp_min, hp_max)
        super().__init__(monster_id, hp)
        self.data = data
        self.attacks = data.get("attacks", [])
        self.move_names = data.get("moves", [])
        self._move_idx = 0

    def roll_move(self, combat: Combat):
        if not self.attacks:
            self.intent = Intent(IntentType.ATTACK, damage=6)
            return
        # 简单循环招式
        atk = self.attacks[self._move_idx % len(self.attacks)]
        if atk["type"] == "multi":
            self.intent = Intent(IntentType.ATTACK, damage=atk["damage"], hits=atk.get("hits", 2))
        else:
            self.intent = Intent(IntentType.ATTACK, damage=atk["damage"])
        self._move_idx += 1


# ---------------------------------------------------------------------------
# 手写的关键怪物AI（忠实于反编译源码）
# ---------------------------------------------------------------------------

class JawWorm(Monster):
    """颚虫：经典Act1怪物。"""
    def __init__(self):
        super().__init__("JawWorm", random.randint(40, 44))

    def roll_move(self, combat: Combat):
        if self.turn_count == 0:
            self.intent = Intent(IntentType.ATTACK, damage=11)
            self._next = "chomp"
        else:
            r = random.random()
            if r < 0.45:
                self.intent = Intent(IntentType.ATTACK, damage=11)
                self._next = "chomp"
            elif r < 0.75:
                self.intent = Intent(IntentType.BUFF)
                self._next = "bellow"
            else:
                self.intent = Intent(IntentType.ATTACK, damage=7)
                self._next = "thrash"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        move = getattr(self, '_next', 'chomp')
        if move == "chomp":
            dmg = self._calc_attack_damage(11)
            combat.player.take_damage(dmg, attacker=self)
        elif move == "bellow":
            self.add_power(create_power("StrengthPower", 3, self))
            self.gain_block(6)
        elif move == "thrash":
            dmg = self._calc_attack_damage(7)
            combat.player.take_damage(dmg, attacker=self)
            self.gain_block(5)
        self.turn_count += 1


class Cultist(Monster):
    """邪教徒：每回合加力量。"""
    def __init__(self):
        super().__init__("Cultist", random.randint(48, 54))

    def roll_move(self, combat: Combat):
        if self.turn_count == 0:
            self.intent = Intent(IntentType.BUFF)
            self._next = "incantation"
        else:
            self.intent = Intent(IntentType.ATTACK, damage=6)
            self._next = "dark_strike"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        move = getattr(self, '_next', 'dark_strike')
        if move == "incantation":
            self.add_power(create_power("RitualPower", 3, self))
        elif move == "dark_strike":
            dmg = self._calc_attack_damage(6)
            combat.player.take_damage(dmg, attacker=self)
        self.turn_count += 1


class LouseRed(Monster):
    """红虱：攻击+上弱。"""
    def __init__(self):
        super().__init__("LouseRed", random.randint(10, 15))
        self._bite_dmg = random.randint(5, 7)

    def roll_move(self, combat: Combat):
        if random.random() < 0.75:
            self.intent = Intent(IntentType.ATTACK, damage=self._bite_dmg)
            self._next = "bite"
        else:
            self.intent = Intent(IntentType.BUFF)
            self._next = "grow"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        if getattr(self, '_next', '') == "bite":
            dmg = self._calc_attack_damage(self._bite_dmg)
            combat.player.take_damage(dmg, attacker=self)
        else:
            self.add_power(create_power("StrengthPower", 3, self))
        self.turn_count += 1


class LouseGreen(Monster):
    """绿虱：攻击+上弱。"""
    def __init__(self):
        super().__init__("LouseGreen", random.randint(11, 17))
        self._bite_dmg = random.randint(5, 7)

    def roll_move(self, combat: Combat):
        if random.random() < 0.75:
            self.intent = Intent(IntentType.ATTACK, damage=self._bite_dmg)
            self._next = "bite"
        else:
            self.intent = Intent(IntentType.DEBUFF)
            self._next = "spit_web"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        if getattr(self, '_next', '') == "bite":
            dmg = self._calc_attack_damage(self._bite_dmg)
            combat.player.take_damage(dmg, attacker=self)
        else:
            combat.player.add_power(create_power("WeakPower", 2, combat.player))
        self.turn_count += 1


class SlaverBlue(Monster):
    """蓝奴隶主。"""
    def __init__(self):
        super().__init__("SlaverBlue", random.randint(46, 50))

    def roll_move(self, combat: Combat):
        if self.turn_count == 0:
            self.intent = Intent(IntentType.ATTACK, damage=12)
            self._next = "stab"
        else:
            r = random.random()
            if r < 0.6:
                self.intent = Intent(IntentType.ATTACK, damage=12)
                self._next = "stab"
            else:
                self.intent = Intent(IntentType.DEBUFF)
                self._next = "rake"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        if getattr(self, '_next', '') == "stab":
            dmg = self._calc_attack_damage(12)
            combat.player.take_damage(dmg, attacker=self)
        else:
            dmg = self._calc_attack_damage(7)
            combat.player.take_damage(dmg, attacker=self)
            combat.player.add_power(create_power("WeakPower", 1, combat.player))
        self.turn_count += 1


class GremlinNob(Monster):
    """哥布林头目（Elite）。"""
    def __init__(self):
        super().__init__("GremlinNob", random.randint(82, 86))
        self._enraged = False

    def roll_move(self, combat: Combat):
        if self.turn_count == 0:
            self.intent = Intent(IntentType.BUFF)
            self._next = "bellow"
        elif random.random() < 0.67:
            self.intent = Intent(IntentType.ATTACK, damage=14)
            self._next = "rush"
        else:
            self.intent = Intent(IntentType.ATTACK, damage=6)
            self._next = "skull_bash"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        move = getattr(self, '_next', 'rush')
        if move == "bellow":
            self._enraged = True
        elif move == "rush":
            dmg = self._calc_attack_damage(14)
            combat.player.take_damage(dmg, attacker=self)
        elif move == "skull_bash":
            dmg = self._calc_attack_damage(6)
            combat.player.take_damage(dmg, attacker=self)
            combat.player.add_power(create_power("VulnerablePower", 2, combat.player))
        # Enrage: 玩家打 Skill 时加力量（简化为每回合加）
        if self._enraged:
            self.add_power(create_power("StrengthPower", 2, self))
        self.turn_count += 1


class Lagavulin(Monster):
    """拉格瓦林（Elite）：前3回合睡觉，之后攻击+debuff。"""
    def __init__(self):
        super().__init__("Lagavulin", random.randint(109, 111))
        self._asleep = True

    def roll_move(self, combat: Combat):
        if self._asleep and self.turn_count < 3:
            self.intent = Intent(IntentType.UNKNOWN)
            self._next = "sleep"
        elif self.turn_count % 3 == 0 or (self._asleep and self.turn_count >= 3):
            self.intent = Intent(IntentType.DEBUFF)
            self._next = "siphon_soul"
            self._asleep = False
        else:
            self.intent = Intent(IntentType.ATTACK, damage=18)
            self._next = "attack"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        move = getattr(self, '_next', 'attack')
        if move == "sleep":
            self.gain_block(8)
        elif move == "siphon_soul":
            combat.player.add_power(create_power("WeakPower", 1, combat.player))
            from sts_env.powers import DexterityPower
            dex = combat.player.get_power("DexterityPower")
            if dex:
                dex.stack(-1)
            else:
                combat.player.add_power(DexterityPower(-1, combat.player))
            str_p = combat.player.get_power("StrengthPower")
            if str_p:
                str_p.stack(-1)
            else:
                from sts_env.powers import StrengthPower
                combat.player.add_power(StrengthPower(-1, combat.player))
        elif move == "attack":
            dmg = self._calc_attack_damage(18)
            combat.player.take_damage(dmg, attacker=self)
        self.turn_count += 1


class Sentry(Monster):
    """哨兵。"""
    def __init__(self, start_with_bolt: bool = True):
        super().__init__("Sentry", random.randint(38, 42))
        self._bolt_turn = start_with_bolt

    def roll_move(self, combat: Combat):
        if self._bolt_turn:
            self.intent = Intent(IntentType.ATTACK, damage=9)
            self._next = "bolt"
        else:
            self.intent = Intent(IntentType.DEBUFF)
            self._next = "beam"

    def perform_move(self, combat: Combat):
        if self.is_dead:
            return
        if getattr(self, '_next', '') == "bolt":
            dmg = self._calc_attack_damage(9)
            combat.player.take_damage(dmg, attacker=self)
        else:
            from sts_env.combat import make_card
            for _ in range(2):
                combat.player.discard_pile.append(make_card("Dazed"))
        self._bolt_turn = not self._bolt_turn
        self.turn_count += 1


# ---------------------------------------------------------------------------
# 怪物工厂
# ---------------------------------------------------------------------------

MONSTER_CLASSES: dict[str, type] = {
    "JawWorm": JawWorm,
    "Cultist": Cultist,
    "LouseRed": LouseRed,
    "LouseGreen": LouseGreen,
    "SlaverBlue": SlaverBlue,
    "GremlinNob": GremlinNob,
    "Lagavulin": Lagavulin,
    "Sentry": Sentry,
}


def create_monster(monster_id: str) -> Monster:
    cls = MONSTER_CLASSES.get(monster_id)
    if cls:
        return cls()
    return DataDrivenMonster(monster_id)
