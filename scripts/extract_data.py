"""从反编译的C#源码中提取杀戮尖塔2的结构化游戏数据。"""
import json
import os
import re
from pathlib import Path

DECOMPILED = Path(os.environ.get(
    "STS2_DECOMPILED",
    str(Path(__file__).resolve().parent.parent.parent / "decompiled"),
))
OUT = Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# 卡牌提取
# ---------------------------------------------------------------------------

_CTOR_RE = re.compile(
    r":\s*base\(\s*"
    r"(?P<cost>-?\d+|null)\s*,\s*"
    r"CardType\.(?P<type>\w+)\s*,\s*"
    r"CardRarity\.(?P<rarity>\w+)\s*,\s*"
    r"TargetType\.(?P<target>\w+)",
)

_DAMAGE_RE = re.compile(r"new DamageVar\((\d+)m")
_BLOCK_RE = re.compile(r"new BlockVar\((\d+)m")
_POWER_RE = re.compile(r"new PowerVar<(\w+)>\((\d+)m")
_DRAW_RE = re.compile(r"new DrawVar\((\d+)m")
_MAGIC_RE = re.compile(r"new MagicNumberVar\((\d+)m")
_GENERIC_VAR_RE = re.compile(r'new DynamicVar\("(\w+)",\s*(\d+)m\)')

_KEYWORD_RE = re.compile(r"CardKeyword\.(\w+)")
_TAG_RE = re.compile(r"CardTag\.(\w+)")

_UPG_DAMAGE_RE = re.compile(r"Damage\.UpgradeValueBy\((-?\d+)m\)")
_UPG_BLOCK_RE = re.compile(r"Block\.UpgradeValueBy\((-?\d+)m\)")
_UPG_COST_RE = re.compile(r"EnergyCost\.UpgradeBy\((-?\d+)\)")
_UPG_POWER_RE = re.compile(r"(\w+)\.UpgradeValueBy\((-?\d+)m\)")

_MULTI_ATK_RE = re.compile(r"MultiAttack.*?(\d+)m.*?(\d+)")
_EXHAUST_RE = re.compile(r"override bool Exhaust\w*\s*=>\s*true")
_ETHEREAL_RE = re.compile(r"override bool IsEthereal\s*=>\s*true")
_INNATE_RE = re.compile(r"override bool Innate\s*=>\s*true")
_RETAIN_RE = re.compile(r"override bool Retain\w*\s*=>\s*true")

# 卡池映射
CARD_POOLS = {
    "IroncladCardPool": "Ironclad",
    "SilentCardPool": "Silent",
    "DefectCardPool": "Defect",
    "NecrobinderCardPool": "Necrobinder",
    "RegentCardPool": "Regent",
    "ColorlessCardPool": "Colorless",
    "CurseCardPool": "Curse",
    "StatusCardPool": "Status",
    "TokenCardPool": "Token",
    "EventCardPool": "Event",
    "QuestCardPool": "Quest",
}


def _build_card_pool_map() -> dict[str, str]:
    """解析卡池文件，建立 card_name -> pool_name 映射。"""
    pool_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.CardPools"
    mapping: dict[str, str] = {}
    card_ref_re = re.compile(r"ModelDb\.Card<(\w+)>\(\)")
    for f in pool_dir.glob("*.cs"):
        pool_name = f.stem
        char = CARD_POOLS.get(pool_name, pool_name)
        text = f.read_text(encoding="utf-8-sig")
        for m in card_ref_re.finditer(text):
            mapping[m.group(1)] = char
    return mapping


def extract_cards() -> list[dict]:
    card_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.Cards"
    pool_map = _build_card_pool_map()
    cards = []
    for f in sorted(card_dir.glob("*.cs")):
        text = f.read_text(encoding="utf-8-sig")
        name = f.stem
        ctor = _CTOR_RE.search(text)
        if not ctor:
            continue
        card: dict = {
            "id": name,
            "cost": int(ctor.group("cost")) if ctor.group("cost") != "null" else -1,
            "type": ctor.group("type"),
            "rarity": ctor.group("rarity"),
            "target": ctor.group("target"),
            "pool": pool_map.get(name, "Unknown"),
        }
        # 动态变量
        dm = _DAMAGE_RE.search(text)
        if dm:
            card["damage"] = int(dm.group(1))
        bm = _BLOCK_RE.search(text)
        if bm:
            card["block"] = int(bm.group(1))
        for pm in _POWER_RE.finditer(text):
            card.setdefault("powers", {})[pm.group(1)] = int(pm.group(2))
        drm = _DRAW_RE.search(text)
        if drm:
            card["draw"] = int(drm.group(1))
        mm = _MAGIC_RE.search(text)
        if mm:
            card["magic"] = int(mm.group(1))
        for gm in _GENERIC_VAR_RE.finditer(text):
            card.setdefault("vars", {})[gm.group(1)] = int(gm.group(2))
        # 关键词
        kws = set(_KEYWORD_RE.findall(text))
        if _EXHAUST_RE.search(text):
            kws.add("Exhaust")
        if _ETHEREAL_RE.search(text):
            kws.add("Ethereal")
        if _INNATE_RE.search(text):
            kws.add("Innate")
        if _RETAIN_RE.search(text):
            kws.add("Retain")
        if kws:
            card["keywords"] = sorted(kws)
        tags = set(_TAG_RE.findall(text))
        if tags:
            card["tags"] = sorted(tags)
        # 升级
        upgrade: dict = {}
        udm = _UPG_DAMAGE_RE.search(text)
        if udm:
            upgrade["damage"] = int(udm.group(1))
        ubm = _UPG_BLOCK_RE.search(text)
        if ubm:
            upgrade["block"] = int(ubm.group(1))
        ucm = _UPG_COST_RE.search(text)
        if ucm:
            upgrade["cost"] = int(ucm.group(1))
        if upgrade:
            card["upgrade"] = upgrade
        cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# 怪物提取
