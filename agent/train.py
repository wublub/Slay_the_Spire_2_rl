"""训练入口：使用 MaskablePPO 训练 STS2 Agent。"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

import gymnasium as gym
import numpy as np

# 将项目根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import TrainConfig
from agent.model_paths import CHARACTERS

CHARACTER_PRESETS: dict[str, dict[str, int | float]] = {
    "Ironclad": {
        "total_timesteps": 1_200_000,
        "n_envs": 8,
        "learning_rate": 3e-4,
        "n_steps": 2048,
        "batch_size": 256,
        "eval_freq": 10_000,
        "eval_episodes": 20,
        "post_eval_episodes": 30,
    },
    "Silent": {
        "total_timesteps": 1_400_000,
        "n_envs": 8,
        "learning_rate": 2.5e-4,
        "n_steps": 2048,
        "batch_size": 256,
        "eval_freq": 10_000,
        "eval_episodes": 20,
        "post_eval_episodes": 30,
    },
    "Defect": {
        "total_timesteps": 1_400_000,
        "n_envs": 8,
        "learning_rate": 2.5e-4,
        "n_steps": 2048,
        "batch_size": 256,
        "eval_freq": 10_000,
        "eval_episodes": 20,
        "post_eval_episodes": 30,
    },
    "Necrobinder": {
        "total_timesteps": 1_600_000,
        "n_envs": 8,
        "learning_rate": 2e-4,
        "n_steps": 2048,
        "batch_size": 256,
        "eval_freq": 12_000,
        "eval_episodes": 20,
        "post_eval_episodes": 30,
    },
    "Regent": {
        "total_timesteps": 1_400_000,
        "n_envs": 8,
        "learning_rate": 2.5e-4,
        "n_steps": 2048,
        "batch_size": 256,
        "eval_freq": 10_000,
        "eval_episodes": 20,
        "post_eval_episodes": 30,
    },
}



def make_env(character: str, seed: int, rank: int):
    """创建单个环境的工厂函数。"""

    def _init():
        from sts_env.env import StsEnv

        env = StsEnv(character=character, seed=seed + rank)
        env = ActionMaskWrapper(env)
        return env

    return _init


class ActionMaskWrapper(gym.Wrapper):
    """将 action mask 接口暴露给 MaskablePPO。"""

    def __init__(self, env: gym.Env):
        super().__init__(env)

    def action_masks(self) -> np.ndarray:
        if hasattr(self.env, "action_masks"):
            return self.env.action_masks()
        return self.env.action_mask()

    def action_mask(self) -> np.ndarray:
        return self.action_masks()



def build_parser(
    default_character: str = "Ironclad",
    include_character_arg: bool = True,
) -> argparse.ArgumentParser:
    """构建训练命令行参数。"""
    parser = argparse.ArgumentParser(description="STS2 RL Agent 训练")
    if include_character_arg:
        parser.add_argument("--character", default=default_character, choices=CHARACTERS)
    parser.add_argument("--timesteps", type=int, default=None)
    parser.add_argument("--n-envs", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--n-steps", type=int, default=None)
    parser.add_argument("--eval-freq", type=int, default=None)
    parser.add_argument("--eval-episodes", type=int, default=None)
    parser.add_argument("--post-eval-episodes", type=int, default=None)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--save-dir", default=None)
    parser.add_argument("--resume-from", default=None, help="从指定模型或 checkpoint 继续训练")
    parser.add_argument("--auto-resume", action="store_true", help="自动从已有 checkpoint/best/final 继续训练")
    parser.add_argument("--no-preset", action="store_true", help="禁用角色默认超参数")
    return parser



def _resolve_value(args, arg_name: str, config_name: str, preset: dict, base: TrainConfig):
    value = getattr(args, arg_name, None)
    if value is not None:
        return value
    if config_name in preset:
        return preset[config_name]
    return getattr(base, config_name)



def build_config_from_args(
    args,
    forced_character: str | None = None,
    use_character_preset: bool = True,
) -> TrainConfig:
    """将命令行参数转换为训练配置。"""
    character = forced_character or getattr(args, "character", "Ironclad")
    base = TrainConfig(character=character)
    preset = CHARACTER_PRESETS.get(character, {}) if use_character_preset else {}
    log_dir = args.log_dir or os.path.join("logs", character)
    save_dir = args.save_dir or os.path.join("models", character)
    return TrainConfig(
        character=character,
        total_timesteps=_resolve_value(args, "timesteps", "total_timesteps", preset, base),
        n_envs=_resolve_value(args, "n_envs", "n_envs", preset, base),
        learning_rate=_resolve_value(args, "lr", "learning_rate", preset, base),
        n_steps=_resolve_value(args, "n_steps", "n_steps", preset, base),
        batch_size=_resolve_value(args, "batch_size", "batch_size", preset, base),
        n_epochs=base.n_epochs,
        gamma=base.gamma,
        gae_lambda=base.gae_lambda,
        clip_range=base.clip_range,
        ent_coef=base.ent_coef,
        vf_coef=base.vf_coef,
        max_grad_norm=base.max_grad_norm,
        seed=_resolve_value(args, "seed", "seed", preset, base),
        log_dir=log_dir,
        save_dir=save_dir,
        eval_freq=_resolve_value(args, "eval_freq", "eval_freq", preset, base),
        eval_episodes=_resolve_value(args, "eval_episodes", "eval_episodes", preset, base),
        post_eval_episodes=_resolve_value(
            args,
            "post_eval_episodes",
            "post_eval_episodes",
            preset,
            base,
        ),
    )



def run_post_training_evaluation(model, cfg: TrainConfig) -> dict[str, float | int | str]:
    """训练完成后做一轮确定性评估，并返回汇总指标。"""
    from sts_env.env import StsEnv

    episodes = max(int(cfg.post_eval_episodes), 0)
    if episodes == 0:
        return {
            "character": cfg.character,
            "episodes": 0,
            "wins": 0,
            "win_rate": 0.0,
            "avg_floor": 0.0,
            "avg_hp": 0.0,
        }

    env = ActionMaskWrapper(StsEnv(character=cfg.character, seed=cfg.seed + 10_000))
    wins = 0
    total_floors = 0
    total_hp = 0

    for ep in range(episodes):
        obs, info = env.reset(seed=cfg.seed + 10_000 + ep)
        done = False
        while not done:
            action_masks = env.action_masks()
            action, _ = model.predict(obs, deterministic=True, action_masks=action_masks)
            obs, reward, terminated, truncated, info = env.step(int(action))
            done = terminated or truncated

        wins += int(info.get("won", False))
        total_floors += int(info.get("floor", 0))
        total_hp += max(0, int(info.get("hp", 0)))

    env.close()
    return {
        "character": cfg.character,
        "episodes": episodes,
        "wins": wins,
        "win_rate": wins / episodes,
        "avg_floor": total_floors / episodes,
        "avg_hp": total_hp / episodes,
    }


def callback_trigger_freq(target_timesteps: int, n_envs: int) -> int:
    """将“按 timesteps 理解的目标频率”换算成 vec env callback 触发频率。"""
    return max(int(target_timesteps) // max(int(n_envs), 1), 1)


def resolve_training_artifact_paths(
    save_dir: str | Path,
    final_model_path: str | Path,
) -> tuple[str, str | None, str]:
    """解析当前训练输出目录下的 final/best/preferred 模型路径。"""
    final_path = Path(final_model_path)
    best_path = Path(save_dir) / "best" / "best_model.zip"
    best_model_path = str(best_path) if best_path.exists() else None
    preferred_model_path = best_model_path or str(final_path)
    return str(final_path), best_model_path, preferred_model_path


def list_training_checkpoints(character: str, save_dir: str | Path) -> list[Path]:
    """返回当前角色可用于续训的 checkpoint，按最新优先排序。"""
    checkpoints_dir = Path(save_dir) / "checkpoints"
    if not checkpoints_dir.exists():
        return []

    pattern = f"sts2_{character}_*.zip"

    def _sort_key(path: Path) -> tuple[int, float]:
        match = re.search(r"_(\d+)_steps$", path.stem)
        steps = int(match.group(1)) if match else -1
        return steps, path.stat().st_mtime

    return sorted(checkpoints_dir.glob(pattern), key=_sort_key, reverse=True)


def resolve_resume_source(
    character: str,
    save_dir: str | Path,
    resume_from: str | Path | None = None,
    *,
    auto_resume: bool = False,
) -> Path | None:
    """解析续训来源，优先显式路径，其次自动选择最新 checkpoint。"""
    if resume_from is not None:
        candidate = Path(resume_from).expanduser()
        if not candidate.exists():
            raise FileNotFoundError(f"续训模型不存在: {candidate}")
        return candidate

    if not auto_resume:
        return None

    checkpoints = list_training_checkpoints(character, save_dir)
    if checkpoints:
        return checkpoints[0]

    _, best_model_path, preferred_model_path = resolve_training_artifact_paths(
        save_dir,
        Path(save_dir) / f"sts2_{character}_final.zip",
    )
    if best_model_path is not None:
        return Path(best_model_path)

    final_path = Path(preferred_model_path)
    if final_path.exists():
        return final_path
    return None


def build_policy_kwargs() -> dict:
    from agent.network import StsFeatureExtractor

    return {
        "features_extractor_class": StsFeatureExtractor,
        "features_extractor_kwargs": {"features_dim": 256},
        "net_arch": dict(pi=[256, 128], vf=[256, 128]),
    }


def build_model(cfg: TrainConfig, train_env):
    from sb3_contrib import MaskablePPO

    return MaskablePPO(
        "MlpPolicy",
        train_env,
        learning_rate=cfg.learning_rate,
        n_steps=cfg.n_steps,
        batch_size=cfg.batch_size,
        n_epochs=cfg.n_epochs,
        gamma=cfg.gamma,
        gae_lambda=cfg.gae_lambda,
        clip_range=cfg.clip_range,
        ent_coef=cfg.ent_coef,
        vf_coef=cfg.vf_coef,
        max_grad_norm=cfg.max_grad_norm,
        policy_kwargs=build_policy_kwargs(),
        verbose=1,
        tensorboard_log=cfg.log_dir,
        seed=cfg.seed,
    )


def save_training_summary(
    cfg: TrainConfig,
    final_model_path: str,
    post_eval: dict[str, float | int | str],
    *,
    best_model_path: str | None = None,
    preferred_model_path: str | None = None,
) -> dict:
    """保存训练摘要。"""
    normalized_preferred_model_path = preferred_model_path or best_model_path or final_model_path
    summary = {
        "character": cfg.character,
        "config": asdict(cfg),
        "final_model_path": final_model_path,
        "best_model_path": best_model_path,
        "preferred_model_path": normalized_preferred_model_path,
        "post_eval": post_eval,
    }
    summary_path = Path(cfg.save_dir) / "training_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary["summary_path"] = str(summary_path)
    return summary



def train(
    cfg: TrainConfig,
    *,
    resume_from: str | Path | None = None,
    auto_resume: bool = False,
) -> dict:
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
    from stable_baselines3.common.callbacks import CheckpointCallback
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.callbacks import MaskableEvalCallback

    os.makedirs(cfg.log_dir, exist_ok=True)
    os.makedirs(cfg.save_dir, exist_ok=True)

    if cfg.n_envs > 1:
        train_env = SubprocVecEnv([make_env(cfg.character, cfg.seed, i) for i in range(cfg.n_envs)])
    else:
        train_env = DummyVecEnv([make_env(cfg.character, cfg.seed, 0)])

    eval_env = DummyVecEnv([make_env(cfg.character, cfg.seed + 1000, 0)])
    eval_freq = callback_trigger_freq(cfg.eval_freq, cfg.n_envs)
    checkpoint_freq = callback_trigger_freq(max(cfg.eval_freq * 5, 1), cfg.n_envs)
    model = build_model(cfg, train_env)
    resume_source = resolve_resume_source(
        cfg.character,
        cfg.save_dir,
        resume_from,
        auto_resume=auto_resume,
    )
    if resume_source is not None:
        print(f"从已有模型继续训练（保留当前超参数）: {resume_source}")
        model.set_parameters(str(resume_source), exact_match=False, device="auto")

    eval_callback = MaskableEvalCallback(
        eval_env,
        best_model_save_path=os.path.join(cfg.save_dir, "best"),
        log_path=cfg.log_dir,
        eval_freq=eval_freq,
        n_eval_episodes=cfg.eval_episodes,
        deterministic=True,
    )
    checkpoint_callback = CheckpointCallback(
        save_freq=checkpoint_freq,
        save_path=os.path.join(cfg.save_dir, "checkpoints"),
        name_prefix=f"sts2_{cfg.character}",
    )

    print(f"开始训练: {cfg.character}, {cfg.total_timesteps} steps, {cfg.n_envs} envs")
    model.learn(
        total_timesteps=cfg.total_timesteps,
        callback=[eval_callback, checkpoint_callback],
        progress_bar=True,
    )

    final_path = Path(cfg.save_dir) / f"sts2_{cfg.character}_final"
    model.save(str(final_path))
    final_model_path, best_model_path, preferred_model_path = resolve_training_artifact_paths(
        cfg.save_dir,
        final_path.with_suffix(".zip"),
    )
    print(f"模型已保存: {final_model_path}")

    model_for_eval = model
    if best_model_path is not None and Path(best_model_path) != Path(final_model_path):
        from sb3_contrib import MaskablePPO

        model_for_eval = MaskablePPO.load(best_model_path)

    post_eval = run_post_training_evaluation(model_for_eval, cfg)
    print(
        f"训练后评估: win_rate={post_eval['win_rate']:.2%}, "
        f"avg_floor={post_eval['avg_floor']:.1f}, avg_hp={post_eval['avg_hp']:.1f}"
    )

    summary = save_training_summary(
        cfg,
        final_model_path,
        post_eval,
        best_model_path=best_model_path,
        preferred_model_path=preferred_model_path,
    )
    print(f"训练摘要已保存: {summary['summary_path']}")

    train_env.close()
    eval_env.close()
    return summary



def main(
    default_character: str | None = None,
    include_character_arg: bool = True,
):
    default_character = default_character or "Ironclad"
    parser = build_parser(
        default_character=default_character,
        include_character_arg=include_character_arg,
    )
    args = parser.parse_args()
    forced_character = default_character if not include_character_arg else None
    cfg = build_config_from_args(
        args,
        forced_character=forced_character,
        use_character_preset=not args.no_preset,
    )
    train(
        cfg,
        resume_from=args.resume_from,
        auto_resume=args.auto_resume,
    )


if __name__ == "__main__":
    main()
