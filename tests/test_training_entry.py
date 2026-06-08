"""训练入口与桥接/评估脚本测试。"""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import gymnasium as gym
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bridge_server import PROTOCOL_VERSION, BridgeServer, RuntimeRegistry
from agent.evaluate import _build_metrics, save_evaluation_summary
from agent.model_paths import resolve_best_model_path, resolve_final_model_path, resolve_preferred_model_path
from agent.runtime import (
    BridgeRequest,
    BridgeResponse,
    PolicyRuntime,
    ensure_action_mask_array,
    ensure_observation_array,
    request_from_dict,
    resolve_model_path,
    response_to_dict,
)
from agent.train import (
    ActionMaskWrapper,
    CHARACTER_PRESETS,
    build_config_from_args,
    list_training_checkpoints,
    load_resume_parameters,
    callback_trigger_freq,
    resolve_resume_source,
    resolve_training_artifact_paths,
    run_post_training_evaluation,
    save_training_summary,
    validate_resume_source,
)
from bridge.control_state import BridgeControlStateStore
from bridge.bridge_client import (
    _write_readable_run_log,
    _choose_best_event_decision,
    _choose_best_map_decision,
    _choose_best_boss_relic_decision,
    _choose_best_rest_decision,
    _choose_best_shop_decision,
    _should_allow_potion_use,
    adapt_response_for_websocket,
    build_game_state_from_payload,
    decode_action,
    normalize_bridge_message,
    normalize_state_envelope,
    process_websocket_message,
    raw_state_to_act_message,
)
from scripts.evaluate_all import (
    build_leaderboard,
    default_model_path,
    default_output_path,
    load_eval_summary,
    save_leaderboard,
)
from scripts.audit_bridge_training_coverage import audit_selection_coverage
from scripts.bridge_ui import (
    format_model_option_label,
    format_game_over_summary,
    format_training_metrics_summary as format_bridge_training_metrics_summary,
    list_character_model_paths,
    load_character_training_summary as load_bridge_ui_training_summary,
    load_training_summary_for_model,
    resolve_preferred_model_for_target,
)
from scripts.run_pipeline import build_eval_command, build_train_command
from scripts.training_ui import (
    apply_preferred_model_to_bridge,
    build_evaluation_command,
    build_training_command,
<<<<<<< HEAD
    determine_followup_action,
=======
    collect_runtime_report,
    default_character_training_profile,
    default_log_dir,
    default_save_dir,
    determine_followup_action,
    format_bridge_game_over_summary,
    format_training_metrics_summary as format_training_ui_metrics_summary,
    load_training_ui_profiles,
    normalize_character_training_profile,
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)
    resolve_preferred_training_model,
)
from scripts.train_all import build_extra_args, load_character_summary, save_combined_summary
from sts_env.archetypes import (
    ALL_ARCHETYPES,
    CHARACTER_STRATEGIES,
    REMOVE_ALWAYS,
    SUPPORT_DEPENDENCIES,
    card_pick_score,
    deck_quality_score,
    removable_priority,
)
from sts_env.combat import Combat, Monster, Player, make_card
from sts_env.encoding import (
    COMBAT_RUNTIME_CARD_DIM,
    COMBAT_RUNTIME_GLOBAL_DIM,
    encode_card,
    encode_observation,
    get_obs_dim,
)
from sts_env.env import (
    A_BOSS_START,
    A_END_TURN,
    A_EVENT_START,
    A_PICK_END,
    A_PLAY_START,
    A_REST,
    A_SELECT_START,
    A_SHOP_CARD_START,
    A_SHOP_LEAVE,
    A_SHOP_POTION_START,
    A_SHOP_RELIC_START,
    A_SHOP_REMOVE,
    A_SKIP,
    A_UPGRADE,
    StsEnv,
    TOTAL_ACTIONS,
)
from sts_env.game_state import GamePhase, MapNode, RoomType
from sts_env.powers import create_power
from sts_env.rewards import compute_card_reward, compute_route_reward


class StubModel:
    def __init__(self):
        self.calls = []

    def predict(self, obs, deterministic=True, action_masks=None):
        self.calls.append((obs, deterministic, action_masks))
        valid = np.where(action_masks)[0]
        return int(valid[-1]), None


class StubRuntime(PolicyRuntime):
    def __init__(self, character: str, model_path: str | Path):
        super().__init__(model=StubModel(), character=character, model_path=model_path)



def make_stub_loader():
    def _loader(character: str, model_path: Path):
        return StubRuntime(character=character, model_path=model_path)

    return _loader


def test_run_post_training_evaluation_returns_combat_and_run_metrics(monkeypatch):
    class FakeEnv(gym.Env):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.action_space = gym.spaces.Discrete(1)
            self.observation_space = gym.spaces.Box(low=0.0, high=1.0, shape=(1,), dtype=np.float32)

        def reset(self, *, seed=None, options=None):
            return np.zeros((1,), dtype=np.float32), {"phase": "MAP"}

        def action_mask(self):
            return np.array([True], dtype=bool)

        def step(self, action):
            return np.zeros((1,), dtype=np.float32), 0.0, True, False, {
                "won": True,
                "floor": 18,
                "hp": 42,
                "combat_score_total": 67.5,
                "run_score": 96.5,
                "avg_turns_per_combat": 3.0,
                "avg_hp_lost_per_combat": 2.0,
                "avg_avoidable_hp_lost_per_combat": 0.5,
            }

    class FakeModel:
        def predict(self, obs, deterministic=True, action_masks=None):
            return 0, None

    monkeypatch.setattr("sts_env.env.StsEnv", FakeEnv)
    cfg = SimpleNamespace(character="Ironclad", post_eval_episodes=2, seed=7)

    metrics = run_post_training_evaluation(FakeModel(), cfg)

    assert "avg_combat_score" in metrics
    assert "avg_run_score" in metrics
    assert "avg_turns_per_combat" in metrics
    assert "avg_avoidable_hp_lost_per_combat" in metrics


def test_evaluate_build_metrics_includes_new_scoring_fields():
    metrics = _build_metrics(
        character="Ironclad",
        episodes=4,
        wins=2,
        total_floors=20,
        total_hp=50,
        total_combat_score=40.0,
        total_run_score=70.0,
        total_turns_per_combat=12.0,
        total_hp_lost_per_combat=8.0,
        total_avoidable_hp_lost_per_combat=4.0,
    )

    assert metrics["avg_combat_score"] == 10.0
    assert metrics["avg_run_score"] == 17.5
    assert metrics["avg_avoidable_hp_lost_per_combat"] == 1.0





def test_action_mask_wrapper_is_gymnasium_wrapper():
    """多进程训练前包装器应保持 Gymnasium Wrapper 类型。"""
    env = StsEnv(character="Ironclad", seed=123)
    wrapped = ActionMaskWrapper(env)

    assert isinstance(wrapped, gym.Wrapper)
    assert wrapped.unwrapped is env



def test_build_config_from_args_sets_character_dirs_and_preset():
    """默认日志目录和角色预设应生效。"""
    args = SimpleNamespace(
        character="Silent",
        timesteps=None,
        n_envs=None,
        lr=None,
        seed=None,
        batch_size=None,
        n_steps=None,
        eval_freq=None,
        eval_episodes=None,
        post_eval_episodes=None,
        log_dir=None,
        save_dir=None,
    )

    cfg = build_config_from_args(args)
    preset = CHARACTER_PRESETS["Silent"]

    assert cfg.character == "Silent"
    assert cfg.total_timesteps == preset["total_timesteps"]
    assert cfg.n_envs == preset["n_envs"]
    assert cfg.learning_rate == preset["learning_rate"]
    assert cfg.batch_size == preset["batch_size"]
    assert cfg.n_steps == preset["n_steps"]
    assert cfg.eval_freq == preset["eval_freq"]
    assert cfg.eval_episodes == preset["eval_episodes"]
    assert cfg.post_eval_episodes == preset["post_eval_episodes"]
    assert Path(cfg.log_dir) == Path("logs") / "Silent"
    assert Path(cfg.save_dir) == Path("models") / "Silent"



def test_build_config_from_args_allows_override_and_disable_preset():
    """显式参数和禁用预设应覆盖角色默认值。"""
    args = SimpleNamespace(
        character="Defect",
        timesteps=1234,
        n_envs=2,
        lr=1e-4,
        seed=7,
        batch_size=64,
        n_steps=512,
        eval_freq=250,
        eval_episodes=3,
        post_eval_episodes=5,
        log_dir="custom_logs",
        save_dir="custom_models",
    )

    cfg = build_config_from_args(args, use_character_preset=False)

    assert cfg.character == "Defect"
    assert cfg.total_timesteps == 1234
    assert cfg.n_envs == 2
    assert cfg.learning_rate == 1e-4
    assert cfg.seed == 7
    assert cfg.batch_size == 64
    assert cfg.n_steps == 512
    assert cfg.eval_freq == 250
    assert cfg.eval_episodes == 3
    assert cfg.post_eval_episodes == 5
    assert cfg.log_dir == "custom_logs"
    assert cfg.save_dir == "custom_models"



def test_character_training_scripts_exist():
    """相关训练/评估/桥接脚本应存在。"""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    expected = [
        "train_ironclad.py",
        "train_silent.py",
        "train_defect.py",
        "train_necrobinder.py",
        "train_regent.py",
        "train_all.py",
        "evaluate_all.py",
        "run_pipeline.py",
        "bridge_server.py",
        "bridge_ui.py",
        "training_ui.py",
    ]

    for filename in expected:
        assert (scripts_dir / filename).exists(), f"缺少脚本: {filename}"



def test_build_extra_args_only_includes_specified_values():
    """批量训练参数应只透传显式指定的值。"""
    args = SimpleNamespace(
        timesteps=100,
        n_envs=None,
        lr=2e-4,
        seed=9,
        batch_size=None,
        n_steps=256,
        eval_freq=None,
        eval_episodes=4,
        post_eval_episodes=6,
        auto_resume=True,
        no_preset=True,
    )

    extra_args = build_extra_args(args)

    assert extra_args == [
        "--timesteps", "100",
        "--lr", "0.0002",
        "--seed", "9",
        "--n-steps", "256",
        "--eval-episodes", "4",
        "--post-eval-episodes", "6",
        "--auto-resume",
        "--no-preset",
    ]

<<<<<<< HEAD
=======
    save_training_ui_profiles(profiles, profile_path)
    loaded = load_training_ui_profiles(profile_path)

    assert loaded["Silent"]["timesteps"] == "1234"
    assert loaded["Silent"]["resume_from"] == "models/Silent/best/best_model.zip"
    assert loaded["Silent"]["auto_resume"] is False
    assert loaded["Silent"]["no_preset"] is True
    assert loaded["Ironclad"] == default_character_training_profile("Ironclad")


def test_default_character_training_profile_leaves_seed_blank():
    """训练 UI 默认不应偷偷固定 seed。"""
    ironclad = default_character_training_profile("Ironclad")
    silent = default_character_training_profile("Silent")
    defect = default_character_training_profile("Defect")

    assert ironclad["seed"] == ""
    assert silent["seed"] == ""
    assert defect["seed"] == ""


def test_normalize_character_training_profile_migrates_legacy_default_seed_to_blank():
    """旧版 UI 默认 profile 里的固定 seed 应迁移为空，避免继续偷偷锁死训练种子。"""
    legacy_silent_profile = {
        "timesteps": "1400000",
        "n_envs": "8",
        "lr": "0.00025",
        "seed": "1042",
        "batch_size": "256",
        "n_steps": "2048",
        "eval_freq": "10000",
        "eval_episodes": "20",
        "post_eval_episodes": "30",
        "log_dir": default_log_dir("Silent"),
        "save_dir": default_save_dir("Silent"),
        "resume_from": "",
        "auto_resume": True,
        "no_preset": False,
    }

    normalized = normalize_character_training_profile("Silent", legacy_silent_profile)

    assert normalized["seed"] == ""


def test_build_character_command_uses_character_profile_when_provided():
    """批量训练应能按角色读取 UI 保存的参数与续训模型。"""
    args = SimpleNamespace(
        timesteps=None,
        n_envs=None,
        lr=None,
        seed=None,
        batch_size=None,
        n_steps=None,
        eval_freq=None,
        eval_episodes=None,
        post_eval_episodes=None,
        auto_resume=False,
        no_preset=False,
    )
    profile = {
        "timesteps": "321",
        "n_envs": "2",
        "lr": "0.0002",
        "seed": "7",
        "batch_size": "64",
        "n_steps": "256",
        "eval_freq": "50",
        "eval_episodes": "3",
        "post_eval_episodes": "4",
        "log_dir": "logs/SilentCustom",
        "save_dir": "models/SilentCustom",
        "resume_from": "models/Silent/best/best_model.zip",
        "auto_resume": True,
        "no_preset": True,
    }

    command = build_character_command("Silent", args, profile)

    assert command[1].endswith("train_silent.py")
    assert "--timesteps" in command and "321" in command
    assert "--save-dir" in command and "models/SilentCustom" in command
    assert "--resume-from" in command and "models/Silent/best/best_model.zip" in command
    assert "--auto-resume" in command
    assert "--no-preset" in command
>>>>>>> 7c96e45 (feat: implement combat and run scoring system)


def test_list_training_checkpoints_and_resume_source_prefer_latest_checkpoint(tmp_path):
    """自动续训应优先使用最新 checkpoint，其次 best，再回退 final。"""
    save_dir = tmp_path / "Ironclad"
    checkpoints_dir = save_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True)

    older = checkpoints_dir / "sts2_Ironclad_10000_steps.zip"
    newer = checkpoints_dir / "sts2_Ironclad_25000_steps.zip"
    older.write_text("", encoding="utf-8")
    newer.write_text("", encoding="utf-8")

    checkpoints = list_training_checkpoints("Ironclad", save_dir)
    assert checkpoints == [newer, older]
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr("agent.train.validate_resume_source", lambda path: (True, None))
    assert resolve_resume_source("Ironclad", save_dir, auto_resume=True) == newer

    older.unlink()
    newer.unlink()
    best_model = save_dir / "best" / "best_model.zip"
    best_model.parent.mkdir(parents=True)
    best_model.write_text("", encoding="utf-8")
    assert resolve_resume_source("Ironclad", save_dir, auto_resume=True) == best_model

    best_model.unlink()
    final_model = save_dir / "sts2_Ironclad_final.zip"
    final_model.write_text("", encoding="utf-8")
    assert resolve_resume_source("Ironclad", save_dir, auto_resume=True) == final_model
    monkeypatch.undo()


def test_validate_resume_source_returns_reason_for_incompatible_model(monkeypatch):
    class FakeMaskablePPO:
        @staticmethod
        def load(path):
            return SimpleNamespace(
                action_space=SimpleNamespace(n=TOTAL_ACTIONS - 1),
                observation_space=SimpleNamespace(shape=(get_obs_dim(),)),
            )

    monkeypatch.setitem(sys.modules, "sb3_contrib", SimpleNamespace(MaskablePPO=FakeMaskablePPO))

    is_compatible, reason = validate_resume_source("models/Ironclad/legacy.zip")

    assert is_compatible is False
    assert "动作空间不兼容" in reason


def test_resolve_resume_source_skips_incompatible_auto_resume_candidates(tmp_path, monkeypatch):
    save_dir = tmp_path / "Ironclad"
    checkpoints_dir = save_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True)

    latest_checkpoint = checkpoints_dir / "sts2_Ironclad_25000_steps.zip"
    older_checkpoint = checkpoints_dir / "sts2_Ironclad_10000_steps.zip"
    best_model = save_dir / "best" / "best_model.zip"
    final_model = save_dir / "sts2_Ironclad_final.zip"
    best_model.parent.mkdir(parents=True)

    for path in [latest_checkpoint, older_checkpoint, best_model, final_model]:
        path.write_text("", encoding="utf-8")

    compatibility = {
        latest_checkpoint.resolve(): (False, "旧动作协议"),
        older_checkpoint.resolve(): (False, "旧动作协议"),
        best_model.resolve(): (True, None),
        final_model.resolve(): (True, None),
    }

    monkeypatch.setattr(
        "agent.train.validate_resume_source",
        lambda path: compatibility[Path(path).resolve()],
    )

    assert resolve_resume_source("Ironclad", save_dir, auto_resume=True) == best_model


def test_resolve_resume_source_returns_none_when_all_auto_resume_candidates_incompatible(tmp_path, monkeypatch):
    save_dir = tmp_path / "Ironclad"
    checkpoints_dir = save_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True)

    checkpoint = checkpoints_dir / "sts2_Ironclad_25000_steps.zip"
    final_model = save_dir / "sts2_Ironclad_final.zip"
    checkpoint.write_text("", encoding="utf-8")
    final_model.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "agent.train.validate_resume_source",
        lambda path: (False, "旧动作协议"),
    )

    assert resolve_resume_source("Ironclad", save_dir, auto_resume=True) is None


def test_resolve_resume_source_rejects_incompatible_explicit_resume(tmp_path, monkeypatch):
    resume_path = tmp_path / "Ironclad" / "legacy.zip"
    resume_path.parent.mkdir(parents=True)
    resume_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "agent.train.validate_resume_source",
        lambda path: (False, "动作空间不兼容"),
    )

    try:
        resolve_resume_source("Ironclad", resume_path.parent, resume_from=resume_path, auto_resume=True)
    except ValueError as exc:
        assert "指定续训模型不兼容" in str(exc)
        assert "动作空间不兼容" in str(exc)
    else:
        raise AssertionError("expected ValueError for incompatible explicit resume source")



def test_build_training_command_supports_resume_and_custom_dirs():
    """训练 UI 生成的单角色命令应支持续训和自定义目录。"""
    command = build_training_command(
        run_mode="single",
        character="Silent",
        timesteps="1234",
        n_envs="4",
        lr="0.0002",
        seed="7",
        batch_size="64",
        n_steps="512",
        eval_freq="250",
        eval_episodes="5",
        post_eval_episodes="6",
        log_dir="logs/SilentCustom",
        save_dir="models/SilentCustom",
        resume_from="models/Silent/best/best_model.zip",
        auto_resume=True,
        no_preset=True,
    )

    assert command[1].endswith("agent\\train.py") or command[1].endswith("agent/train.py")
    assert command[2:4] == ["--character", "Silent"]
    assert "--resume-from" in command and "models/Silent/best/best_model.zip" in command
    assert "--log-dir" in command and "logs/SilentCustom" in command
    assert "--save-dir" in command and "models/SilentCustom" in command
    assert "--auto-resume" in command
    assert "--no-preset" in command



def test_build_training_command_rejects_resume_from_for_batch_mode():
    """批量训练命令不应接受单个 resume_from 路径。"""
    try:
        build_training_command(
            run_mode="all",
            character="Ironclad",
            resume_from="models/Ironclad/best/best_model.zip",
        )
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "resume_from" in str(exc)



def test_build_evaluation_command_uses_preferred_training_model_and_output(tmp_path):
    """训练 UI 的评估命令应默认评估 preferred model，并写入 ui_eval.json。"""
    save_dir = tmp_path / "Silent"
    best_model = save_dir / "best" / "best_model.zip"
    best_model.parent.mkdir(parents=True)
    best_model.write_text("", encoding="utf-8")

    command = build_evaluation_command(
        character="Silent",
        save_dir=save_dir,
        episodes="25",
        seed="9",
    )

    assert command[1].endswith("agent\\evaluate.py") or command[1].endswith("agent/evaluate.py")
    assert "--model" in command and str(best_model) in command
    assert "--seed" in command and "9" in command
    assert "--output" in command and str(save_dir / "ui_eval.json") in command


def test_build_evaluation_command_omits_seed_when_left_blank(tmp_path):
    save_dir = tmp_path / "Defect"
    best_model = save_dir / "best" / "best_model.zip"
    best_model.parent.mkdir(parents=True)
    best_model.write_text("", encoding="utf-8")

    command = build_evaluation_command(
        character="Defect",
        save_dir=save_dir,
        episodes="12",
        seed=None,
    )

    assert "--seed" not in command



