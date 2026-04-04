"""角色常量与默认模型路径约定。"""
from __future__ import annotations

from pathlib import Path

from sts_env.game_state import PLAYABLE_CHARACTERS

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
CHARACTERS = list(PLAYABLE_CHARACTERS)


def ensure_character(character: str) -> str:
    normalized = str(character)
    if normalized not in CHARACTERS:
        raise ValueError(f"未知角色: {character}")
    return normalized


def resolve_character_models_dir(character: str, models_dir: str | Path = MODELS_DIR) -> Path:
    return Path(models_dir) / ensure_character(character)


def resolve_final_model_path(character: str, models_dir: str | Path = MODELS_DIR) -> Path:
    normalized = ensure_character(character)
    return resolve_character_models_dir(normalized, models_dir) / f"sts2_{normalized}_final.zip"


def resolve_best_model_path(character: str, models_dir: str | Path = MODELS_DIR) -> Path:
    return resolve_character_models_dir(character, models_dir) / "best" / "best_model.zip"


def resolve_preferred_model_path(character: str, models_dir: str | Path = MODELS_DIR) -> Path:
    best_path = resolve_best_model_path(character, models_dir)
    if best_path.exists():
        return best_path
    return resolve_final_model_path(character, models_dir)


def resolve_model_path(character: str, models_dir: str | Path = MODELS_DIR) -> Path:
    """默认供 bridge/评估加载的模型路径，优先 best，否则回退 final。"""
    return resolve_preferred_model_path(character, models_dir)
