# STS-Agent

《杀戮尖塔 2》强化学习项目。这个仓库的目标不是只做单场战斗，而是训练能够完成完整跑图流程的 agent，并通过 bridge 接到真实游戏。

当前仓库包含：

- `sts_env/` 模拟环境与奖励设计
- `agent/` 训练、评估、运行时推理
- `bridge/` WebSocket bridge
- `game_mod/` 游戏侧 DLL 模组
- `scripts/` 数据提取、批量训练、评估、桥接 UI
- `tests/` bridge/schema/env 回归测试

## 1. 目录概览

```text
Slay_the_Spire_2_rl-main/
├── agent/                  # 训练、评估、运行时
├── bridge/                 # WebSocket bridge
├── data/                   # 从反编译结果提取出的结构化数据
├── decompiled/             # 反编译 sts2.dll 后的源码
├── game_mod/               # 游戏侧桥接 DLL
├── models/                 # 训练产物
├── scripts/                # 常用脚本
├── sts_env/                # 模拟环境
├── tests/                  # 测试
├── bridge_control_state.json
├── CLAUDE.md
└── README.md
```

核心文件：

- `agent/train.py`
- `agent/evaluate.py`
- `agent/runtime.py`
- `agent/bridge_server.py`
- `bridge/bridge_client.py`
- `sts_env/env.py`
- `sts_env/encoding.py`
- `sts_env/card_effects.py`
- `sts_env/rewards.py`
- `game_mod/Sts2RlBridge/GameIntrospection.cs`

## 2. 常用命令

### 2.1 提取反编译数据

如果你的反编译目录在仓库内的 `decompiled/`，直接运行：

```powershell
python scripts\extract_data.py
```

如果在其他目录，先设置：

```powershell
$env:STS2_DECOMPILED = "D:\path\to\decompiled"
python scripts\extract_data.py
```

### 2.2 训练

启动本地训练控制 UI：

```powershell
python scripts\training_ui.py
```

支持：

- 单角色训练
- 自动续训（优先最近 checkpoint，其次 best/final）
- 手动指定已有模型继续训练
- 调整 timesteps、`n_envs`、`lr`、`batch_size`、`n_steps`、评估频率等参数
- 一键评估当前角色 preferred model
- 一键把当前角色 preferred model 写入 `bridge_control_state.json`
- 可选工作流：训练完成后自动评估，评估完成后自动推送到 bridge

单角色训练：

```powershell
python scripts\train_ironclad.py
python scripts\train_silent.py
python scripts\train_defect.py
python scripts\train_necrobinder.py
python scripts\train_regent.py
```

全角色批量训练：

```powershell
python scripts\train_all.py
```

命令行自动续训：

```powershell
python agent\train.py --character Ironclad --auto-resume
python scripts\train_all.py --auto-resume
```

### 2.3 评估

```powershell
python scripts\evaluate_all.py --episodes 100
```

### 2.4 启动 bridge

启动本地控制 UI：

```powershell
python scripts\bridge_ui.py
```

当前 `bridge_ui.py` 支持：

- 启停 bridge
- 切换目标角色
- 暂停 / 恢复自动游玩
- 浏览并应用任意模型 zip
- 扫描当前角色已有的 `best` / `final` / `checkpoint`
- 读取对应角色的 `training_summary.json`
- 一键把扫描到的模型切成当前 bridge 生效模型

直接启动 WebSocket bridge：

```powershell
python bridge\bridge_client.py --host 127.0.0.1 --port 8765 --character Ironclad --model <model.zip>
```

### 2.5 构建游戏模组

```powershell
.\game_mod\deploy_mod.ps1
```

详细说明见 [game_mod/README.md](/D:/Slay%20the%20Spire%202%20v0.101.0/Slay_the_Spire_2_rl-main/game_mod/README.md)。

## 3. 当前 bridge / runtime 行为

`bridge/bridge_client.py` 负责：

- 接受游戏侧 WebSocket 文本消息
- 兼容正式 `type: "state"` schema 和 legacy/raw payload
- 重建 `GameState`
- 编码为 `observation`
- 生成 `action_mask`
- 返回离散 `action` 和结构化 `decision`

当前主要 phase：

- `map`
- `combat`
- `card_reward`
- `event`
- `rest`
- `shop`
- `treasure`
- `boss_relic`

本地控制状态保存在：

- `bridge_control_state.json`

它包含：

- `paused`
- `desired_character`
- `model_overrides`
- `last_request_id`
- `last_response_type`
- `last_error`