def test_resolve_preferred_training_model_and_apply_to_bridge(tmp_path):
    """训练 UI 应能把 preferred model 写入 bridge 控制状态。"""
    save_dir = tmp_path / "Ironclad"
    best_model = save_dir / "best" / "best_model.zip"
    best_model.parent.mkdir(parents=True)
    best_model.write_text("", encoding="utf-8")

    (save_dir / "training_summary.json").write_text(
        json.dumps(
            {
                "character": "Ironclad",
                "preferred_model_path": str(best_model),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert resolve_preferred_training_model("Ironclad", save_dir) == best_model

    control_state_path = tmp_path / "bridge_control_state.json"
    applied = apply_preferred_model_to_bridge(
        character="Ironclad",
        save_dir=save_dir,
        control_state_path=control_state_path,
    )

    assert applied == best_model
    control_state = BridgeControlStateStore(control_state_path).load()
    assert control_state.desired_character == "Ironclad"
    assert control_state.effective_model_path("Ironclad") == str(best_model)


def test_bridge_control_state_records_last_reason_and_game_over_summary(tmp_path):
    store = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    store.ensure_initialized(desired_character="Silent")

    store.record_bridge_result(
        {
            "ok": True,
            "type": "idle",
            "request_id": "game-over-store-1",
            "reason": "game_over",
            "game_over": {
                "won": False,
                "score": 1234,
                "badges": [{"id": "BigDeck", "rarity": "Bronze"}],
                "run_history": {"floor_reached": 42, "ascension": 6},
                "character_stats": {"total_wins": 8, "total_losses": 5},
            },
        }
    )

    state = store.load()

    assert state.last_request_id == "game-over-store-1"
    assert state.last_response_type == "idle"
    assert state.last_reason == "game_over"
    assert state.last_game_over["score"] == 1234
    assert state.last_game_over["run_history"]["floor_reached"] == 42


def test_format_game_over_summary_compacts_key_fields_for_bridge_ui():
    summary = format_game_over_summary(
        {
            "won": True,
            "score": 2222,
            "badges": [{"id": "EliteKiller", "rarity": "Silver"}],
            "run_history": {"floor_reached": 51, "ascension": 8},
            "character_stats": {"total_wins": 12, "total_losses": 9},
        }
    )

    assert "胜利" in summary
    assert "score=2222" in summary
    assert "badges=1" in summary
    assert "floor=51" in summary
    assert "asc=8" in summary
    assert "W/L=12/9" in summary


def test_format_bridge_game_over_summary_compacts_key_fields_for_training_ui():
    summary = format_bridge_game_over_summary(
        {
            "won": False,
            "score": 1111,
            "badges": [{"id": "BigDeck", "rarity": "Bronze"}],
            "run_history": {"floor_reached": 33, "ascension": 6},
        }
    )

    assert "失败" in summary
    assert "score=1111" in summary
    assert "badges=1" in summary
    assert "floor=33" in summary
    assert "asc=6" in summary



def test_determine_followup_action_for_training_workflow():
    """训练 UI 工作流应按 train -> evaluate -> apply_bridge 串联。"""
    assert determine_followup_action(
        task_kind="train",
        run_mode="single",
        return_code=0,
        auto_eval_after_train=True,
        auto_apply_after_eval=False,
    ) == "evaluate"

    assert determine_followup_action(
        task_kind="evaluate",
        run_mode="single",
        return_code=0,
        auto_eval_after_train=True,
        auto_apply_after_eval=True,
    ) == "apply_bridge"

    assert determine_followup_action(
        task_kind="train",
        run_mode="single",
        return_code=0,
        auto_eval_after_train=False,
        auto_apply_after_eval=True,
    ) == "apply_bridge"

    assert determine_followup_action(
        task_kind="train",
        run_mode="all",
        return_code=0,
        auto_eval_after_train=True,
        auto_apply_after_eval=True,
    ) is None

    assert determine_followup_action(
        task_kind="evaluate",
        run_mode="single",
        return_code=1,
        auto_eval_after_train=True,
        auto_apply_after_eval=True,
    ) is None



def test_save_training_summary_writes_json(tmp_path):
    """训练摘要应写入 JSON 文件。"""
    cfg = build_config_from_args(
        SimpleNamespace(
            character="Ironclad",
            timesteps=200,
            n_envs=1,
            lr=3e-4,
            seed=42,
            batch_size=32,
            n_steps=128,
            eval_freq=10,
            eval_episodes=2,
            post_eval_episodes=3,
            log_dir=str(tmp_path / "logs"),
            save_dir=str(tmp_path / "models"),
        ),
        use_character_preset=False,
    )
    post_eval = {
        "character": "Ironclad",
        "episodes": 3,
        "wins": 1,
        "win_rate": 1 / 3,
        "avg_floor": 12.0,
        "avg_hp": 20.0,
    }

    summary = save_training_summary(
        cfg,
        "models/Ironclad/final.zip",
        post_eval,
        best_model_path="models/Ironclad/best/best_model.zip",
        preferred_model_path="models/Ironclad/best/best_model.zip",
    )

    summary_path = Path(summary["summary_path"])
    assert summary_path.exists()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["character"] == "Ironclad"
    assert payload["final_model_path"] == "models/Ironclad/final.zip"
    assert payload["best_model_path"] == "models/Ironclad/best/best_model.zip"
    assert payload["preferred_model_path"] == "models/Ironclad/best/best_model.zip"
    assert payload["post_eval"]["avg_floor"] == 12.0
    assert payload["config"]["post_eval_episodes"] == 3



def test_callback_trigger_freq_scales_with_n_envs():
    """多环境训练时，回调频率应按并行环境数折算。"""
    assert callback_trigger_freq(10_000, 8) == 1250
    assert callback_trigger_freq(999, 4) == 249
    assert callback_trigger_freq(1, 8) == 1



def test_resolve_training_artifact_paths_prefers_best_model_in_save_dir(tmp_path):
    """训练输出路径应在当前 save_dir 内优先选择 best 模型。"""
    save_dir = tmp_path / "custom_models"
    save_dir.mkdir(parents=True)
    final_model_path = save_dir / "sts2_Ironclad_final.zip"

    resolved_final, best_model_path, preferred_model_path = resolve_training_artifact_paths(
        save_dir,
        final_model_path,
    )

    assert resolved_final == str(final_model_path)
    assert best_model_path is None
    assert preferred_model_path == str(final_model_path)

    best_model = save_dir / "best" / "best_model.zip"
    best_model.parent.mkdir(parents=True)
    best_model.write_text("", encoding="utf-8")

    resolved_final, best_model_path, preferred_model_path = resolve_training_artifact_paths(
        save_dir,
        final_model_path,
    )

    assert resolved_final == str(final_model_path)
    assert best_model_path == str(best_model)
    assert preferred_model_path == str(best_model)



def test_save_combined_summary_collects_available_character_summaries(tmp_path):
    """批量摘要应汇总已有角色训练结果。"""
    for character in ["Ironclad", "Silent"]:
        character_dir = tmp_path / character
        character_dir.mkdir(parents=True)
        (character_dir / "training_summary.json").write_text(
            json.dumps({"character": character, "post_eval": {"win_rate": 0.5}}, ensure_ascii=False),
            encoding="utf-8",
        )

    out_path = save_combined_summary(models_dir=tmp_path)

    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert payload["characters"] == ["Ironclad", "Silent"]
    assert len(payload["summaries"]) == 2



def test_save_combined_summary_reads_custom_save_dirs_from_profile_config(tmp_path):
    """批量摘要应支持从角色 profile 指向的自定义 save_dir 读取结果。"""
    aggregate_dir = tmp_path / "aggregate"
    silent_save_dir = tmp_path / "runs" / "SilentCustom"
    regent_save_dir = tmp_path / "runs" / "RegentCustom"
    silent_save_dir.mkdir(parents=True)
    regent_save_dir.mkdir(parents=True)

    (silent_save_dir / "training_summary.json").write_text(
        json.dumps({"character": "Silent", "post_eval": {"win_rate": 0.55}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (regent_save_dir / "training_summary.json").write_text(
        json.dumps({"character": "Regent", "post_eval": {"win_rate": 0.42}}, ensure_ascii=False),
        encoding="utf-8",
    )

    out_path = save_combined_summary(
        models_dir=aggregate_dir,
        profile_config={
            "Silent": {"save_dir": str(silent_save_dir)},
            "Regent": {"save_dir": str(regent_save_dir)},
        },
    )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert out_path == aggregate_dir / "all_training_summary.json"
    assert payload["count"] == 2
    assert payload["characters"] == ["Silent", "Regent"]


def test_load_character_summary_returns_none_for_missing_file(tmp_path):
    """缺失角色摘要时应返回 None。"""
    assert load_character_summary("Regent", models_dir=tmp_path) is None



def test_save_evaluation_summary_writes_json(tmp_path):
    """评估摘要应写入 JSON 文件。"""
    metrics = {
        "character": "Silent",
        "episodes": 10,
        "wins": 4,
        "win_rate": 0.4,
        "avg_floor": 20.5,
        "avg_hp": 15.2,
    }

    out_path = save_evaluation_summary(metrics, tmp_path / "eval" / "silent_eval.json")

    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload == metrics



def test_evaluate_all_default_paths(tmp_path):
    """批量评估默认路径应优先使用 best 模型，否则回退 final。"""
    root = Path(__file__).resolve().parent.parent
    ironclad_dir = tmp_path / "Ironclad"
    ironclad_dir.mkdir(parents=True)

    assert default_model_path("Ironclad", models_dir=tmp_path) == ironclad_dir / "sts2_Ironclad_final.zip"

    best_model = ironclad_dir / "best" / "best_model.zip"
    best_model.parent.mkdir(parents=True)
    best_model.write_text("", encoding="utf-8")

    assert default_model_path("Ironclad", models_dir=tmp_path) == best_model
    assert default_output_path("Silent") == root / "eval" / "silent_eval.json"



def test_load_eval_summary_returns_none_for_missing_file(tmp_path):
    """缺失评估摘要时应返回 None。"""
    assert load_eval_summary(tmp_path / "missing.json") is None



def test_build_leaderboard_sorts_by_win_rate_floor_run_score_hp():
    """排行榜应按胜率、楼层、run score、剩余 HP 排序。"""
    entries = [
        {"character": "B", "win_rate": 0.5, "avg_floor": 20.0, "avg_run_score": 50.0, "avg_hp": 10.0},
        {"character": "A", "win_rate": 0.6, "avg_floor": 18.0, "avg_run_score": 45.0, "avg_hp": 5.0},
        {"character": "C", "win_rate": 0.5, "avg_floor": 20.0, "avg_run_score": 70.0, "avg_hp": 8.0},
    ]

    leaderboard = build_leaderboard(entries)

    assert [entry["character"] for entry in leaderboard] == ["A", "C", "B"]



def test_save_leaderboard_writes_sorted_payload(tmp_path):
    """排行榜 JSON 应写入并保持排序。"""
    entries = [
        {"character": "Silent", "win_rate": 0.4, "avg_floor": 18.0, "avg_run_score": 35.0, "avg_hp": 7.0},
        {"character": "Ironclad", "win_rate": 0.5, "avg_floor": 16.0, "avg_run_score": 30.0, "avg_hp": 9.0},
    ]

    out_path = save_leaderboard(entries, tmp_path / "eval" / "leaderboard.json")

    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert [entry["character"] for entry in payload["leaderboard"]] == ["Ironclad", "Silent"]



def test_resolve_model_path_prefers_best_model_when_available(tmp_path):
    """桥接运行时默认模型路径应优先使用 best。"""
    regent_dir = tmp_path / "Regent"
    regent_dir.mkdir(parents=True)

    assert resolve_final_model_path("Regent", tmp_path) == regent_dir / "sts2_Regent_final.zip"
    assert resolve_best_model_path("Regent", tmp_path) == regent_dir / "best" / "best_model.zip"
    assert resolve_preferred_model_path("Regent", tmp_path) == regent_dir / "sts2_Regent_final.zip"
    assert resolve_model_path("Regent", tmp_path) == regent_dir / "sts2_Regent_final.zip"

    best_model = regent_dir / "best" / "best_model.zip"
    best_model.parent.mkdir(parents=True)
    best_model.write_text("", encoding="utf-8")

    assert resolve_preferred_model_path("Regent", tmp_path) == best_model
    assert resolve_model_path("Regent", tmp_path) == best_model



def test_bridge_ui_lists_models_and_loads_training_summary(tmp_path):
    """bridge UI 应能发现 best/final/checkpoint 并读取训练摘要。"""
    character_dir = tmp_path / "Silent"
    best_model = character_dir / "best" / "best_model.zip"
    final_model = character_dir / "sts2_Silent_final.zip"
    checkpoint = character_dir / "checkpoints" / "sts2_Silent_50000_steps.zip"
    for path in [best_model, final_model, checkpoint]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    summary_path = character_dir / "training_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "character": "Silent",
                "preferred_model_path": str(best_model),
                "post_eval": {"win_rate": 0.4, "avg_floor": 18.0, "avg_hp": 7.0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    model_paths = list_character_model_paths("Silent", models_dir=tmp_path)
    assert model_paths[0] == best_model
    assert final_model in model_paths
    assert checkpoint in model_paths

    label = format_model_option_label("Silent", best_model, models_dir=tmp_path)
    assert label.startswith("[preferred]")

    summary = load_bridge_ui_training_summary("Silent", models_dir=tmp_path)
    assert summary is not None
    assert summary["character"] == "Silent"


<<<<<<< HEAD
=======
def test_training_metric_formatters_include_run_and_combat_scores():
    metrics = {
        "win_rate": 0.4,
        "avg_floor": 18.0,
        "avg_hp": 7.0,
        "avg_run_score": 35.5,
        "avg_combat_score": 12.25,
    }

    bridge_text = format_bridge_training_metrics_summary(metrics)
    training_ui_text = format_training_ui_metrics_summary(metrics)

    assert "avg_run_score=35.50" in bridge_text
    assert "avg_combat_score=12.25" in bridge_text
    assert "avg_run_score=35.50" in training_ui_text
    assert "avg_combat_score=12.25" in training_ui_text


def test_bridge_ui_prefers_summary_near_selected_custom_model(tmp_path):
    """bridge UI 选中默认 models 目录外的模型时，应跟随该目录的 training_summary。"""
    default_models_dir = tmp_path / "models"
    default_character_dir = default_models_dir / "Silent"
    default_final = default_character_dir / "sts2_Silent_final.zip"
    default_final.parent.mkdir(parents=True, exist_ok=True)
    default_final.write_text("", encoding="utf-8")

    export_dir = tmp_path / "exports" / "silent_run"
    best_model = export_dir / "best" / "best_model.zip"
    final_model = export_dir / "sts2_Silent_final.zip"
    checkpoint = export_dir / "checkpoints" / "sts2_Silent_12345_steps.zip"
    for path in [best_model, final_model, checkpoint]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    (export_dir / "training_summary.json").write_text(
        json.dumps(
            {
                "character": "Silent",
                "preferred_model_path": "best/best_model.zip",
                "best_model_path": "best/best_model.zip",
                "final_model_path": "sts2_Silent_final.zip",
                "post_eval": {"win_rate": 0.61, "avg_floor": 26.0, "avg_hp": 14.0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = load_training_summary_for_model(checkpoint)
    assert summary is not None
    assert summary["character"] == "Silent"

    resolved = resolve_preferred_model_for_target(
        "Silent",
        selected_model_path=checkpoint,
        models_dir=default_models_dir,
    )
    assert resolved == best_model


def test_collect_runtime_report_lists_core_scripts():
    """统一 UI 应能给出关键脚本与依赖检查结果。"""
    report = collect_runtime_report()

    assert report["files"]["train_script"]["exists"] is True
    assert report["files"]["train_all_script"]["exists"] is True
    assert report["files"]["evaluate_script"]["exists"] is True
    assert report["files"]["bridge_ui"]["exists"] is True
    assert "torch" in report["modules"]
    assert "torch_runtime" in report
    assert "gpu_ready" in report["summary"]
    assert isinstance(report["summary"]["all_ready"], bool)


>>>>>>> 7c96e45 (feat: implement combat and run scoring system)

def test_ensure_observation_array_validates_shape():
    """桥接观测应校验维度。"""
    obs = ensure_observation_array(np.zeros(get_obs_dim(), dtype=np.float32))
    assert obs.shape == (get_obs_dim(),)

    try:
        ensure_observation_array(np.zeros(get_obs_dim() - 1, dtype=np.float32))
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "observation 维度错误" in str(exc)



def test_ensure_action_mask_array_validates_shape_and_validity():
    """桥接 action mask 应校验维度和合法动作。"""
    mask = np.zeros(TOTAL_ACTIONS, dtype=bool)
    mask[3] = True
    parsed = ensure_action_mask_array(mask)
    assert parsed.shape == (TOTAL_ACTIONS,)
    assert parsed[3]

    try:
        ensure_action_mask_array(np.zeros(TOTAL_ACTIONS - 1, dtype=bool))
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "action_mask 维度错误" in str(exc)

    try:
        ensure_action_mask_array(np.zeros(TOTAL_ACTIONS, dtype=bool))
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "至少需要一个合法动作" in str(exc)



def test_bridge_request_and_response_conversion():
    """桥接请求与响应应可在 dict 间转换。"""
    payload = {
        "character": "Ironclad",
        "observation": [0.0] * get_obs_dim(),
        "action_mask": [False] * TOTAL_ACTIONS,
        "deterministic": False,
        "request_id": "req-1",
    }
    payload["action_mask"][5] = True

    request = request_from_dict(payload)
    assert isinstance(request, BridgeRequest)
    assert request.character == "Ironclad"
    assert request.request_id == "req-1"

    response_payload = response_to_dict(
        BridgeResponse(
            character="Ironclad",
            action=0,
            model_path="models/Ironclad/test.zip",
            request_id="req-2",
        )
    )
    assert response_payload["character"] == "Ironclad"
    assert response_payload["action"] == 0
    assert response_payload["request_id"] == "req-2"


def test_bridge_request_defaults_to_stochastic_when_deterministic_missing():
    """桥接请求缺省 deterministic 时应走采样策略。"""
    payload = {
        "character": "Ironclad",
        "observation": [0.0] * get_obs_dim(),
        "action_mask": [True] + [False] * (TOTAL_ACTIONS - 1),
    }

    request = request_from_dict(payload)

    assert request.deterministic is False



def test_policy_runtime_predict_and_handle_request_with_stub_model():
    """桥接运行时应使用外部观测和 mask 进行推理。"""
    model = StubModel()
    runtime = PolicyRuntime(model=model, character="Silent", model_path="models/Silent/test.zip")
    obs = np.zeros(get_obs_dim(), dtype=np.float32)
    mask = np.zeros(TOTAL_ACTIONS, dtype=bool)
    mask[2] = True
    mask[7] = True

    action = runtime.predict(obs, mask, deterministic=False)
    assert action == 7
    assert len(model.calls) == 1
    assert model.calls[0][1] is False

    response = runtime.handle_request(
        BridgeRequest(
            character="Silent",
            observation=obs.tolist(),
            action_mask=mask.tolist(),
            deterministic=True,
            request_id="req-3",
        )
    )
    assert response.action == 7
    assert response.character == "Silent"
    assert response.request_id == "req-3"


def test_policy_runtime_truncates_appended_observation_for_legacy_models():
    """旧模型若仍使用旧 observation 维度，运行时应只截取前缀特征。"""

    class LegacyStubModel:
        def __init__(self, legacy_dim: int):
            self.calls = []
            self.observation_space = SimpleNamespace(shape=(legacy_dim,))

        def predict(self, obs, deterministic=True, action_masks=None):
            self.calls.append((obs, deterministic, action_masks))
            valid = np.where(action_masks)[0]
            return int(valid[0]), None

    legacy_dim = get_obs_dim() - 45
    model = LegacyStubModel(legacy_dim)
    runtime = PolicyRuntime(model=model, character="Silent", model_path="models/Silent/legacy.zip")
    obs = np.arange(get_obs_dim(), dtype=np.float32)
    mask = np.zeros(TOTAL_ACTIONS, dtype=bool)
    mask[3] = True

    action = runtime.predict(obs, mask, deterministic=True)

    assert action == 3
    assert len(model.calls) == 1
    assert model.calls[0][0].shape == (legacy_dim,)
    np.testing.assert_array_equal(model.calls[0][0], obs[:legacy_dim])


def test_evaluate_truncates_observation_for_legacy_models(monkeypatch):
    """离线评估也应兼容旧模型 observation 维度。"""
    import agent.evaluate as evaluate_module

    legacy_dim = get_obs_dim() - 45

    class LegacyEvalModel:
        def __init__(self):
            self.observation_space = SimpleNamespace(shape=(legacy_dim,))
            self.calls = []

        def predict(self, obs, deterministic=True, action_masks=None):
            self.calls.append((obs, deterministic, action_masks))
            valid = np.where(action_masks)[0]
            return int(valid[0]), None

    class FakeMaskablePPO:
        @staticmethod
        def load(_model_path):
            return model

    class FakeEnv:
        def __init__(self, *args, **kwargs):
            self._steps = 0

        def reset(self, seed=None):
            self._steps = 0
            return np.arange(get_obs_dim(), dtype=np.float32), {}

        def action_masks(self):
            mask = np.zeros(TOTAL_ACTIONS, dtype=bool)
            mask[0] = True
            return mask

        def step(self, action):
            self._steps += 1
            return (
                np.arange(get_obs_dim(), dtype=np.float32),
                0.0,
                True,
                False,
                {"won": False, "floor": 1, "hp": 70},
            )

        def render(self):
            return None

        def close(self):
            return None

    model = LegacyEvalModel()
    monkeypatch.setitem(sys.modules, "sb3_contrib", SimpleNamespace(MaskablePPO=FakeMaskablePPO))
    monkeypatch.setattr(evaluate_module, "StsEnv", FakeEnv)
    monkeypatch.setattr(evaluate_module, "ActionMaskWrapper", lambda env: env)

    metrics = evaluate_module.evaluate("models/Ironclad/legacy.zip", "Ironclad", n_episodes=1, seed=42)

    assert metrics["episodes"] == 1
    assert len(model.calls) == 1
    assert model.calls[0][0].shape == (legacy_dim,)
    np.testing.assert_array_equal(model.calls[0][0], np.arange(legacy_dim, dtype=np.float32))


def test_load_resume_parameters_rejects_incompatible_action_space(monkeypatch):
    """续训不应静默接受旧动作空间模型。"""
    class FakeLoadedModel:
        observation_space = SimpleNamespace(shape=(get_obs_dim(),))
        action_space = SimpleNamespace(n=TOTAL_ACTIONS - 3)

        def get_parameters(self):
            return {"policy": {"ok": 1}}

    class FakeMaskablePPO:
        @staticmethod
        def load(_model_path):
            return FakeLoadedModel()

    monkeypatch.setitem(sys.modules, "sb3_contrib", SimpleNamespace(MaskablePPO=FakeMaskablePPO))

    try:
        load_resume_parameters("models/Ironclad/legacy.zip")
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "动作空间不兼容" in str(exc)


def test_load_resume_parameters_rejects_incompatible_observation_shape(monkeypatch):
    """续训不应静默接受旧 observation 维度模型。"""
    class FakeLoadedModel:
        observation_space = SimpleNamespace(shape=(get_obs_dim() - 5,))
        action_space = SimpleNamespace(n=TOTAL_ACTIONS)

        def get_parameters(self):
            return {"policy": {"ok": 1}}

    class FakeMaskablePPO:
        @staticmethod
        def load(_model_path):
            return FakeLoadedModel()

    monkeypatch.setitem(sys.modules, "sb3_contrib", SimpleNamespace(MaskablePPO=FakeMaskablePPO))

    try:
        load_resume_parameters("models/Ironclad/legacy_obs.zip")
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "observation 维度不兼容" in str(exc)


def test_card_pick_score_prefers_hybrid_draw_over_junk():
    """复合抽牌应显著优于继续拿基础垃圾卡。"""
    silent_deck = ["StrikeSilent"] * 5 + ["DefendSilent"] * 5
    ironclad_deck = ["StrikeIronclad"] * 5 + ["DefendIronclad"] * 4 + ["Bash"]

    assert card_pick_score("Silent", silent_deck, "Prepared") > card_pick_score("Silent", silent_deck, "StrikeSilent")
    assert card_pick_score("Ironclad", ironclad_deck, "PommelStrike") > card_pick_score("Ironclad", ironclad_deck, "StrikeIronclad")


def test_archetype_and_strategy_card_ids_exist_in_card_database():
    """训练启发式引用的卡牌 ID 应全部存在于当前 cards.json。"""
    cards = json.loads((Path(__file__).resolve().parent.parent / "data" / "cards.json").read_text(encoding="utf-8"))
    known = {card["id"] for card in cards if isinstance(card, dict) and "id" in card}

    referenced = set(REMOVE_ALWAYS)
    for archetypes in ALL_ARCHETYPES.values():
        for archetype in archetypes:
            referenced.update(archetype.core_cards)
            referenced.update(archetype.synergy_cards)
            referenced.update(archetype.bad_cards)
    for strategy in CHARACTER_STRATEGIES.values():
        referenced.update(strategy.draw_cards)
        referenced.update(strategy.hybrid_draw_cards)
        referenced.update(strategy.resource_cards)
        referenced.update(strategy.payoff_cards)
        referenced.update(strategy.premium_upgrade_cards)
    referenced.update(SUPPORT_DEPENDENCIES.keys())
    for deps in SUPPORT_DEPENDENCIES.values():
        referenced.update(deps)

    missing = sorted(card_id for card_id in referenced if card_id not in known)
    assert missing == []


def test_character_strategy_card_pools_match_character_or_global_sources():
    """角色训练先验不应引用其它角色专属卡。"""
    cards = json.loads((Path(__file__).resolve().parent.parent / "data" / "cards.json").read_text(encoding="utf-8"))
    pool_by_id = {card["id"]: card.get("pool") for card in cards if isinstance(card, dict) and "id" in card}
    global_pools = {"Colorless", "Curse", "Event", "Status"}

    for character, archetypes in ALL_ARCHETYPES.items():
        referenced = set()
        for archetype in archetypes:
            referenced.update(archetype.core_cards)
            referenced.update(archetype.synergy_cards)
            referenced.update(archetype.bad_cards)

        strategy = CHARACTER_STRATEGIES[character]
        referenced.update(strategy.draw_cards)
        referenced.update(strategy.hybrid_draw_cards)
        referenced.update(strategy.resource_cards)
        referenced.update(strategy.payoff_cards)
        referenced.update(strategy.premium_upgrade_cards)

        for card_id, deps in SUPPORT_DEPENDENCIES.items():
            pool = pool_by_id.get(card_id)
            if pool in {character, *global_pools}:
                referenced.add(card_id)
                referenced.update(deps)

        cross_pool = sorted(
            (card_id, pool_by_id.get(card_id))
            for card_id in referenced
            if pool_by_id.get(card_id) not in {character, *global_pools}
        )
        assert cross_pool == []


def test_compute_card_reward_rewards_reasonable_skip_for_bloated_deck():
    """牌库过厚且提供的都不是强引擎时，跳牌应得到正反馈。"""
    env = StsEnv(character="Ironclad", seed=7)
    env.reset()
    env.gs.deck = [make_card("StrikeIronclad") for _ in range(14)] + [make_card("DefendIronclad") for _ in range(14)]

    skip_reward = compute_card_reward(
        env.gs,
        None,
        skipped=True,
        offered_card_ids=["StrikeIronclad", "DefendIronclad", "TwinStrike"],
    )
    take_reward = compute_card_reward(
        env.gs,
        "PommelStrike",
        skipped=False,
        offered_card_ids=["PommelStrike", "StrikeIronclad", "DefendIronclad"],
    )

    assert skip_reward > 0.0
    assert take_reward > skip_reward


def test_removable_priority_keeps_engine_cards_ahead_of_base_cards():
    """删牌排序应先删基础打防，而不是 Pommel/Burning Pact 这种引擎牌。"""
    deck_ids = [
        "StrikeIronclad",
        "DefendIronclad",
        "PommelStrike",
        "BurningPact",
        "DarkEmbrace",
    ]

    priority = removable_priority("Ironclad", deck_ids, floor=20, act=2)

    assert priority.index("StrikeIronclad") < priority.index("PommelStrike")
    assert priority.index("DefendIronclad") < priority.index("BurningPact")


def test_env_best_upgrade_index_prefers_engine_cards():
    """篝火升级应优先引擎牌，而不是起始 Strike/Defend。"""
    env = StsEnv(character="Ironclad", seed=11)
    env.reset()
    env.gs.deck = [
        make_card("StrikeIronclad"),
        make_card("BurningPact"),
        make_card("PommelStrike"),
        make_card("DefendIronclad"),
    ]

    idx = env._best_upgrade_index()

    assert idx is not None
    assert env.gs.deck[idx].card_id in {"BurningPact", "PommelStrike"}


def test_compute_route_reward_rewards_rest_before_and_after_elite_on_correct_nodes():
    """路线奖励应正确区分精英前篝火与精英后篝火。"""
    env = StsEnv(character="Ironclad", seed=19)
    env.reset()
    env.gs.floor = 6
    env.gs.player.hp = 70
    env.gs.player.max_hp = 80
    env.gs.deck = [
        make_card("BurningPact"),
        make_card("PommelStrike"),
        make_card("StrikeIronclad"),
        make_card("StrikeIronclad"),
        make_card("DefendIronclad"),
    ]

    rest_before_elite = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.REST,
        n_alternatives=2,
        floor=env.gs.floor,
        next_nodes_preview=[RoomType.ELITE],
    )
    rest_without_elite = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.REST,
        n_alternatives=2,
        floor=env.gs.floor,
        next_nodes_preview=[RoomType.MONSTER],
    )
    elite_then_rest = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.ELITE,
        n_alternatives=1,
        floor=env.gs.floor,
        next_nodes_preview=[RoomType.REST],
    )
    elite_without_rest = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.ELITE,
        n_alternatives=1,
        floor=env.gs.floor,
        next_nodes_preview=[RoomType.MONSTER],
    )

    assert rest_before_elite > rest_without_elite
    assert elite_then_rest > elite_without_rest


def test_compute_route_reward_prefers_shop_when_gold_and_remove_targets_are_ready():
    """当金币足够且牌库急需删牌时，路线应偏向商店。"""
    env = StsEnv(character="Ironclad", seed=23)
    env.reset()
    env.gs.floor = 8
    env.gs.player.gold = 150
    env.gs.deck = [make_card("StrikeIronclad") for _ in range(12)] + [make_card("DefendIronclad") for _ in range(10)]

    shop_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.SHOP,
        n_alternatives=1,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )
    event_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.EVENT,
        n_alternatives=1,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )

    assert shop_reward > event_reward


def test_compute_route_reward_avoids_elite_in_early_act_even_with_some_strength():
    """前期未成型牌组即使略有强度，也仍应谨慎对待精英。"""
    env = StsEnv(character="Ironclad", seed=31)
    env.reset()
    env.gs.floor = 3
    env.gs.player.hp = 74
    env.gs.player.max_hp = 80
    env.gs.deck = [
        make_card("BurningPact"),
        make_card("PommelStrike"),
        make_card("ShrugItOff"),
        make_card("StrikeIronclad"),
        make_card("StrikeIronclad"),
        make_card("DefendIronclad"),
    ]

    monster_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.MONSTER,
        n_alternatives=2,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )
    elite_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.ELITE,
        n_alternatives=2,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )

    assert monster_reward > elite_reward


def test_compute_route_reward_prefers_elite_in_early_act_when_frontload_and_aoe_are_ready():
    """ACT1 若前压、AOE 与防守已达标，应明确允许更积极地走精英。"""
    env = StsEnv(character="Ironclad", seed=33)
    env.reset()
    env.gs.floor = 3
    env.gs.player.hp = 76
    env.gs.player.max_hp = 80
    env.gs.deck = [
        make_card("Bash"),
        make_card("PommelStrike"),
        make_card("Whirlwind"),
        make_card("ShrugItOff"),
        make_card("Uppercut"),
        make_card("DefendIronclad"),
    ]

    monster_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.MONSTER,
        n_alternatives=2,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )
    elite_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.ELITE,
        n_alternatives=2,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )

    assert elite_reward > monster_reward


def test_compute_route_reward_midgame_elite_depends_on_deck_quality():
    """中期是否打精英应取决于当前牌组质量和血量。"""
    strong_env = StsEnv(character="Silent", seed=37)
    strong_env.reset()
    strong_env.gs.floor = 8
    strong_env.gs.player.hp = 63
    strong_env.gs.player.max_hp = 70
    strong_env.gs.deck = [
        make_card("Acrobatics"),
        make_card("Backflip"),
        make_card("NoxiousFumes"),
        make_card("DeadlyPoison"),
        make_card("Survivor"),
        make_card("Neutralize"),
    ]

    weak_env = StsEnv(character="Silent", seed=41)
    weak_env.reset()
    weak_env.gs.floor = 8
    weak_env.gs.player.hp = 50
    weak_env.gs.player.max_hp = 70
    weak_env.gs.deck = [make_card("StrikeSilent") for _ in range(7)] + [make_card("DefendSilent") for _ in range(6)]

    strong_elite = compute_route_reward(
        strong_env.gs,
        chosen_node_type=RoomType.ELITE,
        n_alternatives=1,
        floor=strong_env.gs.floor,
        next_nodes_preview=[],
    )
    weak_elite = compute_route_reward(
        weak_env.gs,
        chosen_node_type=RoomType.ELITE,
        n_alternatives=1,
        floor=weak_env.gs.floor,
        next_nodes_preview=[],
    )

    assert strong_elite > weak_elite


def test_compute_route_reward_lategame_stops_overpenalizing_elite():
    """后期牌组基本成型后，不应再像前期那样强烈回避精英。"""
    env = StsEnv(character="Silent", seed=43)
    env.reset()
    env.gs.floor = 13
    env.gs.player.hp = 58
    env.gs.player.max_hp = 70
    env.gs.deck = [
        make_card("Acrobatics"),
        make_card("Backflip"),
        make_card("NoxiousFumes"),
        make_card("Catalyst"),
        make_card("DeadlyPoison"),
        make_card("Survivor"),
        make_card("Neutralize"),
    ]

    monster_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.MONSTER,
        n_alternatives=1,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )
    elite_reward = compute_route_reward(
        env.gs,
        chosen_node_type=RoomType.ELITE,
        n_alternatives=1,
        floor=env.gs.floor,
        next_nodes_preview=[],
    )

    assert elite_reward >= monster_reward - 1.5


