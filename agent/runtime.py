"""推理运行时：为桥接层提供统一的模型加载与动作选择接口。"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from agent.model_paths import (
    CHARACTERS,
    MODELS_DIR,
    resolve_best_model_path,
    resolve_final_model_path,
    resolve_model_path,
    resolve_preferred_model_path,
)
from sts_env.encoding import get_obs_dim
from sts_env.env import TOTAL_ACTIONS


@dataclass
class BridgeRequest:
    character: str
    observation: list[float]
    action_mask: list[bool]
    deterministic: bool = False
    request_id: str | None = None


@dataclass
class BridgeResponse:
    character: str
    action: int
    model_path: str
    request_id: str | None = None

def ensure_observation_array(observation: Sequence[float] | np.ndarray) -> np.ndarray:
    arr = np.asarray(observation, dtype=np.float32)
    expected_shape = (get_obs_dim(),)
    if arr.shape != expected_shape:
        raise ValueError(f"observation 维度错误，期望 {expected_shape}，实际 {arr.shape}")
    return arr



def ensure_action_mask_array(action_mask: Sequence[bool] | np.ndarray) -> np.ndarray:
    arr = np.asarray(action_mask, dtype=bool)
    expected_shape = (TOTAL_ACTIONS,)
    if arr.shape != expected_shape:
        raise ValueError(f"action_mask 维度错误，期望 {expected_shape}，实际 {arr.shape}")
    if not arr.any():
        raise ValueError("action_mask 至少需要一个合法动作")
    return arr



def request_from_dict(payload: dict[str, Any]) -> BridgeRequest:
    return BridgeRequest(
        character=str(payload["character"]),
        observation=list(payload["observation"]),
        action_mask=list(payload["action_mask"]),
        deterministic=bool(payload.get("deterministic", False)),
        request_id=payload.get("request_id"),
    )



def response_to_dict(response: BridgeResponse) -> dict[str, Any]:
    return {
        "character": response.character,
        "action": response.action,
        "model_path": response.model_path,
        "request_id": response.request_id,
    }


class PolicyRuntime:
    """加载训练模型，并基于外部提供的观测与 action mask 选动作。"""

    def __init__(self, model: Any, character: str, model_path: str | Path):
        self.model = model
        self.character = character
        self.model_path = Path(model_path)

    @classmethod
    def load(cls, model_path: str | Path, character: str):
        from sb3_contrib import MaskablePPO

        resolved_path = Path(model_path)
        model = MaskablePPO.load(str(resolved_path))
        return cls(model=model, character=character, model_path=resolved_path)

    @classmethod
    def load_for_character(cls, character: str, models_dir: Path = MODELS_DIR):
        return cls.load(resolve_model_path(character, models_dir=models_dir), character)

    def predict(
        self,
        observation: Sequence[float] | np.ndarray,
        action_mask: Sequence[bool] | np.ndarray,
        deterministic: bool = False,
    ) -> int:
        obs = ensure_observation_array(observation)
        mask = ensure_action_mask_array(action_mask)
        model_obs = self._coerce_observation_for_model(obs)
        action, _ = self.model.predict(model_obs, deterministic=deterministic, action_masks=mask)
        return int(action)

    def _coerce_observation_for_model(self, observation: np.ndarray) -> np.ndarray:
        expected_dim = get_obs_dim()
        obs_space = getattr(self.model, "observation_space", None)
        if obs_space is not None and getattr(obs_space, "shape", None):
            shape = tuple(int(item) for item in obs_space.shape)
            if len(shape) == 1 and shape[0] > 0:
                expected_dim = shape[0]

        if observation.shape == (expected_dim,):
            return observation
        if observation.shape[0] > expected_dim:
            return observation[:expected_dim]

        padded = np.zeros(expected_dim, dtype=np.float32)
        padded[:observation.shape[0]] = observation
        return padded

    def handle_request(self, request: BridgeRequest) -> BridgeResponse:
        if request.character != self.character:
            raise ValueError(f"角色不匹配: request={request.character}, runtime={self.character}")
        action = self.predict(
            request.observation,
            request.action_mask,
            deterministic=request.deterministic,
        )
        return BridgeResponse(
            character=self.character,
            action=action,
            model_path=str(self.model_path),
            request_id=request.request_id,
        )
