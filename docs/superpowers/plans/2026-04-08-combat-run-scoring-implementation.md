# Combat And Run Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement combat-level and run-level scoring that rewards faster kills, lower HP loss, and lower avoidable HP loss, including same-turn draw/combo lines.

**Architecture:** Keep reward aggregation in `sts_env/rewards.py`, combat/run bookkeeping in `sts_env/env.py`, and the turn-local search engine in a new focused helper module under `sts_env/`. Feed the new metrics into training/evaluation summaries and cover the behavior with targeted environment and training-entry tests.

**Tech Stack:** Python, Gymnasium environment code in `sts_env/`, pytest, Stable-Baselines3 evaluation wrappers in `agent/`

## Implementation Notes

- Actual implementation also updated `scripts/evaluate_all.py`, `scripts/bridge_ui.py`, and `scripts/training_ui.py` so leaderboard sorting and local UI summaries reflect the new scoring metrics.
- `sts_env/combat.py` did not require direct edits. The turn-local scoring adapter was implemented in `sts_env/combat_scoring.py` against existing combat/player state.
- `sts_env.rewards.compute_run_score()` currently supports the run-end aggregation interface from the spec and a compatibility path used by the existing reward-focused tests.

---

## File Map

- Modify: `sts_env/rewards.py`
  Responsibility: combat score formula, run score formula, scoring config constants.
- Modify: `sts_env/env.py`
  Responsibility: combat/run stat lifecycle, turn analysis trigger points, info payload wiring.
- Create: `sts_env/combat_scoring.py`
  Responsibility: turn-local search state, beam search, avoidable-damage analysis helpers.
- Modify: `agent/evaluate.py`
  Responsibility: aggregate and print new metrics.
- Modify: `agent/train.py`
  Responsibility: post-training evaluation metrics and training summary fields.
- Modify: `scripts/evaluate_all.py`
  Responsibility: leaderboard ordering should incorporate run-level score.
- Modify: `scripts/bridge_ui.py`
  Responsibility: surface new scoring metrics in local bridge model summaries.
- Modify: `scripts/training_ui.py`
  Responsibility: surface new scoring metrics in training/evaluation summaries.
- Modify: `tests/test_env.py`
  Responsibility: environment-level combat scoring and avoidable-damage tests.
- Modify: `tests/test_training_entry.py`
  Responsibility: evaluation/training summary metric tests.

### Task 1: Add Failing Reward Tests For Combat And Run Scores

**Files:**
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`

- [x] **Step 1: Write the failing tests for the new score formulas**

```python
def test_compute_combat_reward_penalizes_avoidable_hp_loss_more_than_raw_hp_loss():
    from sts_env.rewards import compute_combat_reward
    from sts_env.game_state import RoomType

    reward_clean = compute_combat_reward(
        gs=_dummy_game_state(),
        room_type=RoomType.MONSTER,
        won=True,
        hp_before=80,
        hp_after=76,
        turns=3,
        max_hp=80,
        avoidable_hp_lost=0,
    )
    reward_misplayed = compute_combat_reward(
        gs=_dummy_game_state(),
        room_type=RoomType.MONSTER,
        won=True,
        hp_before=80,
        hp_after=76,
        turns=3,
        max_hp=80,
        avoidable_hp_lost=4,
    )

    assert reward_clean > reward_misplayed


def test_compute_combat_reward_drops_faster_after_turn_threshold():
    from sts_env.rewards import compute_combat_reward
    from sts_env.game_state import RoomType

    reward_turn_3 = compute_combat_reward(
        gs=_dummy_game_state(),
        room_type=RoomType.MONSTER,
        won=True,
        hp_before=80,
        hp_after=80,
        turns=3,
        max_hp=80,
        avoidable_hp_lost=0,
    )
    reward_turn_5 = compute_combat_reward(
        gs=_dummy_game_state(),
        room_type=RoomType.MONSTER,
        won=True,
        hp_before=80,
        hp_after=80,
        turns=5,
        max_hp=80,
        avoidable_hp_lost=0,
    )

    assert reward_turn_3 > reward_turn_5
    assert (reward_turn_3 - reward_turn_5) > 0.2


def test_compute_run_score_accumulates_progress_combat_scores_and_remaining_hp():
    from sts_env.rewards import compute_run_score

    run_score = compute_run_score(
        won=True,
        floor=18,
        hp=42,
        max_hp=80,
        combat_score_total=67.5,
    )

    assert run_score > 67.5
