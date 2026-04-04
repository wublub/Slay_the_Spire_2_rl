# STS-Agent 协作说明

本文件面向后续继续维护这个仓库的 Claude / 自动化代理，用来快速建立对项目现状的正确理解，并避免文档与代码脱节。

## 1. 项目定位

这是一个《杀戮尖塔 2》强化学习项目，目标是：

- 用 `sts_env/` 中的模拟环境训练策略
- 覆盖完整跑图，而不是只做单场战斗
- 支持 5 个角色
- 通过 bridge 层把真实游戏状态转成模型输入
- 返回离散动作 `action`，同时返回结构化 `decision`

当前仓库已经包含：

- 训练入口
- 批量训练/评估脚本
- 数据提取脚本
- 本地 JSONL bridge
- WebSocket bridge
- 针对 bridge/schema 的回归测试

不要把它描述成“已经稳定通关”的成品系统，除非当前仓库里有新的评估结果明确支持这个说法。

## 2. 当前代码结构

### 训练与推理

- `agent/train.py`
  - 单角色训练主入口
  - 定义 5 角色：`Ironclad`、`Silent`、`Defect`、`Necrobinder`、`Regent`
  - 使用 `MaskablePPO`
  - 保存：
    - 最终模型 `models/<Character>/sts2_<Character>_final.zip`
    - 训练摘要 `models/<Character>/training_summary.json`
    - best/checkpoints
- `agent/evaluate.py`
  - 单角色评估
  - 支持 `--random`
  - 支持输出评估 JSON
- `agent/runtime.py`
  - 负责模型加载、输入维度检查、推理
  - `BridgeRequest` 包含：
    - `character`
    - `observation`
    - `action_mask`
    - `deterministic`
    - `request_id`
- `agent/bridge_server.py`
  - 本地 JSONL 协议服务
  - 支持 `ping` / `describe` / `load` / `act` / `shutdown`

### WebSocket bridge

- `bridge/bridge_client.py`
  - 核心职责：
    - 接受游戏侧 WebSocket 文本消息
    - 兼容正式 `type: "state"` schema 和 legacy/raw payload
    - 重建 `GameState`
    - 编码成 `observation`
    - 生成 `action_mask`
    - 返回结构化 `decision`
  - 当前支持的主要 phase：
    - `map`
    - `combat`
    - `card_reward`
    - `event`
    - `rest`
    - `shop`
    - `treasure`
    - `boss_relic`
  - 当前还接入了本地 UI 控制状态：
    - 读取 `bridge/control_state.py` 持久化的控制状态
    - `paused = true` 时返回 `type: "idle"`
    - UI 目标角色与实机上报角色不一致时返回 `type: "restart_required"`
    - 同角色只切模型时不要求重开，而是为当前请求注入该角色的覆盖 `model_path`
    - `ping` / `describe` / `load` / `shutdown` 不应被 UI 控制语义拦截
  - 注意：
    - `deterministic` 现在应从 `state/raw_state` 一路透传到 `act`
    - UI `enabled` 字段只用于**收紧**动作掩码，不能放宽环境原本判定的不合法动作
- `bridge/control_state.py`
  - 本地 UI 与 WebSocket bridge 共享的控制状态存储
  - 持久化字段包括：
    - `paused`
    - `desired_character`
    - `model_overrides`
    - `last_request_id`
    - `last_response_type`
    - `last_error`
  - 采用 JSON 文件 + 原子替换写入，避免 UI/bridge 同步时写坏状态

### 环境与模拟逻辑

- `sts_env/env.py`
  - Gymnasium 环境
  - 当前 94 个离散动作
  - 各阶段动作掩码规则以这里为准
- `sts_env/encoding.py`
  - `GameState -> observation`
- `sts_env/game_state.py`
  - 游戏状态定义
- `sts_env/combat.py`
  - 战斗对象、卡牌、玩家、怪物、意图
- `sts_env/card_effects.py`
- `sts_env/powers.py`
- `sts_env/monster_ai.py`
- `sts_env/events.py`
- `sts_env/rewards.py`
- `sts_env/archetypes.py`
- `sts_env/map_gen.py`

### 数据提取