当前 live bridge 会优先读取 `bridge_control_state.json` 里的角色模型覆盖。你现在实机游玩时，Ironclad 对应的是：

- `models\Ironclad\best\best_model.zip`

也就是说，当前不是固定用最终导出的 `final` 模型，而是优先用 `best` 目录下的最佳检查点。

## 4. 正式 WebSocket schema

推荐游戏侧发送正式 schema，而不是继续扩散 legacy flat payload。

示例：

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

约束：

- `phase` 决定读取 `state.<phase>`
- `request_id` 应原样回传
- `deterministic` 必须一路透传到运行时预测
- UI `enabled` 只允许用于收紧 mask，不能把环境原本不合法的动作放开
- `rest.options[*].id` 当前只识别：
  - `rest`
  - `upgrade`
  - `dig`
  - `cook`
  - `lift`
- `boss_relic.choices[*]` 如果发送对象，必须带 `id` 或 `relic_id`
- `card_reward.can_skip = false` 会禁用 `skip`
- `treasure.can_proceed = false` 会禁用继续动作
- `combat` 若处于手牌二段选择，建议额外发送：
  - `selection_mode`
  - `selectable_cards`
  - `selected_cards`
  - `selection_confirm_enabled`
  - `selection_min`
  - `selection_max`
  - `selection_manual_confirm`
  - `selection_selected_count`

## 5. 测试

最相关的回归测试：

- `tests/test_training_entry.py`
- `tests/test_env.py`

常用命令：

```powershell
pytest tests\test_training_entry.py -v
pytest tests\test_env.py -v
pytest tests\test_cards.py -v
pytest tests\test_combat.py -v
pytest tests -v
```

bridge/schema 改动后，至少回归：

- `bridge/bridge_client.py`
- `agent/bridge_server.py`
- `agent/runtime.py`
- `sts_env/env.py`
- `sts_env/encoding.py`

## 6. 游戏更新后的维护流程

当《杀戮尖塔 2》打 patch 后，建议按这个顺序处理：

1. 重新反编译 `sts2.dll`
2. 如有需要，重新提取 `.pck`
3. 对 `decompiled/` 做 diff
4. 运行 `python scripts/extract_data.py`
5. 运行 bridge / training 覆盖审计
6. 先判断是数值变化还是机制变化
7. 跑测试
8. 先评估旧模型
9. 如果确实语义变了，再修改代码并重训

### 6.1 重新反编译 `sts2.dll`

常见路径示例：

```text
C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2\data_sts2_windows_x86_64\sts2.dll
```

示例命令：

```bash
ilspycmd -p -o decompiled/ <path-to-sts2.dll>
```

### 6.2 如有需要，重新提取 `.pck`

```bash
gdre_tools --headless --recover=<path-to-game.pck>
```

主要用于美术、场景、UI 或非 dll 资源排查。

### 6.3 先对 `decompiled/` 做 diff

```bash
git diff decompiled/
```

优先关注这些目录：

- `MegaCrit.Sts2.Core.Models.Cards/`
- `MegaCrit.Sts2.Core.Models.Powers/`
- `MegaCrit.Sts2.Core.Models.Monsters/`
- `MegaCrit.Sts2.Core.Models.Encounters/`
- `MegaCrit.Sts2.Core.Models.Events/`
- `MegaCrit.Sts2.Core.Combat/`
- `MegaCrit.Sts2.Core.Hooks/`
- `MegaCrit.Sts2.GameInfo.Objects/`

### 6.4 每次版本更新后先跑覆盖审计

新增了本地脚本：

```powershell
python scripts\audit_bridge_training_coverage.py --json-out data\bridge_training_gap_report.json
```

它会自动：

- 扫描 `decompiled/MegaCrit.Sts2.Core.Models.Cards/` 里的真实选牌入口
- 对照 `sts_env/card_effects.py` 是否存在显式 handler
- 标出哪些是缺 handler，哪些是有 handler 但仍明显简化

这一步的目标不是自动修一切，而是把“哪些真实语义训练没覆盖”变成固定清单。

### 6.5 优先按 room / screen 类型对照，不要按症状补洞

游戏更新后，bridge 最容易退化的地方不是 Python schema，而是真实游戏 UI 流程变了。这时优先把解包后的 `AutoSlay` 当成“当前版本真值表”：

