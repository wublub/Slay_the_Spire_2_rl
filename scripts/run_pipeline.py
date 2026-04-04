"""总控脚本：顺序训练、评估并生成总排行榜。"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAIN_ALL = ROOT / "scripts" / "train_all.py"
EVALUATE_ALL = ROOT / "scripts" / "evaluate_all.py"



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一键执行训练、评估与排行榜汇总")
    parser.add_argument("--timesteps", type=int, default=None)
    parser.add_argument("--n-envs", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--n-steps", type=int, default=None)
    parser.add_argument("--eval-freq", type=int, default=None)
    parser.add_argument("--eval-episodes", type=int, default=None)
    parser.add_argument("--post-eval-episodes", type=int, default=None)
    parser.add_argument("--episodes", type=int, default=100, help="批量评估局数")
    parser.add_argument("--auto-resume", action="store_true")
    parser.add_argument("--no-preset", action="store_true")
    return parser



def build_train_command(args) -> list[str]:
    command = [sys.executable, str(TRAIN_ALL)]
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
            command.extend([flag, str(value)])
    if args.auto_resume:
        command.append("--auto-resume")
    if args.no_preset:
        command.append("--no-preset")
    return command



def build_eval_command(args) -> list[str]:
    command = [sys.executable, str(EVALUATE_ALL), "--episodes", str(args.episodes)]
    if args.seed is not None:
        command.extend(["--seed", str(args.seed)])
    return command



def main():
    parser = build_parser()
    args = parser.parse_args()

    train_command = build_train_command(args)
    eval_command = build_eval_command(args)

    print("=== 开始批量训练 ===")
    subprocess.run(train_command, check=True)

    print("\n=== 开始批量评估 ===")
    subprocess.run(eval_command, check=True)

    print("\n总控流程完成。")


if __name__ == "__main__":
    main()