- `scripts/extract_data.py`
  - 从反编译目录提取并覆盖：
    - `data/cards.json`
    - `data/monsters.json`
    - `data/encounters.json`
    - `data/events.json`
    - `data/characters.json`
    - `data/relics.json`
    - `data/potions.json`
  - 默认读取同级 `decompiled/`
  - 也支持环境变量 `STS2_DECOMPILED`

### 批处理脚本

- `scripts/train_all.py`
- `scripts/evaluate_all.py`
- `scripts/run_pipeline.py`
- `scripts/train_ironclad.py`
- `scripts/train_silent.py`
- `scripts/train_defect.py`
- `scripts/train_necrobinder.py`
- `scripts/train_regent.py`
- `scripts/bridge_server.py`
- `scripts/bridge_ui.py`
  - 本地 Tkinter 控制面板
  - 可切换目标角色
  - 可为 5 个角色分别设置模型覆盖路径
  - 可启动 / 停止 WebSocket bridge
  - 可暂停 / 继续自动游玩
  - 通过子进程启动 `bridge/bridge_client.py`，不要把 asyncio 服务直接塞进 Tk 主线程
- `scripts/audit_bridge_training_coverage.py`
  - 对照 `decompiled/` 与 `sts_env/card_effects.py`
  - 输出真实选牌语义与训练覆盖缺口

## 3. 文档维护约束

维护文档时请遵守：

- 不要写未经验证的胜率、平均楼层或“稳定通关”表述
- 不要假设仓库里存在未提交目录，比如：
  - `bridge_mod/`
  - `docs/`
  - 其他未实际存在的 sender/mod 工程
- 如果代码和 README/CLAUDE.md 不一致，应优先以代码为准，然后更新文档
- 如果修改了 bridge schema、测试范围或运行命令，记得同时更新：
  - `README.md`
  - `CLAUDE.md`
  - 对应测试

## 4. 游戏更新后的维护流程

当《杀戮尖塔 2》打 patch 后，按这个顺序处理最稳妥：

1. 重新反编译 `sts2.dll`
2. 如有需要，重新提取 `.pck`
3. `git diff decompiled/` 看变化范围
4. 运行 `python scripts/extract_data.py`
5. 运行 `python scripts/audit_bridge_training_coverage.py --json-out data/bridge_training_gap_report.json`
6. 如果只是数值变化，先跑测试和评估
7. 如果有机制变化，再修改 `sts_env/` 和 `bridge/`
8. 跑测试
9. 必要时重新训练

优先关注这些反编译目录：

- `MegaCrit.Sts2.Core.Models.Cards/`
- `MegaCrit.Sts2.Core.Models.Powers/`
- `MegaCrit.Sts2.Core.Models.Monsters/`
- `MegaCrit.Sts2.Core.Models.Encounters/`
- `MegaCrit.Sts2.Core.Models.Events/`
- `MegaCrit.Sts2.Core.Combat/`
- `MegaCrit.Sts2.Core.Hooks/`
- `MegaCrit.Sts2.GameInfo.Objects/`

如果你维护仓库外的游戏侧 sender / Harmony patch，还要额外检查：

- patch 目标类/方法是否仍存在
- 房间处理器 / UI 按钮对象是否改名
- 游戏侧发给 Python 的字段是否仍匹配当前 schema

### 4.1 维护检查清单

每次游戏更新后，优先按下面顺序执行：

- [ ] 保留旧版 `decompiled/` 结果，不要覆盖后才开始分析
- [ ] 重新反编译 `sts2.dll`
- [ ] 如果怀疑 UI / 资源也变了，再提取 `.pck`
- [ ] 对比重点目录：
  - `MegaCrit.Sts2.Core.Models.Cards/`
  - `MegaCrit.Sts2.Core.Models.Powers/`
  - `MegaCrit.Sts2.Core.Models.Monsters/`
  - `MegaCrit.Sts2.Core.Models.Encounters/`
  - `MegaCrit.Sts2.Core.Models.Events/`
  - `MegaCrit.Sts2.Core.Combat/`
  - `MegaCrit.Sts2.Core.Hooks/`
  - `MegaCrit.Sts2.GameInfo.Objects/`
