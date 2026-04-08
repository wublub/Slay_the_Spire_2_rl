# Combat And Run Scoring Design

## Goal

Adjust training-time and evaluation-time scoring so the agent is rewarded for:

- ending combats in fewer turns
- taking less actual HP loss
- avoiding HP loss that was preventable within the current turn

The design must preserve full-run incentives so the model does not overfit to local combat score at the expense of survival, routing, or long-term run progress.

## Scope

This design covers:

- combat-level score shaping during training
- run-level score aggregation for evaluation and model selection
- a turn-local search definition for avoidable HP loss
- online approximation and offline calibration for that search

This design does not cover:

- route reward redesign outside the new run score output
- changes to card evaluation heuristics unrelated to combat defense search
- multi-turn planning or full-run lookahead

## Design Summary

Training should use a two-level scoring model:

- `combat_score` is the primary local reward signal for combat performance
- `run_score` is the aggregate run-level score used for evaluation summaries and model selection

The core principle is:

- winning is still required
- faster combats should score higher
- lower HP loss should score higher
- preventable HP loss should be penalized more heavily than unavoidable HP loss

## Combat Score

Each combat ends with a single `combat_score`:

```text
combat_score =
  victory_base
  - turn_penalty(turns)
  - actual_hp_loss_penalty
  - avoidable_hp_loss_penalty
  + fast_kill_bonus
```

### Components

`victory_base`

- positive base reward for winning the fight
- boss > elite > normal monster

`turn_penalty(turns)`

- score must monotonically decrease as combat turn count increases
- this is not only a flat per-turn penalty
- the penalty grows faster after a threshold

Recommended form:

```text
turn_penalty(turns) = a * turns + b * max(0, turns - T)^2
```

Recommended thresholds:

- normal monster: `T = 3`
- elite: `T = 4`
- boss: `T = 6`

This preserves normal pacing in short fights while strongly discouraging dragging fights past a reasonable turn count.

`actual_hp_loss_penalty`

- proportional to HP actually lost during the combat
- this remains a direct punishment for damage taken, regardless of whether it was avoidable

`avoidable_hp_loss_penalty`

- proportional to HP loss that could have been prevented by a better legal sequence within the same turn
- this penalty should be stronger than ordinary HP-loss penalty

`fast_kill_bonus`

- small bonus for one-turn or two-turn kills
- must remain smaller than the combined penalties for avoidable misplay

### Priority Order

The intended pressure is:

`avoidable_hp_loss` > `actual_hp_loss` > `turn_count`

This avoids a degenerate policy that rushes damage and ignores defense just to shorten the fight.

## Run Score

Each run ends with a `run_score`:

```text
run_score =
  run_progress_reward
  + sum(combat_score)
  + remaining_hp_bonus
```

### Components

`run_progress_reward`

- retains existing incentives for floor progress and run victory
- final run success must still dominate any single combat's local score

`sum(combat_score)`

- sum of all completed combat scores in the run

`remaining_hp_bonus`

- small positive reward for finishing the run with more HP

### Intended Use

`run_score` is not a replacement for win-rate or floor metrics. It is an additional run-level quality metric used for:

- post-training evaluation summaries
- preferred/best model selection
- regression tracking across training changes

## Avoidable HP Loss Definition

Avoidable HP loss is defined by the best legal action sequence within the current turn, not by the currently visible hand only.

```text
avoidable_hp_loss =
  max(0, actual_hp_loss - optimal_min_hp_loss_this_turn)
```

Where:

```text
optimal_min_hp_loss_this_turn =
  the smallest HP loss achievable from the current turn state
  over all legal within-turn action sequences
```

The search must account for:

- current hand
- cards drawn later in the same turn
- playing zero-cost draw first
- playing cards to free hand space before continuing to draw
- draw, discard, cost reduction, temporary energy gain, and generated cards
- defensive lines
- offensive lines that reduce incoming damage
- applying weak or other debuffs that reduce same-turn incoming damage
- killing an enemy to remove or reduce same-turn incoming damage
- potion usage
- action ordering and target selection

The search is explicitly turn-local. It does not look ahead to future turns or future map decisions.

