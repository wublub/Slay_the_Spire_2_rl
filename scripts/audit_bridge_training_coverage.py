"""审计 bridge / 训练环境 与反编译实机交互语义的覆盖差距。"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CARD_EFFECTS_PATH = REPO_ROOT / "sts_env" / "card_effects.py"


def _resolve_decompiled_root() -> Path:
    candidates = []
    env_path = os.environ.get("STS2_DECOMPILED")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend([
        REPO_ROOT / "decompiled",
        REPO_ROOT.parent / "decompiled",
    ])
    for path in candidates:
        if (path / "MegaCrit.Sts2.Core.Models.Cards").exists():
            return path
    return candidates[0]


DECOMPILED_ROOT = _resolve_decompiled_root()
CARD_MODELS_DIR = DECOMPILED_ROOT / "MegaCrit.Sts2.Core.Models.Cards"


HAND_SELECTION_MECHANICS = {
    "combat_hand_discard": "战斗内弃手牌",
    "combat_hand_upgrade": "战斗内升级手牌",
    "combat_hand_exhaust": "战斗内消耗手牌",
    "combat_hand_transform": "战斗内变化/替换手牌",
    "combat_hand_select": "战斗内泛化手牌选择",
}

OTHER_SELECTION_MECHANICS = {
    "grid_discard_pile": "网格选弃牌堆",
    "grid_draw_pile": "网格选抽牌堆",
    "grid_other": "其他网格选牌",
    "choose_a_card_screen": "Choose A Card 屏幕",
}


def _load_env_handler_bodies() -> dict[str, str]:
    lines = CARD_EFFECTS_PATH.read_text(encoding="utf-8").splitlines()
    handlers: dict[str, str] = {}
    current_card: str | None = None
    body: list[str] = []

    for line in lines:
        match = re.match(r'@_register\("([^"]+)"\)', line)
        if match:
            if current_card is not None:
                handlers[current_card] = "\n".join(body)
            current_card = match.group(1)
            body = []
            continue
        if current_card is not None:
            body.append(line)

    if current_card is not None:
        handlers[current_card] = "\n".join(body)
    return handlers


def _extract_mechanics(text: str) -> list[str]:
    mechanics: list[str] = []

    if "CardSelectCmd.FromHandForDiscard" in text:
        mechanics.append("combat_hand_discard")
    if "CardSelectCmd.FromHandForUpgrade" in text:
        mechanics.append("combat_hand_upgrade")
    if "CardSelectCmd.FromHand" in text and "ExhaustSelectionPrompt" in text:
        mechanics.append("combat_hand_exhaust")
    if "CardSelectCmd.FromHand" in text and "TransformSelectionPrompt" in text:
        mechanics.append("combat_hand_transform")
    if (
        "CardSelectCmd.FromHand" in text
        and "FromHandForDiscard" not in text
        and "FromHandForUpgrade" not in text
        and "ExhaustSelectionPrompt" not in text
        and "TransformSelectionPrompt" not in text
    ):
        mechanics.append("combat_hand_select")

    if "CardSelectCmd.FromSimpleGrid" in text:
        if "PileType.Discard" in text:
            mechanics.append("grid_discard_pile")
        elif "PileType.Draw" in text:
            mechanics.append("grid_draw_pile")
        else:
            mechanics.append("grid_other")

    if "CardSelectCmd.FromChooseACardScreen" in text:
        mechanics.append("choose_a_card_screen")

    return mechanics


def _env_notes(handler_body: str) -> list[str]:
    notes: list[str] = []
    if "简化" in handler_body:
        notes.append("handler_contains_simplification_comment")
    if "player.hand.pop()" in handler_body:
        notes.append("drops_last_hand_card")
    if "for c in player.hand[:]" in handler_body and "player.exhaust_pile.append(c)" in handler_body:
        notes.append("exhausts_entire_hand")
    return notes


def audit_selection_coverage() -> dict[str, object]:
    if not CARD_MODELS_DIR.exists():
        raise FileNotFoundError(f"找不到反编译卡牌目录: {CARD_MODELS_DIR}")
    if not CARD_EFFECTS_PATH.exists():
        raise FileNotFoundError(f"找不到训练卡效文件: {CARD_EFFECTS_PATH}")

    env_handlers = _load_env_handler_bodies()
    entries: list[dict[str, object]] = []

    for path in sorted(CARD_MODELS_DIR.glob("*.cs")):
        text = path.read_text(encoding="utf-8-sig")
        mechanics = _extract_mechanics(text)
        if not mechanics:
            continue

        card_id = path.stem
        handler_body = env_handlers.get(card_id)
        notes = _env_notes(handler_body or "")
        hand_selection = [m for m in mechanics if m in HAND_SELECTION_MECHANICS]
        other_selection = [m for m in mechanics if m in OTHER_SELECTION_MECHANICS]

        if handler_body is None:
            env_status = "missing_explicit_handler"
        elif notes:
            env_status = "custom_handler_but_simplified"
        else:
            env_status = "custom_handler_present"

        risk = "low"
        if hand_selection and env_status != "custom_handler_present":
            risk = "high"
        elif other_selection and env_status != "custom_handler_present":
            risk = "medium"

        entries.append(
            {
                "card_id": card_id,
                "mechanics": mechanics,
                "hand_selection_mechanics": hand_selection,
                "other_selection_mechanics": other_selection,
                "env_status": env_status,
                "env_notes": notes,
                "decompiled_file": str(path.relative_to(REPO_ROOT)),
                "bridge_can_tighten_mask": bool(hand_selection),
                "bridge_observation_gap": bool(hand_selection),
                "risk": risk,
            }
        )

    entries.sort(key=lambda item: (item["risk"] != "high", item["card_id"]))  # type: ignore[index]

    risk_counts = Counter(entry["risk"] for entry in entries)
    env_status_counts = Counter(entry["env_status"] for entry in entries)
    mechanic_counts = Counter()
    for entry in entries:
        mechanic_counts.update(entry["mechanics"])  # type: ignore[arg-type]

    return {
        "decompiled_root": str(DECOMPILED_ROOT),
        "card_effects_path": str(CARD_EFFECTS_PATH),
        "summary": {
            "selection_cards_total": len(entries),
            "high_risk_cards": risk_counts.get("high", 0),
            "medium_risk_cards": risk_counts.get("medium", 0),
            "low_risk_cards": risk_counts.get("low", 0),
            "missing_explicit_handler": env_status_counts.get("missing_explicit_handler", 0),
            "custom_handler_but_simplified": env_status_counts.get("custom_handler_but_simplified", 0),
            "custom_handler_present": env_status_counts.get("custom_handler_present", 0),
            "mechanics": dict(sorted(mechanic_counts.items())),
        },
        "high_risk_examples": [
            entry for entry in entries if entry["risk"] == "high"
        ][:20],
        "entries": entries,
    }


def _print_report(report: dict[str, object], *, limit: int) -> None:
    summary = report["summary"]  # type: ignore[index]
    print("Bridge / Training Coverage Audit")
    print(f"- decompiled: {report['decompiled_root']}")
    print(f"- card effects: {report['card_effects_path']}")
    print(f"- selection cards: {summary['selection_cards_total']}")
    print(
        "- env status: "
        f"missing={summary['missing_explicit_handler']}, "
        f"simplified={summary['custom_handler_but_simplified']}, "
        f"custom={summary['custom_handler_present']}"
    )
    print(
        "- risk: "
        f"high={summary['high_risk_cards']}, "
        f"medium={summary['medium_risk_cards']}, "
        f"low={summary['low_risk_cards']}"
    )
    print("- mechanics:")
    for mechanic, count in summary["mechanics"].items():  # type: ignore[index]
        label = HAND_SELECTION_MECHANICS.get(mechanic) or OTHER_SELECTION_MECHANICS.get(mechanic) or mechanic
        print(f"  - {mechanic}: {count} ({label})")

    examples = report["high_risk_examples"]  # type: ignore[index]
    if not examples:
        return

    print("- high risk examples:")
    for entry in examples[:limit]:
        mechanics = ", ".join(entry["mechanics"])  # type: ignore[index]
        notes = ", ".join(entry["env_notes"]) or "-"  # type: ignore[index]
        print(
            f"  - {entry['card_id']}: mechanics={mechanics}; "
            f"env_status={entry['env_status']}; notes={notes}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="审计 bridge / 训练覆盖缺口")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="可选，将完整报告写入 JSON 文件",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=12,
        help="控制台输出的高风险样例数量",
    )
    args = parser.parse_args()

    report = audit_selection_coverage()
    _print_report(report, limit=max(args.limit, 0))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"- json written: {args.json_out}")


if __name__ == "__main__":
    main()
