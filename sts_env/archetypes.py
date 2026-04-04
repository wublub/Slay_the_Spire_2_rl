"""角色流派定义与训练先验。

这里不只做“流派命名”，还显式编码：
1. 牌库尺寸偏好（15~18 最优，25 以上开始明显惩罚）
2. 复合抽牌/滤牌优先于纯数值牌
3. 资源牌与爆发牌的角色化权重
4. 删牌、升级、跳牌时的启发式分数
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class Archetype:
    name: str
    character: str
    core_cards: list[str]
    synergy_cards: list[str]
    bad_cards: list[str]
    description: str = ""

    def score(self, deck_ids: list[str]) -> float:
        """计算牌组对本流派的契合度 [0, 1]。"""
        deck_counts = Counter(deck_ids)
        core_hit = sum(1 for card_id in self.core_cards if deck_counts[card_id] > 0)
        synergy_hit = sum(1 for card_id in self.synergy_cards if deck_counts[card_id] > 0)
        core_ratio = core_hit / max(len(self.core_cards), 1)
        synergy_ratio = synergy_hit / max(len(self.synergy_cards), 1)
        return 0.7 * core_ratio + 0.3 * synergy_ratio


@dataclass(frozen=True)
class CharacterStrategy:
    ideal_deck_min: int
    ideal_deck_max: int
    workable_deck_max: int
    draw_cards: tuple[str, ...] = ()
    hybrid_draw_cards: tuple[str, ...] = ()
    resource_cards: tuple[str, ...] = ()
    payoff_cards: tuple[str, ...] = ()
    premium_upgrade_cards: tuple[str, ...] = ()


REMOVE_ALWAYS: set[str] = {
    "AscendersBane",
    "BadLuck",
    "Clumsy",
    "CurseOfTheBell",
    "Debt",
    "Decay",
    "Doubt",
    "Enthralled",
    "Folly",
    "Greed",
    "Guilty",
    "Injury",
    "Normality",
    "PoorSleep",
    "Regret",
    "Shame",
    "SporeMind",
    "Writhe",
}
BASE_STRIKES: set[str] = {
    "StrikeIronclad", "StrikeSilent", "StrikeDefect", "StrikeNecrobinder", "StrikeRegent",
}
BASE_DEFENDS: set[str] = {
    "DefendIronclad", "DefendSilent", "DefendDefect", "DefendNecrobinder", "DefendRegent",
}


IRONCLAD_ARCHETYPES: list[Archetype] = [
    Archetype(
        name="ExhaustCycle",
        character="Ironclad",
        description="Pommel/Burning Pact 起手，Dark Embrace/Corruption/Feel No Pain 形成循环。",
        core_cards=["PommelStrike", "BurningPact", "DarkEmbrace", "Corruption", "FeelNoPain"],
        synergy_cards=["BattleTrance", "ShrugItOff", "Offering", "TrueGrit", "FiendFire", "Hellraiser"],
        bad_cards=["StrikeIronclad", "DefendIronclad", "BodySlam"],
    ),
    Archetype(
        name="StrengthBurst",
        character="Ironclad",
        description="力量/易伤爆发，抽牌负责尽快找到增伤轴。",
        core_cards=["Inflame", "DemonForm", "PommelStrike"],
        synergy_cards=["Uppercut", "TwinStrike", "Whirlwind", "BattleTrance", "BurningPact", "Bash"],
        bad_cards=["DefendIronclad", "StoneArmor"],
    ),
    Archetype(
        name="BlockSlam",
        character="Ironclad",
        description="Barricade/Entrench/Body Slam 小牌库格挡转伤害。",
        core_cards=["Barricade", "Entrench", "BodySlam"],
        synergy_cards=["ShrugItOff", "Impervious", "FlameBarrier", "PommelStrike", "BurningPact"],
        bad_cards=["StrikeIronclad", "Bludgeon"],
    ),
]

SILENT_ARCHETYPES: list[Archetype] = [
    Archetype(
        name="SlyCycle",
        character="Silent",
        description="Prepared/Acrobatics/Tools of the Trade 抽弃循环，Sly 爆发。",
        core_cards=["Prepared", "Acrobatics", "ToolsOfTheTrade", "MasterPlanner"],
        synergy_cards=["Backflip", "CalculatedGamble", "Tactician", "Reflex", "DaggerThrow", "Nightmare"],
        bad_cards=["StrikeSilent", "DefendSilent"],
    ),
    Archetype(
        name="PoisonBurst",
        character="Silent",
        description="毒层与毒系 Power 叠加，抽弃负责快速找到持续毒源与收头牌。",
        core_cards=["NoxiousFumes", "Outbreak"],
        synergy_cards=["DeadlyPoison", "BouncingFlask", "PoisonedStab", "Haze", "CorrosiveWave", "Acrobatics", "Backflip"],
        bad_cards=["StrikeSilent", "Accuracy"],
    ),
    Archetype(
        name="ShivTempo",
        character="Silent",
        description="Shiv 节奏流，抽弃维持一回合多打牌。",
        core_cards=["BladeDance", "Accuracy"],
        synergy_cards=["CloakAndDagger", "Backflip", "Acrobatics", "Prepared", "ToolsOfTheTrade"],
        bad_cards=["StrikeSilent", "NoxiousFumes"],
    ),
]

DEFECT_ARCHETYPES: list[Archetype] = [
    Archetype(
        name="ClawCycle",
        character="Defect",
        description="Claw/Scrape/Hologram/All For One 零费循环。",
        core_cards=["Claw", "Scrape", "Hologram", "AllForOne"],
        synergy_cards=["Coolheaded", "Dualcast", "Zap"],
        bad_cards=["StrikeDefect", "DefendDefect"],
    ),
    Archetype(
        name="OrbFocus",
        character="Defect",
        description="球体/Focus 轴，Coolheaded 兼顾过牌与球体。",
        core_cards=["Coolheaded", "Defragment", "Thunder"],
        synergy_cards=["Glacier", "Loop", "BiasedCognition", "BallLightning", "LightningRod"],
        bad_cards=["StrikeDefect", "Claw"],
    ),
]

NECROBINDER_ARCHETYPES: list[Archetype] = [
    Archetype(
        name="OstyTempo",
        character="Necrobinder",
        description="Osty 在场节奏流，靠 Fetch/Spur/Snap 快速形成爆发。",
        core_cards=["Fetch", "Spur", "Snap"],
        synergy_cards=["Afterlife", "Delay", "SicEm", "Flatten", "RightHandHand", "Rattle", "Squeeze"],
        bad_cards=["StrikeNecrobinder", "DefendNecrobinder"],
    ),
    Archetype(
        name="SoulCycle",
        character="Necrobinder",
        description="灾厄/回手循环，靠 Dredge/Transfigure 快速重打关键灾厄牌。",
        core_cards=["NoEscape", "NegativePulse", "Countdown"],
        synergy_cards=["Oblivion", "EndOfDays", "Dredge", "Delay", "SoulStorm", "Transfigure"],
        bad_cards=["StrikeNecrobinder"],
    ),
]

REGENT_ARCHETYPES: list[Archetype] = [
    Archetype(
        name="StarsEngine",
        character="Regent",
        description="Glow/Shining Strike/Hidden Cache/Genesis 的星辉循环。",
        core_cards=["Glow", "ShiningStrike", "HiddenCache", "Genesis"],
        synergy_cards=["Convergence", "BigBang", "GatherLight", "Radiate", "Stardust"],
        bad_cards=["StrikeRegent", "DefendRegent"],
    ),
    Archetype(
        name="ForgeTempo",
        character="Regent",
        description="铸造强化单卡，抽牌与星辉负责稳定找到锻造轴。",
        core_cards=["BigBang", "Genesis", "Glow"],
        synergy_cards=["ShiningStrike", "Convergence", "GatherLight"],
        bad_cards=["StrikeRegent"],
    ),
]


ALL_ARCHETYPES: dict[str, list[Archetype]] = {
    "Ironclad": IRONCLAD_ARCHETYPES,
    "Silent": SILENT_ARCHETYPES,
    "Defect": DEFECT_ARCHETYPES,
    "Necrobinder": NECROBINDER_ARCHETYPES,
    "Regent": REGENT_ARCHETYPES,
}

JUNK_CARDS: dict[str, list[str]] = {
    "Ironclad": ["StrikeIronclad", "DefendIronclad", "AscendersBane"],
    "Silent": ["StrikeSilent", "DefendSilent", "AscendersBane"],
    "Defect": ["StrikeDefect", "DefendDefect", "AscendersBane"],
    "Necrobinder": ["StrikeNecrobinder", "DefendNecrobinder", "AscendersBane"],
    "Regent": ["StrikeRegent", "DefendRegent", "AscendersBane"],
}

CHARACTER_STRATEGIES: dict[str, CharacterStrategy] = {
    "Ironclad": CharacterStrategy(
        ideal_deck_min=15,
        ideal_deck_max=18,
        workable_deck_max=25,
        draw_cards=("BattleTrance", "PommelStrike"),
        hybrid_draw_cards=("PommelStrike", "BurningPact", "ShrugItOff", "Offering"),
        resource_cards=("Offering", "Corruption", "Hellraiser"),
        payoff_cards=("DarkEmbrace", "FeelNoPain", "DemonForm", "Barricade", "BodySlam"),
        premium_upgrade_cards=("PommelStrike", "BurningPact", "BattleTrance", "DarkEmbrace", "Corruption", "FeelNoPain", "Bash"),
    ),
    "Silent": CharacterStrategy(
        ideal_deck_min=15,
        ideal_deck_max=18,
        workable_deck_max=24,
        draw_cards=("Prepared", "Acrobatics", "CalculatedGamble"),
        hybrid_draw_cards=("Prepared", "Acrobatics", "Backflip", "DaggerThrow", "ToolsOfTheTrade"),
        resource_cards=("Tactician", "Adrenaline", "ToolsOfTheTrade", "MasterPlanner"),
        payoff_cards=("BladeDance", "Accuracy", "NoxiousFumes", "Outbreak", "Nightmare", "Reflex"),
        premium_upgrade_cards=("Prepared", "Acrobatics", "Backflip", "ToolsOfTheTrade", "BladeDance", "NoxiousFumes", "MasterPlanner"),
    ),
    "Defect": CharacterStrategy(
        ideal_deck_min=15,
        ideal_deck_max=18,
        workable_deck_max=24,
        draw_cards=("Scrape", "Coolheaded"),
        hybrid_draw_cards=("Scrape", "Coolheaded"),
        resource_cards=("Hologram", "AllForOne", "Defragment", "Loop"),
        payoff_cards=("Claw", "Thunder", "BiasedCognition", "Glacier"),
        premium_upgrade_cards=("Claw", "Scrape", "Hologram", "Coolheaded", "AllForOne", "Defragment"),
    ),
    "Necrobinder": CharacterStrategy(
        ideal_deck_min=15,
        ideal_deck_max=19,
        workable_deck_max=25,
        draw_cards=("Fetch",),
        hybrid_draw_cards=("Fetch", "Dredge", "Delay"),
        resource_cards=("Spur", "Delay", "Dredge", "Transfigure"),
        payoff_cards=("Snap", "SicEm", "RightHandHand", "Rattle", "Squeeze", "SoulStorm", "NoEscape", "EndOfDays"),
        premium_upgrade_cards=("Fetch", "Spur", "Snap", "Dredge", "Delay", "Transfigure", "NoEscape"),
    ),
    "Regent": CharacterStrategy(
        ideal_deck_min=15,
        ideal_deck_max=18,
        workable_deck_max=24,
        draw_cards=("Glow",),
        hybrid_draw_cards=("Glow", "BigBang", "GatherLight"),
        resource_cards=("ShiningStrike", "HiddenCache", "Genesis", "Convergence"),
        payoff_cards=("Radiate", "Stardust", "BigBang"),
        premium_upgrade_cards=("Glow", "ShiningStrike", "HiddenCache", "Genesis", "BigBang", "Radiate"),
    ),
}

SUPPORT_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "Hellraiser": ("PommelStrike", "BurningPact", "BattleTrance", "DarkEmbrace"),
    "BodySlam": ("Barricade", "Entrench", "ShrugItOff", "Impervious", "FlameBarrier"),
    "Outbreak": ("NoxiousFumes", "DeadlyPoison", "BouncingFlask", "Haze", "CorrosiveWave"),
    "BladeDance": ("Accuracy", "Prepared", "Acrobatics", "ToolsOfTheTrade"),
    "MasterPlanner": ("Prepared", "Acrobatics", "Backflip", "Tactician", "Reflex"),
    "Claw": ("Scrape", "Hologram", "AllForOne"),
    "AllForOne": ("Claw", "Scrape", "Hologram"),
    "Thunder": ("BallLightning", "Coolheaded", "Loop", "Defragment", "LightningRod"),
    "Snap": ("Fetch", "Spur", "Dredge", "Delay"),
    "NoEscape": ("NegativePulse", "Oblivion", "Countdown", "Dredge"),
    "ShiningStrike": ("Glow", "HiddenCache", "Genesis", "Convergence"),
    "Radiate": ("Glow", "ShiningStrike", "HiddenCache"),
}


def get_archetypes(character: str) -> list[Archetype]:
    return ALL_ARCHETYPES.get(character, [])


def get_character_strategy(character: str) -> CharacterStrategy:
    return CHARACTER_STRATEGIES.get(character, CharacterStrategy(15, 18, 25))


def best_archetype(character: str, deck_ids: list[str]) -> Archetype | None:
    archetypes = get_archetypes(character)
    if not archetypes:
        return None
    return max(archetypes, key=lambda archetype: archetype.score(deck_ids))


def _deck_size_score(size: int, strategy: CharacterStrategy) -> float:
    if size <= 0:
        return 0.0
    if size < strategy.ideal_deck_min:
        return max(0.35, size / max(strategy.ideal_deck_min, 1))
    if size <= strategy.ideal_deck_max:
        return 1.0
    if size <= strategy.workable_deck_max:
        overflow = size - strategy.ideal_deck_max
        window = max(strategy.workable_deck_max - strategy.ideal_deck_max, 1)
        return max(0.55, 1.0 - 0.45 * (overflow / window))
    return max(0.1, 0.55 - 0.04 * (size - strategy.workable_deck_max))


def _role_count(deck_ids: list[str], candidates: tuple[str, ...]) -> int:
    if not candidates:
        return 0
    counts = Counter(deck_ids)
    return sum(counts[card_id] for card_id in candidates)


def _support_bonus(deck_ids: list[str], card_id: str) -> float:
    support_cards = SUPPORT_DEPENDENCIES.get(card_id, ())
    if not support_cards:
        return 0.0
    hits = _role_count(deck_ids, support_cards)
    if hits <= 0:
        return -2.5
    return min(3.0, 1.25 * hits)


def card_pick_score(character: str, deck_ids: list[str], card_id: str) -> float:
    """为抓牌/买牌提供角色化启发式分数。"""
    strategy = get_character_strategy(character)
    best = best_archetype(character, deck_ids)
    best_score = best.score(deck_ids) if best is not None else 0.0
    size = len(deck_ids)

    score = 0.0
    if card_id in REMOVE_ALWAYS:
        return -12.0
    if card_id in JUNK_CARDS.get(character, []):
        return -9.0

    if card_id in strategy.hybrid_draw_cards:
        score += 7.0
    elif card_id in strategy.draw_cards:
        score += 4.5

    if card_id in strategy.resource_cards:
        score += 5.5
    if card_id in strategy.payoff_cards:
        score += 3.0

    current_draw = _role_count(deck_ids, strategy.draw_cards) + _role_count(deck_ids, strategy.hybrid_draw_cards)
    current_resource = _role_count(deck_ids, strategy.resource_cards)
    if current_draw < 3:
        if card_id in strategy.hybrid_draw_cards:
            score += 3.0
        elif card_id in strategy.draw_cards:
            score += 2.0
    if current_resource < 2 and card_id in strategy.resource_cards:
        score += 2.5

    if best is not None:
        if card_id in best.core_cards:
            score += 7.0 if best_score < 0.75 else 5.0
        elif card_id in best.synergy_cards:
            score += 3.5
        if card_id in best.bad_cards and best_score > 0.2:
            score -= 5.5

    score += _support_bonus(deck_ids, card_id)

    if size >= strategy.workable_deck_max:
        premium = (
            card_id in strategy.hybrid_draw_cards
            or card_id in strategy.resource_cards
            or (best is not None and card_id in best.core_cards)
        )
        score += 1.0 if premium else -4.0
    elif size >= strategy.ideal_deck_max and card_id not in strategy.hybrid_draw_cards and card_id not in strategy.resource_cards:
        score -= 1.5

    return score


def card_remove_score(
    character: str,
    deck_ids: list[str],
    card_id: str,
    *,
    floor: int = 0,
    act: int = 1,
) -> float:
    """删除优先级分数，越高越应该删。"""
    strategy = get_character_strategy(character)
    best = best_archetype(character, deck_ids)
    best_score = best.score(deck_ids) if best is not None else 0.0
    size = len(deck_ids)

    score = 0.0
    if card_id in REMOVE_ALWAYS:
        return 100.0

    if card_id in BASE_STRIKES:
        score += 32.0
        if act >= 2 or size > strategy.ideal_deck_max:
            score += 6.0
    elif card_id in BASE_DEFENDS:
        score += 24.0
        if act >= 2 or size > strategy.ideal_deck_max:
            score += 5.0
        if size <= strategy.ideal_deck_min and act <= 1:
            score -= 4.0

    if best is not None and best_score > 0.2 and card_id in best.bad_cards:
        score += 16.0

    if card_id in strategy.hybrid_draw_cards:
        score -= 20.0
    elif card_id in strategy.draw_cards:
        score -= 12.0
    if card_id in strategy.resource_cards:
        score -= 14.0
    if card_id in strategy.payoff_cards:
        score -= 8.0

    if best is not None:
        if card_id in best.core_cards:
            score -= 18.0
        elif card_id in best.synergy_cards:
            score -= 9.0

    if size > strategy.workable_deck_max and score < 10.0:
        score += 4.0
    if floor >= 18 and card_id in JUNK_CARDS.get(character, []):
        score += 2.0

    return score


def upgrade_priority_score(character: str, deck_ids: list[str], card_id: str) -> float:
    """升级优先级分数，越高越应该升级。"""
    strategy = get_character_strategy(character)
    best = best_archetype(character, deck_ids)
    best_score = best.score(deck_ids) if best is not None else 0.0

    score = 0.0
    if card_id in REMOVE_ALWAYS or card_id in BASE_STRIKES or card_id in BASE_DEFENDS:
        return -12.0

    if card_id in strategy.premium_upgrade_cards:
        score += 18.0
    if card_id in strategy.hybrid_draw_cards:
        score += 8.0
    elif card_id in strategy.draw_cards:
        score += 5.0
    if card_id in strategy.resource_cards:
        score += 7.0
    if card_id in strategy.payoff_cards:
        score += 4.0

    if best is not None:
        if card_id in best.core_cards:
            score += 8.0 if best_score >= 0.2 else 5.0
        elif card_id in best.synergy_cards:
            score += 3.5
        if card_id in best.bad_cards and best_score > 0.2:
            score -= 10.0

    score += max(0.0, _support_bonus(deck_ids, card_id))
    return score


def deck_quality_score(character: str, deck_ids: list[str]) -> float:
    """综合评估牌组质量 [0, 1]。"""
    strategy = get_character_strategy(character)
    archetypes = get_archetypes(character)
    best_score = max((archetype.score(deck_ids) for archetype in archetypes), default=0.45)

    size_score = _deck_size_score(len(deck_ids), strategy)
    junk_ratio = sum(1 for card_id in deck_ids if card_id in JUNK_CARDS.get(character, [])) / max(len(deck_ids), 1)
    junk_score = max(0.0, 1.0 - junk_ratio * 1.6)

    draw_power = _role_count(deck_ids, strategy.draw_cards) + 1.5 * _role_count(deck_ids, strategy.hybrid_draw_cards)
    resource_power = _role_count(deck_ids, strategy.resource_cards)
    draw_score = min(1.0, draw_power / 5.0)
    resource_score = min(1.0, resource_power / 2.5)

    return (
        best_score * 0.35
        + junk_score * 0.2
        + size_score * 0.2
        + draw_score * 0.15
        + resource_score * 0.1
    )


def should_remove_card(
    character: str,
    deck_ids: list[str],
    card_id: str,
    *,
    floor: int = 0,
    act: int = 1,
) -> bool:
    return card_remove_score(character, deck_ids, card_id, floor=floor, act=act) >= 12.0


def removable_priority(
    character: str,
    deck_ids: list[str],
    *,
    floor: int = 0,
    act: int = 1,
) -> list[str]:
    """返回牌组中按删除优先级排序的卡列表。"""
    scored_cards = [
        (card_remove_score(character, deck_ids, card_id, floor=floor, act=act), idx, card_id)
        for idx, card_id in enumerate(deck_ids)
    ]
    scored_cards.sort(key=lambda item: (-item[0], item[1]))
    return [card_id for _score, _idx, card_id in scored_cards]