## Turn Searcher

### Search State

The turn search state should include at least:

- current hand, draw pile, discard pile, exhaust pile
- current energy and temporary resource modifications
- temporary/generated cards created during the turn
- player block, HP, and relevant combat modifiers
- enemy HP, alive/dead status, intent, multi-hit information, and relevant modifiers
- potion availability
- current hand occupancy relative to hand-size cap
- whether the end-turn action has been taken

### Search Actions

Legal actions include:

- playing any legal card
- using any legal potion
- ending the turn

### Objective Order

Candidate sequences should be compared in this order:

1. minimize turn HP loss
2. minimize surviving enemy threat after current-turn resolution
3. maximize useful remaining resources and position quality

### Search Behavior Requirements

The search must prefer lines that reflect the intended combat logic:

- try zero-cost draw and filter lines early
- when hand space is tight, prefer legal actions that free space before more draw
- if continued draw or sequencing can further reduce damage, continue expanding
- treat end turn as optimal only when no legal continuation improves the objective order

## Online Approximation And Offline Calibration

### Online Training Search

During normal environment stepping, use an approximate search:

- beam search instead of exact enumeration
- only enable it when the current enemy turn presents incoming damage
- use moderate beam width and action-depth limits

Recommended first-pass limits:

- beam width: `32-64`
- max action depth per analyzed turn: `8-12`

This keeps reward shaping practical during training.

### Offline Calibration

Use a deeper search outside the hot training path for:

- evaluation audits
- analyzing logged turns where HP loss occurred
- validating that online approximation is not drifting too far from stronger search
- optionally generating improved imitation targets later

Offline calibration is not required for every turn. It should focus on damage-taking turns and representative combat states.

## Integration Boundaries

### `sts_env/rewards.py`

- keep `compute_combat_reward()` as the public combat reward entry point
- upgrade it to consume combat-level totals for turns, actual HP loss, and avoidable HP loss
- add `compute_run_score()` for run-end aggregation

### `sts_env/env.py`

- initialize and maintain per-combat and per-run scoring state
- trigger turn-local avoidable-damage analysis during combat
- accumulate combat totals
- emit `combat_score` at combat end
- accumulate `run_score` inputs across the run

### `sts_env/combat.py`

- expose minimal stable helpers needed by the turn searcher
- keep reward logic out of this file
- support access to turn-relevant intent, card/resource state, and same-turn combat resolution details

### `agent/train.py` and `agent/evaluate.py`

Add reporting for:

- `avg_combat_score`
- `avg_run_score`
- `avg_turns_per_combat`
- `avg_hp_lost_per_combat`
- `avg_avoidable_hp_lost_per_combat`

Model selection should not rely on win rate alone. It should consider at least:

- win rate
- average floor
- average run score

## Testing

Add coverage for:

- a fight where the agent had a legal defensive or sequencing line and failed to use it, producing positive `avoidable_hp_loss`
- a fight where incoming damage was not preventable, producing zero `avoidable_hp_loss`
- faster wins scoring higher than slower wins in comparable combats
- training/evaluation summaries exposing the new combat and run metrics
- model selection logic no longer depending on a single metric

## Risks And Constraints

Main risks:

- turn-local search becoming too slow in training
- search bugs misclassifying avoidable damage
- reward imbalance causing the policy to over-defend or overvalue local combat score

Mitigations:

- start with bounded beam search online
- validate approximation with deeper offline search on sampled turns
- tune weights so preventable damage matters more than raw speed, but run completion still dominates

## Non-Goals

This design does not attempt to:

- solve optimal multi-turn planning
- evaluate route or shop strategy with the same search engine
- replace current win/floor metrics with a single scalar

## Approval Outcome

Approved design direction:

- two-level scoring: combat plus run
- turn count must reduce score monotonically and more sharply past combat-type thresholds
- avoidable HP loss must include complex same-turn draw and combo lines, not only current visible hand
- the search must consider same-turn defensive, offensive, debuff, and potion lines if they reduce incoming damage
- online training uses approximate beam search; offline analysis uses deeper calibration search
