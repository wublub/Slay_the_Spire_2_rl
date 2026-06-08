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

<<<<<<< HEAD
=======
FALLBACK_CHARACTER_PRESETS: dict[str, dict[str, int | float]] = {
    "Ironclad": {
        "total_timesteps": 5_000_000,
        "n_envs": 16,
        "learning_rate": 1e-4,
        "n_steps": 4096,
        "batch_size": 512,
        "eval_freq": 5_000,
        "eval_episodes": 50,
        "post_eval_episodes": 50,
    },
    "Silent": {
        "total_timesteps": 6_000_000,
        "n_envs": 16,
        "learning_rate": 1e-4,
        "n_steps": 4096,
        "batch_size": 512,
        "eval_freq": 5_000,
        "eval_episodes": 50,
        "post_eval_episodes": 50,
    },
    "Defect": {
        "total_timesteps": 6_000_000,
        "n_envs": 16,
        "learning_rate": 1e-4,
        "n_steps": 4096,
        "batch_size": 512,
        "eval_freq": 5_000,
        "eval_episodes": 50,
        "post_eval_episodes": 50,
    },
    "Necrobinder": {
        "total_timesteps": 7_000_000,
        "n_envs": 16,
        "learning_rate": 1e-4,
        "n_steps": 4096,
        "batch_size": 512,
        "eval_freq": 5_000,
        "eval_episodes": 50,
        "post_eval_episodes": 50,
    },
    "Regent": {
        "total_timesteps": 6_000_000,
        "n_envs": 16,
        "learning_rate": 1e-4,
        "n_steps": 4096,
        "batch_size": 512,
        "eval_freq": 5_000,
        "eval_episodes": 50,
        "post_eval_episodes": 50,
    },
}

TRAIN_IMPORT_ERROR: Exception | None = None
try:
    from agent.train import CHARACTER_PRESETS as _TRAIN_CHARACTER_PRESETS
    from agent.train import resolve_training_artifact_paths as _resolve_training_artifact_paths
except Exception as exc:  # pragma: no cover - 缺训练依赖的机器上才会触发
    TRAIN_IMPORT_ERROR = exc
    _TRAIN_CHARACTER_PRESETS = FALLBACK_CHARACTER_PRESETS

    def _resolve_training_artifact_paths(
        save_dir: str | Path,
        final_model_path: str | Path,
    ) -> tuple[str, str | None, str]:
        final_path = Path(final_model_path)
        best_path = Path(save_dir) / "best" / "best_model.zip"
        best_model_path = str(best_path) if best_path.exists() else None
        preferred_model_path = best_model_path or str(final_path)
        return str(final_path), best_model_path, preferred_model_path


CHARACTER_PRESETS = _TRAIN_CHARACTER_PRESETS or FALLBACK_CHARACTER_PRESETS
resolve_training_artifact_paths = _resolve_training_artifact_paths

>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
TRAIN_SCRIPT = ROOT / "agent" / "train.py"
TRAIN_ALL_SCRIPT = ROOT / "scripts" / "train_all.py"
EVALUATE_SCRIPT = ROOT / "agent" / "evaluate.py"
POLL_INTERVAL_MS = 200


def format_bridge_game_over_summary(payload: dict | None) -> str:
    if not isinstance(payload, dict) or not payload:
        return "-"

    parts: list[str] = []
    won = payload.get("won")
    if won is not None:
        parts.append("胜利" if bool(won) else "失败")
    if payload.get("score") is not None:
        parts.append(f"score={payload['score']}")

    badges = payload.get("badges")
    if isinstance(badges, list):
        parts.append(f"badges={len(badges)}")

    run_history = payload.get("run_history")
    if isinstance(run_history, dict):
        if run_history.get("floor_reached") is not None:
            parts.append(f"floor={run_history['floor_reached']}")
        if run_history.get("ascension") is not None:
            parts.append(f"asc={run_history['ascension']}")

    return " | ".join(str(part) for part in parts) if parts else "-"


def format_training_metrics_summary(metrics: dict | None) -> str:
    payload = metrics if isinstance(metrics, dict) else {}
    return (
        f"win_rate={float(payload.get('win_rate', 0.0)):.2%}, "
        f"avg_floor={float(payload.get('avg_floor', 0.0)):.1f}, "
        f"avg_hp={float(payload.get('avg_hp', 0.0)):.1f}, "
        f"avg_run_score={float(payload.get('avg_run_score', 0.0)):.2f}, "
        f"avg_combat_score={float(payload.get('avg_combat_score', 0.0)):.2f}"
    )


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


