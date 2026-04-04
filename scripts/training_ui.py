"""本地训练控制面板：可调参、可续训、可查看训练摘要。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.model_paths import CHARACTERS
from agent.train import CHARACTER_PRESETS, resolve_training_artifact_paths
from bridge.control_state import BridgeControlStateStore, DEFAULT_CONTROL_STATE_PATH

TRAIN_SCRIPT = ROOT / "agent" / "train.py"
TRAIN_ALL_SCRIPT = ROOT / "scripts" / "train_all.py"
EVALUATE_SCRIPT = ROOT / "agent" / "evaluate.py"
POLL_INTERVAL_MS = 200


def default_log_dir(character: str) -> str:
    return str(ROOT / "logs" / character)


def default_save_dir(character: str) -> str:
    return str(ROOT / "models" / character)


def load_training_summary(summary_path: str | Path) -> dict | None:
    path = Path(summary_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def load_character_training_summary(character: str, save_dir: str | Path | None = None) -> dict | None:
    resolved_dir = Path(save_dir) if save_dir is not None else Path(default_save_dir(character))
    return load_training_summary(resolved_dir / "training_summary.json")


def normalize_local_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    candidate = Path(path).expanduser()
    if candidate.exists():
        return candidate
    relative_candidate = ROOT / candidate
    if relative_candidate.exists():
        return relative_candidate
    return None


def resolve_preferred_training_model(character: str, save_dir: str | Path) -> Path | None:
    summary = load_character_training_summary(character, save_dir)
    if summary is not None:
        for key in ["preferred_model_path", "best_model_path", "final_model_path"]:
            resolved = normalize_local_path(summary.get(key))
            if resolved is not None:
                return resolved

    final_model_path = Path(save_dir) / f"sts2_{character}_final.zip"
    _, _, preferred_model_path = resolve_training_artifact_paths(save_dir, final_model_path)
    resolved = normalize_local_path(preferred_model_path)
    if resolved is not None:
        return resolved
    return None


def append_optional_arg(command: list[str], flag: str, value: str | None):
    if value is None or value == "":
        return
    command.extend([flag, value])


def build_training_command(
    *,
    run_mode: str,
    character: str,
    timesteps: str | None = None,
    n_envs: str | None = None,
    lr: str | None = None,
    seed: str | None = None,
    batch_size: str | None = None,
    n_steps: str | None = None,
    eval_freq: str | None = None,
    eval_episodes: str | None = None,
    post_eval_episodes: str | None = None,
    log_dir: str | None = None,
    save_dir: str | None = None,
    resume_from: str | None = None,
    auto_resume: bool = False,
    no_preset: bool = False,
) -> list[str]:
    if run_mode not in {"single", "all"}:
        raise ValueError(f"未知训练模式: {run_mode}")
    if run_mode == "all" and resume_from:
        raise ValueError("批量训练不支持指定单个 resume_from 路径")

    command = [sys.executable, str(TRAIN_SCRIPT if run_mode == "single" else TRAIN_ALL_SCRIPT)]
    if run_mode == "single":
        command.extend(["--character", character])

    append_optional_arg(command, "--timesteps", timesteps)
    append_optional_arg(command, "--n-envs", n_envs)
    append_optional_arg(command, "--lr", lr)
    append_optional_arg(command, "--seed", seed)
    append_optional_arg(command, "--batch-size", batch_size)
    append_optional_arg(command, "--n-steps", n_steps)
    append_optional_arg(command, "--eval-freq", eval_freq)
    append_optional_arg(command, "--eval-episodes", eval_episodes)
    append_optional_arg(command, "--post-eval-episodes", post_eval_episodes)

    if run_mode == "single":
        append_optional_arg(command, "--log-dir", log_dir)
        append_optional_arg(command, "--save-dir", save_dir)
        append_optional_arg(command, "--resume-from", resume_from)

    if auto_resume:
        command.append("--auto-resume")
    if no_preset:
        command.append("--no-preset")
    return command


def build_evaluation_command(
    *,
    character: str,
    save_dir: str | Path,
    episodes: str | int | None,
    seed: str | int | None,
    model_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> list[str]:
    resolved_model_path = normalize_local_path(model_path) if model_path is not None else resolve_preferred_training_model(character, save_dir)
    if resolved_model_path is None:
        raise FileNotFoundError(f"找不到可评估模型: character={character}, save_dir={save_dir}")

    resolved_output_path = Path(output_path) if output_path is not None else Path(save_dir) / "ui_eval.json"
    resolved_episodes = str(episodes) if episodes not in {None, ""} else "20"
    resolved_seed = str(seed) if seed not in {None, ""} else "42"
    return [
        sys.executable,
        str(EVALUATE_SCRIPT),
        "--character",
        character,
        "--model",
        str(resolved_model_path),
        "--episodes",
        resolved_episodes,
        "--seed",
        resolved_seed,
        "--output",
        str(resolved_output_path),
    ]


def apply_preferred_model_to_bridge(
    *,
    character: str,
    save_dir: str | Path,
    control_state_path: str | Path = DEFAULT_CONTROL_STATE_PATH,
) -> Path:
    resolved_model_path = resolve_preferred_training_model(character, save_dir)
    if resolved_model_path is None:
        raise FileNotFoundError(f"找不到可用于 bridge 的 preferred model: character={character}, save_dir={save_dir}")

    store = BridgeControlStateStore(control_state_path)
    store.ensure_initialized(desired_character=character)
    store.set_desired_character(character)
    store.set_model_override(character, resolved_model_path)
    return resolved_model_path


def determine_followup_action(
    *,
    task_kind: str | None,
    run_mode: str,
    return_code: int,
    auto_eval_after_train: bool,
    auto_apply_after_eval: bool,
) -> str | None:
    if return_code != 0 or run_mode != "single":
        return None
    if task_kind == "train":
        if auto_eval_after_train:
            return "evaluate"
        if auto_apply_after_eval:
            return "apply_bridge"
    if task_kind == "evaluate" and auto_apply_after_eval:
        return "apply_bridge"
    return None


class TrainingControlPanel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("STS Agent Training UI")
        self.root.minsize(1160, 760)

        self.process: subprocess.Popen | None = None
        self.output_queue: Queue[str] = Queue()
        self._output_thread: threading.Thread | None = None

        initial_character = CHARACTERS[0]
        self.run_mode_var = tk.StringVar(value="single")
        self.character_var = tk.StringVar(value=initial_character)
        self.no_preset_var = tk.BooleanVar(value=False)
        self.auto_resume_var = tk.BooleanVar(value=True)
        self.auto_eval_after_train_var = tk.BooleanVar(value=True)
        self.auto_apply_after_eval_var = tk.BooleanVar(value=False)
        self.log_dir_var = tk.StringVar(value=default_log_dir(initial_character))
        self.save_dir_var = tk.StringVar(value=default_save_dir(initial_character))
        self.resume_from_var = tk.StringVar(value="")
        self.bridge_control_state_var = tk.StringVar(value=str(DEFAULT_CONTROL_STATE_PATH))

        self.timesteps_var = tk.StringVar()
        self.n_envs_var = tk.StringVar()
        self.lr_var = tk.StringVar()
        self.seed_var = tk.StringVar()
        self.batch_size_var = tk.StringVar()
        self.n_steps_var = tk.StringVar()
        self.eval_freq_var = tk.StringVar()
        self.eval_episodes_var = tk.StringVar()
        self.post_eval_episodes_var = tk.StringVar()

        self.process_status_var = tk.StringVar(value="未启动")
        self.summary_status_var = tk.StringVar(value="暂无训练摘要")
        self.summary_model_var = tk.StringVar(value="-")
        self.summary_metrics_var = tk.StringVar(value="-")
        self.latest_eval_var = tk.StringVar(value="-")
        self.bridge_apply_status_var = tk.StringVar(value="-")
        self._last_output_path: Path | None = None
        self._active_task_kind: str | None = None

        self._single_only_widgets: list[tk.Widget] = []
        self._single_only_button_widgets: list[tk.Widget] = []
        self._apply_character_preset(initial_character)
        self._build_layout()
        self._refresh_summary()
        self._update_mode_state()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_INTERVAL_MS, self._poll_output)

    def _build_layout(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        self._build_run_section(outer)
        self._build_dir_section(outer)
        self._build_summary_section(outer)
        self._build_output_section(outer)

    def _build_run_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="训练配置", padding=10)
        frame.grid(row=0, column=0, sticky="ew")
        for column in range(7):
            frame.columnconfigure(column, weight=1)

        ttk.Label(frame, text="模式").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(frame, text="单角色", variable=self.run_mode_var, value="single", command=self._update_mode_state).grid(
            row=0,
            column=1,
            sticky="w",
        )
        ttk.Radiobutton(frame, text="全部角色", variable=self.run_mode_var, value="all", command=self._update_mode_state).grid(
            row=0,
            column=2,
            sticky="w",
        )

        ttk.Label(frame, text="角色").grid(row=0, column=3, sticky="w")
        character_box = ttk.Combobox(
            frame,
            textvariable=self.character_var,
            values=list(CHARACTERS),
            state="readonly",
            width=16,
        )
        character_box.grid(row=0, column=4, sticky="ew", padx=(6, 12))
        character_box.bind("<<ComboboxSelected>>", self._on_character_changed)
        self._single_only_widgets.append(character_box)

        ttk.Checkbutton(frame, text="禁用角色预设", variable=self.no_preset_var).grid(row=0, column=5, sticky="w")

        fields = [
            ("Timesteps", self.timesteps_var),
            ("N Envs", self.n_envs_var),
            ("LR", self.lr_var),
            ("Seed", self.seed_var),
            ("Batch Size", self.batch_size_var),
            ("N Steps", self.n_steps_var),
            ("Eval Freq", self.eval_freq_var),
            ("Eval Episodes", self.eval_episodes_var),
            ("Post Eval", self.post_eval_episodes_var),
        ]

        for idx, (label, variable) in enumerate(fields):
            row = 1 + idx // 3
            column = (idx % 3) * 2
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", pady=(10, 0))
            ttk.Entry(frame, textvariable=variable).grid(row=row, column=column + 1, sticky="ew", padx=(6, 16), pady=(10, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(14, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=1)
        buttons.columnconfigure(3, weight=1)
        buttons.columnconfigure(4, weight=1)
        buttons.columnconfigure(5, weight=1)
        buttons.columnconfigure(6, weight=2)

        load_preset_button = ttk.Button(buttons, text="载入角色预设", command=self._load_current_character_preset)
        load_preset_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        start_button = ttk.Button(buttons, text="开始训练", command=self.start_training)
        start_button.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        stop_button = ttk.Button(buttons, text="停止训练", command=self.stop_training)
        stop_button.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        eval_button = ttk.Button(buttons, text="评估当前角色", command=self.evaluate_current_character)
        eval_button.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        apply_bridge_button = ttk.Button(buttons, text="推到 Bridge", command=self.apply_current_model_to_bridge)
        apply_bridge_button.grid(row=0, column=4, sticky="ew", padx=(0, 8))
        ttk.Button(buttons, text="刷新摘要", command=self._refresh_summary).grid(row=0, column=5, sticky="ew")
        ttk.Label(buttons, textvariable=self.process_status_var, anchor="w").grid(row=0, column=6, sticky="ew", padx=(16, 0))

        self._single_only_button_widgets.extend([load_preset_button, start_button, stop_button, eval_button, apply_bridge_button])

    def _build_dir_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="目录与续训", padding=10)
        frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        frame.columnconfigure(1, weight=1)

        rows = [
            ("日志目录", self.log_dir_var, self.browse_log_dir),
            ("模型目录", self.save_dir_var, self.browse_save_dir),
            ("续训模型", self.resume_from_var, self.browse_resume_from),
            ("Bridge 状态", self.bridge_control_state_var, self.browse_bridge_control_state),
        ]
        for row, (label, variable, callback) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=(6, 8), pady=4)
            browse_button = ttk.Button(frame, text="浏览", command=callback)
            browse_button.grid(row=row, column=2, sticky="ew", pady=4)
            self._single_only_widgets.append(entry)
            self._single_only_button_widgets.append(browse_button)

        ttk.Checkbutton(frame, text="自动续训（优先 checkpoint）", variable=self.auto_resume_var).grid(
            row=4,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Checkbutton(frame, text="训练后自动评估", variable=self.auto_eval_after_train_var).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Checkbutton(frame, text="评估后自动推到 Bridge", variable=self.auto_apply_after_eval_var).grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Label(
            frame,
            text="说明：续训会把已有模型权重载入到当前超参数配置里继续训练，适合改 timesteps、lr、n_envs 等参数后继续跑。",
            wraplength=1040,
            justify="left",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_summary_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="训练摘要", padding=10)
        frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        frame.columnconfigure(1, weight=1)

        rows = [
            ("状态", self.summary_status_var),
            ("推荐模型", self.summary_model_var),
            ("评估指标", self.summary_metrics_var),
            ("最近手动评估", self.latest_eval_var),
            ("Bridge 应用", self.bridge_apply_status_var),
        ]
        for row, (label, variable) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="nw", pady=4)
            ttk.Label(frame, textvariable=variable, wraplength=1020, justify="left").grid(
                row=row,
                column=1,
                sticky="nw",
                padx=(6, 0),
                pady=4,
            )

    def _build_output_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="训练输出", padding=10)
        frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.output_text = tk.Text(frame, height=22, wrap="word")
        self.output_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.output_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.output_text.configure(yscrollcommand=scrollbar.set)

    def _apply_character_preset(self, character: str):
        preset = CHARACTER_PRESETS.get(character, {})
        self.timesteps_var.set(str(preset.get("total_timesteps", "")))
        self.n_envs_var.set(str(preset.get("n_envs", "")))
        self.lr_var.set(str(preset.get("learning_rate", "")))
        self.batch_size_var.set(str(preset.get("batch_size", "")))
        self.n_steps_var.set(str(preset.get("n_steps", "")))
        self.eval_freq_var.set(str(preset.get("eval_freq", "")))
        self.eval_episodes_var.set(str(preset.get("eval_episodes", "")))
        self.post_eval_episodes_var.set(str(preset.get("post_eval_episodes", "")))
        if not self.seed_var.get().strip():
            self.seed_var.set("42")

    def _load_current_character_preset(self):
        self._apply_character_preset(self.character_var.get().strip() or CHARACTERS[0])

    def _on_character_changed(self, _event=None):
        character = self.character_var.get().strip() or CHARACTERS[0]
        self.log_dir_var.set(default_log_dir(character))
        self.save_dir_var.set(default_save_dir(character))
        if not self.no_preset_var.get():
            self._apply_character_preset(character)
        self._refresh_summary()

    def _update_mode_state(self):
        is_single = self.run_mode_var.get() == "single"
        state = "normal" if is_single else "disabled"
        for widget in self._single_only_widgets:
            widget.configure(state=state)
        for widget in self._single_only_button_widgets:
            widget.configure(state=state)
        self._refresh_summary()

    def browse_log_dir(self):
        selected = filedialog.askdirectory(title="选择日志目录", initialdir=self.log_dir_var.get() or str(ROOT / "logs"))
        if selected:
            self.log_dir_var.set(selected)

    def browse_save_dir(self):
        selected = filedialog.askdirectory(title="选择模型目录", initialdir=self.save_dir_var.get() or str(ROOT / "models"))
        if selected:
            self.save_dir_var.set(selected)
            self._refresh_summary()

    def browse_resume_from(self):
        selected = filedialog.askopenfilename(
            title="选择续训模型或 checkpoint",
            initialdir=self.save_dir_var.get() or str(ROOT / "models"),
            filetypes=[("模型文件", "*.zip"), ("所有文件", "*.*")],
        )
        if selected:
            self.resume_from_var.set(selected)

    def browse_bridge_control_state(self):
        selected = filedialog.askopenfilename(
            title="选择 bridge 控制状态 JSON",
            initialdir=str(Path(self.bridge_control_state_var.get()).parent if self.bridge_control_state_var.get().strip() else ROOT),
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if selected:
            self.bridge_control_state_var.set(selected)

    def _current_character(self) -> str:
        return self.character_var.get().strip() or CHARACTERS[0]

    def _current_save_dir(self) -> Path:
        return Path(self.save_dir_var.get().strip() or default_save_dir(self._current_character()))

    def _normalized_optional_int(self, label: str, value: str) -> str | None:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ValueError(f"{label} 必须是整数") from exc
        return str(parsed)

    def _normalized_optional_float(self, label: str, value: str) -> str | None:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError as exc:
            raise ValueError(f"{label} 必须是数字") from exc
        return str(parsed)

    def _collect_command(self) -> list[str]:
        is_single = self.run_mode_var.get() == "single"
        try:
            return build_training_command(
                run_mode=self.run_mode_var.get(),
                character=self._current_character(),
                timesteps=self._normalized_optional_int("Timesteps", self.timesteps_var.get()),
                n_envs=self._normalized_optional_int("N Envs", self.n_envs_var.get()),
                lr=self._normalized_optional_float("LR", self.lr_var.get()),
                seed=self._normalized_optional_int("Seed", self.seed_var.get()),
                batch_size=self._normalized_optional_int("Batch Size", self.batch_size_var.get()),
                n_steps=self._normalized_optional_int("N Steps", self.n_steps_var.get()),
                eval_freq=self._normalized_optional_int("Eval Freq", self.eval_freq_var.get()),
                eval_episodes=self._normalized_optional_int("Eval Episodes", self.eval_episodes_var.get()),
                post_eval_episodes=self._normalized_optional_int("Post Eval", self.post_eval_episodes_var.get()),
                log_dir=self.log_dir_var.get().strip() if is_single else None,
                save_dir=self.save_dir_var.get().strip() if is_single else None,
                resume_from=self.resume_from_var.get().strip() if is_single else None,
                auto_resume=self.auto_resume_var.get(),
                no_preset=self.no_preset_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            raise

    def _start_process(
        self,
        command: list[str],
        *,
        status_text: str,
        task_kind: str,
        output_path: str | Path | None = None,
        clear_output: bool = True,
    ):
        if self.process is not None and self.process.poll() is None:
            self._update_process_status()
            return False

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self._last_output_path = Path(output_path) if output_path is not None else None
        self._active_task_kind = task_kind
        if clear_output:
            self.output_text.delete("1.0", "end")
        else:
            self.output_text.insert("end", "\n")
        self.output_text.insert("end", f"$ {' '.join(command)}\n\n")
        self.output_text.see("end")

        self.process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self.process_status_var.set(status_text)
        self._output_thread = threading.Thread(target=self._read_process_output, daemon=True)
        self._output_thread.start()
        return True

    def start_training(self):
        try:
            command = self._collect_command()
        except ValueError:
            return
        self._start_process(command, status_text="训练中", task_kind="train")

    def evaluate_current_character(self):
        if self.run_mode_var.get() != "single":
            messagebox.showerror("模式不支持", "批量模式下不能直接评估单个角色。")
            return
        character = self._current_character()
        save_dir = self._current_save_dir()
        try:
            command = build_evaluation_command(
                character=character,
                save_dir=save_dir,
                episodes=self._normalized_optional_int("Eval Episodes", self.eval_episodes_var.get()),
                seed=self._normalized_optional_int("Seed", self.seed_var.get()),
            )
        except (ValueError, FileNotFoundError) as exc:
            messagebox.showerror("评估失败", str(exc))
            return
        output_path = save_dir / "ui_eval.json"
        self._start_process(
            command,
            status_text="评估中",
            task_kind="evaluate",
            output_path=output_path,
        )

    def apply_current_model_to_bridge(self):
        if self.run_mode_var.get() != "single":
            messagebox.showerror("模式不支持", "批量模式下不能直接把单角色模型推送到 bridge。")
            return
        character = self._current_character()
        save_dir = self._current_save_dir()
        control_state_path = self.bridge_control_state_var.get().strip() or str(DEFAULT_CONTROL_STATE_PATH)
        try:
            resolved_model = apply_preferred_model_to_bridge(
                character=character,
                save_dir=save_dir,
                control_state_path=control_state_path,
            )
        except FileNotFoundError as exc:
            messagebox.showerror("应用失败", str(exc))
            return

        self.bridge_apply_status_var.set(f"{character} -> {resolved_model}")
        messagebox.showinfo("Bridge 已更新", f"已将 {character} 的 bridge 模型切到：\n{resolved_model}")

    def _run_followup_action(self, action: str):
        if action == "evaluate":
            character = self._current_character()
            save_dir = self._current_save_dir()
            try:
                command = build_evaluation_command(
                    character=character,
                    save_dir=save_dir,
                    episodes=self._normalized_optional_int("Eval Episodes", self.eval_episodes_var.get()),
                    seed=self._normalized_optional_int("Seed", self.seed_var.get()),
                )
            except (ValueError, FileNotFoundError) as exc:
                self.output_text.insert("end", f"[workflow] 自动评估失败: {exc}\n")
                self.output_text.see("end")
                return
            output_path = save_dir / "ui_eval.json"
            self._start_process(
                command,
                status_text="评估中（自动）",
                task_kind="evaluate",
                output_path=output_path,
                clear_output=False,
            )
            return

        if action == "apply_bridge":
            try:
                resolved_model = apply_preferred_model_to_bridge(
                    character=self._current_character(),
                    save_dir=self._current_save_dir(),
                    control_state_path=self.bridge_control_state_var.get().strip() or str(DEFAULT_CONTROL_STATE_PATH),
                )
            except FileNotFoundError as exc:
                self.output_text.insert("end", f"[workflow] 自动推送 bridge 失败: {exc}\n")
                self.output_text.see("end")
                return
            self.bridge_apply_status_var.set(f"{self._current_character()} -> {resolved_model}")
            self.output_text.insert("end", f"[workflow] 已自动推送到 bridge: {resolved_model}\n")
            self.output_text.see("end")

    def _read_process_output(self):
        if self.process is None or self.process.stdout is None:
            return
        for line in self.process.stdout:
            self.output_queue.put(line.rstrip("\n"))

    def _update_process_status(self):
        if self.process is None:
            self.process_status_var.set("未启动")
            return
        return_code = self.process.poll()
        if return_code is None:
            if self._active_task_kind == "evaluate":
                self.process_status_var.set("评估中")
            else:
                self.process_status_var.set("训练中")
            return
        completed_task_kind = self._active_task_kind
        self.process_status_var.set(f"已退出（code={return_code}）")
        self.process = None
        self._active_task_kind = None
        self._refresh_summary()
        followup = determine_followup_action(
            task_kind=completed_task_kind,
            run_mode=self.run_mode_var.get(),
            return_code=return_code,
            auto_eval_after_train=self.auto_eval_after_train_var.get(),
            auto_apply_after_eval=self.auto_apply_after_eval_var.get(),
        )
        if followup is not None:
            self._run_followup_action(followup)

    def _poll_output(self):
        appended = False
        while True:
            try:
                line = self.output_queue.get_nowait()
            except Empty:
                break
            self.output_text.insert("end", line + "\n")
            appended = True
        if appended:
            self.output_text.see("end")
        self._update_process_status()
        self.root.after(POLL_INTERVAL_MS, self._poll_output)

    def stop_training(self):
        if self.process is None:
            self._update_process_status()
            return
        if self.process.poll() is not None:
            self._update_process_status()
            return

        pid = self.process.pid
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._update_process_status()

    def _refresh_summary(self):
        if self.run_mode_var.get() == "all":
            summary = load_training_summary(ROOT / "models" / "all_training_summary.json")
            if summary is None:
                self.summary_status_var.set("暂无批量训练摘要")
                self.summary_model_var.set("-")
                self.summary_metrics_var.set("-")
                self.latest_eval_var.set("-")
                self.bridge_apply_status_var.set("-")
                return
            self.summary_status_var.set(f"已找到批量摘要，共 {summary.get('count', 0)} 个角色")
            self.summary_model_var.set(", ".join(summary.get("characters", [])) or "-")
            self.summary_metrics_var.set("通过 models/all_training_summary.json 查看各角色详细结果")
            self.latest_eval_var.set("批量模式不显示单角色手动评估")
            self.bridge_apply_status_var.set("批量模式不直接推送 bridge")
            return

        character = self._current_character()
        save_dir = self._current_save_dir()
        summary = load_character_training_summary(character, save_dir)
        if summary is None:
            self.summary_status_var.set("暂无训练摘要")
            self.summary_model_var.set("-")
            self.summary_metrics_var.set("-")
        else:
            post_eval = summary.get("post_eval") or {}
            self.summary_status_var.set(f"{summary.get('character', character)} 最近一次训练摘要已加载")
            self.summary_model_var.set(summary.get("preferred_model_path") or summary.get("final_model_path") or "-")
            self.summary_metrics_var.set(
                f"win_rate={float(post_eval.get('win_rate', 0.0)):.2%}, "
                f"avg_floor={float(post_eval.get('avg_floor', 0.0)):.1f}, "
                f"avg_hp={float(post_eval.get('avg_hp', 0.0)):.1f}"
            )

        eval_summary = load_training_summary(save_dir / "ui_eval.json")
        if eval_summary is None:
            self.latest_eval_var.set("-")
        else:
            self.latest_eval_var.set(
                f"win_rate={float(eval_summary.get('win_rate', 0.0)):.2%}, "
                f"avg_floor={float(eval_summary.get('avg_floor', 0.0)):.1f}, "
                f"avg_hp={float(eval_summary.get('avg_hp', 0.0)):.1f}"
            )

        control_state_path = self.bridge_control_state_var.get().strip() or str(DEFAULT_CONTROL_STATE_PATH)
        store = BridgeControlStateStore(control_state_path)
        state = store.load()
        effective_path = state.effective_model_path(character)
        self.bridge_apply_status_var.set(effective_path or "-")

    def _on_close(self):
        self.stop_training()
        self.root.destroy()


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="启动 STS Agent 训练控制面板")


def main():
    build_parser().parse_args()
    root = tk.Tk()
    TrainingControlPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
