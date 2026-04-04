"""训练入口与桥接/评估脚本测试。"""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import gymnasium as gym
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bridge_server import BridgeServer, RuntimeRegistry
from agent.evaluate import save_evaluation_summary
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
    callback_trigger_freq,
    resolve_resume_source,
    resolve_training_artifact_paths,
    save_training_summary,
)
from bridge.control_state import BridgeControlStateStore
from bridge.bridge_client import (
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
    list_character_model_paths,
    load_character_training_summary as load_bridge_ui_training_summary,
)
from scripts.run_pipeline import build_eval_command, build_train_command
from scripts.training_ui import (
    apply_preferred_model_to_bridge,
    build_evaluation_command,
    build_training_command,
    determine_followup_action,
    resolve_preferred_training_model,
)
from scripts.train_all import build_extra_args, load_character_summary, save_combined_summary
from sts_env.archetypes import (
    ALL_ARCHETYPES,
    CHARACTER_STRATEGIES,
    REMOVE_ALWAYS,
    SUPPORT_DEPENDENCIES,
    card_pick_score,
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
from sts_env.env import A_REST, A_SHOP_CARD_START, A_SHOP_REMOVE, A_UPGRADE, StsEnv, TOTAL_ACTIONS
from sts_env.game_state import GamePhase, MapNode, RoomType
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
    assert command[-2:] == ["--output", str(save_dir / "ui_eval.json")]



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



def test_build_leaderboard_sorts_by_win_rate_floor_hp():
    """排行榜应按胜率、楼层、剩余 HP 排序。"""
    entries = [
        {"character": "B", "win_rate": 0.5, "avg_floor": 20.0, "avg_hp": 10.0},
        {"character": "A", "win_rate": 0.6, "avg_floor": 18.0, "avg_hp": 5.0},
        {"character": "C", "win_rate": 0.5, "avg_floor": 22.0, "avg_hp": 8.0},
    ]

    leaderboard = build_leaderboard(entries)

    assert [entry["character"] for entry in leaderboard] == ["A", "C", "B"]



def test_save_leaderboard_writes_sorted_payload(tmp_path):
    """排行榜 JSON 应写入并保持排序。"""
    entries = [
        {"character": "Silent", "win_rate": 0.4, "avg_floor": 18.0, "avg_hp": 7.0},
        {"character": "Ironclad", "win_rate": 0.5, "avg_floor": 16.0, "avg_hp": 9.0},
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


def test_env_rest_step_prefers_upgrade_over_rest_when_healthy_and_engine_exists():
    """血量健康且有高优先级升级目标时，火堆升级奖励应高于休息。"""

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

    rest_env = build_env()
    _obs, rest_reward, _terminated, _truncated, _info = rest_env.step(A_REST)

    assert upgrade_reward > rest_reward


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



def test_process_websocket_message_reports_bad_raw_state_payload(tmp_path):
    """原始状态缺少关键字段时应返回明确错误。"""
    registry = RuntimeRegistry(models_dir=tmp_path, runtime_loader=make_stub_loader())
    server = BridgeServer(registry=registry)

    response = process_websocket_message(
        server,
        json.dumps({"type": "raw_state", "request_id": "raw-bad-1", "character": "Ironclad"}),
    )

    assert response["ok"] is False
    assert response["code"] == "bad_message"
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
    assert decode_action(79) == {"type": "buy_card", "index": 0}
    assert decode_action(82) == {"type": "buy_relic", "index": 0}
    assert decode_action(85) == {"type": "remove_card"}
    assert decode_action(86) == {"type": "leave_shop"}
    assert decode_action(88) == {"type": "choose_option", "index": 1}
    assert decode_action(92) == {"type": "choose_boss_relic", "index": 1}

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
    assert mask[8] is False
    assert mask[9] is False
    assert mask[50] is True


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

    assert mask[87] is True
    assert mask[88] is False
    assert mask[89] is True
    assert mask[90] is False



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
    assert normalized["action_mask"][87] is True
    assert normalized["action_mask"][88] is False
    assert normalized["action_mask"][89] is True


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

    runtime_dim = COMBAT_RUNTIME_GLOBAL_DIM + 10 * COMBAT_RUNTIME_CARD_DIM
    tail = encode_observation(gs)[-runtime_dim:]
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
    """Headbutt 应把更关键的弃牌放回牌堆顶。"""
    player = Player("Ironclad", 80, 80)
    player.energy = 1
    player.hand = [make_card("Headbutt")]
    player.discard_pile = [make_card("PommelStrike"), make_card("StrikeIronclad")]
    combat = Combat(player, [Monster("Louse", 12, 12)])

    assert combat.play_card(0, 0) is True
    assert player.draw_pile[-1].card_id == "PommelStrike"


def test_combat_hologram_returns_best_discard_card_to_hand():
    """Hologram 应优先捞回更高价值的弃牌。"""
    player = Player("Defect", 70, 70)
    player.energy = 1
    player.hand = [make_card("Hologram")]
    player.discard_pile = [make_card("StrikeDefect"), make_card("Defragment")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert [card.card_id for card in player.hand] == ["Defragment"]
    assert [card.card_id for card in player.exhaust_pile] == ["Hologram"]


def test_combat_charge_transforms_low_value_draw_cards_into_dive_bombs():
    """Charge 应优先改造抽牌堆里更该被替换的基础牌。"""
    player = Player("Regent", 70, 70)
    player.energy = 1
    player.hand = [make_card("Charge")]
    player.draw_pile = [make_card("StrikeRegent"), make_card("BigBang"), make_card("DefendRegent")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert sorted(card.card_id for card in player.draw_pile) == ["BigBang", "MinionDiveBomb", "MinionDiveBomb"]


def test_combat_discovery_adds_generated_card_free_this_turn_only():
    """Discovery 生成的牌应只在本回合免费，并在编码里体现当前费用。"""
    import random

    random.seed(123)
    player = Player("Silent", 70, 70, draw_per_turn=0)
    player.energy = 1
    player.hand = [make_card("Discovery")]
    combat = Combat(player, [Monster("Cultist", 48, 48)])

    assert combat.play_card(0, 0) is True
    assert len(player.hand) == 1

    generated = player.hand[0]
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

    assert mask[79] is True
    assert mask[80] is False
    assert mask[82] is False
    assert mask[83] is True
    assert mask[85] is False
    assert mask[86] is True



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

    assert mask[91] is True
    assert mask[92] is False
    assert mask[93] is True



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
    assert response["decision"] == {"type": "skip"}



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
    assert response["decision"] == {"type": "skip"}



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
        "protocol_version": 1,
        "request_id": "paused-1",
        "reason": "paused",
    }
    assert registry.cache_size() == 0

    state = control_state.load()
    assert state.last_request_id == "paused-1"
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
        "protocol_version": 1,
        "request_id": "restart-1",
        "target_character": "Silent",
        "current_character": "Ironclad",
    }
    assert registry.cache_size() == 0


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
    assert response["decision"] == {"type": "skip"}
    assert response["model_path"] == str(tmp_path / "silent_new.zip")
    assert captured["runtime"].model_path == Path(tmp_path / "silent_new.zip")

    state = control_state.load()
    assert state.last_request_id == "same-character-1"
    assert state.last_response_type == "action"
    assert state.last_error is None


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