def test_regent_pick_score_prioritizes_act1_frontload_over_base_cards():
    """储君前期应优先补前压，而不是继续拿基础打击。"""
    deck_ids = ["StrikeRegent"] * 5 + ["DefendRegent"] * 4 + ["ShiningStrike"]

    assert card_pick_score("Regent", deck_ids, "GuidingStar") > card_pick_score("Regent", deck_ids, "Radiate")
    assert card_pick_score("Regent", deck_ids, "PhotonCut") > card_pick_score("Regent", deck_ids, "StrikeRegent")



def test_regent_pick_score_shifts_toward_scaling_once_engine_is_formed():
    """储君引擎成型后，应开始更重视中后期收益牌。"""
    deck_ids = [
        "PhotonCut",
        "GuidingStar",
        "CloakOfStars",
        "Glow",
        "HiddenCache",
        "Convergence",
        "Genesis",
        "BigBang",
        "Radiate",
        "GatherLight",
    ]

    assert deck_quality_score("Regent", deck_ids) >= 0.9
    assert card_pick_score("Regent", deck_ids, "Radiate") > card_pick_score("Regent", deck_ids, "PhotonCut")
    assert card_pick_score("Regent", deck_ids, "Stardust") > card_pick_score("Regent", deck_ids, "StrikeRegent")



def test_regent_deck_quality_stays_high_for_large_functional_deck():
    """储君大牌组只要职能完整，质量分不应被牌数本身压垮。"""
    deck_ids = [
        "PhotonCut",
        "GuidingStar",
        "CloakOfStars",
        "Glow",
        "HiddenCache",
        "Convergence",
        "Genesis",
        "BigBang",
        "Radiate",
        "GatherLight",
        "StrikeRegent",
        "StrikeRegent",
        "StrikeRegent",
        "StrikeRegent",
        "StrikeRegent",
        "StrikeRegent",
        "DefendRegent",
        "DefendRegent",
        "DefendRegent",
        "DefendRegent",
        "DefendRegent",
        "Glow",
        "HiddenCache",
        "Convergence",
        "BigBang",
        "Stardust",
        "ShiningStrike",
        "GuidingStar",
    ]

    assert deck_quality_score("Regent", deck_ids) >= 0.85



def test_env_rest_step_prefers_upgrade_over_rest_when_healthy_and_engine_exists():
    """血量健康且有高优先级升级目标时，火堆应先进入模型可选的升级选牌阶段。"""

    def build_env() -> StsEnv:
        env = StsEnv(character="Ironclad", seed=29)
        env.reset()
        env.gs.phase = GamePhase.REST
        env.gs.player.hp = 72
        env.gs.player.max_hp = 80
        env.gs.deck = [
            make_card("StrikeIronclad"),
            make_card("BurningPact"),
            make_card("PommelStrike"),
            make_card("DefendIronclad"),
        ]
        env.gs.current_node = MapNode(floor=0, index=0, room_type=RoomType.REST, children=[0])
        env.gs.map_nodes = [
            [env.gs.current_node],
            [MapNode(floor=1, index=0, room_type=RoomType.MONSTER)],
        ]
        return env

    upgrade_env = build_env()
    _obs, upgrade_reward, _terminated, _truncated, _info = upgrade_env.step(A_UPGRADE)
    assert upgrade_reward == 0.0
    assert upgrade_env.gs.phase == GamePhase.CARD_SELECT
    assert upgrade_env.gs.selection_kind == "upgrade"
    assert [card.card_id for card in upgrade_env.gs.selection_cards] == [
        "StrikeIronclad",
        "BurningPact",
        "PommelStrike",
        "DefendIronclad",
    ]


def test_env_shop_step_prefers_remove_over_buying_junk_when_gold_is_tight():
    """金币只够删牌附近时，不应鼓励去买一张基础垃圾牌。"""

    def build_env() -> StsEnv:
        env = StsEnv(character="Ironclad", seed=31)
        env.reset()
        env.gs.phase = GamePhase.SHOP
        env.gs.player.gold = 100
        env.gs.shop_remove_cost = 75
        env.gs.shop_cards = [make_card("StrikeIronclad")]
        env.gs.shop_relics = []
        env.gs.shop_potions = []
        env.gs.deck = [make_card("StrikeIronclad") for _ in range(12)] + [make_card("DefendIronclad") for _ in range(10)]
        return env

    buy_env = build_env()
    _obs, buy_reward, _terminated, _truncated, _info = buy_env.step(A_SHOP_CARD_START)

    remove_env = build_env()
    _obs, remove_reward, _terminated, _truncated, _info = remove_env.step(A_SHOP_REMOVE)

    assert remove_reward > buy_reward



def test_bridge_shop_decision_allows_medium_value_card_to_beat_remove_when_it_fills_needs():
    request = {
        "_bridge_state": {
            "phase": "shop",
            "character": "Silent",
            "player": {"name": "Silent", "gold": 125, "relics": [], "potions": []},
            "deck": ["StrikeSilent"] * 5 + ["DefendSilent"] * 5 + ["Neutralize", "Survivor"],
            "state": {
                "shop": {
                    "remove_cost": 75,
                    "cards": [{"id": "Backflip", "cost": 50}],
                    "relics": [],
                    "potions": [],
                }
            },
        },
        "_bridge_ui": {
            "shop_remove_enabled": True,
            "shop_cards_enabled": [True],
            "shop_relics_enabled": [],
            "shop_potions_enabled": [],
        },
    }

    assert _choose_best_shop_decision(request) == {"type": "buy_card", "index": 0}


def test_bridge_shop_decision_allows_core_card_to_beat_remove():
    request = {
        "_bridge_state": {
            "phase": "shop",
            "character": "Ironclad",
            "player": {"name": "Ironclad", "gold": 250, "relics": [], "potions": []},
            "deck": ["StrikeIronclad"] * 5 + ["DefendIronclad"] * 4 + ["Inflame", "Bash"],
            "state": {
                "shop": {
                    "remove_cost": 75,
                    "cards": [{"id": "DemonForm", "cost": 150}],
                    "relics": [],
                    "potions": [],
                }
            },
        },
        "_bridge_ui": {
            "shop_remove_enabled": True,
            "shop_cards_enabled": [True],
            "shop_relics_enabled": [],
            "shop_potions_enabled": [],
        },
    }

    assert _choose_best_shop_decision(request) == {"type": "buy_card", "index": 0}


def test_bridge_event_decision_prefers_copy_for_formed_deck():
    request = {
        "_bridge_state": {
            "phase": "event",
            "character": "Silent",
            "act": 2,
            "floor": 20,
            "player": {
                "name": "Silent",
                "hp": 55,
                "max_hp": 70,
                "gold": 80,
                "relics": [],
                "potions": [],
            },
            "deck": [
                "Neutralize",
                "Survivor",
                "Backflip",
                "Acrobatics",
                "NoxiousFumes",
                "DeadlyPoison",
                "Catalyst",
                "Backflip",
            ],
            "event_options": [
                {"id": "copy", "text": "Duplicate a card in your deck"},
                {"id": "heal", "text": "Heal 10 HP", "effect": {"heal": 10}},
            ],
        },
        "_bridge_ui": {"event_enabled": [True, True]},
    }

    assert _choose_best_event_decision(request) == {"type": "choose_option", "index": 0}



def test_bridge_event_decision_avoids_copy_for_unformed_early_deck():
    request = {
        "_bridge_state": {
            "phase": "event",
            "character": "Silent",
            "act": 1,
            "floor": 4,
            "player": {
                "name": "Silent",
                "hp": 35,
                "max_hp": 70,
                "gold": 30,
                "relics": [],
                "potions": [],
            },
            "deck": ["StrikeSilent"] * 5 + ["DefendSilent"] * 5 + ["Neutralize", "Survivor"],
            "event_options": [
                {"id": "copy", "text": "Duplicate a card in your deck"},
                {"id": "heal", "text": "Heal 10 HP", "effect": {"heal": 10}},
            ],
        },
        "_bridge_ui": {"event_enabled": [True, True]},
    }

    assert _choose_best_event_decision(request) == {"type": "choose_option", "index": 1}


def test_bridge_boss_relic_decision_uses_relic_tier_table():
    request = {
        "_bridge_state": {
            "phase": "boss_relic",
            "player": {"relics": []},
            "boss_relic_choices": ["RunicPyramid", "Ectoplasm", "VelvetChoker"],
        },
        "_bridge_ui": {"boss_relic_enabled": [True, True, True]},
    }

    assert _choose_best_boss_relic_decision(request) == {"type": "choose_boss_relic", "index": 0}


def test_bridge_map_decision_prefers_shop_when_remove_is_live():
    request = raw_state_to_act_message(
        {
            "request_id": "map-shop-1",
            "character": "Defect",
            "phase": "map",
            "act": 1,
            "floor": 7,
            "player": {"name": "Defect", "hp": 60, "max_hp": 75, "gold": 120},
            "deck": [{"id": "StrikeDefect"}] * 5 + [{"id": "DefendDefect"}] * 4 + [{"id": "Zap"}, {"id": "Dualcast"}],
            "map": {
                "available_next": [0, 1],
                "lookahead": [
                    [
                        {"room_type": "shop", "children": []},
                        {"room_type": "monster", "children": []},
                    ]
                ],
            },
            "_bridge_ui": {"map_choices_enabled": [True, True]},
        }
    )

    assert _choose_best_map_decision(request) == {"type": "choose_path", "index": 0}


def test_bridge_rest_decision_prefers_upgrade_when_healthy_with_core_upgrade():
    request = normalize_bridge_message(
        {
            "type": "state",
            "request_id": "rest-upgrade-1",
            "character": "Ironclad",
            "phase": "rest",
            "player": {
                "name": "Ironclad",
                "hp": 70,
                "max_hp": 80,
                "relics": ["BurningBlood"],
            },
            "deck": [
                {"id": "StrikeIronclad"},
                {"id": "BurningPact"},
                {"id": "PommelStrike"},
                {"id": "DefendIronclad"},
            ],
            "state": {
                "rest": {
                    "options": [
                        {"id": "rest", "enabled": True},
                        {"id": "upgrade", "enabled": True},
                    ]
                }
            },
        }
    )

    assert _choose_best_rest_decision(request) == {"type": "upgrade"}


def test_adapt_response_for_websocket_overrides_rest_and_syncs_action():
    request = normalize_bridge_message(
        {
            "type": "state",
            "request_id": "rest-upgrade-2",
            "character": "Ironclad",
            "phase": "rest",
            "player": {
                "name": "Ironclad",
                "hp": 70,
                "max_hp": 80,
                "relics": ["BurningBlood"],
            },
            "deck": [
                {"id": "StrikeIronclad"},
                {"id": "BurningPact"},
                {"id": "PommelStrike"},
                {"id": "DefendIronclad"},
            ],
            "state": {
                "rest": {
                    "options": [
                        {"id": "rest", "enabled": True},
                        {"id": "upgrade", "enabled": True},
                    ]
                }
            },
        }
    )

    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": A_REST, "request_id": "rest-upgrade-2"},
        request=request,
    )

    assert response["decision"] == {"type": "upgrade"}
    assert response["action"] == A_UPGRADE


def test_potion_shaped_rock_is_allowed_when_enemy_target_exists():
    request = {
        "_bridge_state": {
            "phase": "combat",
            "player": {
                "hp": 70,
                "max_hp": 70,
                "block": 0,
                "potions": [{"id": "PotionShapedRock", "target": "AnyEnemy"}],
            },
            "state": {
                "combat": {
                    "monsters": [{"hp": 18, "block": 0, "intent": {"type": "attack", "value": 6}}],
                }
            },
        }
    }

    assert _should_allow_potion_use({"type": "use_potion", "potion_index": 0, "target_index": 0}, request) is True