```

- [x] **Step 2: Run the reward-focused tests to verify they fail**

Run: `pytest tests/test_env.py -k "combat_reward or run_score" -v`

Expected: FAIL with `TypeError` because `compute_combat_reward()` does not accept `avoidable_hp_lost`, and `compute_run_score()` is missing.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_env.py
git commit -m "test: add combat and run scoring expectations"
```

### Task 2: Implement Score Formulas In `sts_env/rewards.py`

**Files:**
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\sts_env\rewards.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`

- [x] **Step 1: Extend `RewardConfig` and reward helpers**

```python
class RewardConfig:
    BEAT_BOSS = 120.0
    BEAT_ELITE = 30.0
    BEAT_MONSTER = 15.0

    HP_LOSS_PENALTY = -0.15
    AVOIDABLE_HP_LOSS_PENALTY = -0.3

    TURN_PENALTY_LINEAR = -0.1
    TURN_PENALTY_QUADRATIC = -0.08

    NORMAL_TURN_THRESHOLD = 3
    ELITE_TURN_THRESHOLD = 4
    BOSS_TURN_THRESHOLD = 6

    ONE_TURN_KILL_BONUS = 5.0
    TWO_TURN_KILL_BONUS = 2.5
    LOW_HP_FINISH_BONUS = 3.0

    RUN_FLOOR_FACTOR = 1.0
    RUN_WIN_BONUS = 1000.0
    RUN_REMAINING_HP_FACTOR = 0.2


def _turn_penalty(room_type: RoomType, turns: int) -> float:
    threshold = {
        RoomType.MONSTER: RewardConfig.NORMAL_TURN_THRESHOLD,
        RoomType.ELITE: RewardConfig.ELITE_TURN_THRESHOLD,
        RoomType.BOSS: RewardConfig.BOSS_TURN_THRESHOLD,
    }.get(room_type, RewardConfig.NORMAL_TURN_THRESHOLD)
    overflow = max(0, turns - threshold)
    return (
        turns * RewardConfig.TURN_PENALTY_LINEAR
        + (overflow ** 2) * RewardConfig.TURN_PENALTY_QUADRATIC
    )
```

- [x] **Step 2: Upgrade `compute_combat_reward()` and add `compute_run_score()`**

```python
def compute_combat_reward(
    gs: GameState,
    room_type: RoomType,
    won: bool,
    hp_before: int,
    hp_after: int,
    turns: int,
    max_hp: int,
    avoidable_hp_lost: int = 0,
) -> float:
    reward = 0.0
    if not won:
        return RewardConfig.DEATH

    reward += {
        RoomType.BOSS: RewardConfig.BEAT_BOSS,
        RoomType.ELITE: RewardConfig.BEAT_ELITE,
    }.get(room_type, RewardConfig.BEAT_MONSTER)

    hp_lost = max(0, hp_before - hp_after)
    reward += hp_lost * RewardConfig.HP_LOSS_PENALTY
    reward += max(0, avoidable_hp_lost) * RewardConfig.AVOIDABLE_HP_LOSS_PENALTY
    reward += _turn_penalty(room_type, max(1, turns))

    if turns <= 1:
        reward += RewardConfig.ONE_TURN_KILL_BONUS
    elif turns <= 2:
        reward += RewardConfig.TWO_TURN_KILL_BONUS

    if hp_after > 0 and max_hp > 0 and hp_after / max_hp > 0.8:
        reward += RewardConfig.LOW_HP_FINISH_BONUS
    return reward


def compute_run_score(
    *,
    won: bool,
    floor: int,
    hp: int,
    max_hp: int,
    combat_score_total: float,
) -> float:
    remaining_hp_bonus = 0.0
    if max_hp > 0:
        remaining_hp_bonus = max(0, hp) / max_hp * RewardConfig.RUN_REMAINING_HP_FACTOR * 100.0
    return (
        floor * RewardConfig.RUN_FLOOR_FACTOR
        + combat_score_total
        + remaining_hp_bonus
        + (RewardConfig.RUN_WIN_BONUS if won else 0.0)
    )