- 注册入口：`decompiled/MegaCrit.Sts2.Core.AutoSlay/AutoSlayer.cs`
- 房间处理器目录：`decompiled/MegaCrit.Sts2.Core.AutoSlay.Handlers.Rooms/`
- 屏幕处理器目录：`decompiled/MegaCrit.Sts2.Core.AutoSlay.Handlers.Screens/`

当前版本 `AutoSlay` 注册的主流程 handler 包括：

- Room：`Combat`、`Event`、`Shop`、`Treasure`、`RestSite`
- Screen：`Rewards`、`CardReward`、`DeckUpgrade`、`DeckTransform`、`DeckEnchant`、`DeckCardSelect`、`SimpleCardSelect`、`ChooseACard`、`ChooseABundle`、`ChooseARelic`、`GameOver`、`CrystalSphere`

每次 patch 后，检查 bridge 是否仍覆盖这些类型：

- 状态识别入口：`game_mod/Sts2RlBridge/GameIntrospection.cs` 的 `TryBuildStateCore()`
- 动作执行入口：`game_mod/Sts2RlBridge/GameIntrospection.cs` 的 `TryExecuteDecision()`
- 状态封装：`Build*State()`
- 动作落地：`Execute*Decision()`
- 过渡态兜底：`TryHandle*Automation()`

修 bridge 时遵循两个原则：

- 优先复用解包类里的真实方法、真实 signal、真实 proceed 流程，不要先堆泛化 click heuristic
- 先确认卡住的是哪个真实 `room/screen`，再改对应分支；不要只根据“像地图/像选牌/像篝火”去猜

几个高频坑：

- 战斗内“打出牌后再选一张手牌”不是普通 overlay 选牌，而是 `CardSelectCmd.FromHand(...) -> NPlayerHand.SelectCards(...)`
- 休息区、奖励、事件、宝箱做完一次动作后，可能只剩 `ProceedButton`
- 卡牌选择类 screen 经常有进入动画、ready delay、preview confirm、manual confirm 等前置条件

### 6.6 更新检查清单

- [ ] 保留旧版 `decompiled/` 结果
- [ ] 重新反编译新的 `sts2.dll`
- [ ] 必要时提取 `.pck`
- [ ] 对比重点目录
- [ ] 运行 `python scripts/extract_data.py`
- [ ] 运行 `python scripts/audit_bridge_training_coverage.py --json-out data/bridge_training_gap_report.json`
- [ ] 重新对照 `AutoSlay` handler 集合
- [ ] 至少回归：
  - `pytest tests/test_training_entry.py -v`
  - `pytest tests/test_env.py -v`
- [ ] 如果战斗/卡牌机制变了，再补跑全量测试
- [ ] 先用 `python scripts/evaluate_all.py --episodes 100` 看旧模型是否还能工作
- [ ] 如果评估明显退化或动作语义已变，再运行 `python scripts/train_all.py`

## 7. 当前训练链路没有覆盖完整 bridge 语义

当前 live bridge 已经会从游戏侧带出这些战斗 UI 子状态：

- `combat_playable_cards`
- `combat_selectable_cards`
- `combat_selection_mode`
- `combat_selection_confirm_enabled`

它们会在 `bridge/bridge_client.py` 里收紧 `action_mask`，避免模型去点 UI 上根本不能点的动作。但要注意：

- `sts_env/encoding.py` 现在没有把这些 UI 子状态编码进 observation
- `sts_env/game_state.py` 也没有显式保存这些 bridge-only 子状态
- 结果就是：模型看到的观测里，`现在是在弃牌`、`现在是在消耗`、`现在是在升级手牌` 这些情况可能非常接近，主要只靠 `action_mask` 区分

这会直接导致一个训练 / 推理错位：

- bridge 知道当前要选哪类牌
- 旧模型只能在合法动作集合里盲选，不真正理解当前二段选择的语义

这也是为什么一些 bridge bug 修完后，模型仍可能出现：

- 该斩杀时不斩杀
- 该先上易伤 / 力量时没先上
- 进入弃牌 / 消耗 / 升级界面后虽然不再乱点，但选得不聪明

### 7.1 为什么我现在没有直接改 observation 维度

因为当前运行中的 bridge 控制状态明确指向你训练好的 `best_model.zip`：

- `bridge_control_state.json`

现在 observation 已经正式追加了战斗 UI 子状态特征，但运行时额外做了一层旧模型兼容：

- 新 bridge 总是生成完整 observation
- `PolicyRuntime` 会读取模型自己的 `observation_space.shape`
- 如果加载的是旧模型，就自动截取旧前缀特征

这意味着：

