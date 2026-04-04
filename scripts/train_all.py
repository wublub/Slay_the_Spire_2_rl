"""批量训练入口：顺序训练全部角色并汇总结果。"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
MODELS_DIR = ROOT / "models"

CHARACTER_SCRIPTS = {
    "Ironclad": SCRIPTS_DIR / "train_ironclad.py",
    "Silent": SCRIPTS_DIR / "train_silent.py",
    "Defect": SCRIPTS_DIR / "train_defect.py",
    "Necrobinder": SCRIPTS_DIR / "train_necrobinder.py",
    "Regent": SCRIPTS_DIR / "train_regent.py",
}



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="顺序训练全部角色")
    parser.add_argument("--timesteps", type=int, default=None)
    parser.add_argument("--n-envs", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--n-steps", type=int, default=None)
    parser.add_argument("--eval-freq", type=int, default=None)
    parser.add_argument("--eval-episodes", type=int, default=None)
    parser.add_argument("--post-eval-episodes", type=int, default=None)
    parser.add_argument("--auto-resume", action="store_true", help="每个角色自动从已有 checkpoint/best/final 继续训练")
    parser.add_argument("--no-preset", action="store_true")
    return parser



def build_extra_args(args) -> list[str]:
    extra_args: list[str] = []
    optional_args = {
        "--timesteps": args.timesteps,
        "--n-envs": args.n_envs,
        "--lr": args.lr,
        "--seed": args.seed,
        "--batch-size": args.batch_size,
        "--n-steps": args.n_steps,
        "--eval-freq": args.eval_freq,
        "--eval-episodes": args.eval_episodes,
        "--post-eval-episodes": args.post_eval_episodes,
    }
    for flag, value in optional_args.items():
        if value is not None:
            extra_args.extend([flag, str(value)])
    if args.auto_resume:
        extra_args.append("--auto-resume")
    if args.no_preset:
        extra_args.append("--no-preset")
    return extra_args



def load_character_summary(character: str, models_dir: Path = MODELS_DIR) -> dict | None:
    summary_path = models_dir / character / "training_summary.json"
    if not summary_path.exists():
        return None
    return json.loads(summary_path.read_text(encoding="utf-8"))



def save_combined_summary(models_dir: Path = MODELS_DIR) -> Path:
    summaries = []
    for character in CHARACTER_SCRIPTS:
        summary = load_character_summary(character, models_dir=models_dir)
        if summary is not None:
            summaries.append(summary)

    combined = {
        "count": len(summaries),
        "characters": [summary["character"] for summary in summaries],
        "summaries": summaries,
    }
    models_dir.mkdir(parents=True, exist_ok=True)
    out_path = models_dir / "all_training_summary.json"
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path



def main():
    parser = build_parser()
    args = parser.parse_args()
    extra_args = build_extra_args(args)

    for character, script_path in CHARACTER_SCRIPTS.items():
        command = [sys.executable, str(script_path), *extra_args]
        print(f"\n=== 开始训练 {character} ===")
        subprocess.run(command, check=True)

    summary_path = save_combined_summary()
    print(f"\n全部角色训练摘要已保存: {summary_path}")


if __name__ == "__main__":
    main()