```

- [x] **Step 3: Run the reward tests to verify they pass**

Run: `pytest tests/test_env.py -k "combat_reward or run_score" -v`

Expected: PASS for the three new score tests.

- [ ] **Step 4: Commit the reward implementation**

```bash
git add sts_env/rewards.py tests/test_env.py
git commit -m "feat: add combat and run score formulas"
```

### Task 3: Add Failing Tests For Turn-Local Avoidable Damage Analysis

**Files:**
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`
- Create: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\sts_env\combat_scoring.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`

- [x] **Step 1: Add focused tests for preventable vs unavoidable damage**

```python
def test_analyze_turn_avoidable_hp_loss_detects_skipped_defensive_line():
    from sts_env.combat_scoring import analyze_turn_avoidable_hp_loss

    result = analyze_turn_avoidable_hp_loss(_make_search_fixture(can_prevent_all_damage=True))

    assert result.actual_hp_loss == 6
    assert result.optimal_min_hp_loss == 0
    assert result.avoidable_hp_loss == 6


def test_analyze_turn_avoidable_hp_loss_handles_unavoidable_damage():
    from sts_env.combat_scoring import analyze_turn_avoidable_hp_loss

    result = analyze_turn_avoidable_hp_loss(_make_search_fixture(can_prevent_all_damage=False))

    assert result.actual_hp_loss == 6
    assert result.optimal_min_hp_loss == 6
    assert result.avoidable_hp_loss == 0


def test_analyze_turn_avoidable_hp_loss_prefers_draw_then_block_line():
    from sts_env.combat_scoring import analyze_turn_avoidable_hp_loss

    result = analyze_turn_avoidable_hp_loss(_make_search_fixture(requires_zero_cost_draw_line=True))

    assert result.optimal_min_hp_loss == 0
    assert "play_draw_zero" in result.best_line_labels
    assert "play_block" in result.best_line_labels
```

- [x] **Step 2: Run those tests to verify the helper does not exist yet**

Run: `pytest tests/test_env.py -k "avoidable_hp_loss_detects or prefers_draw_then_block_line" -v`

Expected: FAIL with `ModuleNotFoundError` for `sts_env.combat_scoring`.

- [ ] **Step 3: Commit the failing search tests**

```bash
git add tests/test_env.py
git commit -m "test: add avoidable damage search expectations"
```

### Task 4: Implement The Turn Searcher In `sts_env/combat_scoring.py`

**Files:**
- Create: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\sts_env\combat_scoring.py`
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\sts_env\combat.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`

- [x] **Step 1: Add the search result dataclasses and search entry point**

```python
from dataclasses import dataclass, field


@dataclass(slots=True)
class TurnSearchResult:
    actual_hp_loss: int
    optimal_min_hp_loss: int
    avoidable_hp_loss: int
    best_line_labels: list[str] = field(default_factory=list)


def analyze_turn_avoidable_hp_loss(turn_context, *, beam_width: int = 48, max_depth: int = 10) -> TurnSearchResult:
    best_state = _beam_search_turn(turn_context, beam_width=beam_width, max_depth=max_depth)
    actual_hp_loss = int(turn_context.actual_hp_loss)
    optimal_min_hp_loss = int(best_state.hp_loss)
    return TurnSearchResult(
        actual_hp_loss=actual_hp_loss,
        optimal_min_hp_loss=optimal_min_hp_loss,
        avoidable_hp_loss=max(0, actual_hp_loss - optimal_min_hp_loss),
        best_line_labels=[step.label for step in best_state.lineage],
    )
```

- [x] **Step 2: Implement bounded beam search with the required objective ordering**

```python
def _state_rank(state) -> tuple[int, int, int]:
    return (
        int(state.hp_loss),
        int(state.enemy_threat),
        -int(state.resource_quality),
    )


def _beam_search_turn(turn_context, *, beam_width: int, max_depth: int):
    frontier = [_seed_state(turn_context)]
    best_terminal = None
    for _depth in range(max_depth):
        candidates = []
        for state in frontier:
            for next_state in _expand_legal_actions(state):
                candidates.append(next_state)
        if not candidates:
            break
        deduped = _dedupe_states(candidates)
        deduped.sort(key=_state_rank)
        frontier = deduped[:beam_width]
        maybe_terminal = min(frontier, key=_state_rank)
        if best_terminal is None or _state_rank(maybe_terminal) < _state_rank(best_terminal):
            best_terminal = maybe_terminal
        if all(state.ended_turn for state in frontier):
            break
    return best_terminal or _seed_state(turn_context)
```

- [x] **Step 3: Add expansion logic that prefers zero-cost draw, hand-space management, and same-turn damage reduction lines**

```python
def _expand_legal_actions(state):
    actions = list(_legal_card_actions(state))
    actions.extend(_legal_potion_actions(state))
    actions.append(_end_turn_action(state))
    actions.sort(key=_action_priority)
    for action in actions:
        yield _apply_action(state, action)


