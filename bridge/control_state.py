"""本地 UI 与 WebSocket bridge 共享的控制状态。"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from agent.model_paths import CHARACTERS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTROL_STATE_PATH = ROOT / "bridge_control_state.json"
_VALID_CHARACTERS = set(CHARACTERS)



def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None



def _validate_character(character: str) -> str:
    normalized = str(character)
    if normalized not in _VALID_CHARACTERS:
        raise ValueError(f"未知角色: {character}")
    return normalized



def _normalize_model_overrides(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, str] = {}
    for character, model_path in payload.items():
        if str(character) not in _VALID_CHARACTERS:
            continue
        text = _optional_string(model_path)
        if text is None:
            continue
        normalized[str(character)] = str(Path(text).expanduser())
    return normalized


@dataclass(slots=True)
class BridgeControlState:
    paused: bool = False
    desired_character: str | None = None
    bridge_host: str | None = None
    bridge_port: int | None = None
    model_overrides: dict[str, str] = field(default_factory=dict)
    last_request_id: str | None = None
    last_response_type: str | None = None
    last_error: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "BridgeControlState":
        payload = payload or {}
        desired_character = _optional_string(payload.get("desired_character"))
        if desired_character is not None and desired_character not in _VALID_CHARACTERS:
            desired_character = None
        return cls(
            paused=bool(payload.get("paused", False)),
            desired_character=desired_character,
            bridge_host=_optional_string(payload.get("bridge_host")),
            bridge_port=int(payload["bridge_port"]) if payload.get("bridge_port") is not None else None,
            model_overrides=_normalize_model_overrides(payload.get("model_overrides")),
            last_request_id=_optional_string(payload.get("last_request_id")),
            last_response_type=_optional_string(payload.get("last_response_type")),
            last_error=_optional_string(payload.get("last_error")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "desired_character": self.desired_character,
            "bridge_host": self.bridge_host,
            "bridge_port": self.bridge_port,
            "model_overrides": dict(self.model_overrides),
            "last_request_id": self.last_request_id,
            "last_response_type": self.last_response_type,
            "last_error": self.last_error,
        }

    def effective_model_path(self, character: str) -> str | None:
        return self.model_overrides.get(str(character))


class BridgeControlStateStore:
    """通过 JSON 文件持久化 UI 控制状态。"""

    def __init__(self, path: str | Path = DEFAULT_CONTROL_STATE_PATH):
        self.path = Path(path)
        self._lock = Lock()

    def _read_unlocked(self) -> BridgeControlState:
        if not self.path.exists():
            return BridgeControlState()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return BridgeControlState()

        if not isinstance(payload, dict):
            return BridgeControlState()
        return BridgeControlState.from_dict(payload)

    def _write_unlocked(self, state: BridgeControlState):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    def load(self) -> BridgeControlState:
        with self._lock:
            return self._read_unlocked()

    def save(self, state: BridgeControlState | dict[str, Any]) -> BridgeControlState:
        if isinstance(state, BridgeControlState):
            normalized = BridgeControlState.from_dict(state.to_dict())
        else:
            normalized = BridgeControlState.from_dict(state)

        with self._lock:
            self._write_unlocked(normalized)
        return normalized

    def ensure_initialized(self, *, desired_character: str | None = None) -> BridgeControlState:
        with self._lock:
            state = self._read_unlocked()
            if desired_character is not None and state.desired_character is None:
                state.desired_character = _validate_character(desired_character)
            if not self.path.exists() or desired_character is not None:
                self._write_unlocked(state)
            return BridgeControlState.from_dict(state.to_dict())

    def update(self, **changes: Any) -> BridgeControlState:
        with self._lock:
            payload = self._read_unlocked().to_dict()
            payload.update(changes)
            normalized = BridgeControlState.from_dict(payload)
            self._write_unlocked(normalized)
            return normalized

    def set_paused(self, paused: bool) -> BridgeControlState:
        return self.update(paused=bool(paused))

    def set_desired_character(self, character: str) -> BridgeControlState:
        return self.update(desired_character=_validate_character(character))

    def set_bridge_endpoint(self, host: str | None, port: int | None) -> BridgeControlState:
        normalized_host = _optional_string(host)
        normalized_port = None if port is None else int(port)
        return self.update(bridge_host=normalized_host, bridge_port=normalized_port)

    def set_model_override(self, character: str, model_path: str | Path | None) -> BridgeControlState:
        validated_character = _validate_character(character)
        with self._lock:
            state = self._read_unlocked()
            overrides = dict(state.model_overrides)
            text = _optional_string(model_path)
            if text is None:
                overrides.pop(validated_character, None)
            else:
                overrides[validated_character] = str(Path(text).expanduser())
            payload = state.to_dict()
            payload["model_overrides"] = overrides
            normalized = BridgeControlState.from_dict(payload)
            self._write_unlocked(normalized)
            return normalized

    def record_bridge_result(self, response: dict[str, Any]) -> BridgeControlState:
        return self.update(
            last_request_id=response.get("request_id"),
            last_response_type=response.get("type"),
            last_error=response.get("error") if not response.get("ok") else None,
        )
