# STS2 游戏侧桥接模组

这个目录提供一个游戏内 DLL 模组，用来把仓库里的 Python WebSocket bridge 接到真实游戏。

## 构建和部署

在仓库根目录执行：

```powershell
.\game_mod\deploy_mod.ps1
```

脚本会：

- 编译 `game_mod\Sts2RlBridge`
- 复制 `Sts2RlBridge.dll` 到游戏目录 `mods\Sts2RlBridge\`
- 写入 `manifest.json`、`mod_manifest.json`、`mod.json`

## 启动顺序

1. 启动 Python bridge：

```powershell
cd .\Slay_the_Spire_2_rl-main
python scripts\bridge_ui.py
```

或者直接：

```powershell
python bridge\bridge_client.py --host 127.0.0.1 --port 8765 --character Ironclad --model <你的模型路径>
```

2. 启动游戏
3. 正常开始一局
4. 模组会自动连接 `ws://127.0.0.1:8765`

## 日志

模组日志路径：

```text
<游戏目录>\mods\Sts2RlBridge\bridge.log
```

点击轨迹记录路径：

```text
<游戏目录>\mods\Sts2RlBridge\click_trace.jsonl
```

### 手动录制建议

- 如果你要录自己的点击供后续分析，先不要启动自动游玩；或者在 `bridge_ui.py` 里点“暂停自动游玩”。
- `click_trace.jsonl` 是 JSONL 文件，每一行一条记录，包含：
  - `session_id`
  - `action`
  - `trace.phase_hint`
  - `trace.context`
  - `details`
  - `state`
- 文件默认追加写入。想要一份干净样本时，开新一局前先备份或删除旧的 `click_trace.jsonl`。

## 更新后 bridge 排查

如果游戏更新后出现“经常卡在同一个地方”，不要只按表面现象补 click，优先按**真实 room / screen 类型**排查。

### 1. 先看当前版本的真值表

优先对照这些解包文件：

- `decompiled/MegaCrit.Sts2.Core.AutoSlay/AutoSlayer.cs`
- `decompiled/MegaCrit.Sts2.Core.AutoSlay.Handlers.Rooms/`
- `decompiled/MegaCrit.Sts2.Core.AutoSlay.Handlers.Screens/`

它们基本可以当成“当前版本官方自动游玩知道有哪些流程节点”的清单。

### 2. 再看 bridge 这边是否有对应分支

主要看：

- `game_mod/Sts2RlBridge/GameIntrospection.cs`

重点检查：

- `TryBuildStateCore()`
- `TryExecuteDecision()`
- `Build*State()`
- `Execute*Decision()`
- `TryHandle*Automation()`

原则：

- 优先调用解包类里的真实方法、真实 signal、真实 proceed 流程。
- 不要先靠通用 click heuristic 硬点过去。

### 3. 常见日志含义

看到下面这些日志时，优先往这些方向查：

- `combat input pending; end turn disabled ... hand_mode=...`
  - 先查战斗内子状态，尤其是 `NPlayerHand.SelectCards()`、`CardSelectCmd.FromHand(...)`
- `rest options empty ...` 且 `decision controls` 里只剩 `rest.proceed:ProceedButton`
  - 说明房间动作已经做完，bridge 没切到房间 proceed 流程
- `map UI pending; screen visible but no enabled choices`
  - 先查地图 UI 还没稳定、节点未启用，或 map choice 抓取逻辑失效
- `card selection signal ...`
  - 说明已经进入真实选牌 screen，下一步应检查 confirm / preview / ready delay

### 4. 已知高频坑

- 战斗里“打牌后再选一张手牌”不属于普通 overlay 选牌，而是 `NPlayerHand` 内部选择态。
- 篝火、宝箱、事件、奖励在动作完成后，经常会只剩一个 `ProceedButton`，这时应优先前进，不要重发旧动作。
- 卡牌选择 screen 可能有 ready delay、preview confirm、manual confirm，不能只靠“控件可点击”判定。