# ---------------------------------------------------------------------------

_HP_MIN_RE = re.compile(r"MinInitialHp\s*=>\s*(?:AscensionHelper\.\w+\([^,]+,\s*(\d+)\s*,\s*(\d+)\)|(\d+))")
_HP_MAX_RE = re.compile(r"MaxInitialHp\s*=>\s*(?:AscensionHelper\.\w+\([^,]+,\s*(\d+)\s*,\s*(\d+)\)|(\d+))")
_SINGLE_ATK_INTENT_RE = re.compile(r"SingleAttackIntent\((\d+)")
_MULTI_ATK_INTENT_RE = re.compile(r"MultiAttackIntent\((\d+)\w*,\s*(\d+)")
_MOVE_STATE_RE = re.compile(r'new MoveState\("(\w+)"')


def extract_monsters() -> list[dict]:
    mon_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.Monsters"
    monsters = []
    for f in sorted(mon_dir.glob("*.cs")):
        text = f.read_text(encoding="utf-8-sig")
        name = f.stem
        if name.startswith("Mock") or name.startswith("Deprecated"):
            continue
        mon: dict = {"id": name}
        # HP
        hmin = _HP_MIN_RE.search(text)
        if hmin:
            if hmin.group(3):
                mon["hp_min"] = int(hmin.group(3))
            else:
                mon["hp_min"] = int(hmin.group(2))
                mon["hp_min_asc"] = int(hmin.group(1))
        hmax = _HP_MAX_RE.search(text)
        if hmax:
            if hmax.group(3):
                mon["hp_max"] = int(hmax.group(3))
            else:
                mon["hp_max"] = int(hmax.group(2))
                mon["hp_max_asc"] = int(hmax.group(1))
        # 招式
        moves = sorted(set(_MOVE_STATE_RE.findall(text)))
        if moves:
            mon["moves"] = moves
        # 攻击意图
        atks = []
        for m in _SINGLE_ATK_INTENT_RE.finditer(text):
            atks.append({"type": "single", "damage": int(m.group(1))})
        for m in _MULTI_ATK_INTENT_RE.finditer(text):
            atks.append({"type": "multi", "damage": int(m.group(1)), "hits": int(m.group(2))})
        if atks:
            mon["attacks"] = atks
        if "hp_min" in mon:
            monsters.append(mon)
    return monsters


# ---------------------------------------------------------------------------
# 遭遇战提取
# ---------------------------------------------------------------------------

_ROOM_TYPE_RE = re.compile(r"RoomType\s*=>\s*RoomType\.(\w+)")
_ENCOUNTER_MON_RE = re.compile(r"new (\w+)\(\)")
_IS_WEAK_RE = re.compile(r"IsWeak\s*=>\s*true")


def extract_encounters() -> list[dict]:
    enc_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.Encounters"
    encounters = []
    for f in sorted(enc_dir.glob("*.cs")):
        text = f.read_text(encoding="utf-8-sig")
        name = f.stem
        if name.startswith("Mock") or name.startswith("Deprecated"):
            continue
        enc: dict = {"id": name}
        rt = _ROOM_TYPE_RE.search(text)
        if rt:
            enc["room_type"] = rt.group(1)
        enc["is_weak"] = bool(_IS_WEAK_RE.search(text))
        # 怪物引用（简单提取 new XxxMonster()）
        mons = set()
        for m in _ENCOUNTER_MON_RE.finditer(text):
            mn = m.group(1)
            if mn[0].isupper() and "Model" not in mn and "Encounter" not in mn:
                mons.add(mn)
        if mons:
            enc["monsters"] = sorted(mons)
        if "room_type" in enc:
            encounters.append(enc)
    return encounters


# ---------------------------------------------------------------------------
# 事件提取
# ---------------------------------------------------------------------------

_EVENT_OPTION_RE = re.compile(r'EventOption\(.*?"(\w+)"')
_DAMAGE_VAR_EVENT_RE = re.compile(r"DamageVar\((\d+)")
_HEAL_VAR_RE = re.compile(r"HealVar\((\d+)")
_GOLD_VAR_RE = re.compile(r"GoldVar\((\d+)")