def _action_priority(action) -> tuple[int, int]:
    if action.kind == "card" and action.card_cost == 0 and action.draw_count > 0:
        return (0, 0)
    if action.kind == "card" and action.frees_hand_space:
        return (1, 0)
    if action.kind == "card" and action.reduces_incoming_damage:
        return (2, 0)
    if action.kind == "potion" and action.reduces_incoming_damage:
        return (3, 0)
    if action.kind == "end_turn":
        return (9, 0)
    return (5, 0)
```

- [x] **Step 4: Add the minimal combat-side adapter used by the searcher**

```python
def build_turn_search_context(combat, player, *, actual_hp_loss: int):
    return TurnSearchContext(
        actual_hp_loss=actual_hp_loss,
        hand=list(player.hand),
        draw_pile=list(player.draw_pile),
        discard_pile=list(player.discard_pile),
        exhaust_pile=list(player.exhaust_pile),
        energy=int(player.energy),
        block=int(player.block),
        player_hp=int(player.hp),
        enemies=[_enemy_snapshot(monster) for monster in combat.alive_monsters],
        potions=list(player.potions),
    )
```

- [x] **Step 5: Run the search tests to verify they pass**

Run: `pytest tests/test_env.py -k "avoidable_hp_loss_detects or prefers_draw_then_block_line" -v`

Expected: PASS for the three avoidable-damage tests.

- [ ] **Step 6: Commit the searcher**

```bash
git add sts_env/combat_scoring.py sts_env/combat.py tests/test_env.py
git commit -m "feat: add turn-local avoidable damage search"
```

### Task 5: Wire Combat And Run Stat Tracking Into `sts_env/env.py`

**Files:**
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\sts_env\env.py`
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`

- [x] **Step 1: Add per-run and per-combat stat initialization in `reset()`**

```python
self._run_combat_score_total = 0.0
self._run_combat_count = 0
self._run_hp_lost_total = 0
self._run_avoidable_hp_lost_total = 0
self._run_turns_total = 0

self._combat_hp_before = self.gs.player.hp
self._combat_actual_hp_lost = 0
self._combat_avoidable_hp_lost = 0
self._combat_turn_damage_samples = []
```

- [x] **Step 2: Record turn-local damage samples inside combat stepping**

```python
actual_hp_loss = max(0, hp_before_enemy_phase - gs.player.hp)
if actual_hp_loss > 0:
    search_context = build_turn_search_context(combat, gs.player, actual_hp_loss=actual_hp_loss)
    search_result = analyze_turn_avoidable_hp_loss(search_context)
    self._combat_actual_hp_lost += search_result.actual_hp_loss
    self._combat_avoidable_hp_lost += search_result.avoidable_hp_loss
    self._combat_turn_damage_samples.append(search_result)
```

- [x] **Step 3: Emit `combat_score` from `_finish_combat()` and roll it into run totals**

```python
combat_score = compute_combat_reward(
    gs=gs,
    room_type=room_type,
    won=True,
    hp_before=getattr(gs, "_combat_hp_before", gs.player.hp),
    hp_after=gs.player.hp,
    turns=max(1, combat.turn_count + 1),
    max_hp=gs.player.max_hp,
    avoidable_hp_lost=self._combat_avoidable_hp_lost,
)
self._run_combat_score_total += combat_score
self._run_combat_count += 1
self._run_hp_lost_total += self._combat_actual_hp_lost
self._run_avoidable_hp_lost_total += self._combat_avoidable_hp_lost
self._run_turns_total += max(1, combat.turn_count + 1)
return combat_score
```

- [x] **Step 4: Add final `run_score` and averages to terminal `info`**

```python
if terminated or truncated:
    info["combat_score_total"] = self._run_combat_score_total
    info["combat_count"] = self._run_combat_count
    info["avg_turns_per_combat"] = (
        self._run_turns_total / self._run_combat_count if self._run_combat_count else 0.0
    )
    info["avg_hp_lost_per_combat"] = (
        self._run_hp_lost_total / self._run_combat_count if self._run_combat_count else 0.0
    )
    info["avg_avoidable_hp_lost_per_combat"] = (
        self._run_avoidable_hp_lost_total / self._run_combat_count if self._run_combat_count else 0.0
    )
    info["run_score"] = compute_run_score(
        won=bool(info.get("won", False)),
        floor=int(info.get("floor", 0)),
        hp=int(info.get("hp", 0)),
        max_hp=int(self.gs.player.max_hp),
        combat_score_total=self._run_combat_score_total,
    )