<<<<<<< HEAD
=======
PROFILE_STRING_FIELDS = [
    "timesteps",
    "n_envs",
    "lr",
    "seed",
    "batch_size",
    "n_steps",
    "eval_freq",
    "eval_episodes",
    "post_eval_episodes",
    "log_dir",
    "save_dir",
    "resume_from",
]
PROFILE_BOOL_FIELDS = ["auto_resume", "no_preset"]
LEGACY_DEFAULT_SEED_VALUES = {"1042"}


def _build_default_character_training_profile(
    character: str,
) -> dict[str, str | bool]:
    preset = CHARACTER_PRESETS.get(character, {})
    return {
        "timesteps": str(preset.get("total_timesteps", "")),
        "n_envs": str(preset.get("n_envs", "")),
        "lr": str(preset.get("learning_rate", "")),
        "seed": "",
        "batch_size": str(preset.get("batch_size", "")),
        "n_steps": str(preset.get("n_steps", "")),
        "eval_freq": str(preset.get("eval_freq", "")),
        "eval_episodes": str(preset.get("eval_episodes", "")),
        "post_eval_episodes": str(preset.get("post_eval_episodes", "")),
        "log_dir": default_log_dir(character),
        "save_dir": default_save_dir(character),
        "resume_from": "",
        "auto_resume": True,
        "no_preset": False,
    }


def default_character_training_profile(character: str) -> dict[str, str | bool]:
    return _build_default_character_training_profile(character)