- [ ] 运行 `python scripts/extract_data.py`
- [ ] 运行 `python scripts/audit_bridge_training_coverage.py --json-out data/bridge_training_gap_report.json`
- [ ] 先判断是数值变化还是机制变化
- [ ] 数值变化：先测试、先评估，再决定是否重训
- [ ] 机制变化：优先修改 `sts_env/` 与 `bridge/`
- [ ] 如果 sender / UI 字段变了，同时检查仓库外 mod 是否还匹配正式 schema
- [ ] 至少回归 `tests/test_training_entry.py` 和 `tests/test_env.py`
- [ ] 如果影响战斗/卡牌逻辑，再补跑全量测试
- [ ] 如果 bridge schema、测试范围或命令变了，立刻同步更新 `README.md`、`CLAUDE.md` 和测试

## 5. 正式 WebSocket schema

当前推荐游戏侧发送正式 schema，而不是继续扩散 legacy flat payload。

推荐格式：

```json
{
  "type": "state",
  "schema_version": 1,
  "request_id": "example-1",
  "character": "Ironclad",
  "phase": "card_reward",
  "run": {
    "act": 1,
    "floor": 12,
    "won": false
  },
  "player": {
    "hp": 70,
    "max_hp": 80,
    "gold": 99,
    "relics": ["BurningBlood"]
  },
  "deck": ["StrikeIronclad", "DefendIronclad", "Bash"],
  "state": {
    "card_reward": {
      "cards": [
        {"id": "Clash", "enabled": true},
        {"id": "PommelStrike", "enabled": true}
      ],
      "can_skip": true
    }
  },
  "deterministic": false
}
```

说明：

- `type` 固定为 `state`
- `phase` 决定读取 `state.<phase>`
- `request_id` 应原样回传
- `deterministic` 应传递到运行时预测
- UI `enabled` 只用于收紧 mask

### 5.1 游戏侧 sender 约束

如果后续补写仓库外的 C# sender / Harmony patch，请遵守：

- phase 名统一使用：
  - `map`
  - `combat`
  - `card_reward`
  - `event`
  - `rest`
  - `shop`
  - `treasure`
  - `boss_relic`
- `state` 中只发送当前 `phase` 对应的子结构
- `request_id` 每条请求唯一，并要求响应原样带回
- `deterministic` 如有发送，必须透传到推理请求
- `enabled` 只允许用于收紧 UI 合法动作，不能把环境原本不合法的动作“放开”
- `rest.options[*].id` 当前只识别：
  - `rest`
  - `upgrade`
  - `dig`
  - `cook`
  - `lift`
- `boss_relic.choices[*]` 如果发送对象，必须带：
  - `id`
  - 或 `relic_id`
- `card_reward.can_skip = false` 会禁用 `skip`
- `treasure.can_proceed = false` 会禁用继续动作
- `combat` 若处于手牌二段选择，优先发送：
  - `selection_mode`
  - `selectable_cards`
  - `selected_cards`
  - `selection_confirm_enabled`
  - `selection_min`
  - `selection_max`
  - `selection_manual_confirm`
  - `selection_selected_count`
- `shop` 当前 UI 收紧只覆盖：
  - 卡牌
  - 遗物
  - 删牌
  - 离开
- sender 侧优先发送内部稳定 id，不要发送本地化显示文本作为唯一键
- 如果游戏更新导致字段来源变化，优先修改 sender 字段映射，不要先扩大 Python 兼容层

## 6. 当前测试重点

当前与 bridge/schema 最相关的测试在：

- `tests/test_training_entry.py`
- `tests/test_env.py`

其中 `tests/test_training_entry.py` 当前还覆盖了这组 UI 控制语义：

- control state 中的每角色模型覆盖会注入到 `load/act/state` 归一化结果
- `paused = true` 时返回 `type: "idle"`
- UI 目标角色与当前实机角色不一致时返回 `type: "restart_required"`
- 同角色切换模型时继续当前局，并正常返回 `type: "action"`
- 非 `action` 响应不会被错误附加 `decision`

常用测试命令：

```bash
pytest tests/test_training_entry.py -v
pytest tests/test_env.py -v
pytest tests/test_cards.py -v
pytest tests/test_combat.py -v
pytest tests -v
```

如果你改了这些内容，至少要回归：

- `bridge/bridge_client.py`
- `agent/bridge_server.py`
- `agent/runtime.py`
- `sts_env/env.py`
- `sts_env/encoding.py`