def test_policy_runtime_rejects_character_mismatch():
    """桥接运行时应拒绝角色不匹配的请求。"""
    class RejectStubModel:
        def predict(self, obs, deterministic=True, action_masks=None):
            return 0, None

    runtime = PolicyRuntime(model=RejectStubModel(), character="Defect", model_path="models/Defect/test.zip")
    try:
        runtime.handle_request(
            BridgeRequest(
                character="Silent",
                observation=[0.0] * get_obs_dim(),
                action_mask=[True] + [False] * (TOTAL_ACTIONS - 1),
            )
        )
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "角色不匹配" in str(exc)



def test_runtime_registry_caches_by_character_and_path(tmp_path):
    """运行时注册表应缓存已加载模型。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())

    runtime1 = registry.get_runtime("Ironclad", tmp_path / "a.zip")
    runtime2 = registry.get_runtime("Ironclad", tmp_path / "a.zip")
    runtime3 = registry.get_runtime("Ironclad", tmp_path / "b.zip")

    assert runtime1 is runtime2
    assert runtime1 is not runtime3
    assert registry.cache_size() == 2



def test_runtime_registry_reloads_when_model_file_changes_at_same_path(tmp_path):
    """同一路径模型文件被覆盖后，bridge 运行时应重新加载而不是继续复用旧实例。"""
    model_path = tmp_path / "Silent" / "best" / "best_model.zip"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text("v1", encoding="utf-8")

    load_calls: list[str] = []

    def loader(character: str, path: Path):
        runtime = StubRuntime(character=character, model_path=path)
        load_calls.append(f"{character}:{path.name}:{len(load_calls)}")
        return runtime

    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=loader)

    runtime1 = registry.get_runtime("Silent", model_path)
    runtime2 = registry.get_runtime("Silent", model_path)
    assert runtime1 is runtime2
    assert len(load_calls) == 1

    model_path.write_text("v2-with-new-weights", encoding="utf-8")
    runtime3 = registry.get_runtime("Silent", model_path)

    assert runtime3 is not runtime1
    assert len(load_calls) == 2
    assert registry.cache_size() == 1


def test_bridge_server_handles_ping_describe_load_and_act(tmp_path):
    """桥接服务应支持基础协议消息。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    ping = server.handle_message({"type": "ping", "request_id": "p1"})
    assert ping["ok"] is True and ping["type"] == "pong"

    desc = server.handle_message({"type": "describe", "request_id": "d1"})
    assert desc["ok"] is True
    assert desc["observation_dim"] == get_obs_dim()
    assert desc["total_actions"] == TOTAL_ACTIONS

    loaded = server.handle_message({"type": "load", "character": "Silent", "request_id": "l1"})
    assert loaded["ok"] is True
    assert loaded["type"] == "loaded"
    assert loaded["character"] == "Silent"

    mask = [False] * TOTAL_ACTIONS
    mask[4] = True
    mask[8] = True
    acted = server.handle_message(
        {
            "type": "act",
            "character": "Silent",
            "observation": [0.0] * get_obs_dim(),
            "action_mask": mask,
            "request_id": "a1",
        }
    )
    assert acted["ok"] is True
    assert acted["type"] == "action"
    assert acted["action"] == 8
    assert acted["request_id"] == "a1"



def test_bridge_server_reports_json_and_protocol_errors(tmp_path):
    """桥接服务应返回结构化错误。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    bad_json = server.handle_line("{bad json")
    assert bad_json["ok"] is False
    assert bad_json["code"] == "json_error"

    bad_type = server.handle_message({"type": "unknown", "request_id": "x1"})
    assert bad_type["ok"] is False
    assert bad_type["type"] == "error"
    assert bad_type["request_id"] == "x1"



def test_bridge_server_serve_forever_writes_jsonl_and_stops_on_shutdown(tmp_path):
    """桥接服务应按 JSONL 协议读写，并在 shutdown 后退出。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    input_stream = io.StringIO(
        json.dumps({"type": "ping", "request_id": "p1"}) + "\n" +
        json.dumps({"type": "shutdown", "request_id": "s1"}) + "\n"
    )
    output_stream = io.StringIO()

    server.serve_forever(input_stream, output_stream)

    lines = [json.loads(line) for line in output_stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["type"] == "pong"
    assert lines[1]["type"] == "shutdown"
    assert lines[1]["ok"] is True


def test_write_readable_run_log_includes_hand_costs_and_enemy_intents(tmp_path):
    run_log = tmp_path / "run_IRONCLAD_test.jsonl"
    run_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_seq": 1,
                        "phase": "combat",
                        "act": 1,
                        "floor": 3,
                        "player": {
                            "hp": 68,
                            "max_hp": 80,
                            "block": 5,
                            "energy": 2,
                            "hand": [
                                {"id": "BASH", "cost": 2},
                                {"id": "DEFEND_IRONCLAD", "cost": 1},
                            ],
                        },
                        "combat": {
                            "turn_count": 2,
                            "monsters": [
                                {
                                    "name": "Jaw Worm",
                                    "hp": 40,
                                    "max_hp": 42,
                                    "block": 0,
                                    "is_dead": False,
                                    "powers": [],
                                    "intent": {"type": "attack", "damage": 11, "hits": 1},
                                },
                                {
                                    "name": "Cultist",
                                    "hp": 44,
                                    "max_hp": 44,
                                    "block": 0,
                                    "is_dead": False,
                                    "powers": [],
                                },
                            ],
                        },
                        "decision": {"type": "play_card", "card_index": 0, "target_index": 0},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "_seq": 2,
                        "phase": "game_over",
                        "act": 1,
                        "floor": 3,
                        "character": "IRONCLAD",
                        "game_over": True,
                        "won": False,
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    readable_path = _write_readable_run_log(run_log)

    assert readable_path.exists()
    content = readable_path.read_text(encoding="utf-8")
    assert "手牌：BASH(2), DEFEND_IRONCLAD(1)" in content
    assert "Jaw Worm：40/42，格挡 0，意图：攻击 11" in content
    assert "Cultist：44/44，格挡 0，估算意图：" in content



def test_process_websocket_message_reports_bad_raw_state_payload(tmp_path):
    """原始状态缺少关键字段时应返回明确错误。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps({"type": "raw_state", "request_id": "raw-bad-1", "character": "Ironclad"}),
    )

    assert response["ok"] is True
    assert response["type"] == "idle"
    assert response["reason"] == "bad_state_payload"
    assert response["request_id"] == "raw-bad-1"
    assert "phase" in response["error"]



def test_decode_action_covers_major_phase_actions():
    """动作解码应覆盖主要阶段与关键分支。"""
    assert decode_action(8) == {"type": "play_card", "card_index": 1, "target_index": 3}
    assert decode_action(50) == {"type": "end_turn"}
    assert decode_action(52) == {"type": "use_potion", "potion_index": 0, "target_index": 1}
    assert decode_action(67) == {"type": "choose_path", "index": 1}
    assert decode_action(71) == {"type": "pick_card", "index": 1}
    assert decode_action(73) == {"type": "skip"}
    assert decode_action(74) == {"type": "rest"}
    assert decode_action(75) == {"type": "upgrade"}
    assert decode_action(76) == {"type": "dig"}
    assert decode_action(77) == {"type": "cook"}
    assert decode_action(78) == {"type": "lift"}
    assert decode_action(A_SHOP_CARD_START) == {"type": "buy_card", "index": 0}
    assert decode_action(A_SHOP_POTION_START) == {"type": "buy_potion", "index": 0}
    assert decode_action(A_SHOP_RELIC_START) == {"type": "buy_relic", "index": 0}
    assert decode_action(A_SHOP_REMOVE) == {"type": "remove_card"}
    assert decode_action(A_SHOP_LEAVE) == {"type": "leave_shop"}
    assert decode_action(A_EVENT_START + 1) == {"type": "choose_option", "index": 1}
    assert decode_action(A_BOSS_START + 1) == {"type": "choose_boss_relic", "index": 1}

    try:
        decode_action(TOTAL_ACTIONS)
        assert False, "应抛出 ValueError"
    except ValueError as exc:
        assert "动作越界" in str(exc)



def test_build_game_state_from_payload_supports_map_phase():
    """原始地图状态应能构造成可编码 GameState。"""
    payload = {
        "character": "Ironclad",
        "phase": "map",
        "floor": 0,
        "act": 1,
        "player": {
            "hp": 80,
            "max_hp": 80,
            "gold": 99,
            "relics": ["BurningBlood"],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "map": {
            "available_next": [0, 1],
            "lookahead": [
                [
                    {"room_type": "monster"},
                    {"room_type": "rest"},
                ],
                [
                    {"room_type": "event"},
                ],
            ],
        },
    }

    gs = build_game_state_from_payload(payload, character="Ironclad")

    assert gs.phase.name == "MAP"
    assert gs.floor == 0
    assert gs.available_next == [0, 1]
    assert len(gs.map_nodes[0]) == 2
    assert gs.map_nodes[0][1].room_type.name == "REST"



def test_raw_state_to_act_message_builds_map_observation_and_mask():
    """原始地图状态应自动转成 act 请求。"""
    payload = {
        "character": "Ironclad",
        "phase": "map",
        "floor": 0,
        "act": 1,
        "request_id": "raw-map-1",
        "player": {
            "hp": 80,
            "max_hp": 80,
            "gold": 99,
            "relics": ["BurningBlood"],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "map": {
            "available_next": [0, 1],
            "lookahead": [
                [
                    {"room_type": "monster"},
                    {"room_type": "rest"},
                ]
            ],
        },
    }

    act_message = raw_state_to_act_message(payload)

    assert act_message["type"] == "act"
    assert act_message["character"] == "Ironclad"
    assert act_message["request_id"] == "raw-map-1"
    assert len(act_message["observation"]) == get_obs_dim()
    assert len(act_message["action_mask"]) == TOTAL_ACTIONS
    assert act_message["action_mask"][66] is True
    assert act_message["action_mask"][67] is True
    assert act_message["action_mask"][68] is False



def test_raw_state_to_act_message_builds_combat_mask_with_targets():
    """原始战斗状态应能生成可用的出牌与回合结束动作。"""
    payload = {
        "character": "Ironclad",
        "phase": "combat",
        "player": {
            "hp": 72,
            "max_hp": 80,
            "energy": 3,
            "gold": 120,
            "hand": [
                {
                    "id": "StrikeIronclad",
                    "cost": 1,
                    "type": "Attack",
                    "target": "AnyEnemy",
                    "damage": 6,
                },
                {
                    "id": "DefendIronclad",
                    "cost": 1,
                    "type": "Skill",
                    "target": "Self",
                    "block": 5,
                },
            ],
            "draw_pile": ["StrikeIronclad"],
            "discard_pile": [],
            "exhaust_pile": [],
            "potions": [{"id": "FirePotion", "rarity": "Common", "usage": "CombatOnly"}],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "combat": {
            "monsters": [
                {
                    "name": "Cultist",
                    "hp": 50,
                    "max_hp": 50,
                    "intent": {"type": "attack", "damage": 6, "hits": 1},
                },
                {
                    "name": "Jaw Worm",
                    "hp": 42,
                    "max_hp": 42,
                    "intent": {"type": "attack_buff", "damage": 8, "hits": 1},
                },
            ],
            "turn_count": 2,
            "round_number": 3,
        },
    }

    act_message = raw_state_to_act_message(payload)
    mask = act_message["action_mask"]

    assert mask[0] is True
    assert mask[1] is True
    assert mask[5] is True
    assert mask[51] is True
    assert mask[52] is True
    assert mask[50] is True


def test_raw_state_to_act_message_tightens_combat_mask_from_ui_state():
    """战斗阶段应根据实机可出牌状态收紧非法出牌动作。"""
    payload = {
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy"},
                {"id": "Bash", "cost": -1, "type": "Attack", "target": "AnyEnemy"},
            ],
        },
        "deck": ["StrikeIronclad", "Bash"],
        "combat": {
            "monsters": [
                {
                    "name": "Louse",
                    "hp": 12,
                    "max_hp": 12,
                    "intent": {"type": "attack", "damage": 5, "hits": 1},
                }
            ],
        },
        "_bridge_ui": {
            "combat_playable_cards": [True, False],
            "combat_end_turn_enabled": True,
        },
    }

    act_message = raw_state_to_act_message(payload)
    mask = act_message["action_mask"]

    assert mask[0] is True
    assert mask[5] is False
    assert mask[6] is False
    assert mask[7] is False


def test_raw_state_to_act_message_limits_beneficial_potions_to_self_target():
    """对自己有益的药水不应在 action mask 中暴露敌方目标槽位。"""
    payload = {
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 20,
            "max_hp": 80,
            "energy": 1,
            "hand": [],
            "potions": [{"id": "BloodPotion", "rarity": "Common"}],
        },
        "deck": [],
        "combat": {
            "monsters": [
                {"name": "Cultist", "hp": 50, "max_hp": 50, "intent": {"type": "buff"}},
                {"name": "Jaw Worm", "hp": 40, "max_hp": 40, "intent": {"type": "attack", "damage": 8, "hits": 1}},
            ],
        },
    }

    mask = raw_state_to_act_message(payload)["action_mask"]

    assert mask[51] is True
    assert mask[52] is False
    assert mask[53] is False


def test_adapt_response_for_websocket_redirects_no_attack_block_to_bash():
    """敌人没有攻击意图时，应优先把纯防御改成更高优先级的进攻牌。"""
    payload = {
        "request_id": "combat-priority-1",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 3,
            "block": 0,
            "hand": [
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
                {"id": "Bash", "cost": 2, "type": "Attack", "target": "AnyEnemy", "damage": 8},
            ],
        },
        "deck": ["DefendIronclad", "Bash"],
        "combat": {
            "monsters": [
                {"name": "Cultist", "hp": 48, "max_hp": 48, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-1"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prioritizes_vulnerable_before_plain_attack():
    """同回合可打易伤时，不应先打普通伤害再补易伤。"""
    payload = {
        "request_id": "combat-priority-2",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 3,
            "block": 0,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "Bash", "cost": 2, "type": "Attack", "target": "AnyEnemy", "damage": 8},
            ],
        },
        "deck": ["StrikeIronclad", "Bash"],
        "combat": {
            "monsters": [
                {"name": "Jaw Worm", "hp": 42, "max_hp": 42, "intent": {"type": "attack", "damage": 11, "hits": 1}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-2"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_values_vulnerable_for_followup_damage():
    """易伤优先级应体现后续攻击收益，而不是只看当前这张牌自己的面板伤害。"""
    payload = {
        "request_id": "combat-priority-3",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 4,
            "block": 0,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "Bash", "cost": 2, "type": "Attack", "target": "AnyEnemy", "damage": 8},
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
            ],
        },
        "deck": ["StrikeIronclad", "Bash", "StrikeIronclad"],
        "combat": {
            "monsters": [
                {"name": "Jaw Worm", "hp": 42, "max_hp": 42, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-3"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_aoe_in_multi_enemy_scene():
    """多敌场景下，群攻的总收益明显更高时应优先群攻。"""
    payload = {
        "request_id": "combat-priority-4",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 2,
            "block": 0,
                "hand": [
                    {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                    {"id": "Whirlwind", "cost": 2, "type": "Attack", "target": "AllEnemies", "damage": 12},
                ],
        },
        "deck": ["StrikeIronclad", "Whirlwind"],
        "combat": {
            "monsters": [
                {"name": "Cultist", "hp": 20, "max_hp": 20, "intent": {"type": "attack", "damage": 6, "hits": 1}, "powers": []},
                {"name": "Jaw Worm", "hp": 20, "max_hp": 20, "intent": {"type": "attack", "damage": 8, "hits": 1}, "powers": []},
                {"name": "Louse", "hp": 18, "max_hp": 18, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-4"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_avoids_bubblebubble_without_existing_poison():
    """条件毒牌在目标没有毒时，不应压过稳定收益牌。"""
    payload = {
        "request_id": "combat-priority-5",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 60,
            "max_hp": 70,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "BubbleBubble", "cost": 1, "type": "Skill", "target": "AnyEnemy"},
                {"id": "PoisonedStab", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
            ],
        },
        "deck": ["BubbleBubble", "PoisonedStab"],
        "combat": {
            "monsters": [
                {"name": "Cultist", "hp": 40, "max_hp": 40, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-5"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_avoids_overstacking_doom():
    """Doom 已足够斩杀时，不应继续优先叠 Doom。"""
    payload = {
        "request_id": "combat-priority-6",
        "phase": "combat",
        "character": "Necrobinder",
        "player": {
            "hp": 55,
            "max_hp": 70,
            "energy": 3,
            "block": 0,
            "hand": [
                {"id": "Scourge", "cost": 1, "type": "Skill", "target": "AnyEnemy"},
                {"id": "TimesUp", "cost": 2, "type": "Attack", "target": "AnyEnemy", "damage": 12},
            ],
        },
        "deck": ["Scourge", "TimesUp"],
        "combat": {
            "monsters": [
                {
                    "name": "Target",
                    "hp": 10,
                    "max_hp": 40,
                    "intent": {"type": "attack", "damage": 7, "hits": 1},
                    "powers": [{"id": "DoomPower", "amount": 10}],
                },
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-6"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_malaise_into_big_attack_target():
    """X费减攻/上弱在高攻击目标上应明显优先于普通防御。"""
    payload = {
        "request_id": "combat-priority-7",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 50,
            "max_hp": 70,
            "energy": 3,
            "block": 0,
            "hand": [
                {"id": "DefendSilent", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
                {"id": "Malaise", "cost": 0, "type": "Skill", "target": "AnyEnemy"},
            ],
        },
        "deck": ["DefendSilent", "Malaise"],
        "combat": {
            "monsters": [
                {"name": "Boss", "hp": 90, "max_hp": 90, "intent": {"type": "attack", "damage": 18, "hits": 2}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-7"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_uses_runtime_calculated_damage_var():
    """实机传来的 CalculatedDamage 应优先用于动态牌评分。"""
    payload = {
        "request_id": "combat-priority-8",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 50,
            "max_hp": 80,
            "energy": 1,
            "block": 18,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "BodySlam", "cost": 1, "type": "Attack", "target": "AnyEnemy", "vars": {"CalculatedDamage": 18}},
            ],
        },
        "deck": ["StrikeIronclad", "BodySlam"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 17, "max_hp": 30, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-8"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_targets_vulnerable_enemy_for_dismantle():
    """带条件连击的牌应优先打满足条件的目标。"""
    payload = {
        "request_id": "combat-priority-9",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 60,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Dismantle", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 8},
            ],
        },
        "deck": ["Dismantle"],
        "combat": {
            "monsters": [
                {"name": "A", "hp": 28, "max_hp": 28, "intent": {"type": "buff"}, "powers": []},
                {"name": "B", "hp": 28, "max_hp": 28, "intent": {"type": "attack", "damage": 7, "hits": 1}, "powers": [{"id": "VulnerablePower", "amount": 1}]},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-9"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_targets_existing_vulnerable_for_dominate():
    """Dominate 应优先打已经有易伤的目标，吃更多自增力量。"""
    payload = {
        "request_id": "combat-priority-10",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 60,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Dominate", "cost": 1, "type": "Skill", "target": "AnyEnemy", "powers": {"VulnerablePower": 1}},
            ],
        },
        "deck": ["Dominate"],
        "combat": {
            "monsters": [
                {"name": "A", "hp": 30, "max_hp": 30, "intent": {"type": "buff"}, "powers": []},
                {"name": "B", "hp": 30, "max_hp": 30, "intent": {"type": "attack", "damage": 8, "hits": 1}, "powers": [{"id": "VulnerablePower", "amount": 2}]},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-10"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_targets_low_block_enemy_for_blightstrike():
    """BlightStrike 的 Doom 来自实际造成伤害，应优先打低格挡目标。"""
    payload = {
        "request_id": "combat-priority-11",
        "phase": "combat",
        "character": "Necrobinder",
        "player": {
            "hp": 55,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "BlightStrike", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 8},
            ],
        },
        "deck": ["BlightStrike"],
        "combat": {
            "monsters": [
                {"name": "Blocker", "hp": 25, "max_hp": 25, "block": 6, "intent": {"type": "attack", "damage": 7, "hits": 1}, "powers": []},
                {"name": "Open", "hp": 25, "max_hp": 25, "block": 0, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-11"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_prefers_late_burst_with_runtime_calculated_damage():
    """后手爆发牌若实机已给出高 CalculatedDamage，应压过普通攻击。"""
    payload = {
        "request_id": "combat-priority-12",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 62,
            "max_hp": 70,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "StrikeSilent", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "GoldAxe", "cost": 1, "type": "Attack", "target": "AnyEnemy", "vars": {"CalculatedDamage": 21}},
            ],
        },
        "deck": ["StrikeSilent", "GoldAxe"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 20, "max_hp": 30, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-12"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_runtime_perfected_strike_over_plain_attack():
    """依赖牌组结构的动态伤害牌应使用实机计算后的实时值。"""
    payload = {
        "request_id": "combat-priority-13",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 66,
            "max_hp": 80,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "PerfectedStrike", "cost": 2, "type": "Attack", "target": "AnyEnemy", "vars": {"CalculatedDamage": 24}},
            ],
        },
        "deck": ["StrikeIronclad", "PerfectedStrike", "StrikeIronclad", "StrikeIronclad"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 23, "max_hp": 30, "intent": {"type": "attack", "damage": 9, "hits": 1}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-13"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_targets_vulnerable_enemy_for_bully():
    """Bully 伤害取决于目标已有易伤，应优先打易伤更高的目标。"""
    payload = {
        "request_id": "combat-priority-14",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 66,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Bully", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 4},
            ],
        },
        "deck": ["Bully"],
        "combat": {
            "monsters": [
                {"name": "Plain", "hp": 28, "max_hp": 28, "intent": {"type": "attack", "damage": 7, "hits": 1}, "powers": []},
                {
                    "name": "Exposed",
                    "hp": 30,
                    "max_hp": 30,
                    "intent": {"type": "buff"},
                    "powers": [{"id": "VulnerablePower", "amount": 4}],
                },
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-14"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_targets_debuffed_enemy_for_rend():
    """Rend 额外伤害取决于目标减益数量，应优先选择减益更多的敌人。"""
    payload = {
        "request_id": "combat-priority-15",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 62,
            "max_hp": 70,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "Rend", "cost": 2, "type": "Attack", "target": "AnyEnemy", "damage": 15},
            ],
        },
        "deck": ["Rend"],
        "combat": {
            "monsters": [
                {
                    "name": "LightDebuff",
                    "hp": 35,
                    "max_hp": 35,
                    "intent": {"type": "attack", "damage": 8, "hits": 1},
                    "powers": [{"id": "WeakPower", "amount": 1}],
                },
                {
                    "name": "HeavyDebuff",
                    "hp": 40,
                    "max_hp": 40,
                    "intent": {"type": "buff"},
                    "powers": [
                        {"id": "WeakPower", "amount": 1},
                        {"id": "VulnerablePower", "amount": 2},
                        {"id": "PoisonPower", "amount": 3},
                    ],
                },
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-15"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_prefers_block_when_big_hit_would_leave_large_hp_loss():
    """预计单回合会白吃 10+ 血且无法斩杀时，应优先格挡而不是小额输出。"""
    payload = {
        "request_id": "combat-defense-1",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 48,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
            ],
        },
        "deck": ["StrikeIronclad", "DefendIronclad"],
        "combat": {
            "monsters": [
                {"name": "Gremlin", "hp": 28, "max_hp": 28, "intent": {"type": "attack", "damage": 15, "hits": 1}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-defense-1"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_defense_over_small_attack_when_no_lethal_and_damage_is_high():
    """高攻怪打不死时，不应为了一点伤害硬吃 10+ 血。"""
    payload = {
        "request_id": "combat-defense-2",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 34,
            "max_hp": 70,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "StrikeSilent", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "DefendSilent", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
            ],
        },
        "deck": ["StrikeSilent", "DefendSilent"],
        "combat": {
            "monsters": [
                {"name": "Boss", "hp": 44, "max_hp": 44, "intent": {"type": "attack", "damage": 16, "hits": 1}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-defense-2"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_compile_driver_when_dynamic_draw_is_high():
    """CompileDriver 的实时抽牌数应计入价值，而不只看表面伤害。"""
    payload = {
        "request_id": "combat-priority-16",
        "phase": "combat",
        "character": "Defect",
        "player": {
            "hp": 58,
            "max_hp": 75,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "HeavyBlade", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 10},
                {"id": "CompileDriver", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 7, "vars": {"CalculatedCards": 3}},
            ],
        },
        "deck": ["HeavyBlade", "CompileDriver"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 40, "max_hp": 40, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-16"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_targets_blocked_artifact_enemy_for_expose():
    """Expose 应优先打有格挡或 Artifact 的目标，先拆再挂易伤。"""
    payload = {
        "request_id": "combat-priority-17",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 60,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Expose", "cost": 0, "type": "Skill", "target": "AnyEnemy"},
            ],
        },
        "deck": ["Expose"],
        "combat": {
            "monsters": [
                {"name": "Open", "hp": 24, "max_hp": 24, "block": 0, "intent": {"type": "attack", "damage": 8, "hits": 1}, "powers": []},
                {
                    "name": "Shielded",
                    "hp": 28,
                    "max_hp": 28,
                    "block": 12,
                    "intent": {"type": "buff"},
                    "powers": [{"id": "ArtifactPower", "amount": 1}],
                },
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-17"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_targets_highest_attacker_for_dark_shackles():
    """DarkShackles 应优先压制当回合输出最高的攻击怪。"""
    payload = {
        "request_id": "combat-priority-18",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 54,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "DarkShackles", "cost": 0, "type": "Skill", "target": "AnyEnemy"},
            ],
        },
        "deck": ["DarkShackles"],
        "combat": {
            "monsters": [
                {"name": "LightHit", "hp": 26, "max_hp": 26, "intent": {"type": "attack", "damage": 6, "hits": 1}, "powers": []},
                {"name": "HeavyHit", "hp": 34, "max_hp": 34, "intent": {"type": "attack", "damage": 10, "hits": 2}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-18"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_targets_attacker_for_debilitate():
    """Debilitate 会强化目标承受与造成的攻击修正，应优先给高攻击意图目标。"""
    payload = {
        "request_id": "combat-priority-19",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 61,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Debilitate", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 10},
            ],
        },
        "deck": ["Debilitate"],
        "combat": {
            "monsters": [
                {"name": "Support", "hp": 30, "max_hp": 30, "intent": {"type": "buff"}, "powers": []},
                {"name": "Threat", "hp": 36, "max_hp": 36, "intent": {"type": "attack", "damage": 11, "hits": 2}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-19"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 1}
    assert response["action"] == 1


def test_adapt_response_for_websocket_prefers_attack_before_finisher():
    """Finisher 吃本回合已打出的攻击数，当前有前置攻击时不该先出。"""
    payload = {
        "request_id": "combat-priority-20",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 59,
            "max_hp": 70,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "StrikeSilent", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
            ],
        },
        "deck": ["StrikeSilent", "Finisher"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 30, "max_hp": 30, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 5, "request_id": "combat-priority-20"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 0}
    assert response["action"] == 0


def test_adapt_response_for_websocket_treats_zero_calculated_hits_as_zero_damage():
    """多段牌的 CalculatedHits 若为 0，不应继续按基础伤害高估。"""
    payload = {
        "request_id": "combat-priority-20b",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 59,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Neutralize", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 3},
                {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
            ],
        },
        "deck": ["Neutralize", "Finisher"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 20, "max_hp": 20, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 5, "request_id": "combat-priority-20b"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 0}
    assert response["action"] == 0


def test_adapt_response_for_websocket_prefers_skill_before_lunar_blast():
    """LunarBlast 吃本回合已打出的技能数，当前应先出技能。"""
    payload = {
        "request_id": "combat-priority-21",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 57,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Expose", "cost": 0, "type": "Skill", "target": "AnyEnemy"},
                {"id": "LunarBlast", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 4, "vars": {"CalculatedHits": 0}},
            ],
        },
        "deck": ["Expose", "LunarBlast"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 30, "max_hp": 30, "block": 10, "intent": {"type": "buff"}, "powers": [{"id": "ArtifactPower", "amount": 1}]},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 5, "request_id": "combat-priority-21"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 0}
    assert response["action"] == 0


def test_adapt_response_for_websocket_prefers_attack_before_conflagration():
    """Conflagration 会按本回合已出的攻击变强，多敌人时更该先铺前置攻击。"""
    payload = {
        "request_id": "combat-priority-22",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 64,
            "max_hp": 80,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "Conflagration", "cost": 1, "type": "Attack", "target": "AllEnemies", "vars": {"CalculatedDamage": 8}},
            ],
        },
        "deck": ["StrikeIronclad", "Conflagration"],
        "combat": {
            "monsters": [
                {"name": "A", "hp": 26, "max_hp": 26, "intent": {"type": "buff"}, "powers": []},
                {"name": "B", "hp": 24, "max_hp": 24, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 5, "request_id": "combat-priority-22"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 0}
    assert response["action"] == 0


def test_adapt_response_for_websocket_prefers_zero_cost_setup_before_gold_axe():
    """GoldAxe 吃本回合已出的牌数，能先打的 0 费牌应先打。"""
    payload = {
        "request_id": "combat-priority-23",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 60,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Neutralize", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 3, "powers": {"WeakPower": 1}},
                {"id": "GoldAxe", "cost": 1, "type": "Attack", "target": "AnyEnemy", "vars": {"CalculatedDamage": 6}},
            ],
        },
        "deck": ["Neutralize", "GoldAxe"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 22, "max_hp": 22, "intent": {"type": "attack", "damage": 9, "hits": 1}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 5, "request_id": "combat-priority-23"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 0}
    assert response["action"] == 0


def test_adapt_response_for_websocket_prefers_draw_to_find_attack_for_finisher():
    """如果抽牌能摸到攻击补足 Finisher 连段，应优先出抽牌攻击。"""
    payload = {
        "request_id": "combat-priority-24",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 63,
            "max_hp": 80,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "HeavyBlade", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 10},
                {"id": "PommelStrike", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 9, "draw": 1},
                {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
            ],
            "draw_pile": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
            ],
        },
        "deck": ["HeavyBlade", "PommelStrike", "Finisher", "StrikeIronclad"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 45, "max_hp": 45, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-24"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_dagger_throw_before_memento_mori():
    """DaggerThrow 的弃牌能直接抬高 MementoMori，本回合应先铺垫。"""
    payload = {
        "request_id": "combat-priority-25",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 60,
            "max_hp": 70,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "StrikeSilent", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "DaggerThrow", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 9, "draw": 1},
                {"id": "MementoMori", "cost": 1, "type": "Attack", "target": "AnyEnemy", "vars": {"CalculatedDamage": 8}},
            ],
            "draw_pile": [
                {"id": "DefendSilent", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
            ],
        },
        "deck": ["StrikeSilent", "DaggerThrow", "MementoMori", "DefendSilent"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 40, "max_hp": 40, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 10, "request_id": "combat-priority-25"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_burning_pact_before_ashen_strike():
    """BurningPact 会先消耗一张牌并抽牌，应优先给 AshenStrike 增伤。"""
    payload = {
        "request_id": "combat-priority-26",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 64,
            "max_hp": 80,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
                {"id": "BurningPact", "cost": 1, "type": "Skill", "target": "Self", "draw": 2},
                {"id": "AshenStrike", "cost": 1, "type": "Attack", "target": "AnyEnemy", "vars": {"CalculatedDamage": 6}},
            ],
            "draw_pile": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
            ],
            "exhaust_pile": [],
        },
        "deck": ["DefendIronclad", "BurningPact", "AshenStrike", "StrikeIronclad"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 42, "max_hp": 42, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 10, "request_id": "combat-priority-26"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_hologram_to_recover_finisher_support():
    """Hologram 能直接从弃牌堆拿回关键攻击时，应优先于普通攻击。"""
    payload = {
        "request_id": "combat-priority-27",
        "phase": "combat",
        "character": "Defect",
        "player": {
            "hp": 58,
            "max_hp": 75,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "DefendDefect", "cost": 1, "type": "Skill", "target": "Self", "block": 8},
                {"id": "Hologram", "cost": 1, "type": "Skill", "target": "Self", "block": 3},
                {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
            ],
            "discard_pile": [
                {"id": "BeamCell", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 3, "powers": {"VulnerablePower": 1}},
            ],
        },
        "deck": ["DefendDefect", "Hologram", "Finisher", "BeamCell"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 35, "max_hp": 35, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-27"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_headbutt_to_stack_topdeck_finisher_support():
    """Headbutt 能把高价值攻击放回抽牌堆顶时，应优先于普通攻击。"""
    payload = {
        "request_id": "combat-priority-28",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 65,
            "max_hp": 80,
            "energy": 2,
            "block": 0,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "Headbutt", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 9},
                {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
            ],
            "discard_pile": [
                {"id": "PommelStrike", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 9, "draw": 1},
            ],
        },
        "deck": ["StrikeIronclad", "Headbutt", "Finisher", "PommelStrike"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 40, "max_hp": 40, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-28"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_normalize_bridge_message_supports_raw_state_payload():
    """带 phase 的原始状态消息应自动归一化。"""
    payload = {
        "character": "Silent",
        "phase": "card_reward",
        "player": {
            "hp": 70,
            "max_hp": 70,
        },
        "deck": ["StrikeSilent", "DefendSilent"],
        "card_rewards": ["DaggerThrow", "Backflip"],
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["character"] == "Silent"
    assert normalized["action_mask"][70] is True
    assert normalized["action_mask"][71] is True
    assert normalized["action_mask"][73] is True



def test_process_websocket_message_supports_raw_state_payload(tmp_path):
    """WebSocket 桥接应直接接受原始游戏状态并返回决策。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "character": "Silent",
                "phase": "card_reward",
                "request_id": "raw-ws-1",
                "player": {"hp": 70, "max_hp": 70},
                "deck": ["StrikeSilent", "DefendSilent"],
                "card_rewards": ["DaggerThrow", "Backflip"],
            }
        ),
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["request_id"] == "raw-ws-1"
    assert response["decision"] == {"type": "skip"}



