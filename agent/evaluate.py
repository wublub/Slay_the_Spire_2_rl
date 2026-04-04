"""评估脚本：加载训练好的模型并运行评估。"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sts_env.env import StsEnv
from agent.model_paths import CHARACTERS
from agent.train import ActionMaskWrapper



def _build_metrics(character: str, episodes: int, wins: int, total_floors: int, total_hp: int) -> dict:
    return {
        "character": character,
        "episodes": episodes,
        "wins": wins,
        "win_rate": wins / episodes if episodes else 0.0,
        "avg_floor": total_floors / episodes if episodes else 0.0,
        "avg_hp": total_hp / episodes if episodes else 0.0,
    }



def print_metrics(title: str, metrics: dict):
    print(f"\n=== {title} ({metrics['character']}) ===")
    print(f"局数: {metrics['episodes']}")
    print(f"胜率: {metrics['wins']}/{metrics['episodes']} ({metrics['win_rate']*100:.1f}%)")
    print(f"平均楼层: {metrics['avg_floor']:.1f}")
    print(f"平均剩余HP: {metrics['avg_hp']:.1f}")



def evaluate(
    model_path: str,
    character: str,
    n_episodes: int = 100,
    render: bool = False,
    seed: int = 42,
) -> dict:
    from sb3_contrib import MaskablePPO

    env = ActionMaskWrapper(StsEnv(character=character, seed=seed))
    model = MaskablePPO.load(model_path)

    wins = 0
    total_floors = 0
    total_hp = 0

    for ep in range(n_episodes):
        obs, info = env.reset(seed=seed + ep)
        done = False
        while not done:
            action_masks = env.action_masks()
            action, _ = model.predict(obs, deterministic=True, action_masks=action_masks)
            obs, reward, terminated, truncated, info = env.step(int(action))
            done = terminated or truncated
            if render:
                env.render()

        if info.get("won", False):
            wins += 1
        total_floors += info.get("floor", 0)
        total_hp += max(0, info.get("hp", 0))

    env.close()
    metrics = _build_metrics(character, n_episodes, wins, total_floors, total_hp)
    print_metrics("评估结果", metrics)
    return metrics



def evaluate_random(character: str, n_episodes: int = 100, seed: int = 42) -> dict:
    """随机 agent 基线。"""
    env = StsEnv(character=character, seed=seed)
    wins = 0
    total_floors = 0
    total_hp = 0

    for ep in range(n_episodes):
        obs, info = env.reset(seed=seed + ep)
        done = False
        steps = 0
        while not done and steps < 10000:
            mask = env.action_mask()
            valid = np.where(mask)[0]
            action = np.random.choice(valid)
            obs, reward, terminated, truncated, info = env.step(int(action))
            done = terminated or truncated
            steps += 1

        if info.get("won", False):
            wins += 1
        total_floors += info.get("floor", 0)
        total_hp += max(0, info.get("hp", 0))

    env.close()
    metrics = _build_metrics(character, n_episodes, wins, total_floors, total_hp)
    print_metrics("随机基线", metrics)
    return metrics



def save_evaluation_summary(metrics: dict, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return path



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default=None, help="模型路径")
    parser.add_argument("--character", default="Ironclad", choices=CHARACTERS)
    parser.add_argument("--episodes", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--random", action="store_true", help="运行随机基线")
    parser.add_argument("--output", type=str, default=None, help="评估结果输出 JSON 路径")
    args = parser.parse_args()

    if args.random:
        metrics = evaluate_random(args.character, args.episodes, seed=args.seed)
    else:
        if not args.model:
            print("请指定 --model 路径，或使用 --random 运行随机基线")
            return
        metrics = evaluate(args.model, args.character, args.episodes, args.render, seed=args.seed)

    if args.output:
        out_path = save_evaluation_summary(metrics, args.output)
        print(f"评估摘要已保存: {out_path}")


if __name__ == "__main__":
    main()
