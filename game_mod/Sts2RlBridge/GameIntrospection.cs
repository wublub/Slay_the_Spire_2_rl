using System.Collections;
using System.Reflection;
using System.Threading;
using Godot;
using MegaCrit.Sts2.Core.AutoSlay;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Rewards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Relics;
using MegaCrit.Sts2.Core.Nodes.RestSite;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MapPoint = MegaCrit.Sts2.Core.Map.MapPoint;
using MapPointType = MegaCrit.Sts2.Core.Map.MapPointType;
using MapCoord = MegaCrit.Sts2.Core.Map.MapCoord;
using EventOption = MegaCrit.Sts2.Core.Events.EventOption;

namespace Sts2RlBridge;

internal static class GameIntrospection
{
    private sealed class CombatHandSelectionState
    {
        public required NPlayerHand Hand { get; init; }
        public required string Mode { get; init; }
        public required List<bool> SelectableCards { get; init; }
        public required List<bool> SelectedCards { get; init; }
        public required bool ConfirmEnabled { get; init; }
        public required int SelectedCount { get; init; }
        public required int MinSelect { get; init; }
        public required int MaxSelect { get; init; }
        public required bool ManualConfirm { get; init; }
    }

    private static readonly Dictionary<string, DateTime> RateLimitedLogUtc = [];
    private static DateTime NextEventProceedAttemptUtc = DateTime.MinValue;
    private static DateTime PendingMapDecisionUtc = DateTime.MinValue;
    private static DateTime PendingUiTransitionUntilUtc = DateTime.MinValue;
    private static string? PendingMapDecisionStateKey;
    private static string? PendingMapDecisionOrigin;
    private static string? PendingUiTransitionReason;
    private static int SyntheticClickDepth;
    private static readonly TimeSpan MapDecisionPendingTimeout = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan GenericCardSelectionSettleDelay = TimeSpan.FromMilliseconds(700);
    private static readonly TimeSpan CardRewardSelectionSettleDelay = TimeSpan.FromMilliseconds(900);