def test_normalize_bridge_message_supports_nested_state_envelope():
    """正式 state envelope 应归一化为 act 请求并保留公共字段。"""
    payload = {
        "type": "state",
        "schema_version": 1,
        "request_id": "state-map-1",
        "character": "Ironclad",
        "phase": "map",
        "run": {"act": 2, "floor": 5, "won": False},
        "player": {
            "hp": 63,
            "max_hp": 80,
            "gold": 120,
            "relics": ["BurningBlood"],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "state": {
            "map": {
                "choices": [
                    {"index": 0, "room_type": "monster", "enabled": True},
                    {"index": 1, "room_type": "rest", "enabled": True},
                    {"index": 2, "room_type": "event", "enabled": False},
                ]
            }
        },
        "model_path": "models/Ironclad/state.zip",
        "deterministic": False,
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["character"] == "Ironclad"
    assert normalized["request_id"] == "state-map-1"
    assert normalized["model_path"] == "models/Ironclad/state.zip"
    assert normalized["deterministic"] is False
    assert len(normalized["observation"]) == get_obs_dim()
    assert normalized["action_mask"][66] is True
    assert normalized["action_mask"][67] is True
    assert normalized["action_mask"][68] is False



def test_raw_state_to_act_message_tightens_event_mask_from_ui_options():
    """事件阶段应根据 enabled 标记收紧选项掩码。"""
    payload = {
        "phase": "event",
        "character": "Silent",
        "player": {"hp": 60, "max_hp": 70},
        "deck": ["StrikeSilent", "DefendSilent"],
        "event_options": [
            {"label": "拿金币", "effect": {"gold": 50}},
            {"label": "继续前进", "effect": {}},
            {"label": "受伤换遗物", "effect": {"damage": 8}},
        ],
        "_bridge_ui": {"event_enabled": [True, False, True]},
    }

    act_message = raw_state_to_act_message(payload)
    mask = act_message["action_mask"]

    assert mask[A_EVENT_START] is True
    assert mask[A_EVENT_START + 1] is False
    assert mask[A_EVENT_START + 2] is True
    assert mask[A_EVENT_START + 3] is False



def test_normalize_bridge_message_supports_nested_event_state_and_tightens_mask():
    """嵌套 event schema 应映射为 event_options 并收紧禁用按钮。"""
    payload = {
        "type": "state",
        "phase": "event",
        "character": "Silent",
        "player": {"hp": 64, "max_hp": 70},
        "deck": ["StrikeSilent", "DefendSilent"],
        "state": {
            "event": {
                "options": [
                    {"index": 0, "label": "拿钱", "enabled": True, "effects_preview": ["gold"]},
                    {"index": 1, "label": "离开", "enabled": False, "is_proceed": True},
                    {"index": 2, "label": "失去生命", "enabled": True, "will_kill_player": False},
                ]
            }
        },
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["action_mask"][A_EVENT_START] is True
    assert normalized["action_mask"][A_EVENT_START + 1] is False
    assert normalized["action_mask"][A_EVENT_START + 2] is True


def test_normalize_bridge_message_supports_nested_combat_state_and_tightens_mask():
    """嵌套 combat schema 应按实机可操作状态收紧战斗动作。"""
    payload = {
        "type": "state",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": -1, "type": "Attack", "target": "AnyEnemy"},
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self"},
            ],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "state": {
            "combat": {
                "monsters": [
                    {
                        "name": "Cultist",
                        "hp": 50,
                        "max_hp": 50,
                        "intent": {"type": "attack", "damage": 6, "hits": 1},
                    }
                ],
                "playable_cards": [False, True],
                "end_turn_enabled": False,
            }
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[0] is False
    assert mask[1] is False
    assert mask[2] is False
    assert mask[3] is False
    assert mask[4] is False
    assert mask[5] is True
    assert mask[50] is False


def test_raw_state_to_act_message_keeps_end_turn_as_fallback_when_ui_disables_everything():
    """若 UI 收紧后没有任何合法动作，应保留 end turn 兜底避免空掩码。"""
    payload = {
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": -1, "type": "Attack", "target": "AnyEnemy"},
            ],
        },
        "deck": ["StrikeIronclad", "Bash"],
        "combat": {
            "monsters": [
                {
                    "name": "Louse",
                    "hp": 12,
                    "max_hp": 12,
                    "intent": {"type": "attack", "damage": 5, "hits": 1},
                }
            ],
        },
        "_bridge_ui": {
            "combat_playable_cards": [False],
            "combat_end_turn_enabled": False,
        },
    }

    act_message = raw_state_to_act_message(payload)
    mask = act_message["action_mask"]

    assert mask[0] is False
    assert mask[1] is False
    assert mask[2] is False
    assert mask[3] is False
    assert mask[4] is False
    assert mask[50] is True


def test_raw_state_to_act_message_exposes_combat_hand_selection_as_single_target_slots():
    """战斗内选手牌子状态应复用 play_card 第一个 target 槽位。"""
    payload = {
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 2,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy"},
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self"},
                {"id": "Bash", "cost": 2, "type": "Attack", "target": "AnyEnemy"},
            ],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "combat": {
            "monsters": [
                {
                    "name": "Louse",
                    "hp": 12,
                    "max_hp": 12,
                    "intent": {"type": "attack", "damage": 5, "hits": 1},
                }
            ],
        },
        "_bridge_ui": {
            "combat_selectable_cards": [False, True, False],
            "combat_end_turn_enabled": False,
        },
    }

    act_message = raw_state_to_act_message(payload)
    mask = act_message["action_mask"]

    assert mask[0] is False
    assert mask[1] is False
    assert mask[2] is False
    assert mask[3] is False
    assert mask[4] is False
    assert mask[5] is True
    assert mask[6] is False
    assert mask[7] is False
    assert mask[8] is False
    assert mask[9] is False
    assert mask[10] is False
    assert mask[50] is False
    assert mask[51] is False
    assert mask[52] is False
    assert mask[53] is False
    assert mask[54] is False
    assert mask[55] is False


def test_raw_state_to_act_message_keeps_selection_confirm_action_when_cards_are_still_selectable():
    """战斗内多选界面若 confirm 已可点，应同时暴露 confirm 与可选牌动作。"""
    payload = {
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 2,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy"},
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self"},
            ],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "combat": {
            "monsters": [
                {
                    "name": "Louse",
                    "hp": 12,
                    "max_hp": 12,
                    "intent": {"type": "attack", "damage": 5, "hits": 1},
                }
            ],
        },
        "_bridge_ui": {
            "combat_selectable_cards": [True, False],
            "combat_selection_confirm_enabled": True,
            "combat_end_turn_enabled": False,
        },
    }

    act_message = raw_state_to_act_message(payload)
    mask = act_message["action_mask"]

    assert mask[0] is True
    assert mask[1] is False
    assert mask[2] is False
    assert mask[3] is False
    assert mask[4] is False
    assert mask[5] is False
    assert mask[50] is True


def test_normalize_bridge_message_supports_nested_combat_hand_selection_state():
    """嵌套 combat 选手牌子 schema 应收紧到可选手牌与确认兜底。"""
    payload = {
        "type": "state",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy"},
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self"},
            ],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "state": {
            "combat": {
                "monsters": [
                    {
                        "name": "Cultist",
                        "hp": 50,
                        "max_hp": 50,
                        "intent": {"type": "attack", "damage": 6, "hits": 1},
                    }
                ],
                "selection_mode": "UpgradeSelect",
                "selectable_cards": [False, False],
                "selection_confirm_enabled": True,
                "end_turn_enabled": False,
            }
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[0] is False
    assert mask[1] is False
    assert mask[2] is False
    assert mask[3] is False
    assert mask[4] is False
    assert mask[5] is False
    assert mask[6] is False
    assert mask[7] is False
    assert mask[8] is False
    assert mask[9] is False
    assert mask[50] is True


def test_build_game_state_from_payload_restores_combat_selection_metadata():
    """bridge payload 重建 GameState 时应保留战斗子选择上下文。"""
    payload = {
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy"},
                {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self"},
                {"id": "Bash", "cost": 2, "type": "Attack", "target": "AnyEnemy"},
            ],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "combat": {
            "monsters": [
                {
                    "name": "Cultist",
                    "hp": 48,
                    "max_hp": 48,
                    "intent": {"type": "attack", "damage": 6, "hits": 1},
                }
            ],
            "selection_mode": "UpgradeSelect",
            "selection_min": 1,
            "selection_max": 2,
            "selection_manual_confirm": True,
            "selection_confirm_enabled": True,
            "selectable_cards": [False, True, True],
            "selected_cards": [False, False, True],
            "selection_selected_count": 1,
            "playable_cards": [False, False, False],
            "end_turn_enabled": False,
        },
    }

    gs = build_game_state_from_payload(payload, character="Ironclad")
    selection = gs.combat.hand_selection

    assert selection is not None
    assert selection.mode == "UpgradeSelect"
    assert selection.min_select == 1
    assert selection.max_select == 2
    assert selection.manual_confirm is True
    assert selection.confirm_enabled is True
    assert selection.selectable_cards == [False, True, False]
    assert selection.selected_cards == [False, False, True]
    assert gs.combat.playable_cards_override == [False, False, False]
    assert gs.combat.end_turn_enabled_override is False


def test_build_game_state_from_payload_restores_runtime_card_state_and_obs_tail():
    """bridge payload 应保留 live runtime 卡牌语义，并把新特征追加到观测尾部。"""
    payload = {
        "phase": "combat",
        "character": "Necrobinder",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "energy_per_turn": 3,
            "orb_slots": 3,
            "orbs": ["LightningOrb", "DarkOrb"],
            "is_osty_missing": True,
            "hand": [
                {
                    "id": "Snap",
                    "cost": 1,
                    "type": "Attack",
                    "target": "AnyEnemy",
                    "damage": 10,
                    "keywords": ["Retain", "Exhaust"],
                    "tags": ["OstyAttack"],
                    "pool": "Necrobinder",
                    "vars": {"OstyDamage": 10, "Draw": 1},
                    "replay_count": 1,
                    "retain_this_turn": True,
                    "sly_this_turn": True,
                    "affliction_id": "RotAffliction",
                    "affliction_amount": 2,
                }
            ],
        },
        "deck": ["Snap"],
        "combat": {
            "monsters": [
                {
                    "name": "Cultist",
                    "hp": 48,
                    "max_hp": 48,
                    "intent": {"type": "attack", "damage": 6, "hits": 1},
                }
            ]
        },
    }

    gs = build_game_state_from_payload(payload, character="Necrobinder")
    card = gs.player.hand[0]

    assert gs.player.is_osty_missing is True
    assert gs.player.orb_slots == 3
    assert gs.player.orbs == ["LightningOrb", "DarkOrb"]
    assert card.damage == 10
    assert card.keywords == ["Retain", "Exhaust"]
    assert card.tags == ["OstyAttack"]
    assert card.pool == "Necrobinder"
    assert card.vars == {"OstyDamage": 10, "Draw": 1}
    assert card.replay_count == 1
    assert card.single_turn_retain is True
    assert card.single_turn_sly is True
    assert card.affliction_id == "RotAffliction"
    assert card.affliction_amount == 2

    from sts_env.encoding import DYNAMIC_COMBAT_FEATURE_DIM
    runtime_dim = COMBAT_RUNTIME_GLOBAL_DIM + 10 * COMBAT_RUNTIME_CARD_DIM
    # 修复：encode_observation 最后是 encode_combat_runtime + encode_dynamic_combat
    # 所以需要排除最后的 dynamic_dim 维
    obs = encode_observation(gs)
    dynamic_dim = DYNAMIC_COMBAT_FEATURE_DIM
    tail = obs[-(runtime_dim + dynamic_dim):-dynamic_dim]
    card_base = COMBAT_RUNTIME_GLOBAL_DIM

    assert tail[0] == 1.0
    assert np.isclose(tail[1], 0.3)
    assert np.isclose(tail[2], 0.2)
    assert np.isclose(tail[3], 0.1)
    assert tail[4] == 0.0
    assert np.isclose(tail[5], 0.1)
    assert np.isclose(tail[card_base], 1 / 3)
    assert tail[card_base + 1] == 1.0
    assert tail[card_base + 2] == 1.0
    assert tail[card_base + 3] == 1.0
    assert np.isclose(tail[card_base + 4], 0.2)
    assert tail[card_base + 5] == 1.0
    assert tail[card_base + 6] == 1.0
    assert tail[card_base + 7] == 0.0


