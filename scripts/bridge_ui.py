"""本地 bridge 控制面板：管理角色、模型覆盖与 bridge 进程。"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.model_paths import (
    CHARACTERS,
    MODELS_DIR,
    resolve_best_model_path,
    resolve_final_model_path,
    resolve_model_path,
    resolve_preferred_model_path,
)
from bridge.control_state import BridgeControlStateStore, DEFAULT_CONTROL_STATE_PATH

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_CLIENT_SCRIPT = ROOT / "bridge" / "bridge_client.py"
POLL_INTERVAL_MS = 1000


def load_character_training_summary(character: str, models_dir: str | Path = MODELS_DIR) -> dict | None:
    summary_path = Path(models_dir) / character / "training_summary.json"
    if not summary_path.exists():
        return None
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def list_character_model_paths(character: str, models_dir: str | Path = MODELS_DIR) -> list[Path]:
    character_dir = Path(models_dir) / character
    seen: set[Path] = set()
    discovered: list[Path] = []

    def _add(path: Path):
        normalized = path.resolve() if path.exists() else path
        if not path.exists() or path.suffix.lower() != ".zip" or normalized in seen:
            return
        seen.add(normalized)
        discovered.append(path)

    preferred_path = resolve_preferred_model_path(character, models_dir)
    best_path = resolve_best_model_path(character, models_dir)
    final_path = resolve_final_model_path(character, models_dir)
    _add(preferred_path)
    _add(best_path)
    _add(final_path)

    checkpoints_dir = character_dir / "checkpoints"
    if checkpoints_dir.exists():
        def _checkpoint_sort_key(path: Path) -> tuple[int, str]:
            stem = path.stem
            marker = "_steps"
            if stem.endswith(marker):
                prefix = stem[: -len(marker)]
                digits = prefix.rsplit("_", 1)[-1]
                if digits.isdigit():
                    return int(digits), path.name
            return -1, path.name

        for path in sorted(checkpoints_dir.glob("*.zip"), key=_checkpoint_sort_key, reverse=True):
            _add(path)

    if character_dir.exists():
        for path in sorted(character_dir.rglob("*.zip")):
            _add(path)

    return discovered


def format_model_option_label(character: str, model_path: str | Path, models_dir: str | Path = MODELS_DIR) -> str:
    path = Path(model_path)
    preferred_path = resolve_preferred_model_path(character, models_dir)
    best_path = resolve_best_model_path(character, models_dir)
    final_path = resolve_final_model_path(character, models_dir)
    tag = "custom"
    if path == preferred_path and path.exists():
        tag = "preferred"
    elif path == best_path and path.exists():
        tag = "best"
    elif path == final_path and path.exists():
        tag = "final"
    elif "checkpoints" in path.parts:
        tag = "checkpoint"

    try:
        relative = path.relative_to(ROOT)
        display = str(relative)
    except ValueError:
        display = str(path)
    return f"[{tag}] {display}"


class BridgeControlPanel:
    def __init__(self, root: tk.Tk, *, host: str, port: int, control_state_path: str | Path):
        self.root = root
        self.root.title("STS Agent Bridge UI")
        self.root.minsize(1180, 620)

        self.control_state_store = BridgeControlStateStore(control_state_path)
        initial_character = CHARACTERS[0]
        self.state = self.control_state_store.ensure_initialized(desired_character=initial_character)
        self.process: subprocess.Popen | None = None

        desired_character = self.state.desired_character or initial_character
        initial_host = self.state.bridge_host or host
        initial_port = self.state.bridge_port or port
        self.host_var = tk.StringVar(value=initial_host)
        self.port_var = tk.StringVar(value=str(initial_port))
        self.control_state_path_var = tk.StringVar(value=str(self.control_state_store.path))
        self.desired_character_var = tk.StringVar(value=desired_character)

        self.bridge_status_var = tk.StringVar(value="未启动")
        self.pause_status_var = tk.StringVar(value="运行中" if not self.state.paused else "已暂停")
        self.target_character_status_var = tk.StringVar(value=desired_character)
        self.effective_model_status_var = tk.StringVar(value=self._effective_model_path(desired_character))
        self.last_request_var = tk.StringVar(value=self.state.last_request_id or "-")
        self.last_response_var = tk.StringVar(value=self.state.last_response_type or "-")
        self.last_error_var = tk.StringVar(value=self.state.last_error or "-")
        self.discovered_model_var = tk.StringVar(value="")
        self.discovered_model_summary_var = tk.StringVar(value="-")
        self.discovered_training_summary_var = tk.StringVar(value="-")
        self._discovered_model_options: dict[str, Path] = {}

        self.default_model_vars = {
            character: tk.StringVar(value=str(resolve_model_path(character)))
            for character in CHARACTERS
        }
        self.override_model_vars = {
            character: tk.StringVar(value=self.state.effective_model_path(character) or "")
            for character in CHARACTERS
        }

        self._build_layout()
        self._refresh_status_from_store()
        self.refresh_discovered_models()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_INTERVAL_MS, self._poll)

    def _build_layout(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        self._build_connection_section(outer)
        self._build_status_section(outer)
        self._build_models_section(outer)

    def _build_connection_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="Bridge 控制", padding=10)
        frame.grid(row=0, column=0, sticky="ew")
        for column in range(8):
            frame.columnconfigure(column, weight=1 if column in {1, 3, 5} else 0)

        ttk.Label(frame, text="Host").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.host_var, width=20).grid(row=0, column=1, sticky="ew", padx=(6, 16))

        ttk.Label(frame, text="Port").grid(row=0, column=2, sticky="w")
        ttk.Entry(frame, textvariable=self.port_var, width=10).grid(row=0, column=3, sticky="ew", padx=(6, 16))

        ttk.Label(frame, text="目标角色").grid(row=0, column=4, sticky="w")
        character_box = ttk.Combobox(
            frame,
            textvariable=self.desired_character_var,
            values=list(CHARACTERS),
            state="readonly",
            width=16,
        )
        character_box.grid(row=0, column=5, sticky="ew", padx=(6, 16))
        character_box.bind("<<ComboboxSelected>>", self._on_desired_character_changed)

        ttk.Button(frame, text="启动 bridge", command=self.start_bridge).grid(row=0, column=6, padx=(0, 6), sticky="ew")
        ttk.Button(frame, text="停止 bridge", command=self.stop_bridge).grid(row=0, column=7, sticky="ew")

        ttk.Button(frame, text="暂停自动游玩", command=self.pause_bridge).grid(row=1, column=6, padx=(0, 6), pady=(10, 0), sticky="ew")
        ttk.Button(frame, text="继续自动游玩", command=self.resume_bridge).grid(row=1, column=7, pady=(10, 0), sticky="ew")

        ttk.Label(frame, text="控制状态文件").grid(row=1, column=0, sticky="w", pady=(10, 0))
        state_entry = ttk.Entry(frame, textvariable=self.control_state_path_var, state="readonly")
        state_entry.grid(row=1, column=1, columnspan=5, sticky="ew", padx=(6, 16), pady=(10, 0))

    def _build_status_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="当前状态", padding=10)
        frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for column in range(4):
            frame.columnconfigure(column, weight=1)

        rows = [
            ("Bridge", self.bridge_status_var),
            ("暂停状态", self.pause_status_var),
            ("目标角色", self.target_character_status_var),
            ("当前有效模型", self.effective_model_status_var),
            ("最近 request_id", self.last_request_var),
            ("最近响应类型", self.last_response_var),
            ("最近错误", self.last_error_var),
        ]

        for idx, (label, variable) in enumerate(rows):
            row = idx // 2
            column = (idx % 2) * 2
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="nw", pady=(0, 8))
            ttk.Label(frame, textvariable=variable, wraplength=420, justify="left").grid(
                row=row,
                column=column + 1,
                sticky="nw",
                padx=(6, 16),
                pady=(0, 8),
            )

    def _build_models_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="每角色模型覆盖", padding=10)
        frame.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        picker = ttk.LabelFrame(frame, text="目标角色模型库", padding=10)
        picker.grid(row=0, column=0, sticky="ew")
        picker.columnconfigure(1, weight=1)

        ttk.Label(picker, text="已发现模型").grid(row=0, column=0, sticky="w")
        self.discovered_model_box = ttk.Combobox(
            picker,
            textvariable=self.discovered_model_var,
            state="readonly",
        )
        self.discovered_model_box.grid(row=0, column=1, sticky="ew", padx=(6, 8))
        self.discovered_model_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_discovered_model_metadata(self.desired_character_var.get().strip() or CHARACTERS[0]),
        )
        ttk.Button(picker, text="刷新列表", command=self.refresh_discovered_models).grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(picker, text="应用选中", command=self.apply_selected_model_for_target).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Button(picker, text="用推荐模型", command=self.apply_preferred_model_for_target).grid(row=0, column=4, sticky="ew", padx=(0, 8))
        ttk.Button(picker, text="浏览 ZIP", command=self.browse_model_for_target).grid(row=0, column=5, sticky="ew")

        ttk.Label(picker, text="当前条目").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(picker, textvariable=self.discovered_model_summary_var, wraplength=880, justify="left").grid(
            row=1,
            column=1,
            columnspan=5,
            sticky="nw",
            padx=(6, 0),
            pady=(8, 0),
        )
        ttk.Label(picker, text="训练摘要").grid(row=2, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(picker, textvariable=self.discovered_training_summary_var, wraplength=880, justify="left").grid(
            row=2,
            column=1,
            columnspan=5,
            sticky="nw",
            padx=(6, 0),
            pady=(8, 0),
        )

        table = ttk.Frame(frame)
        table.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        table.columnconfigure(1, weight=1)
        table.columnconfigure(2, weight=1)

        headers = ["角色", "默认模型", "覆盖模型", "操作"]
        for column, header in enumerate(headers):
            ttk.Label(table, text=header).grid(row=0, column=column, sticky="w", padx=(0, 8), pady=(0, 8))

        for row, character in enumerate(CHARACTERS, start=1):
            ttk.Label(table, text=character).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)

            default_entry = ttk.Entry(table, textvariable=self.default_model_vars[character], state="readonly", width=42)
            default_entry.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=4)

            override_entry = ttk.Entry(table, textvariable=self.override_model_vars[character], width=42)
            override_entry.grid(row=row, column=2, sticky="ew", padx=(0, 8), pady=4)

            actions = ttk.Frame(table)
            actions.grid(row=row, column=3, sticky="w", pady=4)
            ttk.Button(actions, text="浏览", command=lambda c=character: self.browse_model(c)).pack(side="left")
            ttk.Button(actions, text="应用", command=lambda c=character: self.apply_model_override(c)).pack(side="left", padx=6)
            ttk.Button(actions, text="恢复默认", command=lambda c=character: self.clear_model_override(c)).pack(side="left")

    def _effective_model_path(self, character: str) -> str:
        state = self.control_state_store.load()
        return state.effective_model_path(character) or str(resolve_model_path(character))

    def _refresh_default_model_paths(self):
        for character in CHARACTERS:
            self.default_model_vars[character].set(str(resolve_model_path(character)))

    def _refresh_discovered_model_metadata(self, character: str):
        label = self.discovered_model_var.get().strip()
        path = self._discovered_model_options.get(label)
        self.discovered_model_summary_var.set(str(path) if path is not None else "-")

        summary = load_character_training_summary(character)
        if summary is None:
            self.discovered_training_summary_var.set("暂无 training_summary.json")
            return

        post_eval = summary.get("post_eval") or {}
        self.discovered_training_summary_var.set(
            f"preferred={summary.get('preferred_model_path') or '-'} | "
            f"win_rate={float(post_eval.get('win_rate', 0.0)):.2%}, "
            f"avg_floor={float(post_eval.get('avg_floor', 0.0)):.1f}, "
            f"avg_hp={float(post_eval.get('avg_hp', 0.0)):.1f}"
        )

    def refresh_discovered_models(self):
        character = self.desired_character_var.get().strip() or CHARACTERS[0]
        options: dict[str, Path] = {}
        labels: list[str] = []
        for path in list_character_model_paths(character):
            label = format_model_option_label(character, path)
            options[label] = path
            labels.append(label)

        self._discovered_model_options = options
        self.discovered_model_box.configure(values=labels)
        if labels:
            current_override = self.override_model_vars[character].get().strip()
            if current_override:
                for label, path in options.items():
                    if str(path) == current_override:
                        self.discovered_model_var.set(label)
                        break
                else:
                    self.discovered_model_var.set(labels[0])
            else:
                self.discovered_model_var.set(labels[0])
        else:
            self.discovered_model_var.set("")
        self._refresh_discovered_model_metadata(character)

    def apply_selected_model_for_target(self):
        character = self.desired_character_var.get().strip() or CHARACTERS[0]
        label = self.discovered_model_var.get().strip()
        model_path = self._discovered_model_options.get(label)
        if model_path is None:
            messagebox.showerror("模型未选择", "请先从已发现模型列表中选择一个模型。")
            return
        self.override_model_vars[character].set(str(model_path))
        self.apply_model_override(character)

    def apply_preferred_model_for_target(self):
        character = self.desired_character_var.get().strip() or CHARACTERS[0]
        model_path = resolve_model_path(character)
        if not model_path.exists():
            messagebox.showerror("模型不存在", f"推荐模型不存在：\n{model_path}")
            return
        self.override_model_vars[character].set(str(model_path))
        self.apply_model_override(character)

    def browse_model_for_target(self):
        character = self.desired_character_var.get().strip() or CHARACTERS[0]
        self.browse_model(character)

    def _refresh_status_from_store(self):
        self.state = self.control_state_store.load()
        desired_character = self.state.desired_character or self.desired_character_var.get() or CHARACTERS[0]
        self._refresh_default_model_paths()
        self.pause_status_var.set("已暂停" if self.state.paused else "运行中")
        self.target_character_status_var.set(desired_character)
        self.effective_model_status_var.set(self._effective_model_path(desired_character))
        self.last_request_var.set(self.state.last_request_id or "-")
        self.last_response_var.set(self.state.last_response_type or "-")
        self.last_error_var.set(self.state.last_error or "-")
        if self.desired_character_var.get() != desired_character:
            self.desired_character_var.set(desired_character)

    def _update_bridge_status(self):
        if self.process is None:
            self.bridge_status_var.set("未启动")
            return
        return_code = self.process.poll()
        if return_code is None:
            self.bridge_status_var.set(f"运行中 ws://{self.host_var.get().strip() or 'localhost'}:{self.port_var.get().strip()}")
            return
        self.bridge_status_var.set(f"已退出（code={return_code}）")
        self.process = None

    def _poll(self):
        self._update_bridge_status()
        self._refresh_status_from_store()
        self.root.after(POLL_INTERVAL_MS, self._poll)

    def _on_desired_character_changed(self, _event=None):
        character = self.desired_character_var.get().strip()
        if character not in CHARACTERS:
            return
        self.control_state_store.set_desired_character(character)
        self._refresh_status_from_store()
        self.refresh_discovered_models()

    def _normalized_host_and_port(self) -> tuple[str, int] | None:
        host = self.host_var.get().strip() or "localhost"
        port_text = self.port_var.get().strip()
        try:
            port = int(port_text)
        except ValueError:
            messagebox.showerror("端口错误", "Port 必须是整数。")
            return None
        if not 1 <= port <= 65535:
            messagebox.showerror("端口错误", "Port 必须在 1-65535 之间。")
            return None
        return host, port

    def start_bridge(self):
        if self.process is not None and self.process.poll() is None:
            self._update_bridge_status()
            return

        normalized = self._normalized_host_and_port()
        if normalized is None:
            return
        host, port = normalized

        desired_character = self.desired_character_var.get().strip() or CHARACTERS[0]
        self.control_state_store.set_desired_character(desired_character)
        self.control_state_store.set_bridge_endpoint(host, port)

        command = [
            sys.executable,
            str(BRIDGE_CLIENT_SCRIPT),
            "--host",
            host,
            "--port",
            str(port),
            "--character",
            desired_character,
            "--control-state",
            str(self.control_state_store.path),
        ]

        self.process = subprocess.Popen(command, cwd=str(ROOT))
        self._update_bridge_status()

    def stop_bridge(self):
        if self.process is None:
            self._update_bridge_status()
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self._update_bridge_status()

    def pause_bridge(self):
        self.control_state_store.set_paused(True)
        self._refresh_status_from_store()

    def resume_bridge(self):
        self.control_state_store.set_paused(False)
        self._refresh_status_from_store()

    def browse_model(self, character: str):
        selected = filedialog.askopenfilename(
            title=f"选择 {character} 模型文件",
            filetypes=[("模型文件", "*.zip"), ("所有文件", "*.*")],
            initialdir=str(ROOT / "models" / character),
        )
        if not selected:
            return
        self.override_model_vars[character].set(selected)
        self.apply_model_override(character)

    def apply_model_override(self, character: str):
        text = self.override_model_vars[character].get().strip()
        if not text:
            self.clear_model_override(character)
            return

        model_path = Path(text).expanduser()
        if not model_path.exists():
            messagebox.showerror("模型不存在", f"找不到模型文件：\n{model_path}")
            return

        self.control_state_store.set_model_override(character, model_path)
        self.override_model_vars[character].set(str(model_path))
        self._refresh_status_from_store()
        if character == (self.desired_character_var.get().strip() or CHARACTERS[0]):
            self.refresh_discovered_models()

    def clear_model_override(self, character: str):
        self.control_state_store.set_model_override(character, None)
        self.override_model_vars[character].set("")
        self._refresh_status_from_store()
        if character == (self.desired_character_var.get().strip() or CHARACTERS[0]):
            self.refresh_discovered_models()

    def _on_close(self):
        self.stop_bridge()
        self.root.destroy()



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 STS Agent 本地 bridge 控制面板")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--control-state", default=str(DEFAULT_CONTROL_STATE_PATH), help="控制状态 JSON 路径")
    return parser



def main():
    args = build_parser().parse_args()
    root = tk.Tk()
    BridgeControlPanel(root, host=args.host, port=args.port, control_state_path=args.control_state)
    root.mainloop()


if __name__ == "__main__":
    main()