```

- [x] **Step 5: Add environment integration tests for faster wins and info metrics**

```python
def test_finish_combat_records_combat_and_run_score_metrics():
    env = StsEnv(seed=123)
    obs, info = env.reset()
    env._run_combat_score_total = 10.0
    env._run_combat_count = 2
    env._run_hp_lost_total = 4
    env._run_avoidable_hp_lost_total = 1
    env._run_turns_total = 5

    terminal_info = env._build_terminal_info(won=True)

    assert terminal_info["run_score"] >= terminal_info["combat_score_total"]
    assert terminal_info["avg_hp_lost_per_combat"] == 2.0
    assert terminal_info["avg_avoidable_hp_lost_per_combat"] == 0.5
```

- [x] **Step 6: Run the environment test slice**

Run: `pytest tests/test_env.py -k "finish_combat_records or combat_reward or avoidable_hp_loss" -v`

Expected: PASS for combat scoring, avoidable-damage, and terminal info tests.

- [ ] **Step 7: Commit the environment wiring**

```bash
git add sts_env/env.py tests/test_env.py
git commit -m "feat: track combat and run scoring stats in env"
```

### Task 6: Add Failing Metric Tests For Training And Evaluation Summaries

**Files:**
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_training_entry.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_training_entry.py`

- [x] **Step 1: Add tests that expect the new summary fields**

```python
def test_run_post_training_evaluation_returns_combat_and_run_metrics(monkeypatch):
    metrics = run_post_training_evaluation(_fake_model(), _fake_cfg())

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
```

- [x] **Step 2: Run the metric tests to verify they fail**

Run: `pytest tests/test_training_entry.py -k "avg_run_score or avg_combat_score" -v`

Expected: FAIL because `_build_metrics()` and `run_post_training_evaluation()` do not expose the new fields yet.

- [ ] **Step 3: Commit the failing metric tests**

```bash
git add tests/test_training_entry.py
git commit -m "test: require combat and run metrics in summaries"
```

### Task 7: Implement Training And Evaluation Metric Aggregation

**Files:**
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\agent\evaluate.py`
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\agent\train.py`
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_training_entry.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_training_entry.py`

- [x] **Step 1: Extend `agent/evaluate.py::_build_metrics()` and printing**

```python
def _build_metrics(
    character: str,
    episodes: int,
    wins: int,
    total_floors: int,
    total_hp: int,
    total_combat_score: float,
    total_run_score: float,
    total_turns_per_combat: float,
    total_hp_lost_per_combat: float,
    total_avoidable_hp_lost_per_combat: float,
) -> dict:
    return {
        "character": character,
        "episodes": episodes,
        "wins": wins,
        "win_rate": wins / episodes if episodes else 0.0,
        "avg_floor": total_floors / episodes if episodes else 0.0,
        "avg_hp": total_hp / episodes if episodes else 0.0,
        "avg_combat_score": total_combat_score / episodes if episodes else 0.0,
        "avg_run_score": total_run_score / episodes if episodes else 0.0,
        "avg_turns_per_combat": total_turns_per_combat / episodes if episodes else 0.0,
        "avg_hp_lost_per_combat": total_hp_lost_per_combat / episodes if episodes else 0.0,
        "avg_avoidable_hp_lost_per_combat": total_avoidable_hp_lost_per_combat / episodes if episodes else 0.0,
    }
```

- [x] **Step 2: Accumulate the new info fields in both evaluation entry points**

```python
total_combat_score = 0.0
total_run_score = 0.0
total_turns_per_combat = 0.0
total_hp_lost_per_combat = 0.0
total_avoidable_hp_lost_per_combat = 0.0

for ep in range(n_episodes):
    ...
    total_combat_score += float(info.get("combat_score_total", 0.0))
    total_run_score += float(info.get("run_score", 0.0))
    total_turns_per_combat += float(info.get("avg_turns_per_combat", 0.0))
    total_hp_lost_per_combat += float(info.get("avg_hp_lost_per_combat", 0.0))
    total_avoidable_hp_lost_per_combat += float(info.get("avg_avoidable_hp_lost_per_combat", 0.0))
```

- [x] **Step 3: Extend `agent/train.py::run_post_training_evaluation()` and `save_training_summary()`**

```python
total_combat_score = 0.0
total_run_score = 0.0
total_turns_per_combat = 0.0
total_hp_lost_per_combat = 0.0
total_avoidable_hp_lost_per_combat = 0.0