def test_build_game_state_from_payload_restores_optional_enchantments():
    payload = {
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 80,
            "max_hp": 80,
            "energy": 3,
            "hand": [
                {
                    "id": "StrikeIronclad",
                    "cost": 1,
                    "type": "Attack",
                    "target": "AnyEnemy",
                    "damage": 6,
                    "enchantments": [
                        {"id": "Steady", "amount": 1, "disabled": False},
                        {"id": "Vigorous", "amount": 2, "disabled": True},
                    ],
                }
            ],
        },
        "deck": ["StrikeIronclad"],
        "combat": {"monsters": []},
    }

    gs = build_game_state_from_payload(payload, character="Ironclad")
    card = gs.player.hand[0]

    assert card.enchantments == [
        {"id": "Steady", "amount": 1, "disabled": False},
        {"id": "Vigorous", "amount": 2, "disabled": True},
    ]
    assert card.affliction_id == "Steady"
    assert card.affliction_amount == 1


def test_combat_burning_pact_enters_and_resolves_hand_selection():
    """训练战斗环境应真正进入手牌消耗子状态，并在选牌后完成结算。"""
    player = Player("Ironclad", 80, 80)
    player.energy = 3
    player.hand = [
        make_card("BurningPact"),
        make_card("StrikeIronclad"),
        make_card("DefendIronclad"),
    ]
    player.draw_pile = [make_card("Bash"), make_card("ShrugItOff")]
    combat = Combat(player, [Monster("Louse", 12, 12)])

    assert combat.play_card(0, 0) is True
    assert combat.hand_selection is not None
    assert combat.hand_selection.mode == "ExhaustSelect"
    assert combat.hand_selection.selectable_cards == [True, True]
    assert combat.select_hand_card(1) is True

    assert combat.hand_selection is None
    assert [card.card_id for card in player.exhaust_pile] == ["BurningPact", "DefendIronclad"]
    assert len(player.hand) == 3
    assert [card.card_id for card in player.hand] == ["StrikeIronclad", "ShrugItOff", "Bash"]


def test_combat_purity_supports_optional_multi_exhaust_selection():
    """Purity 应允许选择至多数张手牌进行消耗，并保留 confirm 兜底。"""
    player = Player("Colorless", 80, 80)
    player.energy = 3
    player.hand = [
        make_card("Purity"),
        make_card("StrikeIronclad"),
        make_card("DefendIronclad"),
        make_card("Bash"),
    ]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert combat.hand_selection is not None
    assert combat.hand_selection.min_select == 0
    assert combat.hand_selection.max_select == 3
    assert combat.hand_selection.confirm_enabled is True
    assert combat.select_hand_card(0) is True
    assert combat.select_hand_card(1) is True
    assert combat.confirm_hand_selection() is True

    assert [card.card_id for card in player.exhaust_pile] == ["Purity", "DefendIronclad", "StrikeIronclad"]
    assert [card.card_id for card in player.hand] == ["Bash"]


def test_combat_nightmare_adds_selected_card_copies_next_turn():
    """Nightmare 选中的手牌应在下回合抽牌前复制到手里。"""
    player = Player("Silent", 80, 80, draw_per_turn=0)
    player.energy = 3
    player.hand = [
        make_card("Nightmare"),
        make_card("Bash"),
    ]
    combat = Combat(player, [Monster("Louse", 12, 12)])

    assert combat.play_card(0, 0) is True
    assert combat.hand_selection is not None
    assert combat.select_hand_card(0) is True
    assert combat.hand_selection is None

    combat.end_player_turn()

    assert [card.card_id for card in player.hand] == ["Bash", "Bash", "Bash"]


def test_combat_transfigure_upgrade_removes_exhaust_and_applies_replay():
    """升级后的 Transfigure 不应自带 Exhaust，且应给目标牌增加费用与 Replay。"""
    player = Player("Necrobinder", 80, 80)
    player.energy = 3
    player.hand = [
        make_card("Transfigure", upgraded=True),
        make_card("StrikeIronclad"),
    ]
    combat = Combat(player, [Monster("Louse", 12, 12)])

    assert combat.play_card(0, 0) is True
    assert combat.hand_selection is not None
    assert combat.select_hand_card(0) is True

    assert combat.hand_selection is None
    assert player.hand[0].card_id == "StrikeIronclad"
    assert player.hand[0].cost == 2
    assert player.hand[0].replay_count == 1
    assert [card.card_id for card in player.discard_pile] == ["Transfigure"]
    assert player.exhaust_pile == []


def test_combat_decisions_decisions_autoplays_selected_skill_three_times():
    """DecisionsDecisions 应免费自动施放所选技能三次，并仅移动原牌一次。"""
    player = Player("Regent", 80, 80)
    player.energy = 0
    player.hand = [
        make_card("DecisionsDecisions"),
        make_card("DefendIronclad"),
    ]
    combat = Combat(player, [Monster("Louse", 12, 12)])

    assert combat.play_card(0, 0) is True
    assert combat.hand_selection is not None
    assert combat.select_hand_card(0) is True

    assert player.block == 15
    assert player.hand == []
    assert [card.card_id for card in player.exhaust_pile] == ["DecisionsDecisions"]
    assert [card.card_id for card in player.discard_pile] == ["DefendIronclad"]


def test_combat_snap_grants_retain_to_selected_card_across_turn():
    """Snap 贴上的 Retain 应让目标牌在回合结束后留在手里。"""
    player = Player("Necrobinder", 80, 80, draw_per_turn=0)
    player.energy = 3
    player.hand = [
        make_card("Snap"),
        make_card("StrikeIronclad"),
    ]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert combat.hand_selection is not None
    assert combat.select_hand_card(0) is True

    combat.end_player_turn()

    assert [card.card_id for card in player.hand] == ["StrikeIronclad"]
    assert "Retain" in player.hand[0].keywords


def test_combat_headbutt_moves_best_discard_card_to_draw_top():
    """Headbutt 应进入弃牌堆选择，并把所选牌放回牌堆顶。"""
    player = Player("Ironclad", 80, 80)
    player.energy = 1
    player.hand = [make_card("Headbutt")]
    player.discard_pile = [make_card("PommelStrike"), make_card("StrikeIronclad")]
    combat = Combat(player, [Monster("Louse", 12, 12)])

    assert combat.play_card(0, 0) is True
    assert combat.card_selection is not None
    assert [card.card_id for card in combat.card_selection.cards] == ["PommelStrike", "StrikeIronclad"]
    assert combat.select_card_option(0) is True
    assert player.draw_pile[-1].card_id == "PommelStrike"