def is_legacy_default_seed_profile(character: str, payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    seed_text = str(payload.get("seed", "")).strip()
    if seed_text not in LEGACY_DEFAULT_SEED_VALUES:
        return False
    if str(payload.get("resume_from", "")).strip():
        return False
    if str(payload.get("log_dir", "")).strip() != default_log_dir(character):
        return False
    if str(payload.get("save_dir", "")).strip() != default_save_dir(character):
        return False
    if bool(payload.get("auto_resume", True)) is not True:
        return False
    if bool(payload.get("no_preset", False)) is not False:
        return False
    return True


def normalize_character_training_profile(character: str, payload: dict | None = None) -> dict[str, str | bool]:
    profile = default_character_training_profile(character)
    if not isinstance(payload, dict):
        return profile

    normalized_payload = dict(payload)
    if is_legacy_default_seed_profile(character, normalized_payload):
        normalized_payload["seed"] = ""

    for key in PROFILE_STRING_FIELDS:
        value = normalized_payload.get(key)
        if value is None:
            continue
        profile[key] = str(value)
    for key in PROFILE_BOOL_FIELDS:
        if key in normalized_payload:
            profile[key] = bool(normalized_payload.get(key))
    return profile


def load_training_ui_profiles(profile_path: str | Path = TRAINING_UI_PROFILE_PATH) -> dict[str, dict[str, str | bool]]:
    payload = load_training_summary(profile_path) or {}
    stored = payload.get("characters") if isinstance(payload.get("characters"), dict) else payload
    profiles: dict[str, dict[str, str | bool]] = {}
    for character in CHARACTERS:
        raw_profile = stored.get(character) if isinstance(stored, dict) else None
        profiles[character] = normalize_character_training_profile(character, raw_profile)
    return profiles


def save_training_ui_profiles(
    profiles: dict[str, dict[str, str | bool]],
    profile_path: str | Path = TRAINING_UI_PROFILE_PATH,
) -> Path:
    path = Path(profile_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        character: normalize_character_training_profile(character, profiles.get(character))
        for character in CHARACTERS
    }
    path.write_text(
        json.dumps({"characters": normalized}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
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
    command = [
        sys.executable,
        str(EVALUATE_SCRIPT),
        "--character",
        character,
        "--model",
        str(resolved_model_path),
        "--episodes",
        resolved_episodes,
        "--output",
        str(resolved_output_path),
    ]
    if seed not in {None, ""}:
        command.extend(["--seed", str(seed)])
    return command


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


<<<<<<< HEAD
=======
def ensure_tk_runtime():
    if tk is None or ttk is None or filedialog is None or messagebox is None:
        detail = f"tkinter 不可用: {TK_IMPORT_ERROR}" if TK_IMPORT_ERROR is not None else "tkinter 不可用"
        raise RuntimeError(detail)


def collect_torch_runtime_report() -> dict[str, object]:
    report: dict[str, object] = {
        "available": importlib.util.find_spec("torch") is not None,
        "version": None,
        "cuda_version": None,
        "cuda_available": False,
        "device_count": 0,
        "devices": [],
        "error": None,
    }
    if not report["available"]:
        return report

    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        device_count = int(torch.cuda.device_count()) if cuda_available else 0
        report.update(
            {
                "version": torch.__version__,
                "cuda_version": torch.version.cuda,
                "cuda_available": cuda_available,
                "device_count": device_count,
                "devices": [torch.cuda.get_device_name(index) for index in range(device_count)],
            }
        )
    except Exception as exc:
        report["error"] = repr(exc)
    return report


def collect_runtime_report() -> dict[str, object]:
    files = {
        "training_ui": {"path": str(Path(__file__).resolve()), "exists": Path(__file__).resolve().exists()},
        "train_script": {"path": str(TRAIN_SCRIPT), "exists": TRAIN_SCRIPT.exists()},
        "train_all_script": {"path": str(TRAIN_ALL_SCRIPT), "exists": TRAIN_ALL_SCRIPT.exists()},
        "evaluate_script": {"path": str(EVALUATE_SCRIPT), "exists": EVALUATE_SCRIPT.exists()},
        "bridge_ui": {"path": str(ROOT / "scripts" / "bridge_ui.py"), "exists": (ROOT / "scripts" / "bridge_ui.py").exists()},
        "bridge_client": {"path": str(ROOT / "bridge" / "bridge_client.py"), "exists": (ROOT / "bridge" / "bridge_client.py").exists()},
    }
    modules = {
        "numpy": importlib.util.find_spec("numpy") is not None,
        "gymnasium": importlib.util.find_spec("gymnasium") is not None,
        "stable_baselines3": importlib.util.find_spec("stable_baselines3") is not None,
        "sb3_contrib": importlib.util.find_spec("sb3_contrib") is not None,
        "torch": importlib.util.find_spec("torch") is not None,
    }
    torch_runtime = collect_torch_runtime_report()
    summary = {
        "ui_ready": TK_IMPORT_ERROR is None,
        "training_ready": TRAIN_IMPORT_ERROR is None and all(modules.values()) and files["train_script"]["exists"] and files["train_all_script"]["exists"] and files["evaluate_script"]["exists"],
        "bridge_ready": files["bridge_ui"]["exists"] and files["bridge_client"]["exists"],
        "gpu_ready": bool(torch_runtime.get("cuda_available")),
    }
    summary["all_ready"] = bool(summary["ui_ready"] and summary["training_ready"] and summary["bridge_ready"])
    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "conda": {
            "default_env": os.environ.get("CONDA_DEFAULT_ENV"),
            "prefix": os.environ.get("CONDA_PREFIX"),
        },
        "root": str(ROOT),
        "files": files,
        "modules": modules,
        "torch_runtime": torch_runtime,
        "errors": {
            "tkinter": None if TK_IMPORT_ERROR is None else repr(TK_IMPORT_ERROR),
            "train_import": None if TRAIN_IMPORT_ERROR is None else repr(TRAIN_IMPORT_ERROR),
        },
        "summary": summary,
    }


def format_runtime_report(report: dict[str, object]) -> str:
    files = report["files"]
    modules = report["modules"]
    torch_runtime = report["torch_runtime"]
    errors = report["errors"]
    summary = report["summary"]
    conda = report["conda"]
    lines = [
        "STS Agent UI Runtime Check",
        f"Python: {report['python_version']} ({report['python_executable']})",
        f"Conda Env: {conda['default_env'] or '-'}",
        f"Conda Prefix: {conda['prefix'] or '-'}",
        f"Root: {report['root']}",
        f"UI Ready: {summary['ui_ready']}",
        f"Training Ready: {summary['training_ready']}",
        f"Bridge Ready: {summary['bridge_ready']}",
        f"GPU Ready: {summary['gpu_ready']}",
        f"All Ready: {summary['all_ready']}",
        "",
        "Files:",
    ]
    for name, payload in files.items():
        lines.append(f"- {name}: {payload['exists']} | {payload['path']}")
    lines.append("")
    lines.append("Modules:")
    for name, ok in modules.items():
        lines.append(f"- {name}: {ok}")
    lines.append("")
    lines.append("Torch Runtime:")
    lines.append(f"- version: {torch_runtime['version'] or '-'}")
    lines.append(f"- cuda_version: {torch_runtime['cuda_version'] or '-'}")
    lines.append(f"- cuda_available: {torch_runtime['cuda_available']}")
    lines.append(f"- device_count: {torch_runtime['device_count']}")
    devices = torch_runtime.get("devices") or []
    if devices:
        for index, name in enumerate(devices):
            lines.append(f"- device[{index}]: {name}")
    else:
        lines.append("- device[0]: -")
    lines.append(f"- error: {torch_runtime['error'] or '-'}")
    lines.append("")
    lines.append("Errors:")
    lines.append(f"- tkinter: {errors['tkinter'] or '-'}")
    lines.append(f"- train_import: {errors['train_import'] or '-'}")
    return "\n".join(lines)


def emit_runtime_report(*, as_json: bool) -> int:
    report = collect_runtime_report()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_runtime_report(report))
    return 0 if report["summary"]["all_ready"] else 1


>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
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
        self.bridge_last_reason_var = tk.StringVar(value="-")
        self.bridge_last_game_over_var = tk.StringVar(value="-")
        self._last_output_path: Path | None = None
        self._active_task_kind: str | None = None
        self._active_task_context: dict[str, object] | None = None

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

        no_preset_button = ttk.Checkbutton(frame, text="禁用角色预设", variable=self.no_preset_var)
        no_preset_button.grid(row=0, column=5, sticky="w")
        self._single_only_widgets.append(no_preset_button)

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
<<<<<<< HEAD
        buttons.columnconfigure(6, weight=2)
=======
        buttons.columnconfigure(6, weight=1)
        buttons.columnconfigure(7, weight=2)
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)

        load_preset_button = ttk.Button(buttons, text="载入角色预设", command=self._load_current_character_preset)
        load_preset_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        
        # 新增：启动游戏按钮
        launch_game_button = ttk.Button(buttons, text="🎮 启动游戏", command=self.launch_game)
        launch_game_button.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        
        start_button = ttk.Button(buttons, text="开始训练", command=self.start_training)
        start_button.grid(row=0, column=2, sticky="ew", padx=(0, 6))
        stop_button = ttk.Button(buttons, text="停止训练", command=self.stop_training)
        stop_button.grid(row=0, column=3, sticky="ew", padx=(0, 6))
        eval_button = ttk.Button(buttons, text="评估当前角色", command=self.evaluate_current_character)
