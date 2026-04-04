"""卡牌数据测试。"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sts_env.combat import make_card


def test_card_data_loaded():
    """测试卡牌数据能正确加载。"""
    data_path = Path(__file__).resolve().parent.parent / "data" / "cards.json"
    cards = json.loads(data_path.read_text("utf-8"))
    assert len(cards) > 100, f"应有 >100 张卡牌，实际 {len(cards)}"
    print(f"PASS: test_card_data_loaded ({len(cards)} cards)")


def test_make_card_basic():
    """测试基础卡牌创建。"""
    strike = make_card("StrikeIronclad")
    assert strike.card_id == "StrikeIronclad"
    assert strike.cost == 1
    assert strike.damage == 6
    assert strike.card_type.value == "Attack"
    assert strike.target.value == "AnyEnemy"
    assert "Strike" in strike.tags

    defend = make_card("DefendIronclad")
    assert defend.block == 5
    assert defend.card_type.value == "Skill"
    assert "Defend" in defend.tags
    print("PASS: test_make_card_basic")


def test_card_pools():
    """测试卡池分配。"""
    data_path = Path(__file__).resolve().parent.parent / "data" / "cards.json"
    cards = json.loads(data_path.read_text("utf-8"))
    pools = set(c.get("pool", "Unknown") for c in cards)
    assert "Ironclad" in pools
    assert "Silent" in pools
    assert "Defect" in pools

    ironclad_cards = [c for c in cards if c.get("pool") == "Ironclad"]
    assert len(ironclad_cards) > 30, f"Ironclad 应有 >30 张卡，实际 {len(ironclad_cards)}"
    print(f"PASS: test_card_pools (pools={sorted(pools)})")


def test_card_types_distribution():
    """测试卡牌类型分布。"""
    data_path = Path(__file__).resolve().parent.parent / "data" / "cards.json"
    cards = json.loads(data_path.read_text("utf-8"))
    types = {}
    for c in cards:
        t = c.get("type", "Unknown")
        types[t] = types.get(t, 0) + 1
    assert "Attack" in types
    assert "Skill" in types
    assert "Power" in types
    print(f"PASS: test_card_types_distribution ({types})")


if __name__ == "__main__":
    test_card_data_loaded()
    test_make_card_basic()
    test_card_pools()
    test_card_types_distribution()
    print("\n全部卡牌测试通过!")