- 你可以先继续用旧模型跑流程
- 旧模型不会真正利用新增 UI 子状态
- 想吃到这些新特征带来的收益，仍然必须重训

所以这一步必须分两段：

1. 先把 bridge 行为修正确、日志补全、缺口审计清楚
2. 再把新的 UI 子状态真正并入训练 observation，并重新训练模型

### 7.2 训练环境当前最明显的未覆盖区

- 真实游戏里大量卡牌会进入 `CardSelectCmd.FromHand...` / `FromSimpleGrid...` / `FromChooseACardScreen...`
- 训练环境 `sts_env/card_effects.py` 里，这些卡很多没有显式 handler，或者是简化处理
- 例如 `Acrobatics` 现在仍是“抽牌后丢最后一张手牌”的简化逻辑，而不是根据当前语义主动弃牌

刚才运行审计脚本得到的当前结果是：

- 真实选牌相关卡牌：`39`
- 缺显式 handler：`0`
- 有 handler 但明显简化：`0`
- 中高风险缺口：`0`

这说明战斗内/网格/发现牌相关的高频选牌卡效，训练环境现在已经都有显式 handler 了。但要做到接近完美的实机效果，仍然不能只继续修 bridge，还要继续补训练语义本身。

### 7.3 如果要彻底补齐，正确顺序是

1. 用审计脚本列出所有高风险 card / screen
2. 先补 `sts_env/card_effects.py`，把高频二段选择卡从简化逻辑改成显式子决策
3. 再给 `GameState` / `encoding.py` 增加对应的 UI 子状态编码
4. 更新测试后重新训练

只有这样，bridge 看到的语义和模型训练过的语义才会真正对齐。

### 7.4 当前已经补上的部分

截至当前版本，下面这些训练 / bridge 对齐工作已经补上了：

- live bridge 会把战斗 UI 子状态写进 payload：
  - `combat_playable_cards`
  - `combat_selectable_cards`
  - `combat_selection_mode`
  - `combat_selection_confirm_enabled`
- `bridge/bridge_client.py` 会基于这些 UI 真值收紧 `action_mask`
- `sts_env/encoding.py` 已追加一段 runtime combat tail，用于编码：
  - 手牌关键字、tag、动态变量、保留、Sly、replay、灾厄信息
  - 玩家 orb / orb slot / Osty 缺席状态
- `PolicyRuntime` 对旧模型保留 observation 前缀兼容，所以 bridge 可以先升级，模型可以后重训
- `sts_env/archetypes.py` / `sts_env/rewards.py` 已按当前 STS2 卡池重新校正：
  - 15~18 张小牌库优先，25+ 明显惩罚
  - 复合抽牌/滤牌高于普通数值牌
  - Act 2 以后更积极删基础 Strike / Defend
  - 商店买卡会显式考虑金币机会成本
  - 篝火、商店、地图路径会根据当前 HP / deck quality / remove 需求做偏好
- 现在有回归测试专门保证：
  - 训练先验引用的卡牌 ID 必须真实存在于 `data/cards.json`
  - 角色策略不会再误引用别的角色专属卡

### 7.5 当前仍然存在的硬缺口

下面这些问题即使 bridge 已修正确，旧模型仍然可能继续犯：

- 旧模型没有重训，所以还吃不到新补的 pile-selection / 发现牌 / 路线 / 商店 / 火堆语义
- 商店当前动作空间还没有显式“买药水”动作位
- 事件环境仍是简化版，不等价于真实游戏所有事件
- 遗物购买 / boss relic 选择目前还是轻启发式，不是完整 build-aware 规划

这几项里，最该优先补的是：

1. 基于当前 observation / reward / card effects 重新训练或续训
2. 商店药水动作与对应训练语义
3. 更完整的事件 / relic / boss relic build-aware 规划

如果只是继续修 bridge，而不补这些训练语义，实机效果通常会表现为：

- 不再卡死，但会“能走流程却不聪明”
- 到消耗 / 升级 / 弃牌 / 网格选牌界面时动作合法，但选择质量差
- 商店、火堆、路线能继续推进，但 build 规划偏弱

## 8. 相关文档

- 维护视角说明见 [CLAUDE.md](/D:/Slay%20the%20Spire%202%20v0.101.0/Slay_the_Spire_2_rl-main/CLAUDE.md)
- 游戏侧模组说明见 [game_mod/README.md](/D:/Slay%20the%20Spire%202%20v0.101.0/Slay_the_Spire_2_rl-main/game_mod/README.md)