...
total_combat_score += float(info.get("combat_score_total", 0.0))
total_run_score += float(info.get("run_score", 0.0))
total_turns_per_combat += float(info.get("avg_turns_per_combat", 0.0))
total_hp_lost_per_combat += float(info.get("avg_hp_lost_per_combat", 0.0))
total_avoidable_hp_lost_per_combat += float(info.get("avg_avoidable_hp_lost_per_combat", 0.0))

return {
    "character": cfg.character,
    "episodes": episodes,
    "wins": wins,
    "win_rate": wins / episodes,
    "avg_floor": total_floors / episodes,
    "avg_hp": total_hp / episodes,
    "avg_combat_score": total_combat_score / episodes,
    "avg_run_score": total_run_score / episodes,
    "avg_turns_per_combat": total_turns_per_combat / episodes,
    "avg_hp_lost_per_combat": total_hp_lost_per_combat / episodes,
    "avg_avoidable_hp_lost_per_combat": total_avoidable_hp_lost_per_combat / episodes,
}
```

- [x] **Step 4: Add the summary-line print update**

```python
print(
    f"训练后评估: win_rate={post_eval['win_rate']:.2%}, "
    f"avg_floor={post_eval['avg_floor']:.1f}, avg_hp={post_eval['avg_hp']:.1f}, "
    f"avg_combat_score={post_eval['avg_combat_score']:.2f}, "
    f"avg_run_score={post_eval['avg_run_score']:.2f}"
)
```

- [x] **Step 5: Run the training-summary tests**

Run: `pytest tests/test_training_entry.py -k "avg_run_score or avg_combat_score or avg_avoidable_hp_lost_per_combat" -v`

Expected: PASS for the new summary-field tests.

- [ ] **Step 6: Commit the metric aggregation**

```bash
git add agent/evaluate.py agent/train.py tests/test_training_entry.py
git commit -m "feat: report combat and run scoring metrics"
```

### Task 8: Full Verification And Cleanup

**Files:**
- Modify: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\docs\superpowers\plans\2026-04-08-combat-run-scoring-implementation.md`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_env.py`
- Test: `D:\Slay the Spire 2 v0.102.0\Slay_the_Spire_2_rl-main\tests\test_training_entry.py`

- [x] **Step 1: Run the targeted verification suite**

Run: `pytest tests/test_env.py -k "combat_reward or run_score or avoidable_hp_loss or finish_combat_records" -v`

Expected: PASS for combat score, avoidable damage, and terminal info coverage.

- [x] **Step 2: Run the training/evaluation verification suite**

Run: `pytest tests/test_training_entry.py -k "avg_run_score or avg_combat_score or avg_avoidable_hp_lost_per_combat" -v`

Expected: PASS for evaluation/training summary coverage.

- [x] **Step 3: Run a combined smoke check**

Run: `pytest tests/test_env.py tests/test_training_entry.py -k "combat_reward or run_score or avoidable_hp_loss or avg_run_score or avg_combat_score" -v`

Expected: PASS with no regressions in the touched scoring paths.

- [ ] **Step 4: Update the plan checkboxes and commit the finished implementation**

```bash
git add sts_env/rewards.py sts_env/combat_scoring.py sts_env/combat.py sts_env/env.py agent/evaluate.py agent/train.py tests/test_env.py tests/test_training_entry.py
git commit -m "feat: implement combat and run scoring system"
```

## Self-Review

Spec coverage check:

- combat score formula: covered by Tasks 1-2 and Task 5
- run score aggregation: covered by Tasks 1-2 and Task 5
- turn-local avoidable damage search including draw/combo lines: covered by Tasks 3-5
- online approximation boundary: covered by Task 4 beam-search implementation
- evaluation/training reporting: covered by Tasks 6-7
- regression tests: covered by Tasks 1, 3, 5, 6, and 8

Placeholder scan:

- no `TODO`, `TBD`, or “implement later” markers remain
- every code-changing task contains concrete code snippets
- every test step includes an exact pytest command and expected outcome

Type consistency check:

- `compute_combat_reward(..., avoidable_hp_lost=...)` is used consistently
- `compute_run_score()` naming is consistent across plan tasks
- `avg_combat_score`, `avg_run_score`, `avg_turns_per_combat`, `avg_hp_lost_per_combat`, and `avg_avoidable_hp_lost_per_combat` are used consistently across env/train/evaluate/tests