<<<<<<< HEAD
        eval_button.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        apply_bridge_button = ttk.Button(buttons, text="推到 Bridge", command=self.apply_current_model_to_bridge)
        apply_bridge_button.grid(row=0, column=4, sticky="ew", padx=(0, 8))
        ttk.Button(buttons, text="刷新摘要", command=self._refresh_summary).grid(row=0, column=5, sticky="ew")
        ttk.Label(buttons, textvariable=self.process_status_var, anchor="w").grid(row=0, column=6, sticky="ew", padx=(16, 0))

        self._single_only_button_widgets.extend([load_preset_button, start_button, stop_button, eval_button, apply_bridge_button])
=======
        eval_button.grid(row=0, column=4, sticky="ew", padx=(0, 6))
        apply_bridge_button = ttk.Button(buttons, text="推到 Bridge", command=self.apply_current_model_to_bridge)
        apply_bridge_button.grid(row=0, column=5, sticky="ew", padx=(0, 6))
        ttk.Label(buttons, textvariable=self.process_status_var, anchor="w").grid(row=0, column=7, sticky="ew", padx=(16, 0))

        self._single_only_button_widgets.extend([load_preset_button, eval_button, apply_bridge_button])
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)

    def _build_dir_section(self, parent: ttk.Frame):
        frame = ttk.LabelFrame(parent, text="目录与续训", padding=10)
        frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        frame.columnconfigure(1, weight=1)

        rows = [
            ("日志目录", self.log_dir_var, self.browse_log_dir),
            ("模型目录", self.save_dir_var, self.browse_save_dir),
            ("续训模型", self.resume_from_var, self.browse_resume_from),
<<<<<<< HEAD
            ("Bridge 状态", self.bridge_control_state_var, self.browse_bridge_control_state),
=======
            ("Bridge 控制状态", self.bridge_control_state_var, self.browse_bridge_control_state),
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
        ]
        for row, (label, variable, callback) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=(6, 8), pady=4)
            browse_button = ttk.Button(frame, text="浏览", command=callback)
            browse_button.grid(row=row, column=2, sticky="ew", pady=4)
            self._single_only_widgets.append(entry)
            self._single_only_button_widgets.append(browse_button)

