"""环境集成测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sts_env.env import StsEnv


def test_env_reset():
    """测试环境 reset。"""
    env = StsEnv(character="Ironclad", seed=42)
    obs, info = env.reset()
    assert obs.shape[0] > 0
    assert info["phase"] == "MAP"
    assert info["hp"] == 80
    assert info["act"] == 1
    print("PASS: test_env_reset")


def test_env_action_mask():
    """测试 action mask 在 MAP 阶段。"""
    env = StsEnv(character="Ironclad", seed=42)
    env.reset()
    mask = env.action_mask()
    assert mask.any(), "至少有一个合法动作"
    # MAP 阶段应该有地图动作可用
    assert mask[66:70].any(), "MAP 阶段应有路径选择动作"
    print("PASS: test_env_action_mask")


def test_env_step_map():
    """测试走一步地图。"""
    env = StsEnv(character="Ironclad", seed=42)
    env.reset()
    mask = env.action_mask()
    valid = np.where(mask)[0]
    action = valid[0]
    obs, reward, terminated, truncated, info = env.step(int(action))
    assert not terminated
    assert obs.shape[0] > 0
    print(f"PASS: test_env_step_map (phase={info['phase']}, floor={info['floor']})")


def test_env_random_rollout():
    """测试随机 agent 跑完整局（不崩溃）。"""
    env = StsEnv(character="Ironclad", seed=123)
    obs, info = env.reset()
    done = False
    steps = 0
    max_steps = 5000

    while not done and steps < max_steps:
        mask = env.action_mask()
        valid = np.where(mask)[0]
        action = np.random.choice(valid)
        obs, reward, terminated, truncated, info = env.step(int(action))
        done = terminated or truncated
        steps += 1

    print(f"PASS: test_env_random_rollout (steps={steps}, floor={info['floor']}, "
          f"hp={info['hp']}, phase={info['phase']}, won={env.gs.won})")


def test_env_all_characters():
    """测试所有角色都能初始化和运行。"""
    for char in ["Ironclad", "Silent", "Defect", "Necrobinder", "Regent"]:
        env = StsEnv(character=char, seed=42)
        obs, info = env.reset()
        assert info["hp"] > 0, f"{char} HP 应该 > 0"
        # 走几步
        for _ in range(20):
            mask = env.action_mask()
            valid = np.where(mask)[0]
            if len(valid) == 0:
                break
            action = np.random.choice(valid)
            obs, reward, terminated, truncated, info = env.step(int(action))
            if terminated or truncated:
                break
        print(f"PASS: test_env_{char} (floor={info['floor']}, hp={info['hp']})")


def test_env_combat_flow():
    """测试进入战斗后的完整流程。"""
    env = StsEnv(character="Ironclad", seed=42)
    env.reset()

    # 走地图直到进入战斗
    for _ in range(10):
        mask = env.action_mask()
        valid = np.where(mask)[0]
        action = np.random.choice(valid)
        obs, reward, terminated, truncated, info = env.step(int(action))
        if info["phase"] == "COMBAT":
            break
        if terminated or truncated:
            break

    if info["phase"] == "COMBAT":
        # 在战斗中随机操作直到结束
        for _ in range(200):
            mask = env.action_mask()
            valid = np.where(mask)[0]
            action = np.random.choice(valid)
            obs, reward, terminated, truncated, info = env.step(int(action))
            if info["phase"] != "COMBAT" or terminated or truncated:
                break
        print(f"PASS: test_env_combat_flow (result_phase={info['phase']}, hp={info['hp']})")
    else:
        print(f"PASS: test_env_combat_flow (no combat entered, phase={info['phase']})")


if __name__ == "__main__":
    test_env_reset()
    test_env_action_mask()
    test_env_step_map()
    test_env_random_rollout()
    test_env_all_characters()
    test_env_combat_flow()
    print("\n全部环境测试通过!")