def test_combat_hologram_opens_discard_selection_and_returns_chosen_card_to_hand():
    """Hologram 应进入弃牌选择，并把所选牌加入手牌。"""
    player = Player("Defect", 70, 70)
    player.energy = 1
    player.hand = [make_card("Hologram")]
    player.discard_pile = [make_card("StrikeDefect"), make_card("Defragment")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert combat.card_selection is not None
    assert combat.card_selection.kind == "discard"
    assert [card.card_id for card in combat.card_selection.cards] == ["StrikeDefect", "Defragment"]
    assert combat.select_card_option(1) is True
    assert [card.card_id for card in player.hand] == ["Defragment"]
    assert [card.card_id for card in player.exhaust_pile] == ["Hologram"]


def test_combat_charge_uses_explicit_draw_selection_for_each_transform():
    """Charge 应分步选择抽牌堆中的牌并逐张变形。"""
    player = Player("Regent", 70, 70)
    player.energy = 1
    player.hand = [make_card("Charge")]
    player.draw_pile = [make_card("StrikeRegent"), make_card("BigBang"), make_card("DefendRegent")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert combat.card_selection is not None
    assert combat.card_selection.kind == "draw"
    assert [card.card_id for card in combat.card_selection.cards] == ["DefendRegent", "StrikeRegent", "BigBang"]
    assert combat.select_card_option(0) is True
    assert combat.card_selection is not None
    assert [card.card_id for card in combat.card_selection.cards] == ["StrikeRegent", "BigBang", "MinionDiveBomb"]
    assert combat.select_card_option(0) is True
    assert sorted(card.card_id for card in player.draw_pile) == ["BigBang", "MinionDiveBomb", "MinionDiveBomb"]


def test_combat_cleanse_opens_draw_selection_and_exhausts_chosen_card():
    """Cleanse 应召唤 Osty，并显式选择一张抽牌堆卡牌 Exhaust。"""
    player = Player("Necrobinder", 70, 70)
    player.energy = 1
    player.hand = [make_card("Cleanse")]
    player.draw_pile = [make_card("StrikeNecrobinder"), make_card("Bury")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert player.is_osty_missing is False
    assert combat.card_selection is not None
    assert combat.card_selection.kind == "draw"
    assert [card.card_id for card in combat.card_selection.cards] == ["StrikeNecrobinder", "Bury"]
    assert combat.select_card_option(0) is True
    assert [card.card_id for card in player.exhaust_pile] == ["Cleanse", "StrikeNecrobinder"]


def test_combat_cosmic_indifference_opens_discard_selection_and_moves_choice_to_draw_top():
    """CosmicIndifference 应进入弃牌选择，并把所选牌放到抽牌堆顶。"""
    player = Player("Regent", 70, 70)
    player.energy = 1
    player.hand = [make_card("CosmicIndifference")]
    player.discard_pile = [make_card("DefendRegent"), make_card("BigBang")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert combat.card_selection is not None
    assert combat.card_selection.kind == "discard"
    assert [card.card_id for card in combat.card_selection.cards] == ["DefendRegent", "BigBang"]
    assert combat.select_card_option(1) is True
    assert player.draw_pile[-1].card_id == "BigBang"


def test_combat_seance_uses_explicit_draw_selection_for_transform():
    """Seance 应进入抽牌堆选择，并把选中的牌变为 Soul。"""
    player = Player("Necrobinder", 70, 70)
    player.energy = 1
    player.hand = [make_card("Seance")]
    player.draw_pile = [make_card("StrikeNecrobinder"), make_card("Bury")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert combat.card_selection is not None
    assert combat.card_selection.kind == "draw"
    assert [card.card_id for card in combat.card_selection.cards] == ["StrikeNecrobinder", "Bury"]
    assert combat.select_card_option(1) is True
    assert sorted(card.card_id for card in player.draw_pile) == ["Soul", "StrikeNecrobinder"]


def test_combat_discovery_adds_generated_card_free_this_turn_only():
    """Discovery 应进入三选一，并让选中的牌只在本回合免费。"""
    import random

    random.seed(123)
    player = Player("Silent", 70, 70, draw_per_turn=0)
    player.energy = 1
    player.hand = [make_card("Discovery")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert combat.card_selection is not None
    assert len(combat.card_selection.cards) == 3
    chosen = combat.card_selection.cards[0]
    assert combat.select_card_option(0) is True
    assert len(player.hand) == 1

    generated = player.hand[0]
    assert generated.card_id == chosen.card_id
    assert generated.single_turn_free is True
    assert generated.effective_cost(player.energy) == 0
    assert encode_card(generated)[0] == 0.0

    player.start_turn()

    assert generated.single_turn_free is False


def test_audit_selection_coverage_has_no_missing_or_simplified_handlers():
    """训练环境对当前反编译选牌机制不应再有缺失或简化 handler。"""
    report = audit_selection_coverage()
    summary = report["summary"]

    assert summary["missing_explicit_handler"] == 0
    assert summary["custom_handler_but_simplified"] == 0


def test_make_card_canonicalizes_runtime_uppercase_ids():
    """实机上传的大写枚举卡牌 id 也应映射回训练牌库。"""
    survivor = make_card("SURVIVOR")

    assert survivor.card_id == "Survivor"
    assert survivor.cost == 1
    assert survivor.block == 8


def test_create_power_canonicalizes_runtime_uppercase_ids():
    """实机上传的大写枚举 power id 也应恢复为已知 Power。"""
    power = create_power("WEAK_POWER", 2)

    assert power.power_id == "WeakPower"
    assert power.amount == 2


def test_build_game_state_from_payload_canonicalizes_runtime_uppercase_payload_ids():
    """桥接重建状态时应把实机枚举式 id 归一化到内部命名。"""
    payload = {
        "character": "Silent",
        "phase": "combat",
        "player": {
            "hp": 60,
            "max_hp": 70,
            "energy": 1,
            "potions": [{"id": "SWIFT_POTION", "target": "AnyPlayer"}],
            "hand": [{"id": "SURVIVOR", "cost": -1, "type": "Skill", "target": "Self", "block": 8}],
            "powers": [{"id": "WEAK_POWER", "amount": 1}],
        },
        "deck": ["SURVIVOR"],
        "combat": {
            "monsters": [{"name": "Louse", "hp": 12, "max_hp": 12, "powers": [{"id": "VULNERABLE_POWER", "amount": 1}]}],
        },
        "request_id": "runtime-uppercase",
    }

    gs = build_game_state_from_payload(payload, character="Silent")
    normalized = raw_state_to_act_message(payload)

    assert gs.player.hand[0].card_id == "Survivor"
    assert gs.player.potions[0]["id"] == "SWIFT_POTION"
    assert gs.player.powers[0].power_id == "WeakPower"
    assert gs.combat.monsters[0].powers[0].power_id == "VulnerablePower"
    assert normalized["action_mask"][A_PLAY_START] is True


def test_normalize_bridge_message_supports_nested_rest_state_and_tightens_mask():
    """嵌套 rest schema 应按按钮 enabled 收紧火堆动作。"""
    payload = {
        "type": "state",
        "phase": "rest",
        "character": "Ironclad",
        "player": {
            "hp": 45,
            "max_hp": 80,
            "relics": ["Girya", "Shovel", "MeatCleaver"],
        },
        "deck": [
            "StrikeIronclad",
            "DefendIronclad",
            "Bash",
            "PommelStrike",
        ],
        "state": {
            "rest": {
                "options": [
                    {"id": "rest", "enabled": True},
                    {"id": "upgrade", "enabled": False},
                    {"id": "dig", "enabled": True},
                    {"id": "cook", "enabled": False},
                    {"id": "lift", "enabled": True},
                ]
            }
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[74] is True
    assert mask[75] is False
    assert mask[76] is True
    assert mask[77] is False
    assert mask[78] is True


def test_normalize_bridge_message_disables_rest_actions_when_no_options_remain():
    """篝火只剩继续时，不应继续把休息/升级等动作暴露给模型。"""
    payload = {
        "type": "state",
        "phase": "rest",
        "character": "Ironclad",
        "player": {
            "hp": 45,
            "max_hp": 80,
            "relics": ["Girya", "Shovel", "MeatCleaver"],
        },
        "deck": [
            "StrikeIronclad",
            "DefendIronclad",
            "Bash",
            "PommelStrike",
        ],
        "state": {
            "rest": {
                "options": [],
                "can_proceed": True,
            }
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[74] is False
    assert mask[75] is False
    assert mask[76] is False
    assert mask[77] is False
    assert mask[78] is False


def test_normalize_bridge_message_disables_rest_actions_when_campfire_can_proceed_even_if_buttons_linger():
    payload = {
        "type": "state",
        "phase": "rest",
        "character": "Silent",
        "player": {
            "hp": 53,
            "max_hp": 77,
            "relics": ["Pomander"],
        },
        "deck": [
            "StrikeSilent",
            "DefendSilent",
            "Neutralize",
            "BladeDance",
        ],
        "state": {
            "rest": {
                "can_proceed": True,
                "options": [
                    {"id": "rest", "enabled": True},
                    {"id": "upgrade", "enabled": True},
                ],
            }
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[74] is False
    assert mask[75] is False



def test_normalize_bridge_message_supports_nested_shop_state_and_tightens_mask():
    """嵌套 shop schema 应映射购买列表并收紧禁用项。"""
    payload = {
        "type": "state",
        "phase": "shop",
        "character": "Ironclad",
        "player": {"hp": 70, "max_hp": 80, "gold": 999},
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "state": {
            "shop": {
                "cards": [
                    {"id": "Clash", "enabled": True},
                    {"id": "PommelStrike", "enabled": False},
                ],
                "relics": [
                    {"id": "Anchor", "rarity": "Common", "enabled": False},
                    {"id": "BagOfPreparation", "rarity": "Common", "enabled": True},
                ],
                "potions": [{"id": "FirePotion", "enabled": True}],
                "remove_cost": 75,
                "remove": {"enabled": False},
                "leave_enabled": True,
            }
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[A_SHOP_CARD_START] is True
    assert mask[A_SHOP_CARD_START + 1] is False
    assert mask[A_SHOP_POTION_START] is True
    assert mask[A_SHOP_RELIC_START] is False
    assert mask[A_SHOP_RELIC_START + 1] is True
    assert mask[A_SHOP_REMOVE] is False
    assert mask[A_SHOP_LEAVE] is True



def test_build_game_state_from_payload_supports_shop_phase():
    """商店阶段 payload 应重建为可消费的商店状态。"""
    payload = {
        "character": "Ironclad",
        "phase": "shop",
        "player": {"hp": 70, "max_hp": 80, "gold": 150},
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "shop": {
            "cards": ["Clash", "PommelStrike"],
            "relics": [{"id": "Anchor", "rarity": "Common"}],
            "potions": [{"id": "FirePotion"}],
            "remove_cost": 100,
        },
    }

    gs = build_game_state_from_payload(payload, character="Ironclad")

    assert gs.phase.name == "SHOP"
    assert [card.card_id for card in gs.shop_cards] == ["Clash", "PommelStrike"]
    assert gs.shop_relics == [{"id": "Anchor", "rarity": "Common"}]
    assert gs.shop_potions == [{"id": "FirePotion"}]
    assert gs.shop_remove_cost == 100



def test_normalize_bridge_message_supports_nested_boss_relic_state_and_tightens_mask():
    """嵌套 boss_relic schema 应保持顺序并收紧禁用选项。"""
    payload = {
        "type": "state",
        "phase": "boss_relic",
        "character": "Defect",
        "player": {"hp": 60, "max_hp": 75},
        "deck": ["StrikeDefect", "DefendDefect", "Zap"],
        "state": {
            "boss_relic": {
                "choices": [
                    {"id": "Inserter", "enabled": True},
                    {"id": "RunicDome", "enabled": False},
                    {"id": "CoffeeDripper", "enabled": True},
                ]
            }
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[A_BOSS_START] is True
    assert mask[A_BOSS_START + 1] is False
    assert mask[A_BOSS_START + 2] is True



def test_build_game_state_from_payload_supports_boss_relic_phase():
    """Boss 遗物阶段 payload 应重建遗物选项顺序。"""
    payload = {
        "character": "Defect",
        "phase": "boss_relic",
        "player": {"hp": 60, "max_hp": 75},
        "deck": ["StrikeDefect", "DefendDefect", "Zap"],
        "boss_relic_choices": ["Inserter", "RunicDome", "CoffeeDripper"],
    }

    gs = build_game_state_from_payload(payload, character="Defect")

    assert gs.phase.name == "BOSS_RELIC"
    assert gs.boss_relic_choices == ["Inserter", "RunicDome", "CoffeeDripper"]



def test_normalize_bridge_message_supports_nested_treasure_state_and_tightens_skip():
    """嵌套 treasure schema 应允许用 can_proceed 收紧 skip。"""
    payload = {
        "type": "state",
        "phase": "treasure",
        "character": "Ironclad",
        "player": {"hp": 70, "max_hp": 80},
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "state": {
            "treasure": {
                "opened": False,
                "can_proceed": False,
                "relics": [{"id": "Anchor"}],
            }
        },
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["action_mask"][73] is False



def test_normalize_bridge_message_supports_nested_card_reward_state_and_tightens_skip():
    """嵌套 card_reward schema 应在 can_skip 为假时禁用 skip。"""
    payload = {
        "type": "state",
        "phase": "card_reward",
        "character": "Silent",
        "player": {"hp": 70, "max_hp": 70},
        "deck": ["StrikeSilent", "DefendSilent"],
        "state": {
            "card_reward": {
                "cards": [
                    {"id": "DaggerThrow", "enabled": True},
                    {"id": "Backflip", "enabled": True},
                ],
                "can_skip": False,
            }
        },
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["action_mask"][70] is True
    assert normalized["action_mask"][71] is True
    assert normalized["action_mask"][73] is False



def test_normalize_bridge_message_allows_advancing_empty_card_selection_confirmation_state():
    """通用选牌确认页若已无候选牌，应保留 skip/confirm 推进行为。"""
    payload = {
        "type": "state",
        "phase": "card_reward",
        "character": "Silent",
        "player": {"hp": 70, "max_hp": 70},
        "deck": ["StrikeSilent", "DefendSilent"],
        "state": {
            "card_reward": {
                "cards": [],
                "can_skip": True,
            }
        },
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["action_mask"][70] is False
    assert normalized["action_mask"][71] is False
    assert normalized["action_mask"][72] is False
    assert normalized["action_mask"][73] is True


def test_normalize_state_envelope_maps_generic_card_selection_to_card_select_phase():
    """泛化选牌界面应转成独立 card_select 阶段，而不是压回 top-3 奖励选牌。"""
    payload = {
        "type": "state",
        "phase": "card_reward",
        "character": "Ironclad",
        "act": 1,
        "floor": 5,
        "player": {"hp": 70, "max_hp": 80},
        "deck": [
            {"id": "StrikeIronclad"},
            {"id": "DefendIronclad"},
            {"id": "Bash"},
            {"id": "ShrugItOff"},
            {"id": "Offering"},
        ],
        "state": {
            "card_reward": {
                "cards": [
                    {"id": "Offering", "enabled": True},
                    {"id": "Bash", "enabled": True},
                    {"id": "DefendIronclad", "enabled": True},
                    {"id": "StrikeIronclad", "enabled": True},
                    {"id": "ShrugItOff", "enabled": True},
                ],
                "can_skip": False,
                "selection_kind": "remove",
            }
        },
    }

    normalized = normalize_state_envelope(payload)

    assert normalized["phase"] == "card_select"
    assert normalized["selection_kind"] == "remove"
    assert [card["id"] for card in normalized["selection_cards"]] == [
        "Offering",
        "Bash",
        "DefendIronclad",
        "StrikeIronclad",
        "ShrugItOff",
    ]
    assert normalized["_bridge_ui"]["selection_can_skip"] is False


def test_normalize_state_envelope_preserves_generic_choose_card_order():
    """choose 类泛化选牌应保留真实候选顺序，交给模型直接决策。"""
    deck = [{"id": "StrikeSilent"}, {"id": "DefendSilent"}, {"id": "Neutralize"}, {"id": "Survivor"}]
    offered = [
        {"id": "Accuracy", "enabled": True},
        {"id": "Backflip", "enabled": True},
        {"id": "Blur", "enabled": True},
        {"id": "DaggerThrow", "enabled": True},
        {"id": "Acrobatics", "enabled": True},
    ]
    payload = {
        "type": "state",
        "phase": "card_reward",
        "character": "Silent",
        "act": 1,
        "floor": 6,
        "player": {"hp": 68, "max_hp": 70},
        "deck": deck,
        "state": {
            "card_reward": {
                "cards": offered,
                "can_skip": False,
                "selection_kind": "choose",
            }
        },
    }

    normalized = normalize_state_envelope(payload)

    assert normalized["phase"] == "card_select"
    assert normalized["selection_kind"] == "choose"
    assert [card["id"] for card in normalized["selection_cards"]] == [item["id"] for item in offered]
    assert normalized["selection_total_count"] == 5
    assert normalized["selection_truncated"] is False


def test_normalize_state_envelope_truncates_generic_card_selection_to_action_capacity():
    """泛化选牌超过动作空间容量时，应稳定截断到 40 个候选。"""
    offered = [
        {"id": f"Card{i}", "enabled": True}
        for i in range(45)
    ]
    payload = {
        "type": "state",
        "phase": "card_reward",
        "character": "Silent",
        "player": {"hp": 68, "max_hp": 70},
        "deck": [{"id": "StrikeSilent"}, {"id": "DefendSilent"}],
        "state": {
            "card_reward": {
                "cards": offered,
                "can_skip": False,
                "selection_kind": "choose",
            }
        },
    }

    normalized = normalize_state_envelope(payload)

    assert normalized["phase"] == "card_select"
    assert len(normalized["selection_cards"]) == 40
    assert [card["id"] for card in normalized["selection_cards"][:3]] == ["Card0", "Card1", "Card2"]
    assert normalized["selection_cards"][-1]["id"] == "Card39"
    assert normalized["selection_total_count"] == 45
    assert normalized["selection_truncated"] is True
    assert normalized["_bridge_ui"]["selection_enabled"] == [True] * 40
    assert normalized["_bridge_ui"]["selection_total_count"] == 45
    assert normalized["_bridge_ui"]["selection_truncated"] is True
    assert normalized["_bridge_ui"]["selection_choice_map"] == list(range(40))


def test_normalize_state_envelope_preserves_embedded_combat_state_for_card_selection():
    """card_reward phase 附带 combat 子状态时应一并透传，供选牌时保留战斗上下文。"""
    payload = {
        "type": "state",
        "phase": "card_reward",
        "character": "Ironclad",
        "player": {
            "hp": 45,
            "max_hp": 80,
            "energy": 2,
            "energy_per_turn": 3,
            "hand": [
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy"},
            ],
        },
        "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
        "state": {
            "card_reward": {
                "cards": [
                    {"id": "PommelStrike", "enabled": True},
                    {"id": "ShrugItOff", "enabled": True},
                ],
                "can_skip": True,
            },
            "combat": {
                "monsters": [
                    {
                        "name": "Cultist",
                        "hp": 32,
                        "max_hp": 50,
                        "intent": {"type": "attack", "damage": 6, "hits": 1},
                    }
                ],
                "playable_cards": [True],
                "end_turn_enabled": False,
            },
        },
    }

    normalized = normalize_state_envelope(payload)

    assert normalized["phase"] == "card_reward"
    assert normalized["card_rewards"][0]["id"] == "PommelStrike"
    assert normalized["combat"]["monsters"][0]["name"] == "Cultist"
    assert normalized["_bridge_ui"]["card_reward_can_skip"] is True
    assert normalized["_bridge_ui"]["combat_end_turn_enabled"] is False


def test_normalize_bridge_message_disables_combat_actions_during_card_select_phase():
    """card_select 阶段即使附带 combat 上下文，也不应继续暴露普通出牌动作。"""
    payload = {
        "type": "state",
        "phase": "card_select",
        "character": "Silent",
        "player": {
            "hp": 55,
            "max_hp": 70,
            "energy": 2,
            "energy_per_turn": 3,
            "hand": [
                {"id": "Scavenge", "cost": 1, "type": "Skill", "target": "Self"},
                {"id": "StrikeSilent", "cost": 1, "type": "Attack", "target": "AnyEnemy"},
            ],
        },
        "deck": ["Scavenge", "StrikeSilent", "DefendSilent"],
        "state": {
            "card_select": {
                "cards": [
                    {"id": "Scavenge", "enabled": True},
                    {"id": "StrikeSilent", "enabled": True},
                ],
                "can_skip": False,
                "selection_kind": "exhaust",
            },
            "combat": {
                "monsters": [
                    {
                        "name": "Cultist",
                        "hp": 28,
                        "max_hp": 50,
                        "intent": {"type": "attack", "damage": 6, "hits": 1},
                    }
                ],
                "playable_cards": [True, True],
                "end_turn_enabled": False,
            },
        },
    }

    normalized = normalize_bridge_message(payload)
    mask = normalized["action_mask"]

    assert mask[A_PLAY_START] is False
    assert mask[A_PLAY_START + 5] is False
    assert mask[A_SELECT_START] is True
    assert mask[A_SELECT_START + 1] is True
    assert mask[A_SKIP] is False
    assert mask[A_END_TURN] is False


def test_normalize_state_envelope_preserves_game_over_badges_and_score_lines():
    """新版 game_over 载荷中的 badges 和 score lines 不应在规范化时丢失。"""
    payload = {
        "type": "state",
        "phase": "game_over",
        "character": "Silent",
        "run": {"act": 3, "floor": 51, "won": True},
        "player": {"hp": 55, "max_hp": 70},
        "state": {
            "game_over": {
                "won": True,
                "score": 1234,
                "badges": [
                    {"id": "BigDeck", "rarity": "Bronze"},
                    {"id": "EliteKiller", "rarity": "Silver"},
                ],
                "score_lines": [
                    {"id": "floors_climbed", "amount": 51, "score": 1110, "score_label": "+1110"},
                    {"id": "ascension", "amount": 6, "score_label": "x1.6"},
                ],
            }
        },
    }

    normalized = normalize_state_envelope(payload)

    assert normalized["phase"] == "game_over"
    assert normalized["won"] is True
    assert normalized["game_over"]["score"] == 1234
    assert normalized["game_over"]["badges"] == [
        {"id": "BigDeck", "rarity": "Bronze"},
        {"id": "EliteKiller", "rarity": "Silver"},
    ]
    assert normalized["game_over"]["score_lines"][0]["id"] == "floors_climbed"
    assert normalized["game_over"]["score_lines"][1]["score_label"] == "x1.6"


def test_normalize_state_envelope_preserves_game_over_history_and_character_stats():
    """game_over 还应保留 run_history 与 character_stats 摘要。"""
    payload = {
        "type": "state",
        "phase": "game_over",
        "character": "Defect",
        "run": {"act": 2, "floor": 34, "won": False},
        "player": {"hp": 0, "max_hp": 70},
        "state": {
            "game_over": {
                "run_history": {
                    "seed": "ABC123",
                    "start_time": 1700000000,
                    "run_time_seconds": 1820.5,
                    "ascension": 6,
                    "build_id": "v0.102.0",
                    "was_abandoned": False,
                    "win": False,
                    "game_mode": "Standard",
                    "player_count": 1,
                    "floor_reached": 34,
                    "killed_by_encounter": "JawWormPack",
                    "player": {
                        "id": 99,
                        "character": "Defect",
                        "deck_size": 28,
                        "relic_count": 9,
                        "potion_count": 2,
                        "max_potion_slots": 3,
                        "badges": [{"id": "BigDeck", "rarity": "Bronze"}],
                    },
                },
                "character_stats": {
                    "character": "Defect",
                    "max_ascension": 8,
                    "preferred_ascension": 6,
                    "total_wins": 12,
                    "total_losses": 9,
                    "fastest_win_time": 1500,
                    "best_win_streak": 4,
                    "current_win_streak": 1,
                    "playtime": 123456,
                    "badges": [{"id": "BigDeck", "rarity": "Bronze", "count": 3}],
                },
            }
        },
    }

    normalized = normalize_state_envelope(payload)

    assert normalized["phase"] == "game_over"
    assert normalized["game_over"]["run_history"]["seed"] == "ABC123"
    assert normalized["game_over"]["run_history"]["player"]["deck_size"] == 28
    assert normalized["game_over"]["run_history"]["player"]["badges"] == [{"id": "BigDeck", "rarity": "Bronze"}]
    assert normalized["game_over"]["character_stats"]["max_ascension"] == 8
    assert normalized["game_over"]["character_stats"]["badges"] == [
        {"id": "BigDeck", "rarity": "Bronze", "count": 3}
    ]


def test_normalize_bridge_message_preserves_game_over_summary_in_act_payload():
    """state -> act 归一化后也应保留 game_over 摘要，供上层直接消费。"""
    payload = {
        "type": "state",
        "request_id": "game-over-act-1",
        "character": "Silent",
        "phase": "game_over",
        "player": {"hp": 0, "max_hp": 70},
        "deck": ["StrikeSilent", "DefendSilent"],
        "state": {
            "game_over": {
                "won": False,
                "score": 999,
                "badges": [{"id": "BigDeck", "rarity": "Bronze"}],
            }
        },
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["game_over"] == {
        "won": False,
        "score": 999,
        "badges": [{"id": "BigDeck", "rarity": "Bronze"}],
    }


def test_process_websocket_message_forwards_deterministic_from_state_payload(tmp_path):
    """正式 state schema 的 deterministic 应透传到运行时预测。"""
    captured = {}

    def loader(character: str, model_path: Path):
        runtime = StubRuntime(character=character, model_path=model_path)
        captured["runtime"] = runtime
        return runtime

    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=loader)
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "nested-ws-det-1",
                "character": "Silent",
                "phase": "card_reward",
                "deterministic": False,
                "player": {"hp": 70, "max_hp": 70},
                "deck": ["StrikeSilent", "DefendSilent"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "DaggerThrow", "enabled": True},
                            {"id": "Backflip", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
    )

    assert response["ok"] is True
    assert captured["runtime"].model.calls[0][1] is False



def test_process_websocket_message_defaults_state_payload_to_stochastic_policy(tmp_path):
    """正式 state schema 缺省 deterministic 时也应走采样策略。"""
    captured = {}

    def loader(character: str, model_path: Path):
        runtime = StubRuntime(character=character, model_path=model_path)
        captured["runtime"] = runtime
        return runtime

    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=loader)
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "nested-ws-det-default-1",
                "character": "Silent",
                "phase": "card_reward",
                "player": {"hp": 70, "max_hp": 70},
                "deck": ["StrikeSilent", "DefendSilent"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "DaggerThrow", "enabled": True},
                            {"id": "Backflip", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
    )

    assert response["ok"] is True
    assert captured["runtime"].model.calls[0][1] is False


def test_process_websocket_message_supports_nested_state_payload(tmp_path):
    """WebSocket 入口应接受正式 state schema 并返回结构化决策。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "schema_version": 1,
                "request_id": "nested-ws-1",
                "character": "Silent",
                "phase": "card_reward",
                "player": {"hp": 70, "max_hp": 70},
                "deck": ["StrikeSilent", "DefendSilent"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "DaggerThrow", "enabled": True},
                            {"id": "Backflip", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["request_id"] == "nested-ws-1"
    assert response["character"] == "Silent"
    assert response["decision"] == {"type": "pick_card", "index": 0}



def test_process_websocket_message_uses_default_character_for_nested_state(tmp_path):
    """正式 state schema 缺少 character 时也应回退到默认角色。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "phase": "card_reward",
                "request_id": "nested-ws-2",
                "player": {"hp": 70, "max_hp": 80},
                "deck": ["StrikeIronclad", "DefendIronclad"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "Clash", "enabled": True},
                            {"id": "PommelStrike", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
        default_character="Ironclad",
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["request_id"] == "nested-ws-2"
    assert response["character"] == "Ironclad"
    assert response["decision"] == {"type": "pick_card", "index": 1}



def test_normalize_bridge_message_supports_direct_act_payload():
    """直接发送 observation 与 action_mask 时应自动补齐 act 类型。"""
    payload = {
        "character": "Silent",
        "observation": [0.0] * get_obs_dim(),
        "action_mask": [True] + [False] * (TOTAL_ACTIONS - 1),
    }

    normalized = normalize_bridge_message(payload)

    assert normalized["type"] == "act"
    assert normalized["character"] == "Silent"



def test_normalize_bridge_message_applies_default_character_and_model():
    """load/act 请求应支持使用默认角色和模型路径。"""
    normalized = normalize_bridge_message(
        {"type": "load"},
        default_character="Defect",
        default_model_path="models/Defect/final.zip",
    )

    assert normalized["character"] == "Defect"
    assert normalized["model_path"] == "models/Defect/final.zip"



def test_process_websocket_message_returns_action_with_decision(tmp_path):
    """WebSocket 桥接应附带结构化 decision 字段。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    mask = [False] * TOTAL_ACTIONS
    mask[4] = True
    mask[8] = True

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "character": "Silent",
                "observation": [0.0] * get_obs_dim(),
                "action_mask": mask,
                "request_id": "ws-1",
            }
        ),
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["action"] == 8
    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 3}
    assert response["request_id"] == "ws-1"



def test_process_websocket_message_uses_default_character_for_raw_state(tmp_path):
    """原始状态缺少 character 时应回退到默认角色。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "phase": "card_reward",
                "request_id": "ws-2",
                "player": {"hp": 70, "max_hp": 70},
                "deck": ["StrikeIronclad", "DefendIronclad"],
                "card_rewards": ["Clash", "PommelStrike"],
            }
        ),
        default_character="Ironclad",
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["request_id"] == "ws-2"
    assert response["character"] == "Ironclad"
    assert response["decision"] == {"type": "skip"}





def test_normalize_bridge_message_uses_control_state_model_override(tmp_path):
    """控制状态中的角色模型覆盖应注入到 act/load 请求。"""
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Silent")
    control_state.set_model_override("Silent", tmp_path / "silent_override.zip")

    normalized = normalize_bridge_message(
        {"type": "load", "character": "Silent"},
        control_state_store=control_state,
    )

    assert normalized["character"] == "Silent"
    assert normalized["model_path"] == str(tmp_path / "silent_override.zip")


def test_process_websocket_message_returns_idle_when_paused(tmp_path):
    """暂停时应返回 idle，而不是继续推理。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Silent")
    control_state.set_paused(True)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "paused-1",
                "character": "Silent",
                "phase": "card_reward",
                "player": {"hp": 70, "max_hp": 70},
                "deck": ["StrikeSilent", "DefendSilent"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "DaggerThrow", "enabled": True},
                            {"id": "Backflip", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
        control_state_store=control_state,
    )

    assert response == {
        "ok": True,
        "type": "idle",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "paused-1",
        "reason": "paused",
    }
    assert registry.cache_size() == 0

    state = control_state.load()
    assert state.last_request_id == "paused-1"
    assert state.last_response_type == "idle"
    assert state.last_error is None


def test_process_websocket_message_returns_idle_when_state_has_no_valid_actions(tmp_path):
    """状态经 UI 收紧后若已无合法动作，bridge 应返回 idle 而不是 error。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Ironclad")

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "no-actions-1",
                "character": "Ironclad",
                "phase": "treasure",
                "player": {"hp": 70, "max_hp": 80},
                "deck": ["StrikeIronclad", "DefendIronclad"],
                "state": {
                    "treasure": {
                        "opened": False,
                        "can_proceed": False,
                    }
                },
            }
        ),
        control_state_store=control_state,
    )

    assert response == {
        "ok": True,
        "type": "idle",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "no-actions-1",
        "reason": "no_valid_actions",
    }
    assert registry.cache_size() == 0

    state = control_state.load()
    assert state.last_request_id == "no-actions-1"
    assert state.last_response_type == "idle"
    assert state.last_error is None


def test_process_websocket_message_returns_idle_when_rest_options_are_empty(tmp_path):
    """篝火只剩继续时应返回 idle，避免模型重复发送 rest 动作。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Ironclad")

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "rest-no-actions-1",
                "character": "Ironclad",
                "phase": "rest",
                "player": {
                    "hp": 45,
                    "max_hp": 80,
                    "relics": ["Girya", "Shovel", "MeatCleaver"],
                },
                "deck": ["StrikeIronclad", "DefendIronclad", "Bash", "PommelStrike"],
                "state": {
                    "rest": {
                        "options": [],
                        "can_proceed": True,
                    }
                },
            }
        ),
        control_state_store=control_state,
    )

    assert response == {
        "ok": True,
        "type": "idle",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "rest-no-actions-1",
        "reason": "no_valid_actions",
    }
    assert registry.cache_size() == 0

    state = control_state.load()
    assert state.last_request_id == "rest-no-actions-1"
    assert state.last_response_type == "idle"
    assert state.last_error is None


def test_process_websocket_message_returns_idle_with_game_over_summary(tmp_path):
    """game_over 应显式返回 idle，并保留结算摘要给上层。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Silent")

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "game-over-1",
                "character": "Silent",
                "phase": "game_over",
                "player": {"hp": 0, "max_hp": 70},
                "deck": ["StrikeSilent", "DefendSilent"],
                "state": {
                    "game_over": {
                        "won": False,
                        "score": 1234,
                        "badges": [{"id": "BigDeck", "rarity": "Bronze"}],
                        "score_lines": [{"id": "floors_climbed", "amount": 42, "score": 760, "score_label": "+760"}],
                    }
                },
            }
        ),
        control_state_store=control_state,
    )

    assert response == {
        "ok": True,
        "type": "idle",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "game-over-1",
        "reason": "game_over",
        "game_over": {
            "won": False,
            "score": 1234,
            "badges": [{"id": "BigDeck", "rarity": "Bronze"}],
            "score_lines": [{"id": "floors_climbed", "amount": 42, "score": 760, "score_label": "+760"}],
        },
    }
    assert registry.cache_size() == 0

    state = control_state.load()
    assert state.last_request_id == "game-over-1"
    assert state.last_response_type == "idle"
    assert state.last_error is None


def test_process_websocket_message_returns_restart_required_on_character_mismatch(tmp_path):
    """目标角色与当前实机角色不一致时应要求重开。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Silent")

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "restart-1",
                "character": "Ironclad",
                "phase": "card_reward",
                "player": {"hp": 70, "max_hp": 80},
                "deck": ["StrikeIronclad", "DefendIronclad"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "Clash", "enabled": True},
                            {"id": "PommelStrike", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
        control_state_store=control_state,
    )

    assert response == {
        "ok": True,
        "type": "restart_required",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "restart-1",
        "target_character": "Silent",
        "current_character": "Ironclad",
    }
    assert registry.cache_size() == 0


def test_process_websocket_message_tolerates_invalid_bridge_port_in_control_state(tmp_path):
    control_state_path = tmp_path / "bridge_control_state.json"
    control_state_path.write_text(
        json.dumps({"bridge_port": "oops"}, ensure_ascii=False),
        encoding="utf-8",
    )
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(control_state_path)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "ping",
                "request_id": "bad-port-1",
            }
        ),
        control_state_store=control_state,
    )

    assert response == {
        "ok": True,
        "type": "pong",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "bad-port-1",
    }
    assert control_state.load().bridge_port is None


def test_process_websocket_message_accepts_character_case_variants(tmp_path):
    """角色名仅大小写不同不应被误判为需要重开。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Ironclad")
    control_state.set_model_override("Ironclad", tmp_path / "ironclad_override.zip")

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "case-1",
                "character": "IRONCLAD",
                "phase": "card_reward",
                "player": {"hp": 70, "max_hp": 80},
                "deck": ["StrikeIronclad", "DefendIronclad"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "Clash", "enabled": True},
                            {"id": "PommelStrike", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
        control_state_store=control_state,
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert registry.cache_size() == 1


def test_process_websocket_message_keeps_current_run_when_only_model_changes(tmp_path):
    """同角色切换模型时应继续当前局，并使用新的模型路径。"""
    captured = {}

    def loader(character: str, model_path: Path):
        runtime = StubRuntime(character=character, model_path=model_path)
        captured["runtime"] = runtime
        return runtime

    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=loader)
    server = BridgeServer(registry=registry)
    control_state = BridgeControlStateStore(tmp_path / "bridge_control_state.json")
    control_state.ensure_initialized(desired_character="Silent")
    control_state.set_model_override("Silent", tmp_path / "silent_new.zip")

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "same-character-1",
                "character": "Silent",
                "phase": "card_reward",
                "player": {"hp": 70, "max_hp": 70},
                "deck": ["StrikeSilent", "DefendSilent"],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "DaggerThrow", "enabled": True},
                            {"id": "Backflip", "enabled": True},
                        ],
                        "can_skip": True,
                    }
                },
            }
        ),
        control_state_store=control_state,
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["request_id"] == "same-character-1"
    assert response["decision"] == {"type": "pick_card", "index": 0}
    assert response["model_path"] == str(tmp_path / "silent_new.zip")
    assert captured["runtime"].model_path == Path(tmp_path / "silent_new.zip")

    state = control_state.load()
    assert state.last_request_id == "same-character-1"
    assert state.last_response_type == "action"
    assert state.last_error is None


def test_process_websocket_message_maps_generic_card_selection_back_to_real_index(tmp_path):
    """泛化 remove 选牌回包应映射到启发式挑中的真实候选索引。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "generic-pick-1",
                "character": "Ironclad",
                "phase": "card_reward",
                "act": 1,
                "floor": 5,
                "player": {"hp": 70, "max_hp": 80},
                "deck": [
                    {"id": "StrikeIronclad"},
                    {"id": "DefendIronclad"},
                    {"id": "Bash"},
                    {"id": "ShrugItOff"},
                    {"id": "Offering"},
                ],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": "Offering", "enabled": True},
                            {"id": "Bash", "enabled": True},
                            {"id": "DefendIronclad", "enabled": True},
                            {"id": "StrikeIronclad", "enabled": True},
                            {"id": "ShrugItOff", "enabled": True},
                        ],
                        "can_skip": False,
                        "selection_kind": "remove",
                    }
                },
            }
        ),
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["action"] == A_SELECT_START + 3
    assert response["decision"] == {"type": "pick_card", "index": 3}
    assert response["request_id"] == "generic-pick-1"


def test_process_websocket_message_uses_last_available_generic_card_selection_slot(tmp_path):
    """泛化 remove 选牌若候选全未知，应回退到第一个可表达槽位。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "generic-pick-max-1",
                "character": "Ironclad",
                "phase": "card_reward",
                "act": 1,
                "floor": 5,
                "player": {"hp": 70, "max_hp": 80},
                "deck": [{"id": "StrikeIronclad"}, {"id": "DefendIronclad"}],
                "state": {
                    "card_reward": {
                        "cards": [
                            {"id": f"Choice{i}", "enabled": True}
                            for i in range(45)
                        ],
                        "can_skip": False,
                        "selection_kind": "remove",
                    }
                },
            }
        ),
    )

    assert response["ok"] is True
    assert response["type"] == "action"
    assert response["action"] == A_SELECT_START
    assert response["decision"] == {"type": "pick_card", "index": 0}
    assert response["request_id"] == "generic-pick-max-1"


def test_adapt_response_for_websocket_prefers_best_discard_retrieval_pick():
    """card_select 弃牌堆选牌时，应优先选最能支撑当前回合连段的牌。"""
    request = normalize_bridge_message(
        {
            "type": "state",
            "request_id": "card-select-heuristic-1",
            "character": "Defect",
            "phase": "card_select",
            "player": {
                "hp": 58,
                "max_hp": 75,
                "energy": 2,
                "block": 0,
                "hand": [
                    {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
                ],
            },
            "state": {
                "card_select": {
                    "selection_kind": "discard",
                    "selection_cards": [
                        {"id": "DefendDefect", "cost": 1, "type": "Skill", "target": "Self", "block": 8},
                        {"id": "BeamCell", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 3, "powers": {"VulnerablePower": 1}},
                    ],
                },
                "combat": {
                    "monsters": [
                        {"name": "Target", "hp": 30, "max_hp": 30, "intent": {"type": "buff"}, "powers": []},
                    ]
                }
            },
        }
    )

    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": A_SELECT_START, "request_id": "card-select-heuristic-1"},
        request=request,
    )

    assert response["decision"] == {"type": "pick_card", "index": 1}
    assert response["action"] == A_SELECT_START + 1


def test_adapt_response_for_websocket_prefers_best_topdeck_pick():
    """放回抽牌堆顶的选牌应优先选择高价值展开牌。"""
    request = normalize_bridge_message(
        {
            "type": "state",
            "request_id": "card-select-heuristic-2",
            "character": "Ironclad",
            "phase": "card_select",
            "player": {
                "hp": 60,
                "max_hp": 80,
                "energy": 1,
                "block": 0,
                "hand": [
                    {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
                ],
            },
            "state": {
                "card_select": {
                    "selection_kind": "draw",
                    "selection_cards": [
                        {"id": "DefendIronclad", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
                        {"id": "PommelStrike", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 9, "draw": 1},
                    ],
                },
                "combat": {
                    "monsters": [
                        {"name": "Target", "hp": 34, "max_hp": 34, "intent": {"type": "buff"}, "powers": []},
                    ]
                }
            },
        }
    )

    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": A_SELECT_START, "request_id": "card-select-heuristic-2"},
        request=request,
    )

    assert response["decision"] == {"type": "pick_card", "index": 1}
    assert response["action"] == A_SELECT_START + 1


def test_adapt_response_for_websocket_prefers_removing_basic_over_power_card():
    """泛化 remove 选牌应优先移除基础 Strike/Defend，而不是功能牌。"""
    request = normalize_bridge_message(
        {
            "type": "state",
            "request_id": "card-select-heuristic-3",
            "character": "Ironclad",
            "phase": "card_select",
            "player": {"hp": 70, "max_hp": 80},
            "state": {
                "card_select": {
                    "selection_kind": "remove",
                    "selection_cards": [
                        {"id": "Inflame", "cost": 1, "type": "Power", "target": "Self"},
                        {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                    ],
                }
            },
        }
    )

    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": A_SELECT_START, "request_id": "card-select-heuristic-3"},
        request=request,
    )

    assert response["decision"] == {"type": "pick_card", "index": 1}
    assert response["action"] == A_SELECT_START + 1


def test_adapt_response_for_websocket_prefers_best_upgrade_target():
    request = normalize_bridge_message(
        {
            "type": "state",
            "request_id": "card-select-upgrade-1",
            "character": "Ironclad",
            "phase": "card_select",
            "player": {"hp": 70, "max_hp": 80},
            "deck": [
                {"id": "StrikeIronclad"},
                {"id": "BurningPact"},
                {"id": "PommelStrike"},
                {"id": "DefendIronclad"},
            ],
            "state": {
                "card_select": {
                    "selection_kind": "upgrade",
                    "selection_cards": [
                        {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                        {"id": "BurningPact", "cost": 1, "type": "Skill", "target": "Self"},
                    ],
                }
            },
        }
    )

    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": A_SELECT_START, "request_id": "card-select-upgrade-1"},
        request=request,
    )

    assert response["decision"] == {"type": "pick_card", "index": 1}
    assert response["action"] == A_SELECT_START + 1


def test_adapt_response_for_websocket_prefers_discarding_sly_card_in_hand_selection():
    """战斗中的弃牌选择应优先丢本回合带 Sly 的牌。"""
    payload = {
        "request_id": "combat-hand-select-1",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 61,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "StrikeSilent", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "Snap", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 10, "sly_this_turn": True},
                {"id": "Prepared", "cost": 0, "type": "Skill", "target": "Self", "draw": 1},
            ],
        },
        "deck": ["StrikeSilent", "Snap", "Prepared"],
        "combat": {
            "monsters": [
                {"name": "Cultist", "hp": 40, "max_hp": 40, "intent": {"type": "buff"}, "powers": []},
            ],
            "selection_mode": "DiscardSelect",
            "selectable_cards": [True, True, False],
            "selection_confirm_enabled": False,
            "end_turn_enabled": False,
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-hand-select-1"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_avoids_discarding_powerful_non_sly_card():
    """没有 Sly 时，弃牌选择不应优先丢掉高价值 Power。"""
    payload = {
        "request_id": "combat-hand-select-2",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 68,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Inflame", "cost": 1, "type": "Power", "target": "Self"},
                {"id": "Wound", "cost": 0, "type": "Status", "target": "None"},
                {"id": "BurningPact", "cost": 1, "type": "Skill", "target": "Self", "draw": 2},
            ],
        },
        "deck": ["Inflame", "Wound", "BurningPact"],
        "combat": {
            "monsters": [
                {"name": "Louse", "hp": 16, "max_hp": 16, "intent": {"type": "attack", "damage": 6, "hits": 1}, "powers": []},
            ],
            "selection_mode": "DiscardSelect",
            "selectable_cards": [True, True, False],
            "selection_confirm_enabled": False,
            "end_turn_enabled": False,
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-hand-select-2"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_prefers_bad_card_first_in_multi_exhaust_selection():
    """多选烧牌时应先选最差的牌，而不是高价值牌。"""
    payload = {
        "request_id": "combat-hand-select-3",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Wound", "cost": 0, "type": "Status", "target": "None"},
                {"id": "StrikeIronclad", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "Inflame", "cost": 1, "type": "Power", "target": "Self"},
                {"id": "Purity", "cost": 0, "type": "Skill", "target": "Self"},
            ],
        },
        "deck": ["Wound", "StrikeIronclad", "Inflame", "Purity"],
        "combat": {
            "monsters": [
                {"name": "Cultist", "hp": 40, "max_hp": 40, "intent": {"type": "buff"}, "powers": []},
            ],
            "selection_mode": "ExhaustSelect",
            "selection_min": 0,
            "selection_max": 3,
            "selection_manual_confirm": True,
            "selection_confirm_enabled": True,
            "selectable_cards": [True, True, True, False],
            "selected_cards": [False, False, False, False],
            "selection_selected_count": 0,
            "end_turn_enabled": False,
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-hand-select-3"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 0, "target_index": 0}
    assert response["action"] == 0


def test_adapt_response_for_websocket_confirms_optional_multi_exhaust_when_no_good_targets_left():
    """可选多选烧牌在坏牌已选完后，应主动确认而不是继续乱选。"""
    payload = {
        "request_id": "combat-hand-select-4",
        "phase": "combat",
        "character": "Ironclad",
        "player": {
            "hp": 70,
            "max_hp": 80,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "Wound", "cost": 0, "type": "Status", "target": "None"},
                {"id": "Inflame", "cost": 1, "type": "Power", "target": "Self"},
                {"id": "Purity", "cost": 0, "type": "Skill", "target": "Self"},
            ],
        },
        "deck": ["Wound", "Inflame", "Purity"],
        "combat": {
            "monsters": [
                {"name": "Cultist", "hp": 40, "max_hp": 40, "intent": {"type": "buff"}, "powers": []},
            ],
            "selection_mode": "ExhaustSelect",
            "selection_min": 0,
            "selection_max": 3,
            "selection_manual_confirm": True,
            "selection_confirm_enabled": True,
            "selectable_cards": [False, True, False],
            "selected_cards": [True, False, False],
            "selection_selected_count": 1,
            "end_turn_enabled": False,
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 1, "request_id": "combat-hand-select-4"},
        request=request,
    )

    assert response["decision"] == {"type": "end_turn"}
    assert response["action"] == 50


