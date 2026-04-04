"""超参数配置。"""
from dataclasses import dataclass


@dataclass
class TrainConfig:
    character: str = "Ironclad"
    total_timesteps: int = 1_000_000
    n_envs: int = 8
    learning_rate: float = 3e-4
    n_steps: int = 2048
    batch_size: int = 256
    n_epochs: int = 10
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    seed: int = 42
    log_dir: str = "logs"
    save_dir: str = "models"
    eval_freq: int = 10_000
    eval_episodes: int = 20
    post_eval_episodes: int = 20