    private static readonly FieldInfo CardRewardOptionsField =
        typeof(NCardRewardSelectionScreen).GetField("_options", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo RewardsButtonsField =
        typeof(NRewardsScreen).GetField("_rewardButtons", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo BossRelicRowField =
        typeof(NChooseARelicSelection).GetField("_relicRow", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo BossRelicsField =
        typeof(NChooseARelicSelection).GetField("_relics", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo PlayerHandCurrentSelectionFilterField =
        typeof(NPlayerHand).GetField("_currentSelectionFilter", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo PlayerHandSelectedCardsField =
        typeof(NPlayerHand).GetField("_selectedCards", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo PlayerHandPrefsField =
        typeof(NPlayerHand).GetField("_prefs", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo? DeckCardSelectSelectedCardsField =
        typeof(NDeckCardSelectScreen).GetField("_selectedCards", BindingFlags.Instance | BindingFlags.NonPublic);

    private static readonly FieldInfo? DeckCardSelectPrefsField =
        typeof(NDeckCardSelectScreen).GetField("_prefs", BindingFlags.Instance | BindingFlags.NonPublic);

    private static readonly FieldInfo? CardGridSelectionScreenGridField =
        typeof(NCardGridSelectionScreen).GetField("_grid", BindingFlags.Instance | BindingFlags.NonPublic);

    private static readonly PropertyInfo RunStateProperty =
        typeof(RunManager).GetProperty("State", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;

    private static readonly MethodInfo CardRewardSelectMethod =
        typeof(NCardRewardSelectionScreen).GetMethod("SelectCard", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;

    private static readonly MethodInfo? CardRewardAlternateSelectedMethod =
        typeof(NCardRewardSelectionScreen).GetMethod("OnAlternateRewardSelected", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);

    private static readonly AutoSlayer AutoSlayer = new();

    private static readonly MethodInfo AutoSlayerClickEventProceedMethod =
        typeof(AutoSlayer).GetMethod("ClickEventProceedIfNeeded", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;

    private static RunState? GetRunState() => RunManager.Instance is null ? null : (RunState?)RunStateProperty.GetValue(RunManager.Instance);

    public static StateEnvelope? TryBuildState() => TryBuildStateCore(allowRewardAutomation: true);

    public static StateEnvelope? TryBuildTraceState() => TryBuildStateCore(allowRewardAutomation: false);

    public static TraceContextSnapshot GetTraceContextSnapshot()
    {
        var state = GetRunState();
        var player = state?.Players.FirstOrDefault();
        var screen = GetCurrentScreenSafe("trace_context");
        return new TraceContextSnapshot
        {
            PhaseHint = DeterminePhaseHint(state, screen),
            Character = player?.Character?.Id.Entry,
            Act = state is null ? null : state.CurrentActIndex + 1,
            Floor = state?.TotalFloor,
            Context = DescribeCurrentContext(),
        };
    }

    public static int? FindHandCardIndex(CardModel card)
    {
        try
        {
            var player = GetRunState()?.Players.FirstOrDefault();
            var hand = player?.PlayerCombatState?.Hand?.Cards;
            if (hand is null)
            {
                return null;
            }

            var index = hand.ToList().FindIndex(candidate => ReferenceEquals(candidate, card));
            return index >= 0 ? index : null;
        }
        catch
        {
            return null;
        }
    }

    public static Dictionary<string, object?> DescribeMapPointSelection(MapPoint point)
    {
        var details = new Dictionary<string, object?>
        {
            ["room_type"] = MapPointTypeToRoomType(point.PointType),
        };

        try
        {
            var state = GetRunState();
            if (state is null)
            {
                return details;
            }

            for (var row = 0; row < 60; row++)
            {
                var points = SafeGetPointsInRow(state, row);
                for (var col = 0; col < points.Count; col++)
                {
                    if (!ReferenceEquals(points[col].point, point))
                    {
                        continue;
                    }

                    details["row"] = row;
                    details["col"] = points[col].col;
                    return details;
                }
            }
        }
        catch
        {
        }

        return details;
    }

    public static Node? GetTraceRootNode()
    {
        var screen = GetCurrentScreenSafe("trace_root");
        if (screen is Node screenNode)
        {
            return screenNode;
        }

        if (NGame.Instance?.CurrentRunNode is Node runNode)
        {
            return runNode;
        }

        if (NGame.Instance?.MainMenu is Node mainMenu)
        {
            return mainMenu;
        }

        return null;
    }

    private static StateEnvelope? TryBuildStateCore(bool allowRewardAutomation)
    {
        var state = GetRunState();
        var player = state?.Players.FirstOrDefault();
        if (state is null || player is null)
        {
            return null;
        }

        var screen = GetCurrentScreenSafe("build_state");
        RefreshMapDecisionGuard(state, screen);
        if (allowRewardAutomation && Volatile.Read(ref SyntheticClickDepth) > 0)
        {
            RateLimitedLog(
                "synthetic_click_pending",
                TimeSpan.FromMilliseconds(200),
                $"build_state deferred during synthetic click room={state.CurrentRoom?.GetType().FullName ?? "<null>"} screen={screen?.GetType().FullName ?? "<null>"} coord={FormatMapCoord(state.CurrentMapCoord)}");
            return null;
        }

        if (allowRewardAutomation && DateTime.UtcNow < PendingUiTransitionUntilUtc)
        {
            RateLimitedLog(
                "ui_transition_pending",
                TimeSpan.FromMilliseconds(250),
                $"build_state deferred during ui transition reason={PendingUiTransitionReason ?? "<unknown>"} " +
                $"room={state.CurrentRoom?.GetType().FullName ?? "<null>"} screen={screen?.GetType().FullName ?? "<null>"} coord={FormatMapCoord(state.CurrentMapCoord)}");
            return null;
        }

        if (DateTime.UtcNow >= PendingUiTransitionUntilUtc)
        {
            PendingUiTransitionReason = null;
        }

        if (IsGameOverScreen(screen) || state.IsGameOver)
        {
            if (allowRewardAutomation && TryHandleGameOverAutomation(screen))
            {
                return null;
            }

            return BuildStateWithContext("game_over", state, screen, () => BuildEnvelope(state, player, "game_over", new Dictionary<string, object>()));
        }

        if (state.CurrentRoom is CombatRoom && CombatManager.Instance is { IsOverOrEnding: true })
        {
            if (!allowRewardAutomation)
            {
                return null;
            }

            if (TryHandleCombatRewardAutomation())
            {
                return null;
            }

            if (HasPendingCombatRewardUi())
            {
                RateLimitedLog("combat_reward_pending", TimeSpan.FromSeconds(1), "combat reward UI pending; deferring map state");
                return null;
            }
        }
        if (screen is NCardRewardSelectionScreen cardRewardScreen)
        {
            return BuildStateWithContext("card_reward", state, screen, () => BuildCardRewardState(state, player, cardRewardScreen));
        }

        if (screen is Node cardSelectionScreen && IsGenericCardSelectionScreen(cardSelectionScreen))
        {
            return BuildStateWithContext("card_reward", state, screen, () => BuildGenericCardSelectionState(state, player, cardSelectionScreen));
        }

        if (screen is NChooseARelicSelection bossRelicScreen)
        {
            return BuildStateWithContext("boss_relic", state, screen, () => BuildBossRelicState(state, player, bossRelicScreen));
        }

        if (screen is NRewardsScreen rewardsScreen)
        {
            if (allowRewardAutomation)
            {
                if (TryHandleRewardScreenAutomation(rewardsScreen))
                {
                    return null;
                }
            }
            else
            {
                return null;
            }
        }

        if (screen is NMapScreen)
        {
            var uiChoices = GetUiMapPointChoices(state);
            if (!uiChoices.Any(x => x.enabled))
            {
                RateLimitedLog(
                    "map_ui_pending",
                    TimeSpan.FromMilliseconds(500),
                    $"map UI pending; screen visible but no enabled choices floor={state.TotalFloor} coord={FormatMapCoord(state.CurrentMapCoord)} choices={uiChoices.Count}");
                return null;
            }

            return BuildStateWithContext("map", state, screen, () => BuildMapStateFromUiChoices(state, player, uiChoices));
        }

        if (state.CurrentRoom is CombatRoom combatRoom)
        {
            if (CombatManager.Instance is { IsOverOrEnding: false })
            {
                var endTurnButton = NRun.Instance?.CombatRoom?.Ui?.EndTurnButton;
                if (endTurnButton?.IsEnabled != true)
                {
                    var selectionState = TryGetCombatHandSelectionState(player);
                    if (selectionState is not null)
                    {
                        return BuildStateWithContext("combat", state, screen, () => BuildCombatState(state, player, combatRoom, selectionState));
                    }

                    RateLimitedLog(
                        "combat_input_pending",
                        TimeSpan.FromMilliseconds(300),
                        $"combat input pending; end turn disabled room={state.CurrentRoom.GetType().FullName} " +
                        $"screen={screen?.GetType().FullName ?? "<null>"} coord={FormatMapCoord(state.CurrentMapCoord)} " +
                        $"{DescribeCombatHandSelectionDiagnostics(player)}");
                    return null;
                }

                return BuildStateWithContext("combat", state, screen, () => BuildCombatState(state, player, combatRoom));
            }

            RateLimitedLog(
                "combat_phase_pending",
                TimeSpan.FromMilliseconds(300),
                $"combat phase pending; manager unavailable room={state.CurrentRoom.GetType().FullName} screen={screen?.GetType().FullName ?? "<null>"} coord={FormatMapCoord(state.CurrentMapCoord)}");
            return null;
        }

        if (state.CurrentRoom is EventRoom && TryHandleEventProceedAutomation())
        {
            return null;
        }

        if (state.CurrentRoom is RestSiteRoom restSiteRoom && TryHandleRestProceedAutomation(restSiteRoom))
        {
            return null;
        }

        if (state.CurrentRoom is null)
        {
            return null;
        }

        return state.CurrentRoom switch
        {
            EventRoom eventRoom => BuildStateWithContext("event", state, screen, () => BuildEventState(state, player, eventRoom)),
            RestSiteRoom restRoom => BuildStateWithContext("rest", state, screen, () => BuildRestState(state, player, restRoom)),
            MerchantRoom merchantRoom => BuildStateWithContext("shop", state, screen, () => BuildShopState(state, player, merchantRoom)),
            TreasureRoom => BuildStateWithContext("treasure", state, screen, () => BuildTreasureState(state, player)),
            _ => null,
        };
    }

    public static bool TryExecuteDecision(DecisionPayload decision)
    {
        var state = GetRunState();
        var player = state?.Players.FirstOrDefault();
        if (state is null || player is null)
        {
            return false;
        }

        var screen = GetCurrentScreenSafe("execute_decision");
        if (IsGameOverScreen(screen) || state.IsGameOver)
        {
            return TryHandleGameOverAutomation(screen);
        }

        if (decision.Type == "choose_path" && screen is not NMapScreen)
        {
            BridgeLoop.Log(
                $"map decision rejected outside map screen room={state.CurrentRoom?.GetType().FullName ?? "<null>"} " +
                $"screen={screen?.GetType().FullName ?? "<null>"} coord={FormatMapCoord(state.CurrentMapCoord)}");
            return false;
        }

        if (screen is NCardRewardSelectionScreen cardRewardScreen)
        {
            return ExecuteCardRewardDecision(cardRewardScreen, decision);
        }

        if (screen is Node cardSelectionScreen && IsGenericCardSelectionScreen(cardSelectionScreen))
        {
            return ExecuteGenericCardSelectionDecision(cardSelectionScreen, decision);
        }

        if (screen is NChooseARelicSelection bossRelicScreen)
        {
            return ExecuteBossRelicDecision(bossRelicScreen, decision);
        }

        if (screen is NMapScreen)
        {
            return ExecuteMapDecision(state, decision);
        }

        if (decision.Type == "skip" && state.CurrentRoom is TreasureRoom)
        {
            var proceedButton = NRun.Instance?.TreasureRoom?.ProceedButton;
            return proceedButton?.IsEnabled == true && Click(proceedButton);
        }

        if (state.CurrentRoom is CombatRoom combatRoom && CombatManager.Instance is { IsOverOrEnding: false })
        {
            return ExecuteCombatDecision(player, combatRoom, decision);
        }

        return state.CurrentRoom switch
        {
            EventRoom eventRoom => ExecuteEventDecision(eventRoom, screen, decision),
            RestSiteRoom restRoom => ExecuteRestDecision(restRoom, decision),
            MerchantRoom merchantRoom => ExecuteShopDecision(merchantRoom, decision),
            _ => false,
        };
    }

    public static string DescribeCurrentContext()
    {
        try
        {
            var state = GetRunState();
            var room = state?.CurrentRoom?.GetType().FullName ?? "<null>";
            var screen = GetCurrentScreenSafe("describe_context")?.GetType().FullName ?? "<null>";
            var runNode = NGame.Instance?.CurrentRunNode?.GetType().FullName ?? "<null>";
            var mapCoord = state?.CurrentMapCoord;
            var coord = mapCoord.HasValue ? $"{mapCoord.Value.row},{mapCoord.Value.col}" : "<null>";
            var gameOver = state?.IsGameOver.ToString() ?? "<null>";
            return $"room={room} screen={screen} run_node={runNode} map_coord={coord} game_over={gameOver}";
        }
        catch (Exception ex)
        {
            return $"describe_context_failed={ex.Message}";
        }
    }

    public static string DescribeBridgeContextKey()
    {
        try
        {
            var state = GetRunState();
            var screen = GetCurrentScreenSafe("bridge_context_key");
            var phase = DeterminePhaseHint(state, screen);
            var act = state is null ? "null" : (state.CurrentActIndex + 1).ToString();
            var floor = state?.TotalFloor.ToString() ?? "null";
            return $"{phase}:{act}:{floor}:{DescribeCurrentContext()}";
        }
        catch (Exception ex)
        {
            return $"bridge_context_key_failed={ex.Message}";
        }
    }

    private static StateEnvelope BuildEnvelope(RunState state, Player player, string phase, Dictionary<string, object> phasePayload)
    {
        var creature = player.Creature;
        return new StateEnvelope
        {
            RequestId = Guid.NewGuid().ToString("N"),
            Character = player.Character.Id.Entry,
            Phase = phase,
            Run = new RunPayload
            {
                Act = state.CurrentActIndex + 1,
                Floor = state.TotalFloor,
                Won = false,
            },
            Player = new PlayerPayload
            {
                Name = player.Character.Id.Entry,
                Hp = creature.CurrentHp,
                MaxHp = creature.MaxHp,
                Block = creature.Block,
                Gold = player.Gold,
                Energy = player.PlayerCombatState?.Energy ?? 0,
                EnergyPerTurn = player.MaxEnergy,
                OrbSlots = player.PlayerCombatState?.OrbQueue?.Capacity ?? player.BaseOrbSlotCount,
                Orbs = player.PlayerCombatState?.OrbQueue?.Orbs.Select(x => x.Id.Entry).ToList() ?? [],
                IsOstyMissing = player.IsOstyMissing,
                Hand = BuildCards(player.PlayerCombatState?.Hand?.Cards),
                DrawPile = BuildCards(player.PlayerCombatState?.DrawPile?.Cards),
                DiscardPile = BuildCards(player.PlayerCombatState?.DiscardPile?.Cards),
                ExhaustPile = BuildCards(player.PlayerCombatState?.ExhaustPile?.Cards),
                Relics = player.Relics.Select(x => x.Id.Entry).ToList(),
                Potions = player.PotionSlots.Where(x => x is not null).Select(x => x!.Id.Entry).ToList(),
                Powers = BuildPowers(creature.Powers),
            },
            Deck = BuildCards(player.Deck.Cards),
            State = new Dictionary<string, object> { [phase] = phasePayload },
        };
    }

    private static StateEnvelope BuildCombatState(
        RunState state,
        Player player,
        CombatRoom combatRoom,
        CombatHandSelectionState? selectionState = null)
        => BuildEnvelope(state, player, "combat", BuildCombatPhasePayload(player, combatRoom, selectionState));

    private static Dictionary<string, object> BuildCombatPhasePayload(
        Player player,
        CombatRoom combatRoom,
        CombatHandSelectionState? selectionState = null)
    {
        var combat = combatRoom.CombatState;
        selectionState ??= TryGetCombatHandSelectionState(player);

        var payload = new Dictionary<string, object>
        {
            ["monsters"] = combat.Enemies.Select(BuildMonster).ToList(),
            ["turn_count"] = combat.RoundNumber,
            ["round_number"] = combat.RoundNumber,
            ["playable_cards"] = BuildPlayableCards(player),
            ["end_turn_enabled"] = NRun.Instance?.CombatRoom?.Ui?.EndTurnButton?.IsEnabled ?? false,
        };

        if (selectionState is not null)
        {
            payload["selection_mode"] = selectionState.Mode;
            payload["selectable_cards"] = selectionState.SelectableCards;
            payload["selected_cards"] = selectionState.SelectedCards;
            payload["selection_confirm_enabled"] = selectionState.ConfirmEnabled;
            payload["selection_selected_count"] = selectionState.SelectedCount;
            payload["selection_min"] = selectionState.MinSelect;
            payload["selection_max"] = selectionState.MaxSelect;
            payload["selection_manual_confirm"] = selectionState.ManualConfirm;
        }

        return payload;
    }

    private static StateEnvelope BuildEventState(RunState state, Player player, EventRoom eventRoom)
    {
        var options = BuildEventOptions(eventRoom);
        if (options.Count == 0)
        {
            throw new InvalidOperationException("event options unavailable");
        }

        var phasePayload = new Dictionary<string, object>
        {
            ["options"] = options,
        };
        return BuildEnvelope(state, player, "event", phasePayload);
    }

    private static StateEnvelope BuildRestState(RunState state, Player player, RestSiteRoom restRoom)
    {
        var phasePayload = new Dictionary<string, object>
        {
            ["options"] = BuildRestOptions(restRoom),
        };
        return BuildEnvelope(state, player, "rest", phasePayload);
    }

    private static StateEnvelope BuildShopState(RunState state, Player player, MerchantRoom merchantRoom)
    {
        var inventory = merchantRoom.Inventory;
        var cards = inventory.CharacterCardEntries.Concat(inventory.ColorlessCardEntries).Take(3).Select(x => new ShopEntryPayload
        {
            Id = x.CreationResult.Card.Id.Entry,
            Cost = x.Cost,
            Enabled = x.EnoughGold && x.IsStocked,
        }).ToList();

        var relics = inventory.RelicEntries.Take(3).Select(x => new ShopEntryPayload
        {
            Id = x.Model.Id.Entry,
            Cost = x.Cost,
            Enabled = x.EnoughGold && x.IsStocked,
        }).ToList();

        var potions = inventory.PotionEntries.Select(x => new ShopEntryPayload
        {
            Id = x.Model.Id.Entry,
            Cost = x.Cost,
            Enabled = x.EnoughGold && x.IsStocked,
        }).ToList();

        var phasePayload = new Dictionary<string, object>
        {
            ["cards"] = cards,
            ["relics"] = relics,
            ["potions"] = potions,
            ["remove"] = new
            {
                cost = inventory.CardRemovalEntry.Cost,
                enabled = inventory.CardRemovalEntry.EnoughGold && !inventory.CardRemovalEntry.Used,
            },
            ["leave_enabled"] = NRun.Instance?.MerchantRoom?.ProceedButton?.IsEnabled ?? true,
        };
        return BuildEnvelope(state, player, "shop", phasePayload);
    }

    private static StateEnvelope BuildMapStateFromUiChoices(
        RunState state,
        Player player,
        IReadOnlyList<(NMapPoint control, MapPoint point, int row, int col, bool enabled)> uiChoices)
    {
        var uiPhasePayload = new Dictionary<string, object>
        {
            ["choices"] = uiChoices.Select(x => new MapChoicePayload
            {
                Row = x.row,
                Col = x.col,
                RoomType = MapPointTypeToRoomType(x.point.PointType),
                Children = x.point.Children.Select(child => FindColumnInRow(state, x.row + 1, child)).Where(i => i >= 0).ToList(),
                Enabled = x.enabled,
            }).ToList(),
        };
        return BuildEnvelope(state, player, "map", uiPhasePayload);
    }

    private static StateEnvelope BuildMapState(RunState state, Player player)
    {
        var currentCoord = state.CurrentMapCoord;
        var currentPoint = state.CurrentMapPoint;
        var row = currentCoord?.row + 1 ?? 0;
        var nextPoints = SafeGetPointsInRow(state, row);
        var filteredPoints = currentPoint is null
            ? nextPoints
            : nextPoints.Where(x => currentPoint.Children.Contains(x.point)).ToList();
        if (filteredPoints.Count == 0 && nextPoints.Count > 0)
        {
            filteredPoints = nextPoints;
        }

        var choices = filteredPoints.Take(4).Select(x => new MapChoicePayload
        {
            Row = row,
            Col = x.col,
            RoomType = MapPointTypeToRoomType(x.point.PointType),
            Children = x.point.Children.Select(child => FindColumnInRow(state, row + 1, child)).Where(i => i >= 0).ToList(),
            Enabled = true,
        }).ToList();

        var phasePayload = new Dictionary<string, object>
        {
            ["choices"] = choices,
        };
        return BuildEnvelope(state, player, "map", phasePayload);
    }

    private static StateEnvelope BuildTreasureState(RunState state, Player player)
    {
        var phasePayload = new Dictionary<string, object>
        {
            ["can_proceed"] = NRun.Instance?.TreasureRoom?.ProceedButton?.IsEnabled ?? false,
        };
        return BuildEnvelope(state, player, "treasure", phasePayload);
    }

    private static StateEnvelope BuildCardRewardState(RunState state, Player player, NCardRewardSelectionScreen screen)
    {
        var options = (IReadOnlyList<CardCreationResult>)CardRewardOptionsField.GetValue(screen)!;
        var envelope = BuildCardSelectionEnvelope(
            state,
            player,
            screen,
            options.Take(3).Select(x => x.Card).ToList(),
            canSkip: FindSkipButton(screen) is not null || FindCardRewardAlternativeButton(screen) is not null);
        return envelope;
    }

    private static StateEnvelope BuildGenericCardSelectionState(RunState state, Player player, Node screen)
    {
        var inDeckPreview = IsDeckCardSelectionPreviewActive(screen);
        var cards = inDeckPreview || IsGenericCardSelectionReadyToConfirm(screen)
            ? []
            : GetActionableGenericCardSelectionHolders(screen).Select(x => x.CardModel!).ToList();
        return BuildCardSelectionEnvelope(
            state,
            player,
            screen,
            cards,
            canSkip: FindCardSelectionActionButton(screen) is not null || inDeckPreview);
    }

    private static StateEnvelope BuildCardSelectionEnvelope(
        RunState state,
        Player player,
        Node screen,
        IReadOnlyList<CardModel> cards,
        bool canSkip)
    {
        var phasePayload = new Dictionary<string, object>
        {
            ["cards"] = cards.Take(3).Select(x => new CardPayload
            {
                Id = x.Id.Entry,
                Upgraded = x.IsUpgraded,
                Cost = x.GetStarCostThisCombat(),
                Type = x.Type.ToString(),
                Target = x.TargetType.ToString(),
            }).ToList(),
            ["can_skip"] = canSkip,
        };

        var envelope = BuildEnvelope(state, player, "card_reward", phasePayload);
        AttachCombatStateIfAvailable(envelope, state, player);
        return envelope;
    }

    private static StateEnvelope BuildBossRelicState(RunState state, Player player, NChooseARelicSelection screen)
    {
        var relics = (IReadOnlyList<RelicModel>)BossRelicsField.GetValue(screen)!;
        var phasePayload = new Dictionary<string, object>
        {
            ["choices"] = relics.Take(3).Select(x => new OptionPayload
            {
                Id = x.Id.Entry,
                Title = SafeText(x.Title, x.Id.Entry),
                Enabled = true,
            }).ToList(),
        };
        return BuildEnvelope(state, player, "boss_relic", phasePayload);
    }

    private static bool ExecuteCombatDecision(Player player, CombatRoom combatRoom, DecisionPayload decision)
    {
        var combat = combatRoom.CombatState;
        var selectionState = TryGetCombatHandSelectionState(player);
        var applied = selectionState is not null
            ? decision.Type switch
            {
                "play_card" => ExecuteCombatHandSelection(player, selectionState, decision.CardIndex),
                "end_turn" => TryFinalizeCardSelection(selectionState.Hand),
                _ => false,
            }
            : decision.Type switch
            {
                "play_card" => ExecutePlayCard(player, combat, decision.CardIndex, decision.TargetIndex),
                "use_potion" => ExecuteUsePotion(player, combat, decision.PotionIndex, decision.TargetIndex),
                "end_turn" => TryEndTurn(),
                _ => false,
            };
        if (!applied)
        {
            var handCount = player.PlayerCombatState?.Hand?.Cards?.Count ?? 0;
            var potionCount = player.PotionSlots.Count(x => x is not null);
            var enemyCount = combat.HittableEnemies.Count();
            var endTurnButton = NRun.Instance?.CombatRoom?.Ui?.EndTurnButton;
            BridgeLoop.Log(
                $"combat decision rejected type={decision.Type} card_index={decision.CardIndex} potion_index={decision.PotionIndex} " +
                $"target_index={decision.TargetIndex} hand={handCount} potions={potionCount} enemies={enemyCount} " +
                $"end_turn_button={(endTurnButton?.GetType().FullName ?? "<null>")} enabled={endTurnButton?.IsEnabled} " +
                $"selection_mode={(selectionState?.Mode ?? "<none>")} confirm_enabled={selectionState?.ConfirmEnabled}");
        }
        return applied;
    }

    private static bool ExecuteCombatHandSelection(Player player, CombatHandSelectionState selectionState, int? cardIndex)
    {
        if (cardIndex is null)
        {
            return false;
        }

        var handCards = player.PlayerCombatState?.Hand?.Cards?.ToList();
        if (handCards is null || cardIndex.Value < 0 || cardIndex.Value >= handCards.Count)
        {
            return false;
        }

        if (cardIndex.Value >= selectionState.SelectableCards.Count || !selectionState.SelectableCards[cardIndex.Value])
        {
            return false;
        }

        var card = handCards[cardIndex.Value];
        var holder = GetCombatHandHolders(selectionState.Hand).FirstOrDefault(x => x.CardModel is not null && ReferenceEquals(x.CardModel, card));
        if (holder is null)
        {
            return false;
        }

        var applied = TryInvokeCombatHandSelectionMethod(selectionState.Hand, holder);
        if (!applied)
        {
            applied = TryEmitCombatHandSelectionSignal(holder);
        }
        if (!applied)
        {
            var clickTarget = ResolveCardSelectionClickTarget(holder);
            applied = clickTarget is not null && Click(clickTarget);
        }

        if (!applied)
        {
            return false;
        }

        TryFinalizeCardSelection(selectionState.Hand);
        MarkUiTransitionPending("combat_card_selection", GenericCardSelectionSettleDelay);
        return true;
    }

    private static bool ExecutePlayCard(Player player, CombatState combat, int? cardIndex, int? targetIndex)
    {
        if (cardIndex is null)
        {
            return false;
        }

        var hand = player.PlayerCombatState?.Hand?.Cards?.ToList();
        if (hand is null || cardIndex.Value < 0 || cardIndex.Value >= hand.Count)
        {
            return false;
        }

        var card = hand[cardIndex.Value];
        var primaryTarget = ResolveCardTarget(card, combat, targetIndex);
        if (card.TryManualPlay(primaryTarget))
        {
            return true;
        }

        if (primaryTarget is not null && card.TryManualPlay(null))
        {
            return true;
        }

        if (primaryTarget is null)
        {
            var fallbackTarget = ResolveTarget(combat, targetIndex);
            if (fallbackTarget is not null && card.TryManualPlay(fallbackTarget))
            {
                return true;
            }
        }

        BridgeLoop.Log(
            $"play_card rejected card={card.Id.Entry} target_type={card.TargetType} card_index={cardIndex.Value} " +
            $"target_index={targetIndex} can_play={card.CanPlay()} " +
            $"target={(primaryTarget?.Name ?? "<null>")}");
        return false;
    }

    private static bool TryInvokeCombatHandSelectionMethod(NPlayerHand hand, NHandCardHolder holder)
    {
        var methodName = hand.CurrentMode switch
        {
            NPlayerHand.Mode.SimpleSelect => "SelectCardInSimpleMode",
            NPlayerHand.Mode.UpgradeSelect => "SelectCardInUpgradeMode",
            _ => null,
        };
        if (methodName is null)
        {
            return false;
        }

        var method = hand.GetType().GetMethod(methodName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (method is null)
        {
            return false;
        }

        try
        {
            method.Invoke(hand, [holder]);
            return true;
        }
        catch (Exception ex)
        {
            BridgeLoop.Log(
                $"combat hand selection invoke failed mode={hand.CurrentMode} card={holder.CardModel?.Id.Entry ?? "<null>"} ex={ex.Message}");
            return false;
        }
    }

    private static bool TryEmitCombatHandSelectionSignal(NHandCardHolder holder)
    {
        try
        {
            holder.EmitSignal(NHandCardHolder.SignalName.HolderMouseClicked, holder);
            BridgeLoop.Log(
                $"combat hand selection signal mode=holder_mouse_clicked card={holder.CardModel?.Id.Entry ?? "<null>"}");
            return true;
        }
        catch
        {
        }

        try
        {
            holder.EmitSignal(NCardHolder.SignalName.Pressed, holder);
            BridgeLoop.Log(
                $"combat hand selection signal mode=pressed card={holder.CardModel?.Id.Entry ?? "<null>"}");
            return true;
        }
        catch (Exception ex)
        {
            BridgeLoop.Log(
                $"combat hand selection signal failed card={holder.CardModel?.Id.Entry ?? "<null>"} ex={ex.Message}");
            return false;
        }
    }

    private static bool ExecuteUsePotion(Player player, CombatState combat, int? potionIndex, int? targetIndex)
    {
        if (potionIndex is null)
        {
            return false;
        }

        var potions = player.PotionSlots.Where(x => x is not null).Select(x => x!).ToList();
        if (potionIndex.Value < 0 || potionIndex.Value >= potions.Count)
        {
            return false;
        }

        potions[potionIndex.Value].EnqueueManualUse(ResolveTarget(combat, targetIndex));
        return true;
    }

    private static Creature? ResolveTarget(CombatState combat, int? targetIndex)
    {
        var enemies = combat.HittableEnemies.ToList();
        if (enemies.Count == 0)
        {
            return null;
        }

        if (targetIndex is null || targetIndex.Value < 0 || targetIndex.Value >= enemies.Count)
        {
            return enemies[0];
        }

        return enemies[targetIndex.Value];
    }

    private static Creature? ResolveCardTarget(CardModel card, CombatState combat, int? targetIndex)
    {
        var targetType = card.TargetType.ToString();
        return targetType switch
        {
            "AnyEnemy" => ResolveTarget(combat, targetIndex),
            "AllEnemies" => null,
            "None" => null,
            "Self" => null,
            _ => ResolveTarget(combat, targetIndex),
        };
    }

    private static bool ExecuteEventDecision(EventRoom eventRoom, object? screen, DecisionPayload decision)
    {
        if (decision.Type is "end_turn" or "skip")
        {
            _ = (Task)AutoSlayerClickEventProceedMethod.Invoke(AutoSlayer, [CancellationToken.None])!;
            return true;
        }

        if (decision.Type != "choose_option" || decision.Index is null)
        {
            return false;
        }

        try
        {
            var options = eventRoom.LocalMutableEvent.CurrentOptions;
            if (IsInRange(options, decision.Index.Value))
            {
                _ = options[decision.Index.Value].Chosen();
                return true;
            }
        }
        catch (Exception ex)
        {
            RateLimitedLog("event_decision_ui_fallback", TimeSpan.FromSeconds(2), $"event decision falling back to UI: {ex.Message}");
        }

        var buttons = GetEventOptionButtonsFromUi();
        if (!IsInRange(buttons, decision.Index.Value))
        {
            return false;
        }

        return Click(buttons[decision.Index.Value]);
    }

    private static bool ExecuteRestDecision(RestSiteRoom restRoom, DecisionPayload decision)
    {
        var target = decision.Type switch
        {
            "rest" => "rest",
            "upgrade" => "upgrade",
            "dig" => "dig",
            "cook" => "cook",
            "lift" => "lift",
            _ => null,
        };

        if (target is null)
        {
            return false;
        }

        if (TryClickRestOptionButton(target))
        {
            return true;
        }

        if (TryHandleRestProceedAutomation(restRoom))
        {
            BridgeLoop.Log($"rest decision redirected to proceed target={target} context={DescribeCurrentContext()}");
            return true;
        }

        var uiButtons = GetRestSiteButtonsFromUi();
        if (uiButtons.Count > 0)
        {
            RateLimitedLog(
                $"rest_ui_option_missing_{target}",
                TimeSpan.FromSeconds(1),
                $"rest ui option missing target={target} " +
                $"available=[{string.Join(",", uiButtons.Select(button => NormalizeRestOptionId(button.Option?.OptionId ?? button.Name.ToString())))}] " +
                $"context={DescribeCurrentContext()}");
            return false;
        }

        var option = restRoom.Options.FirstOrDefault(x => NormalizeRestOptionId(x.OptionId) == target && x.IsEnabled);
        if (option is null)
        {
            RateLimitedLog(
                $"rest_option_missing_{target}",
                TimeSpan.FromSeconds(1),
                $"rest option missing target={target} room_options=[{string.Join(",", restRoom.Options.Select(x => $"{NormalizeRestOptionId(x.OptionId)}:{x.IsEnabled}"))}] " +
                $"context={DescribeCurrentContext()}");
            return false;
        }

        try
        {
            var fallbackTask = option.OnSelect();
            var applied = fallbackTask.IsCompletedSuccessfully && fallbackTask.Result;
            BridgeLoop.Log(
                $"rest fallback onselect target={target} option_id={option.OptionId} applied={applied} task_status={fallbackTask.Status} " +
                $"context={DescribeCurrentContext()}");
            return applied;
        }
        catch (Exception ex)
        {
            BridgeLoop.Log(
                $"rest fallback onselect failed target={target} option_id={option.OptionId} " +
                $"context={DescribeCurrentContext()} ex={ex}");
            return false;
        }
    }

    private static bool ExecuteShopDecision(MerchantRoom merchantRoom, DecisionPayload decision)
    {
        var inventory = merchantRoom.Inventory;
        switch (decision.Type)
        {
            case "buy_card":
                var cards = inventory.CharacterCardEntries.Concat(inventory.ColorlessCardEntries).Take(3).ToList();
                if (decision.Index is null || !IsInRange(cards, decision.Index.Value))
                {
                    return false;
                }
                _ = cards[decision.Index.Value].OnTryPurchaseWrapper(inventory, false);
                return true;
            case "buy_relic":
                var relics = inventory.RelicEntries.Take(3).ToList();
                if (decision.Index is null || !IsInRange(relics, decision.Index.Value))
                {
                    return false;
                }
                _ = relics[decision.Index.Value].OnTryPurchaseWrapper(inventory, false);
                return true;
            case "remove_card":
                _ = inventory.CardRemovalEntry.OnTryPurchaseWrapper(inventory, false);
                return true;
            case "leave_shop":
                var proceedButton = NRun.Instance?.MerchantRoom?.ProceedButton;
                return proceedButton?.IsEnabled == true && Click(proceedButton);
            default:
                return false;
        }
    }

    private static bool ExecuteMapDecision(RunState state, DecisionPayload decision)
    {
        if (decision.Type != "choose_path" || decision.Index is null)
        {
            return false;
        }

        if (IsMapDecisionPending(state))
        {
            return false;
        }

        var screen = GetCurrentScreenSafe("execute_map_decision");
        if (screen is not NMapScreen)
        {
            BridgeLoop.Log(
                $"map decision rejected: screen unavailable room={state.CurrentRoom?.GetType().FullName ?? "<null>"} " +
                $"screen={screen?.GetType().FullName ?? "<null>"} coord={FormatMapCoord(state.CurrentMapCoord)}");
            return false;
        }

        var uiChoices = GetUiMapPointChoices(state);
        if (!uiChoices.Any(x => x.enabled))
        {
            RateLimitedLog(
                "map_decision_ui_pending",
                TimeSpan.FromMilliseconds(500),
                $"map decision deferred; screen visible but no enabled choices floor={state.TotalFloor} coord={FormatMapCoord(state.CurrentMapCoord)} choices={uiChoices.Count}");
            return false;
        }

        if (uiChoices.Any(x => x.enabled))
        {
            if (!IsInRange(uiChoices, decision.Index.Value))
            {
                BridgeLoop.Log($"map ui decision rejected index={decision.Index.Value} ui_choices={uiChoices.Count}");
                return false;
            }

            var uiChoice = uiChoices[decision.Index.Value];
            if (!uiChoice.enabled)
            {
                BridgeLoop.Log($"map ui decision rejected index={decision.Index.Value} row={uiChoice.row} col={uiChoice.col} enabled={uiChoice.enabled}");
                return false;
            }

            var applied = ClickMapPoint(uiChoice.control);
            if (applied)
            {
                MarkMapDecisionIssued(state, uiChoice.row, uiChoice.col, "ui");
            }
            return applied;
        }
        BridgeLoop.Log(
            $"map decision rejected: map screen visible but no enabled ui choice floor={state.TotalFloor} coord={FormatMapCoord(state.CurrentMapCoord)}");
        return false;
    }

    private static bool TryEndTurn()
    {
        var endTurnButton = NRun.Instance?.CombatRoom?.Ui?.EndTurnButton;
        if (endTurnButton is null)
        {
            BridgeLoop.Log("end_turn rejected: end turn button missing");
            return false;
        }

        if (!endTurnButton.IsEnabled)
        {
            BridgeLoop.Log($"end_turn rejected: button disabled type={endTurnButton.GetType().FullName}");
            return false;
        }

        return Click(endTurnButton);
    }

    private static bool ExecuteCardRewardDecision(NCardRewardSelectionScreen screen, DecisionPayload decision)
    {
        if (decision.Type == "skip")
        {
            return TrySkipCardReward(screen);
        }

        if (decision.Type != "pick_card" || decision.Index is null)
        {
            return false;
        }

        var options = (IReadOnlyList<CardCreationResult>)CardRewardOptionsField.GetValue(screen)!;
        var optionCards = options.Take(3).Select(x => x.Card).ToList();
        if (!IsInRange(optionCards, decision.Index.Value))
        {
            return false;
        }

        var targetCard = optionCards[decision.Index.Value];
        var holders = GetSelectableCardHolders(screen);
        var holder = holders.FirstOrDefault(x => x.CardModel is not null && CardsMatch(x.CardModel, targetCard));
        if (holder is null && IsInRange(holders, decision.Index.Value))
        {
            holder = holders[decision.Index.Value];
        }

        if (holder is null)
        {
            BridgeLoop.Log(
                $"card reward holder missing index={decision.Index.Value} target={targetCard.Id.Entry} " +
                $"holders=[{string.Join(",", holders.Select(x => x.CardModel?.Id.Entry ?? "<null>"))}]");
            return false;
        }

        if (TryInvokeCardRewardSelection(screen, holder, targetCard))
        {
            return true;
        }

        if (TryClickCardSelectionHolder(holder))
        {
            MarkUiTransitionPending("card_reward_pick_click", CardRewardSelectionSettleDelay);
            LogCardRewardDecisionOutcome("click", screen, targetCard, holder);
            return true;
        }

        BridgeLoop.Log(
            $"card reward select failed target={targetCard.Id.Entry} chosen={holder.CardModel?.Id.Entry ?? "<null>"} " +
            $"context={DescribeCurrentContext()} controls={DescribeVisibleControlsSummary(6)}");
        return false;
    }

    private static bool ExecuteGenericCardSelectionDecision(Node screen, DecisionPayload decision)
    {
        if (decision.Type == "skip")
        {
            var actionButton = FindCardSelectionActionButton(screen);
            var skipApplied = actionButton is not null && Click(actionButton);
            if (skipApplied)
            {
                MarkUiTransitionPending("generic_card_selection_skip", GenericCardSelectionSettleDelay);
            }

            return skipApplied;
        }

        if (decision.Type != "pick_card" || decision.Index is null)
        {
            return false;
        }

        if (IsDeckCardSelectionPreviewActive(screen))
        {
            if (TryHandleDeckCardSelectionPreviewConfirmation(screen))
            {
                return true;
            }

            RateLimitedLog(
                "deck_card_selection_preview_pending",
                TimeSpan.FromMilliseconds(250),
                $"deck card selection preview pending confirm screen={screen.GetType().FullName} selected={GetSelectedGenericCardSelectionCards(screen).Count}");
            return false;
        }

        if (IsGenericCardSelectionReadyToConfirm(screen) && TryFinalizeCardSelection(screen))
        {
            MarkUiTransitionPending("generic_card_selection_confirm", GenericCardSelectionSettleDelay);
            return true;
        }

        var holders = GetActionableGenericCardSelectionHolders(screen);
        if (!IsInRange(holders, decision.Index.Value))
        {
            return false;
        }

        if (!IsGenericCardSelectionInteractionReady(screen))
        {
            return false;
        }

        var holder = holders[decision.Index.Value];
        var applied = TryEmitCardSelectionSignal(screen, holder)
            || TryInvokeCardSelectionMethod(screen, holder);
        if (!applied)
        {
            applied = TryClickCardSelectionHolder(holder);
        }
        if (!applied)
        {
            return false;
        }

        if (IsDeckCardSelectionPreviewActive(screen))
        {
            MarkUiTransitionPending("generic_card_selection_preview", GenericCardSelectionSettleDelay);
            return true;
        }

        TryFinalizeCardSelection(screen);
        MarkUiTransitionPending("generic_card_selection_pick", GenericCardSelectionSettleDelay);

        return true;
    }

    private static bool ExecuteBossRelicDecision(NChooseARelicSelection screen, DecisionPayload decision)
    {
        if (decision.Type != "choose_boss_relic" || decision.Index is null)
        {
            return false;
        }

        var row = (Control)BossRelicRowField.GetValue(screen)!;
        var holders = row.GetChildren().OfType<NRelicBasicHolder>().Take(3).ToList();
        if (!IsInRange(holders, decision.Index.Value))
        {
            return false;
        }

        return Click(holders[decision.Index.Value]);
    }

    private static bool TryHandleRewardScreenAutomation(NRewardsScreen rewardsScreen)
    {
        var buttons = ((List<Control>)RewardsButtonsField.GetValue(rewardsScreen)!).OfType<NRewardButton>().Where(x => x.IsEnabled).ToList();
        if (buttons.Count == 0)
        {
            var proceedButton = FindProceedButton(rewardsScreen);
            return proceedButton?.IsEnabled == true && Click(proceedButton);
        }

        var rewardTypes = buttons.Select(x => x.Reward.GetType()).ToList();
        var isBossRelicChoice = buttons.Count == 3 && rewardTypes.All(x => x == typeof(RelicReward));
        if (isBossRelicChoice)
        {
            return false;
        }

        var nonCardReward = buttons.FirstOrDefault(x => x.Reward is not CardReward);
        if (nonCardReward is not null)
        {
            return Click(nonCardReward);
        }

        return Click(buttons[0]);
    }

    private static bool TryHandleCombatRewardAutomation()
    {
        var rewardsScreen = FindVisibleRewardsScreen();
        if (rewardsScreen is not null && TryHandleRewardScreenAutomation(rewardsScreen))
        {
            return true;
        }

        var proceedButton = NRun.Instance?.CombatRoom?.ProceedButton;
        return proceedButton is not null
            && proceedButton.Visible
            && proceedButton.IsEnabled
            && Click(proceedButton);
    }

    private static bool HasPendingCombatRewardUi()
    {
        var rewardsScreen = FindVisibleRewardsScreen();
        if (rewardsScreen is not null)
        {
            var enabledButtons = ((List<Control>)RewardsButtonsField.GetValue(rewardsScreen)!).OfType<NRewardButton>().Any(x => x.IsEnabled);
            if (enabledButtons)
            {
                return true;
            }

            var rewardProceedButton = FindProceedButton(rewardsScreen);
            if (rewardProceedButton?.Visible == true && rewardProceedButton.IsEnabled)
            {
                return true;
            }
        }

        var combatProceedButton = NRun.Instance?.CombatRoom?.ProceedButton;
        return combatProceedButton?.Visible == true && combatProceedButton.IsEnabled;
    }

    private static bool TryHandleGameOverAutomation(object? screen)
    {
        if (screen is not Node root || !IsGameOverScreen(screen))
        {
            return false;
        }

        var proceedButton = FindProceedButton(root);
        if (proceedButton?.Visible == true && proceedButton.IsEnabled)
        {
            if (Click(proceedButton))
            {
                BridgeLoop.Log("game over automation clicked proceed");
                return true;
            }
        }

        var fallback = GetChildrenRecursive(root)
            .OfType<Control>()
            .Where(control => control.Visible)
            .Where(control => ReadBoolMember(control, "IsEnabled") != false)
            .Where(CanClick)
            .Select(control => (control, score: ScoreGameOverControl(control)))
            .Where(item => item.score > 0)
            .OrderByDescending(item => item.score)
            .FirstOrDefault();

        if (fallback.control is null)
        {
            RateLimitedLog(
                "game_over_controls_missing",
                TimeSpan.FromSeconds(1),
                $"game over automation found no clickable controls screen={screen.GetType().FullName} context={DescribeCurrentContext()}");
            return false;
        }

        if (!Click(fallback.control))
        {
            return false;
        }

        BridgeLoop.Log(
            $"game over automation clicked fallback type={fallback.control.GetType().FullName} " +
            $"name={fallback.control.Name} score={fallback.score}");
        return true;
    }

    private static NRewardsScreen? FindVisibleRewardsScreen()
    {
        var root = NGame.Instance?.CurrentRunNode as Node;
        if (root is null)
        {
            return null;
        }

        return GetChildrenRecursive(root)
            .OfType<NRewardsScreen>()
            .FirstOrDefault(screen => screen.Visible);
    }

    private static NProceedButton? FindProceedButton(Node root) => GetChildrenRecursive(root).OfType<NProceedButton>().FirstOrDefault();

    private static Node? FindConfirmButton(Node root) =>
        GetChildrenRecursive(root)
            .Where(IsConfirmButtonCandidate)
            .OrderByDescending(ScoreConfirmButton)
            .FirstOrDefault();

    private static Node? FindSkipButton(Node root) => GetChildrenRecursive(root).FirstOrDefault(x => x.GetType().Name.Contains("SkipButton", StringComparison.OrdinalIgnoreCase));

    private static Node? FindCardSelectionActionButton(Node root)
    {
        var skipButton = FindSkipButton(root);
        if (skipButton is not null && IsActionButtonEnabled(skipButton))
        {
            return skipButton;
        }

        var proceedButton = FindProceedButton(root);
        if (proceedButton is not null && IsActionButtonEnabled(proceedButton))
        {
            return proceedButton;
        }

        var confirmButton = FindConfirmButton(root);
        if (confirmButton is not null && IsActionButtonEnabled(confirmButton) && IsGenericCardSelectionReadyToConfirm(root))
        {
            return confirmButton;
        }

        return null;
    }

    private static NCardRewardAlternativeButton? FindCardRewardAlternativeButton(Node root)
        => GetChildrenRecursive(root)
            .OfType<NCardRewardAlternativeButton>()
            .FirstOrDefault(button => IsVisibleNode(button) && IsActionButtonEnabled(button));

    private static bool IsGameOverScreen(object? screen) =>
        screen?.GetType().FullName?.Contains("GameOverScreen", StringComparison.OrdinalIgnoreCase) == true;

    private static IEnumerable<Node> GetChildrenRecursive(Node root)
    {
        foreach (var child in root.GetChildren())
        {
            if (child is not Node node)
            {
                continue;
            }

            yield return node;
            foreach (var nested in GetChildrenRecursive(node))
            {
                yield return nested;
            }
        }
    }

    private static List<NCardHolder> GetSelectableCardHolders(Node root)
        => GetChildrenRecursive(root)
            .OfType<NCardHolder>()
            .Where(x => x.CardModel is not null && !IsPreviewCardHolder(x) && IsVisibleNode(x))
            .ToList();

    private static List<NCardHolder> GetActionableGenericCardSelectionHolders(Node screen)
    {
        var holders = GetSelectableCardHolders(screen);
        var selectedCards = GetSelectedGenericCardSelectionCards(screen);
        if (selectedCards.Count == 0)
        {
            return holders;
        }

        return holders
            .Where(holder => holder.CardModel is not null && !selectedCards.Any(selected => CardsMatch(selected, holder.CardModel)))
            .ToList();
    }

    private static List<CardModel> GetSelectedGenericCardSelectionCards(Node screen)
    {
        var selectedCards = ReadFieldRecursive(screen, "_selectedCards");
        return ReadSelectedCardModels(selectedCards);
    }

    private static bool IsGenericCardSelectionReadyToConfirm(Node screen)
    {
        if (IsDeckCardSelectionPreviewReady(screen))
        {
            return true;
        }

        var confirmButton = FindConfirmButton(screen);
        if (confirmButton is null || !IsActionButtonEnabled(confirmButton))
        {
            return false;
        }

        var confirmButtonName = confirmButton.Name.ToString();
        if (confirmButtonName.Contains("PreviewConfirm", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        if (TryCheckIfCardSelectionComplete(screen, out var selectionComplete))
        {
            return selectionComplete;
        }

        if (TryGetCardSelectionPrefs(screen, out var minSelect, out var maxSelect, out var requireManualConfirmation))
        {
            var selectedCount = GetSelectedGenericCardSelectionCards(screen).Count;
            if (selectedCount == 0)
            {
                return false;
            }

            if (screen is NDeckCardSelectScreen)
            {
                if (maxSelect is > 0 && selectedCount >= maxSelect)
                {
                    return true;
                }

                if (minSelect is > 0 && selectedCount < minSelect)
                {
                    return false;
                }

                return IsActionButtonEnabled(confirmButton);
            }

            if (requireManualConfirmation == true)
            {
                if (minSelect is > 0 && selectedCount < minSelect)
                {
                    return false;
                }

                return IsActionButtonEnabled(confirmButton);
            }

            if (maxSelect is > 0 && selectedCount >= maxSelect)
            {
                return IsActionButtonEnabled(confirmButton);
            }

            return false;
        }

        return true;
    }

    private static bool TryCheckIfCardSelectionComplete(Node screen, out bool selectionComplete)
    {
        selectionComplete = false;

        var method = screen.GetType().GetMethod("CheckIfSelectionComplete", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (method is null || method.GetParameters().Length != 0 || method.ReturnType != typeof(bool))
        {
            return false;
        }

        try
        {
            if (method.Invoke(screen, null) is bool value)
            {
                selectionComplete = value;
                return true;
            }
        }
        catch
        {
        }

        return false;
    }

    private static object? ResolveCardSelectionClickTarget(NCardHolder holder)
    {
        var hitbox = GetChildrenRecursive(holder).FirstOrDefault(x =>
            x.GetType().Name.Contains("Hitbox", StringComparison.OrdinalIgnoreCase) && CanClick(x));
        if (hitbox is not null)
        {
            return hitbox;
        }

        return CanClick(holder) ? holder : null;
    }

    private static bool TryClickCardSelectionHolder(NCardHolder holder)
    {
        var clickTarget = ResolveCardSelectionClickTarget(holder);
        return clickTarget is not null && Click(clickTarget);
    }

    private static bool IsGenericCardSelectionInteractionReady(Node screen)
    {
        if (screen is not NChooseACardSelectionScreen)
        {
            return true;
        }

        if (ReadFieldRecursive(screen, "_openedTicks") is not ulong openedTicks)
        {
            return true;
        }

        var elapsed = Time.GetTicksMsec() - openedTicks;
        if (elapsed > 350)
        {
            return true;
        }

        RateLimitedLog(
            "choose_a_card_not_ready",
            TimeSpan.FromMilliseconds(150),
            $"choose-a-card selection not ready elapsed_ms={elapsed} screen={screen.GetType().FullName}");
        return false;
    }

    private static bool TryEmitCardSelectionSignal(Node screen, NCardHolder holder)
    {
        try
        {
            if (screen is NChooseACardSelectionScreen)
            {
                holder.EmitSignal(NCardHolder.SignalName.Pressed, holder);
                BridgeLoop.Log(
                    $"card selection signal screen={screen.GetType().FullName} mode=holder " +
                    $"card={holder.CardModel?.Id.Entry ?? "<null>"}");
                return true;
            }

            if (screen is not NCardGridSelectionScreen)
            {
                return false;
            }

            if (screen is NSimpleCardSelectScreen or NDeckTransformSelectScreen)
            {
                if (CardGridSelectionScreenGridField?.GetValue(screen) is NCardGrid grid)
                {
                    grid.EmitSignal(NCardGrid.SignalName.HolderPressed, holder);
                    BridgeLoop.Log(
                        $"card selection signal screen={screen.GetType().FullName} mode=grid " +
                        $"card={holder.CardModel?.Id.Entry ?? "<null>"} selected={GetSelectedGenericCardSelectionCards(screen).Count}");
                    return true;
                }
            }

            holder.EmitSignal(NCardHolder.SignalName.Pressed, holder);
            BridgeLoop.Log(
                $"card selection signal screen={screen.GetType().FullName} mode=holder " +
                $"card={holder.CardModel?.Id.Entry ?? "<null>"} selected={GetSelectedGenericCardSelectionCards(screen).Count}");
            return true;
        }
        catch (Exception ex)
        {
            BridgeLoop.Log(
                $"card selection signal failed screen={screen.GetType().FullName} " +
                $"card={holder.CardModel?.Id.Entry ?? "<null>"} ex={ex.Message}");
            return false;
        }
    }

    private static bool IsGenericCardSelectionScreen(Node screen)
    {
        if (screen is NCardRewardSelectionScreen)
        {
            return false;
        }

        var typeName = screen.GetType().FullName ?? screen.GetType().Name;
        if (!typeName.Contains(".Screens.CardSelection.", StringComparison.OrdinalIgnoreCase)
            && !typeName.Contains("CardSelectionScreen", StringComparison.OrdinalIgnoreCase)
            && !typeName.Contains("DeckCardSelectScreen", StringComparison.OrdinalIgnoreCase)
            && !typeName.Contains("DeckUpgradeSelectScreen", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        return GetSelectableCardHolders(screen).Count > 0 || FindCardSelectionActionButton(screen) is not null;
    }

    private static bool TryFinalizeCardSelection(Node screen)
    {
        if (TryHandleDeckCardSelectionPreviewConfirmation(screen))
        {
            return true;
        }

        var confirmButton = FindConfirmButton(screen);
        if (confirmButton is not null && IsActionButtonEnabled(confirmButton))
        {
            var applied = Click(confirmButton);
            BridgeLoop.Log($"card selection confirm button type={confirmButton.GetType().FullName} name={confirmButton.Name} applied={applied}");
            if (applied)
            {
                return true;
            }
        }

        if (TryInvokeCardSelectionConfirmMethod(screen))
        {
            return true;
        }

        return false;
    }

    private static bool TrySkipCardReward(NCardRewardSelectionScreen screen)
    {
        var skipButton = FindSkipButton(screen);
        if (skipButton is not null && Click(skipButton))
        {
            MarkUiTransitionPending("card_reward_skip_button", CardRewardSelectionSettleDelay);
            BridgeLoop.Log($"card reward skip via button screen={screen.GetType().FullName}");
            return true;
        }

        var alternativeButton = FindCardRewardAlternativeButton(screen);
        if (alternativeButton is not null && Click(alternativeButton))
        {
            MarkUiTransitionPending("card_reward_skip_alternative", CardRewardSelectionSettleDelay);
            BridgeLoop.Log(
                $"card reward skip via alternative screen={screen.GetType().FullName} controls={DescribeVisibleControlsSummary(6)}");
            return true;
        }

        try
        {
            if (CardRewardAlternateSelectedMethod is null)
            {
                throw new MissingMethodException(screen.GetType().FullName, "OnAlternateRewardSelected");
            }

            CardRewardAlternateSelectedMethod.Invoke(screen, [PostAlternateCardRewardAction.DismissScreenAndKeepReward]);
            MarkUiTransitionPending("card_reward_skip_invoke", CardRewardSelectionSettleDelay);
            BridgeLoop.Log($"card reward skip invoke action=DismissScreenAndKeepReward screen={screen.GetType().FullName}");
            return true;
        }
        catch (Exception ex)
        {
            BridgeLoop.Log(
                $"card reward skip failed screen={screen.GetType().FullName} ex={ex.Message} " +
                $"controls={DescribeVisibleControlsSummary(6)}");
            return false;
        }
    }

    private static bool TryInvokeCardRewardSelection(NCardRewardSelectionScreen screen, NCardHolder holder, CardModel targetCard)
    {
        try
        {
            CardRewardSelectMethod.Invoke(screen, [holder]);
            MarkUiTransitionPending("card_reward_pick_invoke", CardRewardSelectionSettleDelay);
            LogCardRewardDecisionOutcome("invoke", screen, targetCard, holder);
            return true;
        }
        catch (Exception ex)
        {
            BridgeLoop.Log(
                $"card reward invoke failed target={targetCard.Id.Entry} chosen={holder.CardModel?.Id.Entry ?? "<null>"} " +
                $"screen={screen.GetType().FullName} ex={ex.Message}");
            return false;
        }
    }

    private static void LogCardRewardDecisionOutcome(string source, NCardRewardSelectionScreen screen, CardModel targetCard, NCardHolder holder)
    {
        var currentScreen = GetCurrentScreenSafe("card_reward_outcome");
        var stillVisible = ReferenceEquals(currentScreen, screen) || currentScreen is NCardRewardSelectionScreen;
        BridgeLoop.Log(
            $"card reward {source} target={targetCard.Id.Entry} chosen={holder.CardModel?.Id.Entry ?? "<null>"} " +
            $"still_visible={stillVisible} current_screen={currentScreen?.GetType().FullName ?? "<null>"} " +
            $"controls={DescribeVisibleControlsSummary(6)}");
    }

    private static bool TryHandleDeckCardSelectionPreviewConfirmation(Node screen)
    {
        var previewConfirmButton = FindDeckCardSelectionPreviewConfirmButton(screen, requireEnabled: true);
        if (previewConfirmButton is null)
        {
            return false;
        }

        var applied = Click(previewConfirmButton);
        BridgeLoop.Log(
            $"deck card selection preview confirm type={previewConfirmButton.GetType().FullName} " +
            $"name={previewConfirmButton.Name} applied={applied}");
        return applied;
    }

    private static bool TryInvokeCardSelectionConfirmMethod(Node screen)
    {
        foreach (var methodName in new[] { "ConfirmSelection" })
        {
            var method = screen.GetType().GetMethod(methodName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (method is null || method.GetParameters().Length != 0)
            {
                continue;
            }

            try
            {
                method.Invoke(screen, null);
                BridgeLoop.Log($"card selection confirm invoke method={method.Name} screen={screen.GetType().FullName}");
                return true;
            }
            catch
            {
            }
        }

        return false;
    }

    private static bool IsConfirmButtonCandidate(Node node)
    {
        var typeName = node.GetType().Name;
        var nodeName = node.Name.ToString();
        return typeName.Contains("ConfirmButton", StringComparison.OrdinalIgnoreCase)
            || nodeName.Contains("PreviewConfirm", StringComparison.OrdinalIgnoreCase);
    }

    private static int ScoreConfirmButton(Node node)
    {
        var score = 0;
        var nodeName = node.Name.ToString();

        if (node is CanvasItem canvasItem && canvasItem.Visible)
        {
            score += 500;
        }

        if (IsActionButtonEnabled(node))
        {
            score += 1000;
        }

        if (nodeName.Contains("PreviewConfirm", StringComparison.OrdinalIgnoreCase))
        {
            score += 200;
        }
        else if (nodeName.Contains("Confirm", StringComparison.OrdinalIgnoreCase))
        {
            score += 100;
        }

        return score;
    }

    private static bool IsPreviewCardHolder(NCardHolder holder)
    {
        for (Node? current = holder; current is not null; current = current.GetParent())
        {
            var typeName = current.GetType().Name;
            var nodeName = current.Name.ToString();
            if (typeName.Contains("Preview", StringComparison.OrdinalIgnoreCase)
                || nodeName.Contains("Preview", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    private static bool IsVisibleNode(Node node)
        => node is not CanvasItem canvasItem || canvasItem.Visible;

    private static bool CardsMatch(CardModel left, CardModel right)
        => ReferenceEquals(left, right)
            || (left.Id.Entry == right.Id.Entry && left.IsUpgraded == right.IsUpgraded);

    private static bool CanClick(object target)
    {
        var type = target.GetType();
        return type.GetMethod("ForceClick", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic) is not null
            || type.GetMethod("OnPress", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic) is not null
            || type.GetMethod("OnRelease", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic) is not null;
    }

    private static bool IsActionButtonEnabled(object target)
    {
        try
        {
            var type = target.GetType();
            var isEnabledProperty = type.GetProperty("IsEnabled", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (isEnabledProperty?.GetValue(target) is bool isEnabled)
            {
                return isEnabled;
            }

            var disabledProperty = type.GetProperty("Disabled", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (disabledProperty?.GetValue(target) is bool disabled)
            {
                return !disabled;
            }
        }
        catch
        {
        }

        return true;
    }

    private static bool TryInvokeCardSelectionMethod(Node screen, NCardHolder holder)
    {
        if (screen is NDeckCardSelectScreen)
        {
            return TryInvokeNamedCardSelectionMethod(screen, holder, "PreviewSelection");
        }

        foreach (var method in screen.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
        {
            if (method.GetParameters().Length != 1)
            {
                continue;
            }

            if (!method.Name.Contains("Select", StringComparison.OrdinalIgnoreCase)
                && !method.Name.Contains("Choose", StringComparison.OrdinalIgnoreCase)
                && !method.Name.Contains("Pick", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            if (method.Name.StartsWith("Can", StringComparison.OrdinalIgnoreCase)
                || method.Name.StartsWith("Is", StringComparison.OrdinalIgnoreCase)
                || method.Name.StartsWith("Get", StringComparison.OrdinalIgnoreCase)
                || method.Name.StartsWith("Check", StringComparison.OrdinalIgnoreCase)
                || method.Name.StartsWith("Refresh", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var parameterType = method.GetParameters()[0].ParameterType;
            try
            {
                if (parameterType.IsInstanceOfType(holder))
                {
                    var result = method.Invoke(screen, [holder]);
                    BridgeLoop.Log($"card selection invoke method={method.Name} arg={parameterType.FullName} screen={screen.GetType().FullName}");
                    return result is not bool applied || applied;
                }

                if (holder.CardModel is not null && parameterType.IsInstanceOfType(holder.CardModel))
                {
                    var result = method.Invoke(screen, [holder.CardModel]);
                    BridgeLoop.Log($"card selection invoke method={method.Name} arg={parameterType.FullName} screen={screen.GetType().FullName}");
                    return result is not bool applied || applied;
                }
            }
            catch
            {
            }
        }

        return false;
    }

    private static bool IsDeckCardSelectionPreviewActive(Node screen)
    {
        if (screen is not NDeckCardSelectScreen)
        {
            return false;
        }

        return GetChildrenRecursive(screen)
            .OfType<NCardHolder>()
            .Any(holder => holder.CardModel is not null && IsPreviewCardHolder(holder));
    }

    private static bool IsDeckCardSelectionPreviewReady(Node screen)
        => FindDeckCardSelectionPreviewConfirmButton(screen, requireEnabled: true) is not null;

    private static Node? FindDeckCardSelectionPreviewConfirmButton(Node screen, bool requireEnabled)
    {
        if (screen is not NDeckCardSelectScreen)
        {
            return null;
        }

        return GetChildrenRecursive(screen).FirstOrDefault(node =>
            string.Equals(node.Name.ToString(), "PreviewConfirm", StringComparison.OrdinalIgnoreCase)
            && node is CanvasItem canvasItem
            && canvasItem.Visible
            && (!requireEnabled || IsActionButtonEnabled(node)));
    }

    private static bool TryInvokeNamedCardSelectionMethod(Node screen, NCardHolder holder, string methodName)
    {
        foreach (var method in screen.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
        {
            if (!string.Equals(method.Name, methodName, StringComparison.OrdinalIgnoreCase)
                || method.GetParameters().Length != 1)
            {
                continue;
            }

            var parameterType = method.GetParameters()[0].ParameterType;
            try
            {
                if (parameterType.IsInstanceOfType(holder))
                {
                    var result = method.Invoke(screen, [holder]);
                    BridgeLoop.Log($"card selection invoke method={method.Name} arg={parameterType.FullName} screen={screen.GetType().FullName}");
                    return result is not bool applied || applied;
                }

                if (holder.CardModel is not null && parameterType.IsInstanceOfType(holder.CardModel))
                {
                    var result = method.Invoke(screen, [holder.CardModel]);
                    BridgeLoop.Log($"card selection invoke method={method.Name} arg={parameterType.FullName} screen={screen.GetType().FullName}");
                    return result is not bool applied || applied;
                }
            }
            catch
            {
            }
        }

        return false;
    }

    private static bool TryGetCardSelectionPrefs(Node screen, out int? minSelect, out int? maxSelect, out bool? requireManualConfirmation)
    {
        minSelect = null;
        maxSelect = null;
        requireManualConfirmation = null;

        object? prefs = null;
        if (screen is NDeckCardSelectScreen deckCardSelectScreen)
        {
            prefs = DeckCardSelectPrefsField?.GetValue(deckCardSelectScreen);
        }

        prefs ??= ReadFieldRecursive(screen, "_prefs");
        if (prefs is null)
        {
            return false;
        }

        minSelect = ReadIntMember(prefs, "MinSelect");
        maxSelect = ReadIntMember(prefs, "MaxSelect");
        requireManualConfirmation = ReadBoolMember(prefs, "RequireManualConfirmation");
        return true;
    }

    private static object? ReadFieldRecursive(object target, string name)
        => FindInstanceField(target.GetType(), name)?.GetValue(target);

    private static FieldInfo? FindInstanceField(Type type, string name)
    {
        for (var current = type; current is not null; current = current.BaseType)
        {
            var field = current.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field is not null)
            {
                return field;
            }
        }

        return null;
    }

    private static List<CardModel> ReadSelectedCardModels(object? source)
    {
        if (source is null)
        {
            return [];
        }

        if (source is IEnumerable<CardModel> typed)
        {
            return typed.ToList();
        }

        if (source is not IEnumerable enumerable)
        {
            return [];
        }

        var result = new List<CardModel>();
        foreach (var item in enumerable)
        {
            if (item is CardModel card)
            {
                result.Add(card);
            }
        }

        return result;
    }

    private static int ScoreGameOverControl(Control control)
    {
        var typeName = control.GetType().FullName ?? control.GetType().Name;
        var nodeName = control.Name.ToString();
        var score = 0;

        if (typeName.Contains("ProceedButton", StringComparison.OrdinalIgnoreCase))
        {
            score += 100;
        }
        else if (typeName.Contains("ConfirmButton", StringComparison.OrdinalIgnoreCase))
        {
            score += 90;
        }
        else if (typeName.Contains("CloseButton", StringComparison.OrdinalIgnoreCase))
        {
            score += 80;
        }
        else if (typeName.Contains("BackButton", StringComparison.OrdinalIgnoreCase))
        {
            score += 70;
        }
        else if (typeName.Contains("Button", StringComparison.OrdinalIgnoreCase))
        {
            score += 40;
        }

        if (nodeName.Contains("Proceed", StringComparison.OrdinalIgnoreCase)
            || nodeName.Contains("Continue", StringComparison.OrdinalIgnoreCase)
            || nodeName.Contains("Retry", StringComparison.OrdinalIgnoreCase)
            || nodeName.Contains("Restart", StringComparison.OrdinalIgnoreCase))
        {
            score += 30;
        }

        if (nodeName.Contains("Close", StringComparison.OrdinalIgnoreCase)
            || nodeName.Contains("Back", StringComparison.OrdinalIgnoreCase)
            || nodeName.Contains("Return", StringComparison.OrdinalIgnoreCase)
            || nodeName.Contains("Menu", StringComparison.OrdinalIgnoreCase))
        {
            score += 20;
        }

        return score;
    }

    private static bool Click(object target)
    {
        Interlocked.Increment(ref SyntheticClickDepth);
        try
        {
            var clickMethod = target.GetType().GetMethod("ForceClick", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (clickMethod is not null)
            {
                clickMethod.Invoke(target, null);
                return true;
            }

            var pressMethod = target.GetType().GetMethod("OnPress", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            var releaseMethod = target.GetType().GetMethod("OnRelease", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (pressMethod is null && releaseMethod is null)
            {
                BridgeLoop.Log($"click rejected: no click method type={target.GetType().FullName}");
                return false;
            }

            pressMethod?.Invoke(target, null);
            releaseMethod?.Invoke(target, null);
            return true;
        }
        finally
        {
            Interlocked.Decrement(ref SyntheticClickDepth);
        }
    }

    private static bool ClickMapPoint(NMapPoint mapPoint)
    {
        if (Click(mapPoint))
        {
            return true;
        }

        var onSelectedMethod = mapPoint.GetType().GetMethod("OnSelected", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (onSelectedMethod is null)
        {
            BridgeLoop.Log($"map click rejected: no selectable method type={mapPoint.GetType().FullName}");
            return false;
        }

        onSelectedMethod.Invoke(mapPoint, null);
        BridgeLoop.Log($"map click fallback onselected type={mapPoint.GetType().FullName}");
        return true;
    }

    private static object? GetCurrentScreenSafe(string origin)
    {
        try
        {
            var context = ActiveScreenContext.Instance;
            if (context is null)
            {
                BridgeLoop.Log($"active screen context missing origin={origin}");
                return null;
            }

            return context.GetCurrentScreen();
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"get current screen failed origin={origin} ex={ex}");
            return null;
        }
    }

    private static List<CardPayload> BuildCards(IEnumerable<CardModel>? cards) => cards?.Select(BuildCardPayload).ToList() ?? [];

    private static CardPayload BuildCardPayload(CardModel card)
    {
        var affliction = TryGetCardAffliction(card);
        var hasRetainKeyword = TryHasCardKeyword(card, "Retain");
        var hasSlyKeyword = TryHasCardKeyword(card, "Sly");
        return new CardPayload
        {
            Id = card.Id.Entry,
            Upgraded = card.IsUpgraded,
            Cost = card.GetStarCostThisCombat(),
            Type = card.Type.ToString(),
            Target = card.TargetType.ToString(),
            Damage = TryGetDynamicVarInt(card, "Damage", "CalculatedDamage", "OstyDamage"),
            Block = TryGetDynamicVarInt(card, "Block", "CalculatedBlock"),
            Draw = TryGetDynamicVarInt(card, "Draw"),
            Keywords = TryGetCardKeywords(card),
            Tags = TryGetCardTags(card),
            Pool = TryGetCardPool(card),
            Vars = TryGetCardVars(card),
            ReplayCount = TryGetCardReplayCount(card),
            RetainThisTurn = card.ShouldRetainThisTurn && !hasRetainKeyword ? true : null,
            SlyThisTurn = card.IsSlyThisTurn && !hasSlyKeyword ? true : null,
            AfflictionId = affliction?.Id.Entry,
            AfflictionAmount = affliction?.Amount,
        };
    }

    private static int? TryGetDynamicVarInt(CardModel card, params string[] names)
    {
        try
        {
            foreach (var name in names)
            {
                if (card.DynamicVars.ContainsKey(name))
                {
                    return card.DynamicVars[name].IntValue;
                }
            }
        }
        catch
        {
        }

        return null;
    }

    private static List<string>? TryGetCardKeywords(CardModel card)
    {
        try
        {
            return card.Keywords.Select(x => x.ToString()).ToList();
        }
        catch
        {
            return null;
        }
    }

    private static List<string>? TryGetCardTags(CardModel card)
    {
        try
        {
            return card.Tags.Select(x => x.ToString()).ToList();
        }
        catch
        {
            return null;
        }
    }

    private static bool TryHasCardKeyword(CardModel card, string keyword)
    {
        try
        {
            return card.Keywords.Any(x => string.Equals(x.ToString(), keyword, StringComparison.Ordinal));
        }
        catch
        {
            return false;
        }
    }

    private static string? TryGetCardPool(CardModel card)
    {
        try
        {
            var title = card.Pool.Title;
            if (string.IsNullOrWhiteSpace(title))
            {
                return null;
            }

            return char.ToUpperInvariant(title[0]) + title[1..];
        }
        catch
        {
            return null;
        }
    }

    private static Dictionary<string, int>? TryGetCardVars(CardModel card)
    {
        try
        {
            return card.DynamicVars.ToDictionary(x => x.Key, x => x.Value.IntValue);
        }
        catch
        {
            return null;
        }
    }

    private static int? TryGetCardReplayCount(CardModel card)
    {
        try
        {
            var replayCount = card.GetEnchantedReplayCount();
            return replayCount != 0 ? replayCount : null;
        }
        catch
        {
            return null;
        }
    }

    private static AfflictionModel? TryGetCardAffliction(CardModel card)
    {
        try
        {
            return card.Affliction;
        }
        catch
        {
            return null;
        }
    }

    private static List<bool> BuildPlayableCards(Player player)
    {
        var hand = player.PlayerCombatState?.Hand?.Cards;
        if (hand is null)
        {
            return [];
        }

        return hand.Select(card =>
        {
            try
            {
                return card.CanPlay();
            }
            catch
            {
                return false;
            }
        }).ToList();
    }

    private static CombatHandSelectionState? TryGetCombatHandSelectionState(Player player)
    {
        var hand = NRun.Instance?.CombatRoom?.Ui?.Hand;
        if (hand is null || !hand.IsInCardSelection || hand.CurrentMode == NPlayerHand.Mode.None)
        {
            return null;
        }

        var handCards = player.PlayerCombatState?.Hand?.Cards?.ToList();
        if (handCards is null)
        {
            return null;
        }

        var filter = PlayerHandCurrentSelectionFilterField.GetValue(hand) as Func<CardModel, bool>;
        var selectedCards = (PlayerHandSelectedCardsField.GetValue(hand) as IEnumerable<CardModel>)?.ToList() ?? [];
        var selectableCards = new List<bool>(handCards.Count);
        var selectedFlags = new List<bool>(handCards.Count);
        var prefs = PlayerHandPrefsField.GetValue(hand) is CardSelectorPrefs selectionPrefs
            ? selectionPrefs
            : default;

        var holders = GetCombatHandHolders(hand);
        foreach (var card in handCards)
        {
            var holder = holders.FirstOrDefault(x => x.CardModel is not null && ReferenceEquals(x.CardModel, card));
            var isSelected = selectedCards.Any(selected => ReferenceEquals(selected, card));
            var selectable = holder is not null
                && IsVisibleNode(holder)
                && !isSelected;

            if (selectable && filter is not null)
            {
                try
                {
                    selectable = filter(card);
                }
                catch
                {
                    selectable = false;
                }
            }

            selectedFlags.Add(isSelected);
            selectableCards.Add(selectable);
        }

        var confirmEnabled = FindConfirmButton(hand) is { } confirmButton && IsActionButtonEnabled(confirmButton);
        if (!selectableCards.Any(x => x) && !confirmEnabled)
        {
            return null;
        }

        return new CombatHandSelectionState
        {
            Hand = hand,
            Mode = hand.CurrentMode.ToString(),
            SelectableCards = selectableCards,
            SelectedCards = selectedFlags,
            ConfirmEnabled = confirmEnabled,
            SelectedCount = selectedCards.Count,
            MinSelect = prefs.MinSelect,
            MaxSelect = prefs.MaxSelect,
            ManualConfirm = prefs.RequireManualConfirmation,
        };
    }

    private static string DescribeCombatHandSelectionDiagnostics(Player player)
    {
        try
        {
            var hand = NRun.Instance?.CombatRoom?.Ui?.Hand;
            if (hand is null)
            {
                return "hand_state=unavailable";
            }

            var handCards = player.PlayerCombatState?.Hand?.Cards?.ToList() ?? [];
            var holders = GetCombatHandHolders(hand);
            var selectedCards = (PlayerHandSelectedCardsField.GetValue(hand) as IEnumerable<CardModel>)?.ToList() ?? [];
            var prefs = PlayerHandPrefsField.GetValue(hand) is CardSelectorPrefs selectionPrefs
                ? selectionPrefs
                : default;
            var confirmEnabled = FindConfirmButton(hand) is { } confirmButton && IsActionButtonEnabled(confirmButton);

            return
                $"hand_mode={hand.CurrentMode} in_selection={hand.IsInCardSelection} peeking={hand.PeekButton?.IsPeeking} " +
                $"hand_cards={handCards.Count} holders={holders.Count} visible_holders={holders.Count(IsVisibleNode)} " +
                $"selected={selectedCards.Count} min={prefs.MinSelect} max={prefs.MaxSelect} " +
                $"manual_confirm={prefs.RequireManualConfirmation} confirm_enabled={confirmEnabled}";
        }
        catch (Exception ex)
        {
            return $"hand_state_error={ex.Message}";
        }
    }

    private static List<NHandCardHolder> GetCombatHandHolders(NPlayerHand hand)
    {
        try
        {
            foreach (var propertyName in new[] { "Holders", "ActiveHolders" })
            {
                var property = hand.GetType().GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (property?.GetValue(hand) is IEnumerable<NHandCardHolder> holders)
                {
                    return holders.Where(x => x.CardModel is not null).ToList();
                }
            }
        }
        catch
        {
        }

        return GetChildrenRecursive(hand)
            .OfType<NHandCardHolder>()
            .Where(x => x.CardModel is not null)
            .ToList();
    }

    private static List<PowerPayload> BuildPowers(IEnumerable<PowerModel> powers) => powers.Select(x => new PowerPayload
    {
        Id = x.Id.Entry,
        Amount = x.Amount,
    }).ToList();

    private static MonsterPayload BuildMonster(Creature creature) => new()
    {
        Name = creature.Name,
        Hp = creature.CurrentHp,
        MaxHp = creature.MaxHp,
        Block = creature.Block,
        IsDead = creature.IsDead,
        Powers = BuildPowers(creature.Powers),
    };

    private static OptionPayload ToOptionPayload(EventOption option) => new()
    {
        Id = option.TextKey,
        Title = SafeText(option.Title, option.TextKey),
        Enabled = !option.IsLocked && !option.WasChosen,
    };

    private static StateEnvelope BuildStateWithContext(string phase, RunState state, object? screen, Func<StateEnvelope> builder)
    {
        try
        {
            return builder();
        }
        catch (Exception ex)
        {
            var roomType = state.CurrentRoom?.GetType().FullName ?? "<null>";
            var screenType = screen?.GetType().FullName ?? "<null>";
            var mapCoord = state.CurrentMapCoord;
            var coord = mapCoord.HasValue ? $"{mapCoord.Value.row},{mapCoord.Value.col}" : "<null>";
            if (phase == "event" && ex is InvalidOperationException && ex.Message == "event options unavailable")
            {
                TryKickEventProceedIfNeeded();
                RateLimitedLog(
                    "build_state_deferred_event",
                    TimeSpan.FromSeconds(2),
                    $"build_state deferred phase=event room={roomType} screen={screenType} map_coord={coord}");
                return null!;
            }
            BridgeLoop.Log($"build_state failed phase={phase} room={roomType} screen={screenType} map_coord={coord} ex={ex}");
            throw;
        }
    }

    private static List<OptionPayload> BuildEventOptions(EventRoom eventRoom)
    {
        var result = new List<OptionPayload>();
        IReadOnlyList<EventOption> options;
        try
        {
            options = eventRoom.LocalMutableEvent.CurrentOptions.ToList();
        }
        catch (Exception ex)
        {
            RateLimitedLog("event_options_unavailable", TimeSpan.FromSeconds(2), $"event options unavailable: {ex}");
            return BuildEventOptionsFromUi();
        }

        if (options.Count == 0)
        {
            RateLimitedLog("event_options_empty", TimeSpan.FromSeconds(1), "event options empty; falling back to UI");
            return BuildEventOptionsFromUi();
        }

        for (var i = 0; i < options.Count; i++)
        {
            try
            {
                result.Add(ToOptionPayload(options[i]));
            }
            catch (Exception ex)
            {
                BridgeLoop.Log($"event option failed index={i}: {ex}");
                result.Add(new OptionPayload
                {
                    Id = $"event_option_{i}",
                    Title = $"event_option_{i}",
                    Enabled = false,
                });
            }
        }

        return result;
    }

    private static List<OptionPayload> BuildEventOptionsFromUi()
    {
        var controls = ModBootstrap.ListVisibleClickableControls();
        var controlOptions = new List<OptionPayload>();
        foreach (var control in controls.Where(IsEventOptionControl))
        {
            var id = ReadControlString(control, "option_id")
                ?? ReadControlString(control, "text_key")
                ?? ReadControlString(control, "node_path")
                ?? $"event_option_{controlOptions.Count}";
            var title = ReadControlString(control, "title")
                ?? ReadControlString(control, "text")
                ?? id;

            controlOptions.Add(new OptionPayload
            {
                Id = id,
                Title = title,
                Enabled = ReadControlBool(control, "enabled") != false && ReadControlBool(control, "visible") != false,
            });
        }

        if (controlOptions.Count > 0)
        {
            return controlOptions;
        }

        var buttons = GetEventOptionButtonsFromUi();
        var result = new List<OptionPayload>();
        foreach (var button in buttons)
        {
            var option = button.Option;
            var textKey = option?.TextKey;
            var fallbackId = string.IsNullOrWhiteSpace(textKey) ? $"event_option_{result.Count}" : textKey;

            result.Add(new OptionPayload
            {
                Id = fallbackId,
                Title = SafeText(option?.Title, fallbackId),
                Enabled = button.IsEnabled && button.Visible,
            });
        }

        if (result.Count == 0)
        {
            var screenType = GetCurrentScreenSafe("event_options_ui_empty")?.GetType().FullName ?? "<null>";
            RateLimitedLog(
                "event_options_ui_empty",
                TimeSpan.FromSeconds(1),
                $"event ui fallback empty screen={screenType} controls={controls.Count} buttons={buttons.Count} context={DescribeCurrentContext()}");
        }

        return result;
    }

    private static void RateLimitedLog(string key, TimeSpan interval, string message)
    {
        lock (RateLimitedLogUtc)
        {
            var now = DateTime.UtcNow;
            if (RateLimitedLogUtc.TryGetValue(key, out var lastLoggedUtc) && now - lastLoggedUtc < interval)
            {
                return;
            }

            RateLimitedLogUtc[key] = now;
        }

        BridgeLoop.Log(message);
    }

    private static void TryKickEventProceedIfNeeded()
    {
        var now = DateTime.UtcNow;
        if (now < NextEventProceedAttemptUtc)
        {
            return;
        }

        NextEventProceedAttemptUtc = now.AddMilliseconds(500);
        if (TryHandleEventProceedAutomation())
        {
            return;
        }

        try
        {
            _ = (Task)AutoSlayerClickEventProceedMethod.Invoke(AutoSlayer, [CancellationToken.None])!;
            RateLimitedLog("event_proceed_autoslayer", TimeSpan.FromSeconds(1), "event proceed delegated to autoslayer");
        }
        catch (Exception ex)
        {
            RateLimitedLog("event_proceed_autoslayer_failed", TimeSpan.FromSeconds(2), $"event proceed autoslayer failed: {ex.Message}");
        }
    }

    private static bool TryHandleEventProceedAutomation()
    {
        var controls = ModBootstrap.ListVisibleClickableControls()
            .Where(IsEventOptionControl)
            .ToList();
        var proceedControls = controls
            .Where(control =>
                string.Equals(ReadControlString(control, "option_id") ?? ReadControlString(control, "text_key"), "PROCEED", StringComparison.OrdinalIgnoreCase))
            .ToList();
        if (proceedControls.Count != 1)
        {
            return false;
        }

        var buttons = GetEventOptionButtonsFromUi();
        var proceedButtons = buttons
            .Where(button =>
                string.Equals(button.Option?.TextKey, "PROCEED", StringComparison.OrdinalIgnoreCase))
            .ToList();
        if (proceedButtons.Count == 0 && buttons.Count == 1)
        {
            proceedButtons.Add(buttons[0]);
        }

        if (proceedButtons.Count != 1)
        {
            return false;
        }

        var button = proceedButtons[0];
        var clicked = Click(button);
        if (clicked)
        {
            RateLimitedLog("event_proceed_automation", TimeSpan.FromSeconds(1), "event proceed auto-clicked");
        }

        return clicked;
    }

    private static bool TryHandleRestProceedAutomation(RestSiteRoom restRoom)
    {
        if (NOverlayStack.Instance?.ScreenCount > 0)
        {
            return false;
        }

        if (restRoom.Options.Count > 0 || GetRestSiteButtonsFromUi().Count > 0)
        {
            return false;
        }

        var room = GetCurrentScreenSafe("rest_proceed_automation") as NRestSiteRoom;
        var proceedButton = room?.ProceedButton;
        if (proceedButton?.Visible != true || proceedButton.IsEnabled != true)
        {
            return false;
        }

        var clicked = Click(proceedButton);
        if (clicked)
        {
            MarkUiTransitionPending("rest_site_proceed", GenericCardSelectionSettleDelay);
            RateLimitedLog("rest_proceed_automation", TimeSpan.FromSeconds(1), "rest site auto-clicked proceed");
        }

        return clicked;
    }

    private static List<NEventOptionButton> GetEventOptionButtonsFromUi()
    {
        var root = GetCurrentScreenSafe("event_option_buttons_ui") as Node
            ?? NGame.Instance?.CurrentRunNode as Node;
        if (root is null)
        {
            return [];
        }

        return GetChildrenRecursive(root)
            .OfType<NEventOptionButton>()
            .Where(button => button.Visible)
            .ToList();
    }

    private static bool IsEventOptionControl(Dictionary<string, object?> control)
    {
        var controlType = ReadControlString(control, "control_type");
        return !string.IsNullOrWhiteSpace(controlType)
            && controlType.EndsWith(".NEventOptionButton", StringComparison.Ordinal);
    }

    private static List<(NMapPoint control, MapPoint point, int row, int col, bool enabled)> GetUiMapPointChoices(RunState state)
    {
        var root = GetCurrentScreenSafe("map_points_ui") as Node
            ?? NGame.Instance?.CurrentRunNode as Node;
        if (root is null)
        {
            return [];
        }

        var currentCoord = state.CurrentMapCoord;
        var targetRow = currentCoord?.row + 1 ?? 0;
        var currentPoint = state.CurrentMapPoint;

        var allVisiblePoints = GetChildrenRecursive(root)
            .OfType<NMapPoint>()
            .Where(mapPoint => mapPoint.Visible)
            .Select(mapPoint =>
            {
                var details = DescribeMapPointSelection(mapPoint.Point);
                var row = ReadControlInt(details, "row");
                var col = ReadControlInt(details, "col");
                return new
                {
                    Control = mapPoint,
                    Point = mapPoint.Point,
                    Row = row,
                    Col = col,
                    Enabled = mapPoint.IsEnabled || ReadBoolMember(mapPoint, "IsTravelable") == true,
                };
            })
            .Where(item => item.Row is not null && item.Col is not null)
            .Select(item => (control: item.Control, point: item.Point, row: item.Row!.Value, col: item.Col!.Value, enabled: item.Enabled))
            .ToList();

        var directChoices = allVisiblePoints
            .Where(item => item.row == targetRow)
            .Where(item => currentPoint is null || currentPoint.Children.Contains(item.point))
            .GroupBy(item => (item.row, item.col))
            .Select(group => group.OrderByDescending(item => item.enabled).First())
            .OrderByDescending(item => item.enabled)
            .ThenBy(item => item.col)
            .Take(4)
            .ToList();
        if (directChoices.Count > 0)
        {
            return directChoices;
        }

        return allVisiblePoints
            .GroupBy(item => (item.row, item.col))
            .Select(group => group.OrderByDescending(item => item.enabled).First())
            .OrderByDescending(item => item.enabled)
            .ThenBy(item => item.row)
            .ThenBy(item => item.col)
            .Take(4)
            .ToList();
    }

    private static bool IsMapDecisionPending(RunState state)
    {
        var key = GetMapDecisionStateKey(state);
        var now = DateTime.UtcNow;
        if (PendingMapDecisionStateKey != key)
        {
            return false;
        }

        if (now - PendingMapDecisionUtc >= MapDecisionPendingTimeout)
        {
            BridgeLoop.Log($"map decision pending expired key={key} origin={PendingMapDecisionOrigin}");
            ClearMapDecisionGuard();
            return false;
        }

        RateLimitedLog(
            "map_decision_pending",
            TimeSpan.FromMilliseconds(500),
            $"map decision pending key={key} origin={PendingMapDecisionOrigin}");
        return true;
    }

    private static void MarkMapDecisionIssued(RunState state, int row, int col, string origin)
    {
        PendingMapDecisionStateKey = GetMapDecisionStateKey(state);
        PendingMapDecisionUtc = DateTime.UtcNow;
        PendingMapDecisionOrigin = origin;
        BridgeLoop.Log($"map decision issued origin={origin} target={row},{col} state_key={PendingMapDecisionStateKey}");
    }

    private static string GetMapDecisionStateKey(RunState state)
    {
        var coord = state.CurrentMapCoord;
        return $"{state.CurrentActIndex}:{state.TotalFloor}:{FormatMapCoord(coord)}";
    }

    private static void RefreshMapDecisionGuard(RunState state, object? screen)
    {
        var currentKey = screen is NMapScreen ? GetMapDecisionStateKey(state) : null;
        if (PendingMapDecisionStateKey is null)
        {
            return;
        }

        if (currentKey == PendingMapDecisionStateKey)
        {
            return;
        }

        ClearMapDecisionGuard();
    }

    private static void ClearMapDecisionGuard()
    {
        PendingMapDecisionStateKey = null;
        PendingMapDecisionOrigin = null;
        PendingMapDecisionUtc = DateTime.MinValue;
    }

    private static string FormatMapCoord(MapCoord? coord) =>
        coord.HasValue ? $"{coord.Value.row},{coord.Value.col}" : "null,null";

    private static void MarkUiTransitionPending(string reason, TimeSpan duration)
    {
        PendingUiTransitionReason = reason;
        PendingUiTransitionUntilUtc = DateTime.UtcNow.Add(duration);
    }

    public static string DescribeVisibleControlsSummary(int limit = 8)
    {
        try
        {
            var trace = GetTraceContextSnapshot();
            var availableControls = ModBootstrap.ListVisibleClickableControls();
            var actionableControls = ModBootstrap.ListActionableControls(trace, availableControls);
            var summary = string.Join(" | ", actionableControls.Take(limit).Select(FormatControlSummary));
            return $"visible={availableControls.Count} actionable={actionableControls.Count} [{summary}]";
        }
        catch (Exception ex)
        {
            return $"controls_summary_failed={ex.Message}";
        }
    }

    private static string FormatControlSummary(Dictionary<string, object?> control)
    {
        var actionHint = ReadControlString(control, "action_hint")
            ?? ReadControlString(control, "control_type")
            ?? "<unknown>";
        var label = ReadControlString(control, "card_id")
            ?? ReadControlString(control, "option_id")
            ?? ReadControlString(control, "relic_id")
            ?? ReadControlString(control, "reward_type")
            ?? ReadControlString(control, "room_type")
            ?? ReadControlString(control, "node_name")
            ?? "<item>";
        return $"{actionHint}:{label}";
    }

    private static string? ReadControlString(Dictionary<string, object?> control, string key)
    {
        if (!control.TryGetValue(key, out var value))
        {
            return null;
        }

        return value switch
        {
            null => null,
            string text when string.IsNullOrWhiteSpace(text) => null,
            string text => text,
            _ => value.ToString(),
        };
    }

    private static bool? ReadControlBool(Dictionary<string, object?> control, string key)
    {
        if (!control.TryGetValue(key, out var value))
        {
            return null;
        }

        return value switch
        {
            bool flag => flag,
            null => null,
            _ => bool.TryParse(value.ToString(), out var parsed) ? parsed : null,
        };
    }

    private static bool? ReadBoolMember(object target, string memberName)
    {
        try
        {
            var type = target.GetType();
            var property = type.GetProperty(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property?.GetValue(target) is bool propertyValue)
            {
                return propertyValue;
            }

            var getter = type.GetMethod($"get_{memberName}", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (getter?.Invoke(target, null) is bool getterValue)
            {
                return getterValue;
            }
        }
        catch
        {
        }

        return null;
    }

    private static int? ReadIntMember(object? target, string memberName)
    {
        if (target is null)
        {
            return null;
        }

        try
        {
            var type = target.GetType();

            var property = type.GetProperty(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property?.GetValue(target) is int propertyValue)
            {
                return propertyValue;
            }

            var field = type.GetField(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field?.GetValue(target) is int fieldValue)
            {
                return fieldValue;
            }

            var getter = type.GetMethod($"get_{memberName}", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (getter?.Invoke(target, null) is int getterValue)
            {
                return getterValue;
            }
        }
        catch
        {
        }

        return null;
    }

    private static int? ReadControlInt(Dictionary<string, object?> control, string key)
    {
        if (!control.TryGetValue(key, out var value))
        {
            return null;
        }

        return value switch
        {
            int number => number,
            null => null,
            _ => int.TryParse(value.ToString(), out var parsed) ? parsed : null,
        };
    }

    private static List<OptionPayload> BuildRestOptions(RestSiteRoom restRoom)
    {
        var uiOptions = BuildRestOptionsFromUi();
        if (uiOptions.Count > 0)
        {
            return uiOptions;
        }

        var result = new List<OptionPayload>();
        var options = restRoom.Options.ToList();
        try
        {
            for (var i = 0; i < options.Count; i++)
            {
                var option = options[i];
                result.Add(new OptionPayload
                {
                    Id = NormalizeRestOptionId(option.OptionId),
                    Title = SafeText(option.Title, option.OptionId),
                    Enabled = option.IsEnabled,
                });
            }
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"rest options unavailable: {ex}");
        }

        if (result.Count == 0)
        {
            RateLimitedLog(
                "rest_options_empty",
                TimeSpan.FromSeconds(1),
                $"rest options empty room_options={options.Count} context={DescribeCurrentContext()}");
        }

        return result;
    }

    private static List<OptionPayload> BuildRestOptionsFromUi()
    {
        var controls = ModBootstrap.ListVisibleClickableControls();
        var controlOptions = new List<OptionPayload>();
        foreach (var control in controls.Where(IsRestOptionControl))
        {
            var fallbackId = NormalizeRestOptionId(
                ReadControlString(control, "option_id")
                ?? ReadControlString(control, "text_key")
                ?? ReadControlString(control, "node_path")
                ?? $"rest_option_{controlOptions.Count}");

            controlOptions.Add(new OptionPayload
            {
                Id = fallbackId,
                Title = NormalizeUiOptionTitle(ReadControlString(control, "title"), fallbackId),
                Enabled = ReadControlBool(control, "enabled") != false && ReadControlBool(control, "visible") != false,
            });
        }

        if (controlOptions.Count > 0)
        {
            return controlOptions;
        }

        var buttons = GetRestSiteButtonsFromUi();
        var buttonOptions = new List<OptionPayload>();
        foreach (var button in buttons)
        {
            var fallbackId = NormalizeRestOptionId(button.Option?.OptionId ?? $"rest_option_{buttonOptions.Count}");
            buttonOptions.Add(new OptionPayload
            {
                Id = fallbackId,
                Title = SafeText(button.Option?.Title, fallbackId),
                Enabled = button.IsEnabled && button.Visible,
            });
        }

        return buttonOptions;
    }

    private static bool TryClickRestOptionButton(string target)
    {
        var button = GetRestSiteButtonsFromUi().FirstOrDefault(button =>
            button.Visible
            && button.IsEnabled
            && NormalizeRestOptionId(button.Option?.OptionId ?? button.Name.ToString()) == target);
        if (button is null)
        {
            return false;
        }

        var clicked = Click(button);
        if (clicked)
        {
            BridgeLoop.Log(
                $"rest ui click target={target} option_id={button.Option?.OptionId ?? "<null>"} " +
                $"context={DescribeCurrentContext()}");
        }

        return clicked;
    }

    private static List<NRestSiteButton> GetRestSiteButtonsFromUi()
    {
        var root = GetCurrentScreenSafe("rest_option_buttons_ui") as Node
            ?? NGame.Instance?.CurrentRunNode as Node;
        if (root is null)
        {
            return [];
        }

        return GetChildrenRecursive(root)
            .OfType<NRestSiteButton>()
            .Where(button => button.Visible)
            .ToList();
    }

    private static bool IsRestOptionControl(Dictionary<string, object?> control)
    {
        var controlType = ReadControlString(control, "control_type");
        return !string.IsNullOrWhiteSpace(controlType)
            && controlType.EndsWith(".NRestSiteButton", StringComparison.Ordinal);
    }

    private static string NormalizeUiOptionTitle(string? title, string fallback)
    {
        if (string.IsNullOrWhiteSpace(title)
            || string.Equals(title, "<null>", StringComparison.OrdinalIgnoreCase)
            || title.Contains("LocString", StringComparison.OrdinalIgnoreCase))
        {
            return fallback;
        }

        return title;
    }

    private static string NormalizeRestOptionId(string optionId)
    {
        var lowered = optionId.ToLowerInvariant();
        if (lowered.Contains("heal"))
        {
            return "rest";
        }
        if (lowered.Contains("smith"))
        {
            return "upgrade";
        }
        if (lowered.Contains("dig"))
        {
            return "dig";
        }
        if (lowered.Contains("cook"))
        {
            return "cook";
        }
        if (lowered.Contains("lift"))
        {
            return "lift";
        }
        return lowered;
    }

    private static string MapPointTypeToRoomType(MapPointType pointType) => pointType switch
    {
        MapPointType.Shop => "shop",
        MapPointType.Treasure => "treasure",
        MapPointType.RestSite => "rest",
        MapPointType.Monster => "monster",
        MapPointType.Elite => "elite",
        MapPointType.Boss => "boss",
        MapPointType.Ancient => "event",
        _ => "monster",
    };

    private static int FindColumnInRow(RunState state, int row, MapPoint point)
    {
        var points = SafeGetPointsInRow(state, row).Select(x => x.point).ToList();
        return points.FindIndex(x => ReferenceEquals(x, point));
    }

    private static List<(MapPoint point, int col)> SafeGetPointsInRow(RunState state, int row)
    {
        try
        {
            return state.Map.GetPointsInRow(row).Select((point, col) => (point, col)).ToList();
        }
        catch
        {
            return [];
        }
    }

    private static string SafeText(object? value, string fallback)
    {
        if (value is null)
        {
            return fallback;
        }

        try
        {
            var text = value.ToString();
            return string.IsNullOrWhiteSpace(text) ? fallback : text;
        }
        catch
        {
            return fallback;
        }
    }

    private static bool IsInRange<T>(IReadOnlyList<T> list, int index) => index >= 0 && index < list.Count;

    private static string DeterminePhaseHint(RunState? state, object? screen)
    {
        if (screen is NCardRewardSelectionScreen)
        {
            return "card_reward";
        }

        if (screen is Node cardSelectionScreen && IsGenericCardSelectionScreen(cardSelectionScreen))
        {
            return "card_reward";
        }

        if (screen is NChooseARelicSelection)
        {
            return "boss_relic";
        }

        if (screen is NRewardsScreen)
        {
            return "reward_screen";
        }

        if (screen is NMapScreen)
        {
            return "map";
        }

        if (state?.IsGameOver == true)
        {
            return "game_over";
        }

        if (state?.CurrentRoom is CombatRoom)
        {
            return "combat";
        }

        return state?.CurrentRoom switch
        {
            EventRoom => "event",
            RestSiteRoom => "rest",
            MerchantRoom => "shop",
            TreasureRoom => "treasure",
            null => "none",
            _ => "map",
        };
    }

    private static void AttachCombatStateIfAvailable(StateEnvelope envelope, RunState state, Player player)
    {
        if (state.CurrentRoom is not CombatRoom combatRoom)
        {
            return;
        }

        envelope.State["combat"] = BuildCombatPhasePayload(player, combatRoom);
    }
}