<<<<<<< HEAD
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
=======
        auto_resume_button = ttk.Checkbutton(frame, text="自动续训（优先 checkpoint）", variable=self.auto_resume_var)
        auto_resume_button.grid(
            row=4,
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )
        auto_eval_button = ttk.Checkbutton(frame, text="训练后自动评估", variable=self.auto_eval_after_train_var)
        auto_eval_button.grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )
        auto_apply_button = ttk.Checkbutton(frame, text="评估后自动推到 Bridge", variable=self.auto_apply_after_eval_var)
        auto_apply_button.grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )
        self._single_only_widgets.extend([auto_resume_button, auto_eval_button, auto_apply_button])
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
            ("Bridge 最近原因", self.bridge_last_reason_var),
            ("Bridge 最近结算", self.bridge_last_game_over_var),
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
<<<<<<< HEAD
        if not self.seed_var.get().strip():
            self.seed_var.set("42")
=======
        if not self.log_dir_var.get().strip():
            self.log_dir_var.set(default_log_dir(character))
        if not self.save_dir_var.get().strip():
            self.save_dir_var.set(default_save_dir(character))
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)

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
            self._refresh_summary()

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

    def _build_task_context(self, *, task_kind: str) -> dict[str, object]:
        return {
            "task_kind": task_kind,
            "run_mode": self.run_mode_var.get(),
            "character": self._current_character(),
            "save_dir": self._current_save_dir(),
            "eval_episodes": self.eval_episodes_var.get().strip(),
            "seed": self.seed_var.get().strip(),
            "control_state_path": self.bridge_control_state_var.get().strip() or str(DEFAULT_CONTROL_STATE_PATH),
            "auto_eval_after_train": self.auto_eval_after_train_var.get(),
            "auto_apply_after_eval": self.auto_apply_after_eval_var.get(),
        }

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
        task_context: dict[str, object] | None = None,
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
        self._active_task_context = dict(task_context or {})
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

    def launch_game(self):
        """启动游戏进程"""
        # 游戏exe路径（相对于项目根目录的上级目录）
        game_exe = ROOT.parent / "SlayTheSpire2.exe"
        
        if not game_exe.exists():
            messagebox.showerror(
                "错误",
                f"找不到游戏可执行文件:\n{game_exe}\n\n请确认游戏路径正确。"
            )
            return
        
        try:
            # 启动游戏进程（不等待结束，后台运行）
            self.process = subprocess.Popen(
                [str(game_exe)],
                cwd=str(game_exe.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            )
            
            # 更新状态
            self.process_status_var.set("游戏已启动")
            self.output_queue.put(f"[{self._timestamp()}] 游戏进程已启动: PID={self.process.pid}\n")
            
            # 不启动输出线程（游戏进程不需要捕获输出）
            messagebox.showinfo("成功", f"游戏已启动！\nPID: {self.process.pid}")
            
        except Exception as exc:
            messagebox.showerror("启动失败", f"无法启动游戏:\n{exc}")

    def start_training(self):
        try:
            command = self._collect_command()
        except ValueError:
            return
<<<<<<< HEAD
        self._start_process(command, status_text="训练中", task_kind="train")
=======
        status_text = "批量训练中" if self.run_mode_var.get() == "all" else "训练中"
        self._start_process(
            command,
            status_text=status_text,
            task_kind="train",
            task_context=self._build_task_context(task_kind="train"),
        )
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)

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
            task_context=self._build_task_context(task_kind="evaluate"),
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
        self._refresh_summary()
        messagebox.showinfo("Bridge 已更新", f"已将 {character} 的 bridge 模型切到：\n{resolved_model}")

    def _run_followup_action(self, action: str, task_context: dict[str, object]):
        character = str(task_context["character"])
        save_dir = Path(task_context["save_dir"])
        control_state_path = str(task_context["control_state_path"])
        if action == "evaluate":
            try:
                command = build_evaluation_command(
                    character=character,
                    save_dir=save_dir,
                    episodes=str(task_context["eval_episodes"]),
                    seed=str(task_context["seed"]),
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
                task_context={**task_context, "task_kind": "evaluate"},
                output_path=output_path,
                clear_output=False,
            )
            return

        if action == "apply_bridge":
            try:
                resolved_model = apply_preferred_model_to_bridge(
                    character=character,
                    save_dir=save_dir,
                    control_state_path=control_state_path,
                )
            except FileNotFoundError as exc:
                self.output_text.insert("end", f"[workflow] 自动推送 bridge 失败: {exc}\n")
                self.output_text.see("end")
                return
            self.bridge_apply_status_var.set(f"{character} -> {resolved_model}")
            self.output_text.insert("end", f"[workflow] 已自动推送到 bridge: {resolved_model}\n")
            self.output_text.see("end")

    def _read_process_output(self):
        if self.process is None or self.process.stdout is None:
            return
        for line in self.process.stdout:
            self.output_queue.put(line.rstrip("\n"))

    @staticmethod
    def _timestamp() -> str:
        """生成时间戳"""
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def _update_process_status(self):
        if self.process is None:
            self.process_status_var.set("未启动")
            return
        return_code = self.process.poll()
        if return_code is None:
            active_run_mode = str(self._active_task_context.get("run_mode")) if isinstance(self._active_task_context, dict) else self.run_mode_var.get()
            if self._active_task_kind == "evaluate":
                self.process_status_var.set("评估中")