## 7. 最近已经确认的 bridge 行为

以下行为已经由测试覆盖，修改时不要破坏：

- legacy raw payload 支持直接归一化到 `act`
- 正式 `type: "state"` 支持归一化到 `act`
- `request_id` 保留
- `model_path` 保留
- `deterministic` 保留
- 缺少 `character` 时可回退到 `default_character`
- `event` / `rest` / `shop` / `boss_relic` / `treasure` 支持 UI enabled 收紧
- `card_reward.can_skip = false` 会禁用 `skip`
- WebSocket 输出同时包含：
  - `action`
  - `decision`
- control state 的 `paused = true` 会直接返回：
  - `type: "idle"`
- control state 的 `desired_character` 与当前请求角色不一致时会返回：
  - `type: "restart_required"`
  - `target_character`
  - `current_character`
- 同角色只切换模型覆盖时：
  - 不会返回 `restart_required`
  - 会继续当前局
  - 下一条请求直接使用新的 `model_path`
- `ping` / `describe` / `load` / `shutdown` 不应被控制层错误拦截

## 8. 本地 UI / 实机联动约束

维护这套 UI 联动能力时，请始终遵守：

- UI 只是本地控制面板，不负责替代 bridge 推理链
- 角色切换语义是：
  - 如果 UI 目标角色和实机当前角色不同，bridge 返回 `restart_required`
  - 是否真正“退回菜单并新开一局”由仓库外游戏侧 sender/executor 负责执行
- 模型切换语义是：
  - 同角色切模型时不要要求重开
  - 直接依赖 `(character, model_path)` 运行时缓存切换模型
- 暂停语义是：
  - `paused = true` 时返回 `idle`
  - 游戏侧收到后不执行动作，等待继续
- `scripts/bridge_ui.py` 推荐继续保持 Tkinter + `subprocess.Popen(...)` 的结构
  - 不要为了这类轻量控制面板额外引入 Web UI 框架
  - 不要把 asyncio bridge 直接并进 Tk 主线程
- 当前仓库没有内置 C# `bridge_mod/` 工程；如果要做真实游戏接入，仓库外 sender 必须识别：
  - `type == "action"`
  - `type == "idle"`
  - `type == "restart_required"`

## 8.1 当前训练与 bridge 的已知错位

当前 bridge 已经能从游戏侧拿到并利用这些战斗 UI 子状态收紧动作掩码：

- `combat_playable_cards`
- `combat_selectable_cards`
- `combat_selection_mode`
- `combat_selection_confirm_enabled`

但训练侧仍有两个关键缺口：

- `sts_env/encoding.py` 现在已经把这些 UI 子状态追加编码进 observation
- `sts_env/card_effects.py` 对大量真实二段选牌卡牌仍缺显式 handler，或只有简化逻辑

这意味着 bridge 可能已经知道“当前是在弃牌 / 消耗 / 升级手牌”，但旧模型并没有在训练里真正学会这些语义。

因此：

- 不要误以为 bridge 修通以后，当前 `best_model.zip` 就一定会在这些界面里做出高质量决策
- 新 observation 虽然已追加 UI 子状态，但运行时只对旧模型做前缀兼容，不代表旧模型真的学会了这些语义
- 想真正利用新增 UI 特征，仍然必须重训

## 9. 修改时的偏好

在这个仓库里继续工作时：

- 优先修复真实不一致，不要只改文档表面描述
- 优先补测试，尤其是 schema 兼容与边界情况
- 不要为了“向后兼容”保留明显已经无用的死代码
- 不要新建 README/docs 文件，除非确实需要；优先更新现有 `README.md` 和 `CLAUDE.md`
- 当用户提到“游戏更新后怎么办”，要优先从：
  - 反编译
  - 数据提取
  - diff
  - 测试
  - 重评估/重训练
  这条链路来回答

## 10. 已知现实约束

- 当前工作目录不是 git 仓库根，很多自动 git 流程不可用
- 内存目录可能不存在，不要依赖它作为唯一信息来源
- 仓库当前没有内置 C# `bridge_mod/` 工程
- 如果用户要你“重写文档”，优先根据**实际存在的代码与脚本**重写，而不是沿用旧说明