def extract_events() -> list[dict]:
    ev_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.Events"
    events = []
    for f in sorted(ev_dir.glob("*.cs")):
        text = f.read_text(encoding="utf-8-sig")
        name = f.stem
        if name.startswith("Deprecated"):
            continue
        ev: dict = {"id": name}
        # 动态变量
        dvars: dict = {}
        for dm in _DAMAGE_VAR_EVENT_RE.finditer(text):
            dvars["damage"] = int(dm.group(1))
        for hm in _HEAL_VAR_RE.finditer(text):
            dvars["heal"] = int(hm.group(1))
        for gm in _GOLD_VAR_RE.finditer(text):
            dvars["gold"] = int(gm.group(1))
        if dvars:
            ev["vars"] = dvars
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 角色提取
# ---------------------------------------------------------------------------

_START_HP_RE = re.compile(r"StartingHp\s*=>\s*(\d+)")
_START_GOLD_RE = re.compile(r"StartingGold\s*=>\s*(\d+)")
_START_CARD_RE = re.compile(r"ModelDb\.Card<(\w+)>\(\)")
_START_RELIC_RE = re.compile(r"ModelDb\.Relic<(\w+)>\(\)")


def extract_characters() -> list[dict]:
    char_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.Characters"
    chars = []
    for f in sorted(char_dir.glob("*.cs")):
        text = f.read_text(encoding="utf-8-sig")
        name = f.stem
        if name in ("CharacterModel", "CharacterGender") or name.startswith("Mock"):
            continue
        ch: dict = {"id": name}
        hp = _START_HP_RE.search(text)
        if hp:
            ch["starting_hp"] = int(hp.group(1))
        gold = _START_GOLD_RE.search(text)
        if gold:
            ch["starting_gold"] = int(gold.group(1))
        # 初始牌组
        deck_section = text[text.find("StartingDeck"):] if "StartingDeck" in text else ""
        deck_cards = _START_CARD_RE.findall(deck_section.split("StartingRelics")[0] if "StartingRelics" in deck_section else deck_section)
        if deck_cards:
            ch["starting_deck"] = deck_cards
        relics = _START_RELIC_RE.findall(text)
        if relics:
            ch["starting_relics"] = relics
        if "starting_hp" in ch:
            chars.append(ch)
    return chars


# ---------------------------------------------------------------------------
# 遗物提取
# ---------------------------------------------------------------------------

_RELIC_RARITY_RE = re.compile(r"Rarity\s*=>\s*RelicRarity\.(\w+)")
_RELIC_POOL_RE = re.compile(r"Pool\s*=>\s*RelicPool\.(\w+)")


def extract_relics() -> list[dict]:
    relic_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.Relics"
    relics = []
    for f in sorted(relic_dir.glob("*.cs")):
        text = f.read_text(encoding="utf-8-sig")
        name = f.stem
        if name.startswith("Mock") or name.startswith("Deprecated"):
            continue
        r: dict = {"id": name}
        rr = _RELIC_RARITY_RE.search(text)
        if rr:
            r["rarity"] = rr.group(1)
        rp = _RELIC_POOL_RE.search(text)
        if rp:
            r["pool"] = rp.group(1)
        if "rarity" in r:
            relics.append(r)
    return relics


# ---------------------------------------------------------------------------
# 药水提取
# ---------------------------------------------------------------------------

_POTION_RARITY_RE = re.compile(r"Rarity\s*=>\s*PotionRarity\.(\w+)")
_POTION_TARGET_RE = re.compile(r"TargetType\s*=>\s*PotionTargetType\.(\w+)")


def extract_potions() -> list[dict]:
    pot_dir = DECOMPILED / "MegaCrit.Sts2.Core.Models.Potions"
    potions = []
    for f in sorted(pot_dir.glob("*.cs")):
        text = f.read_text(encoding="utf-8-sig")
        name = f.stem
        if name.startswith("Mock") or name.startswith("Deprecated"):
            continue
        p: dict = {"id": name}
        pr = _POTION_RARITY_RE.search(text)
        if pr:
            p["rarity"] = pr.group(1)
        pt = _POTION_TARGET_RE.search(text)
        if pt:
            p["target"] = pt.group(1)
        if "rarity" in p:
            potions.append(p)
    return potions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"反编译目录: {DECOMPILED}")
    print(f"输出目录: {OUT}")

    data_map = {
        "cards": extract_cards,
        "monsters": extract_monsters,
        "encounters": extract_encounters,
        "events": extract_events,
        "characters": extract_characters,
        "relics": extract_relics,
        "potions": extract_potions,
    }
    for name, func in data_map.items():
        data = func()
        out_path = OUT / f"{name}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {name}: {len(data)} 条 -> {out_path}")

    print("数据提取完成!")


if __name__ == "__main__":
    main()