<<<<<<< HEAD
=======
            elif active_run_mode == "all":
                self.process_status_var.set("批量训练中")
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
            else:
                self.process_status_var.set("训练中")
            return
        completed_task_kind = self._active_task_kind
        completed_task_context = dict(self._active_task_context or {})
        self.process_status_var.set(f"已退出（code={return_code}）")
        self.process = None
        self._active_task_kind = None
        self._active_task_context = None
        self._refresh_summary()
        followup = determine_followup_action(
            task_kind=completed_task_kind,
            run_mode=str(completed_task_context.get("run_mode", self.run_mode_var.get())),
            return_code=return_code,
            auto_eval_after_train=bool(completed_task_context.get("auto_eval_after_train", self.auto_eval_after_train_var.get())),
            auto_apply_after_eval=bool(completed_task_context.get("auto_apply_after_eval", self.auto_apply_after_eval_var.get())),
        )
        if followup is not None:
            self._run_followup_action(followup, completed_task_context)

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
                self.bridge_last_reason_var.set("-")
                self.bridge_last_game_over_var.set("-")
                return
            self.summary_status_var.set(f"已找到批量摘要，共 {summary.get('count', 0)} 个角色")
            self.summary_model_var.set(", ".join(summary.get("characters", [])) or "-")
            self.summary_metrics_var.set("通过 models/all_training_summary.json 查看各角色详细结果")
            self.latest_eval_var.set("批量模式不显示单角色手动评估")
            self.bridge_apply_status_var.set("批量模式不直接推送 bridge")
            self.bridge_last_reason_var.set("-")
            self.bridge_last_game_over_var.set("-")
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
            self.summary_metrics_var.set(format_training_metrics_summary(post_eval))

        eval_summary = load_training_summary(save_dir / "ui_eval.json")
        if eval_summary is None:
            self.latest_eval_var.set("-")
        else:
            self.latest_eval_var.set(format_training_metrics_summary(eval_summary))

        control_state_path = self.bridge_control_state_var.get().strip() or str(DEFAULT_CONTROL_STATE_PATH)
        store = BridgeControlStateStore(control_state_path)
        state = store.load()
        effective_path = state.effective_model_path(character)
        self.bridge_apply_status_var.set(effective_path or "-")
        self.bridge_last_reason_var.set(state.last_reason or "-")
        self.bridge_last_game_over_var.set(format_bridge_game_over_summary(state.last_game_over))

    def _on_close(self):
        self.stop_training()
<<<<<<< HEAD
=======

    def _on_close(self):
        self.shutdown()
        self.root.destroy()


class ControlCenterApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        initial_tab: str,
        host: str,
        port: int,
        control_state_path: str | Path,
    ):
        ensure_tk_runtime()
        self.root = root
        self.root.title("STS Agent Control Center")
        self.root.minsize(1040, 700)

        try:
            from scripts.bridge_ui import BridgeControlPanel
        except ModuleNotFoundError:
            from bridge_ui import BridgeControlPanel

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        training_tab = ttk.Frame(notebook)
        bridge_tab = ttk.Frame(notebook)
        notebook.add(training_tab, text="训练")
        notebook.add(bridge_tab, text="实机 / Bridge")

        self.training_panel = TrainingControlPanel(training_tab)
        self.training_panel.bridge_control_state_var.set(str(control_state_path))
        self.training_panel._refresh_summary()
        self.bridge_panel = BridgeControlPanel(
            bridge_tab,
            host=host,
            port=port,
            control_state_path=control_state_path,
        )

        if initial_tab == "bridge":
            notebook.select(bridge_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.training_panel.shutdown()
        self.bridge_panel.shutdown()
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
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
