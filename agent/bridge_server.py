"""桥接协议与本地 JSONL 服务端。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable, TextIO

from agent.runtime import (
    MODELS_DIR,
    BridgeRequest,
    PolicyRuntime,
    request_from_dict,
    resolve_model_path,
    response_to_dict,
)
from agent.model_paths import CHARACTERS
from sts_env.encoding import get_obs_dim
from sts_env.env import TOTAL_ACTIONS

PROTOCOL_VERSION = 1


RuntimeLoader = Callable[[str, Path], PolicyRuntime]


class RuntimeRegistry:
    """按角色与模型路径缓存已加载的运行时。"""

    def __init__(
        self,
        models_dir: str | Path = MODELS_DIR,
        runtime_loader: RuntimeLoader | None = None,
    ):
        self.models_dir = Path(models_dir)
        self.runtime_loader = runtime_loader or self._default_loader
        self._cache: dict[tuple[str, str], PolicyRuntime] = {}

    def _default_loader(self, character: str, model_path: Path) -> PolicyRuntime:
        return PolicyRuntime.load(model_path, character)

    def get_runtime(self, character: str, model_path: str | Path | None = None) -> PolicyRuntime:
        resolved_path = Path(model_path) if model_path is not None else resolve_model_path(character, self.models_dir)
        cache_key = (character, str(resolved_path))
        if cache_key not in self._cache:
            self._cache[cache_key] = self.runtime_loader(character, resolved_path)
        return self._cache[cache_key]

    def cache_size(self) -> int:
        return len(self._cache)


class BridgeServer:
    """通过 JSON Lines 与外部游戏进程通信。"""

    def __init__(self, registry: RuntimeRegistry | None = None):
        self.registry = registry or RuntimeRegistry()

    def _ok_response(self, response_type: str, **payload) -> dict[str, Any]:
        response = {
            "ok": True,
            "type": response_type,
            "protocol_version": PROTOCOL_VERSION,
        }
        response.update(payload)
        return response

    def _error_response(
        self,
        error: str,
        request_id: str | None = None,
        code: str = "bad_request",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "type": "error",
            "protocol_version": PROTOCOL_VERSION,
            "code": code,
            "error": error,
            "request_id": request_id,
        }

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = message.get("request_id")
        try:
            message_type = message.get("type", "act")
            if message_type == "ping":
                return self._ok_response("pong", request_id=request_id)

            if message_type == "describe":
                return self._ok_response(
                    "describe",
                    request_id=request_id,
                    characters=CHARACTERS,
                    observation_dim=get_obs_dim(),
                    total_actions=TOTAL_ACTIONS,
                )

            if message_type == "load":
                character = str(message["character"])
                runtime = self.registry.get_runtime(character, message.get("model_path"))
                return self._ok_response(
                    "loaded",
                    request_id=request_id,
                    character=character,
                    model_path=str(runtime.model_path),
                )

            if message_type == "act":
                request = request_from_dict(message)
                runtime = self.registry.get_runtime(request.character, message.get("model_path"))
                response = response_to_dict(runtime.handle_request(request))
                return self._ok_response("action", **response)

            if message_type == "shutdown":
                return self._ok_response("shutdown", request_id=request_id)

            raise ValueError(f"不支持的消息类型: {message_type}")
        except Exception as exc:
            return self._error_response(str(exc), request_id=request_id)

    def handle_line(self, line: str) -> dict[str, Any]:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            return self._error_response(f"JSON 解析失败: {exc}", code="json_error")

        if not isinstance(message, dict):
            return self._error_response("消息必须是 JSON 对象", code="bad_message")
        return self.handle_message(message)

    def serve_forever(self, input_stream: TextIO, output_stream: TextIO):
        for raw_line in input_stream:
            line = raw_line.strip()
            if not line:
                continue
            response = self.handle_line(line)
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
            if response.get("type") == "shutdown" and response.get("ok"):
                break

    def preload(self, characters: list[str]):
        for character in characters:
            self.registry.get_runtime(character)