def test_adapt_response_for_websocket_prefers_acrobatics_without_sly_when_draw_line_is_strong():
    """没有 Sly 时，若弃牌和抽牌能明显强化当前回合，也应主动打弃牌牌。"""
    payload = {
        "request_id": "combat-priority-29",
        "phase": "combat",
        "character": "Silent",
        "player": {
            "hp": 60,
            "max_hp": 70,
            "energy": 1,
            "block": 0,
            "hand": [
                {"id": "DefendSilent", "cost": 1, "type": "Skill", "target": "Self", "block": 5},
                {"id": "Acrobatics", "cost": 1, "type": "Skill", "target": "Self", "draw": 3},
                {"id": "Finisher", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6, "vars": {"CalculatedHits": 0}},
            ],
            "draw_pile": [
                {"id": "Neutralize", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 3},
                {"id": "StrikeSilent", "cost": 1, "type": "Attack", "target": "AnyEnemy", "damage": 6},
                {"id": "BeamCell", "cost": 0, "type": "Attack", "target": "AnyEnemy", "damage": 3, "powers": {"VulnerablePower": 1}},
            ],
        },
        "deck": ["DefendSilent", "Acrobatics", "Finisher", "Neutralize", "StrikeSilent", "BeamCell"],
        "combat": {
            "monsters": [
                {"name": "Target", "hp": 32, "max_hp": 32, "intent": {"type": "buff"}, "powers": []},
            ],
        },
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 0, "request_id": "combat-priority-29"},
        request=request,
    )

    assert response["decision"] == {"type": "play_card", "card_index": 1, "target_index": 0}
    assert response["action"] == 5


def test_adapt_response_for_websocket_advances_event_by_picking_first_enabled_option():
    """事件阶段若模型给出 skip，bridge 仍应优先点可用选项避免卡住。"""
    payload = {
        "request_id": "event-heuristic-1",
        "phase": "event",
        "character": "Silent",
        "player": {"hp": 68, "max_hp": 70},
        "event_options": [
            {"id": "leave", "enabled": True},
            {"id": "locked", "enabled": False},
            {"id": "take", "enabled": True},
        ],
        "_bridge_ui": {"event_enabled": [True, False, True]},
    }

    request = raw_state_to_act_message(payload)
    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": 73, "request_id": "event-heuristic-1"},
        request=request,
    )

    assert response["decision"] == {"type": "choose_option", "index": 0}


def test_bridge_event_decision_avoids_sword_of_stone_early():
    request = {
        "_bridge_state": {
            "phase": "event",
            "character": "Silent",
            "act": 1,
            "floor": 6,
            "player": {"name": "Silent", "hp": 60, "max_hp": 70},
            "deck": ["StrikeSilent"] * 5 + ["DefendSilent"] * 5 + ["Neutralize", "Survivor"],
            "event_options": [
                {"id": "grab_sword", "title": "Grab Sword", "text": "Take Sword of Stone"},
                {"id": "leave", "title": "Leave", "text": "Walk away"},
            ],
        },
        "_bridge_ui": {"event_enabled": [True, True]},
    }

    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": A_EVENT_START, "request_id": "event-sword-early"},
        request=request,
    )

    assert response["decision"] == {"type": "choose_option", "index": 1}
    assert response["action"] == A_EVENT_START + 1


def test_bridge_event_decision_takes_sword_of_stone_after_deck_forms():
    request = {
        "_bridge_state": {
            "phase": "event",
            "character": "Silent",
            "act": 2,
            "floor": 24,
            "player": {"name": "Silent", "hp": 58, "max_hp": 77},
            "deck": [
                "Neutralize",
                "Survivor",
                "BladeDance",
                "BladeDance",
                "Accuracy",
                "Footwork",
                "Backflip",
                "CloakAndDagger",
                "CloakAndDagger",
                "Adrenaline",
            ],
            "event_options": [
                {"id": "grab_sword", "title": "Grab Sword", "text": "Take Sword of Stone"},
                {"id": "leave", "title": "Leave", "text": "Walk away"},
            ],
        },
        "_bridge_ui": {"event_enabled": [True, True]},
    }

    response = adapt_response_for_websocket(
        {"ok": True, "type": "action", "action": A_EVENT_START + 1, "request_id": "event-sword-late"},
        request=request,
    )

    assert response["decision"] == {"type": "choose_option", "index": 0}
    assert response["action"] == A_EVENT_START
    assert response["action"] == A_EVENT_START


def test_process_websocket_message_accepts_string_potions_in_state(tmp_path):
    """实机状态里的药水槽可能只传药水 id 字符串，bridge 应能正常编码。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps(
            {
                "type": "state",
                "request_id": "potions-1",
                "character": "Ironclad",
                "phase": "map",
                "player": {
                    "hp": 70,
                    "max_hp": 80,
                    "potions": ["FirePotion"],
                },
                "deck": ["StrikeIronclad", "DefendIronclad"],
                "state": {
                    "map": {
                        "choices": [
                            {"room_type": "monster", "children": [], "enabled": True},
                        ],
                    }
                },
            }
        ),
    )

    assert response["ok"] is True
    assert response["type"] == "action"



def test_adapt_response_for_websocket_keeps_non_action_response():
    """非 action 响应不应被额外篡改。"""
    response = adapt_response_for_websocket({"ok": True, "type": "pong", "request_id": "p1"})
    assert response == {"ok": True, "type": "pong", "request_id": "p1"}



def test_build_pipeline_commands_include_requested_args():
    """总控脚本应正确拼接训练和评估命令。"""
    args = SimpleNamespace(
        timesteps=100,
        n_envs=2,
        lr=2e-4,
        seed=11,
        batch_size=64,
        n_steps=256,
        eval_freq=500,
        eval_episodes=4,
        post_eval_episodes=6,
        episodes=7,
        auto_resume=True,
        no_preset=True,
    )

    train_command = build_train_command(args)
    eval_command = build_eval_command(args)

    assert train_command[1].endswith("train_all.py")
    assert "--timesteps" in train_command and "100" in train_command
    assert "--n-envs" in train_command and "2" in train_command
    assert "--post-eval-episodes" in train_command and "6" in train_command
    assert "--auto-resume" in train_command
    assert "--no-preset" in train_command
    assert eval_command[1].endswith("evaluate_all.py")
    assert eval_command[-4:] == ["--episodes", "7", "--seed", "11"]


def test_modbootstrap_character_select_patch_uses_current_parameter_names():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "ModBootstrap.cs"
    ).read_text(encoding="utf-8")

    assert "BeforeSelectCharacter(NCharacterSelectButton charSelectButton, object? character)" in source
    assert "AfterSelectCharacter(NCharacterSelectButton charSelectButton, object? character)" in source


def test_gameintrospection_only_filters_upgraded_cards_for_upgrade_selection():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert 'var filterUpgradedCards = IsUpgradeCardSelection(screen);' in source
    assert '&& (!filterUpgradedCards || !holder.CardModel.IsUpgraded)' in source


def test_gameintrospection_keeps_plain_card_rewards_on_card_reward_phase():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert 'phase: "card_reward"' in source


def test_gameintrospection_uses_card_select_phase_hint_for_generic_upgrade_selection():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert 'return "card_select";' in source
    assert 'return "upgrade"; // 篝火升级' not in source


def test_gameintrospection_guards_treasure_proceed_until_room_is_visible_and_opened():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert "canvasRoot.IsVisibleInTree()" in source
    assert 'var hasChestBeenOpened = ReadBoolMember(root, "_hasChestBeenOpened") == true;' in source
    assert "if (hasChestBeenOpened && proceedButton?.Visible == true && proceedButton.IsEnabled && Click(proceedButton))" in source


def test_gameintrospection_requires_real_event_ui_before_event_actions():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert "private static bool IsEventInteractionScreen(object? screen)" in source
    assert 'if (!IsEventInteractionScreen(screen))' in source
    assert "event options UI fallback skipped outside event screen" in source
    assert "event decision ignored outside event UI" in source
    assert 'var screen = GetCurrentScreenSafe("event_proceed_automation");' in source


def test_gameintrospection_ignores_rest_state_key_once_map_screen_is_open():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert "if (screen is NMapScreen)" in source
    assert 'return "map_screen";' in source


def test_gameintrospection_allows_rest_proceed_recovery_from_map_screen_transition():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert "currentScreen is NMapScreen" in source
    assert "rest site auto-clicked proceed from map screen" in source
    assert "if (allowRewardAutomation && state.CurrentRoom is RestSiteRoom restRoomOnMap" in source


def test_gameintrospection_matches_map_children_by_coord_not_reference():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert "currentPoint.Children.Select(child => child.coord).ToHashSet()" in source
    assert "reachableCoords is null || reachableCoords.Contains(item.point.coord)" in source
    assert "currentPoint.Children.Contains(item.point)" not in source


def test_gameintrospection_describes_map_points_by_coord_not_reference():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert "if (points[col].point.coord != point.coord)" in source
    assert "ReferenceEquals(points[col].point, point)" not in source


def test_gameintrospection_logs_map_pending_diagnostics():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert '"map_ui_pending_details"' in source
    assert 'screen_travel_enabled=' in source
    assert 'current_children=' in source
    assert 'visible_points=' in source
    assert 'travelable=' in source
    assert 'state=' in source


def test_gameintrospection_reads_monster_intent_from_next_move_intents():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert 'ReadMemberObject(creature, "Monster")' in source
    assert 'ReadMemberObject(monsterModel, "NextMove")' in source
    assert 'ReadMemberObject(nextMove, "Intents")' in source


def test_gameintrospection_can_fall_back_to_visible_intent_ui():
    source = (
        Path(__file__).resolve().parent.parent
        / "game_mod"
        / "Sts2RlBridge"
        / "GameIntrospection.cs"
    ).read_text(encoding="utf-8")

    assert "NCombatRoom.Instance?.GetCreatureNode(creature)" in source
    assert 'ReadMemberObject(creatureNode, "IntentContainer")' in source
    assert 'ReadMemberObject(intentNode, "_valueLabel")' in source
