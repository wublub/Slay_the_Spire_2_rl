"""批量评估入口：顺序评估全部角色并生成排行榜。"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.model_paths import CHARACTERS, MODELS_DIR, resolve_model_path

AGENT_EVALUATE = ROOT / "agent" / "evaluate.py"
EVAL_DIR = ROOT / "eval"



def default_model_path(character: str, models_dir: Path = MODELS_DIR) -> Path:
    return resolve_model_path(character, models_dir=models_dir)



def default_output_path(character: str, eval_dir: Path = EVAL_DIR) -> Path:
    return eval_dir / f"{character.lower()}_eval.json"



def load_eval_summary(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))



def build_leaderboard(entries: list[dict]) -> list[dict]:
    return sorted(
        entries,
        key=lambda item: (-item["win_rate"], -item["avg_floor"], -item["avg_hp"], item["character"]),
    )



def save_leaderboard(entries: list[dict], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(entries),
        "leaderboard": build_leaderboard(entries),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path



def run_evaluation(character: str, episodes: int, seed: int):
    model_path = default_model_path(character)
    output_path = default_output_path(character)
    command = [
        sys.executable,
        str(AGENT_EVALUATE),
        "--character",
        character,
        "--episodes",
        str(episodes),
        "--seed",
        str(seed),
        "--model",
        str(model_path),
        "--output",
        str(output_path),
    ]
    subprocess.run(command, check=True)
    return output_path



def main():
    parser = argparse.ArgumentParser(description="顺序评估全部角色并生成排行榜")
    parser.add_argument("--episodes", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    summaries: list[dict] = []
    for idx, character in enumerate(CHARACTERS):
        print(f"\n=== 开始评估 {character} ===")
        output_path = run_evaluation(character, args.episodes, args.seed + idx * 1000)
        summary = load_eval_summary(output_path)
        if summary is not None:
            summaries.append(summary)

    leaderboard_path = save_leaderboard(summaries, EVAL_DIR / "leaderboard.json")
    print(f"\n排行榜已保存: {leaderboard_path}")


if __name__ == "__main__":
    main()
