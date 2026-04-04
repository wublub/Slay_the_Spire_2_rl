"""战斗系统测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sts_env.combat import (
    Combat, CombatResult, Player, Monster, Card, CardType, TargetType, make_card,
)
from sts_env.monster_ai import JawWorm, Cultist, create_monster
from sts_env.powers import StrengthPower, VulnerablePower, WeakPower, PoisonPower


def test_basic_combat_setup():
    """测试基础战斗初始化。"""
    player = Player("Ironclad", 80)
    deck = [make_card("StrikeIronclad") for _ in range(5)] + \
           [make_card("DefendIronclad") for _ in range(4)] + \
           [make_card("Bash")]
    player.init_deck(deck)
    monster = JawWorm()
    combat = Combat(player, [monster])
    combat.start_combat()

    assert combat.phase.name == "PLAYER_TURN"
    assert len(player.hand) == 5
    assert player.energy == 3
    assert not combat.is_over
    print("PASS: test_basic_combat_setup")


def test_play_strike():
    """测试打出 Strike。"""
    player = Player("Ironclad", 80)
    strike = make_card("StrikeIronclad")
    player.hand = [strike]
    player.energy = 3

    monster = Monster("TestMonster", 30)
    combat = Combat(player, [monster])
    combat.phase = combat.phase  # already PLAYER_TURN

    success = combat.play_card(0, 0)
    assert success
    assert monster.hp == 30 - 6  # Strike does 6 damage
    assert player.energy == 2
    assert len(player.hand) == 0
    print("PASS: test_play_strike")


def test_play_defend():
    """测试打出 Defend。"""
    player = Player("Ironclad", 80)
    defend = make_card("DefendIronclad")
    player.hand = [defend]
    player.energy = 3

    monster = Monster("TestMonster", 30)
    combat = Combat(player, [monster])

    success = combat.play_card(0, 0)
    assert success
    assert player.block == 5
    assert player.energy == 2
    print("PASS: test_play_defend")


def test_play_bash():
    """测试打出 Bash：伤害 + 上易伤。"""
    player = Player("Ironclad", 80)
    bash = make_card("Bash")
    player.hand = [bash]
    player.energy = 3

    monster = Monster("TestMonster", 30)
    combat = Combat(player, [monster])

    success = combat.play_card(0, 0)
    assert success
    assert monster.hp == 30 - 8  # Bash does 8
    vuln = monster.get_power("VulnerablePower")
    assert vuln is not None
    assert vuln.amount == 2
    assert player.energy == 1  # Bash costs 2
    print("PASS: test_play_bash")


def test_vulnerable_damage():
    """测试易伤增加伤害。"""
    player = Player("Ironclad", 80)
    strike = make_card("StrikeIronclad")
    player.hand = [strike]
    player.energy = 3

    monster = Monster("TestMonster", 50)
    monster.add_power(VulnerablePower(2, monster))
    combat = Combat(player, [monster])

    combat.play_card(0, 0)
    # Strike 6 * 1.5 = 9
    assert monster.hp == 50 - 9
    print("PASS: test_vulnerable_damage")


def test_weak_reduces_damage():
    """测试虚弱减少伤害。"""
    player = Player("Ironclad", 80)
    player.add_power(WeakPower(2, player))
    strike = make_card("StrikeIronclad")
    player.hand = [strike]
    player.energy = 3

    monster = Monster("TestMonster", 50)
    combat = Combat(player, [monster])

    combat.play_card(0, 0)
    # Strike 6 * 0.75 = 4
    assert monster.hp == 50 - 4
    print("PASS: test_weak_reduces_damage")


def test_strength_adds_damage():
    """测试力量增加伤害。"""
    player = Player("Ironclad", 80)
    player.add_power(StrengthPower(3, player))
    strike = make_card("StrikeIronclad")
    player.hand = [strike]
    player.energy = 3

    monster = Monster("TestMonster", 50)
    combat = Combat(player, [monster])

    combat.play_card(0, 0)
    # Strike 6 + 3 = 9
    assert monster.hp == 50 - 9
    print("PASS: test_strength_adds_damage")


def test_block_absorbs_damage():
    """测试格挡吸收伤害。"""
    player = Player("Ironclad", 80)
    player.block = 10

    monster = Monster("TestMonster", 30)
    player.take_damage(8, attacker=monster)
    assert player.hp == 80
    assert player.block == 2
    print("PASS: test_block_absorbs_damage")


def test_poison_damage():
    """测试毒伤害。"""
    monster = Monster("TestMonster", 30)
    monster.add_power(PoisonPower(5, monster))
    monster.start_turn()
    assert monster.hp == 30 - 5
    poison = monster.get_power("PoisonPower")
    assert poison.amount == 4
    print("PASS: test_poison_damage")


def test_combat_win():
    """测试战斗胜利。"""
    player = Player("Ironclad", 80)
    strike = make_card("StrikeIronclad")
    player.hand = [strike]
    player.energy = 3

    monster = Monster("TestMonster", 5)
    combat = Combat(player, [monster])

    combat.play_card(0, 0)
    assert monster.is_dead
    assert combat.result == CombatResult.WIN
    print("PASS: test_combat_win")


def test_combat_lose():
    """测试战斗失败。"""
    player = Player("Ironclad", 5)
    player.hand = []
    player.energy = 0

    monster = Monster("TestMonster", 100)
    from sts_env.combat import Intent, IntentType
    monster.intent = Intent(IntentType.ATTACK, damage=20)
    combat = Combat(player, [monster])

    combat.end_player_turn()
    assert player.is_dead
    assert combat.result == CombatResult.LOSE
    print("PASS: test_combat_lose")


def test_card_not_enough_energy():
    """测试能量不足无法打牌。"""
    player = Player("Ironclad", 80)
    bash = make_card("Bash")  # cost 2
    player.hand = [bash]
    player.energy = 1

    monster = Monster("TestMonster", 30)
    combat = Combat(player, [monster])

    success = combat.play_card(0, 0)
    assert not success
    assert len(player.hand) == 1
    print("PASS: test_card_not_enough_energy")


def test_upgraded_card():
    """测试升级卡牌。"""
    strike_plus = make_card("StrikeIronclad", upgraded=True)
    assert strike_plus.damage == 6 + 3  # +3 upgrade
    assert strike_plus.upgraded

    bash_plus = make_card("Bash", upgraded=True)
    assert bash_plus.damage == 8 + 2  # +2 upgrade
    print("PASS: test_upgraded_card")


def test_create_monster_factory():
    """测试怪物工厂。"""
    jw = create_monster("JawWorm")
    assert isinstance(jw, JawWorm)
    assert 40 <= jw.hp <= 44

    cult = create_monster("Cultist")
    assert isinstance(cult, Cultist)

    # 未知怪物用 DataDrivenMonster
    unknown = create_monster("SomeRandomMonster")
    assert unknown.name == "SomeRandomMonster"
    print("PASS: test_create_monster_factory")


if __name__ == "__main__":
    test_basic_combat_setup()
    test_play_strike()
    test_play_defend()
    test_play_bash()
    test_vulnerable_damage()
    test_weak_reduces_damage()
    test_strength_adds_damage()
    test_block_absorbs_damage()
    test_poison_damage()
    test_combat_win()
    test_combat_lose()
    test_card_not_enough_energy()
    test_upgraded_card()
    test_create_monster_factory()
    print("\n全部测试通过!")
