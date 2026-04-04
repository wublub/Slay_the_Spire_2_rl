using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Relics;
using MegaCrit.Sts2.Core.Nodes.RestSite;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Map;
using System.Reflection;

namespace Sts2RlBridge;

[ModInitializer(nameof(Initialize))]
public static class ModBootstrap
{
    private static Harmony? _harmony;

    public static void Initialize()
    {
        if (_harmony is not null)
        {
            return;
        }

        BridgeLoop.Log($"initialize base_dir={AppContext.BaseDirectory}");
        ClickTraceRecorder.InitializeSession();
        _harmony = new Harmony("local.sts2.rl.bridge");
        var runProcessOriginal = AccessTools.Method(typeof(NRun), nameof(NRun._Process));
        var runPostfix = AccessTools.Method(typeof(ModBootstrap), nameof(AfterRunProcess));
        _harmony.Patch(runProcessOriginal, postfix: new HarmonyMethod(runPostfix));

        var mainMenuReadyOriginal = AccessTools.Method(typeof(NMainMenu), nameof(NMainMenu._Ready));
        var mainMenuReadyPostfix = AccessTools.Method(typeof(ModBootstrap), nameof(AfterMainMenuReady));
        _harmony.Patch(mainMenuReadyOriginal, postfix: new HarmonyMethod(mainMenuReadyPostfix));

        var singleplayerOpenedOriginal = AccessTools.Method(typeof(NSingleplayerSubmenu), nameof(NSingleplayerSubmenu.OnSubmenuOpened));
        var singleplayerOpenedPostfix = AccessTools.Method(typeof(ModBootstrap), nameof(AfterSingleplayerSubmenuOpened));
        _harmony.Patch(singleplayerOpenedOriginal, postfix: new HarmonyMethod(singleplayerOpenedPostfix));

        var initSingleplayerOriginal = AccessTools.Method(typeof(NCharacterSelectScreen), nameof(NCharacterSelectScreen.InitializeSingleplayer));
        var initSingleplayerPostfix = AccessTools.Method(typeof(ModBootstrap), nameof(AfterCharacterSelectInitialized));
        _harmony.Patch(initSingleplayerOriginal, postfix: new HarmonyMethod(initSingleplayerPostfix));

        var charSelectProcessOriginal = AccessTools.Method(typeof(NCharacterSelectScreen), nameof(NCharacterSelectScreen._Process));
        var charSelectProcessPostfix = AccessTools.Method(typeof(ModBootstrap), nameof(AfterCharacterSelectProcess));
        _harmony.Patch(charSelectProcessOriginal, postfix: new HarmonyMethod(charSelectProcessPostfix));

        PatchRecordMethod(typeof(NClickableControl), "OnReleaseHandler", nameof(BeforeAnyClickableReleased));
        PatchRecordMethod(typeof(NProceedButton), "OnRelease", nameof(BeforeProceedButtonRelease));
        PatchRecordMethod(typeof(NEndTurnButton), "OnRelease", nameof(BeforeEndTurnButtonRelease));
        PatchRecordMethod(typeof(NRewardButton), "OnRelease", nameof(BeforeRewardButtonRelease));
        PatchRecordMethod(typeof(NButton), "OnPress", nameof(BeforeButtonPress));
        PatchRecordMethod(typeof(NMainMenu), nameof(NMainMenu.OpenSingleplayerSubmenu), nameof(BeforeOpenSingleplayerSubmenu));
        PatchRecordMethod(typeof(NSingleplayerSubmenu), "OpenCharacterSelect", nameof(BeforeOpenCharacterSelect));
        PatchRecordMethod(typeof(NCharacterSelectScreen), nameof(NCharacterSelectScreen.SelectCharacter), nameof(BeforeSelectCharacter));
        PatchRecordMethod(typeof(NCharacterSelectScreen), "StartNewSingleplayerRun", nameof(BeforeStartNewSingleplayerRun));
        PatchRecordMethod(typeof(RunManager), nameof(RunManager.EnterMapCoord), nameof(BeforeEnterMapCoord));
        PatchRecordMethod(typeof(MegaCrit.Sts2.Core.Nodes.Rooms.NEventRoom), "OptionButtonClicked", nameof(BeforeEventOptionClicked));
        PatchRecordMethod(typeof(CardModel), nameof(CardModel.TryManualPlay), nameof(BeforeTryManualPlay));
        PatchRecordMethod(typeof(NCardPlay), "TryPlayCard", nameof(BeforeTryPlayCardUi));
        PatchRecordMethod(typeof(PotionModel), nameof(PotionModel.EnqueueManualUse), nameof(BeforeEnqueueManualUse));
        PatchRecordMethod(typeof(NCardRewardSelectionScreen), "SelectCard", nameof(BeforeSelectCard));
        PatchRecordMethod(typeof(NChoiceSelectionSkipButton), "OnPress", nameof(BeforeChoiceSelectionSkipPress));
        PatchRecordMethod(typeof(NMapPoint), "OnSelected", nameof(BeforeMapPointSelected));
        PatchRecordMethod(typeof(NEventOptionButton), "OnRelease", nameof(BeforeEventOptionButtonRelease));
        PatchRecordMethod(typeof(RestSiteOption), nameof(RestSiteOption.OnSelect), nameof(BeforeRestOptionSelect));
        PatchRecordMethod(typeof(NRestSiteButton), "SelectOption", nameof(BeforeRestSiteButtonSelectOption));
        PatchRecordMethod(typeof(MerchantCardEntry), "OnTryPurchaseWrapper", nameof(BeforeBuyCard));
        PatchRecordMethod(typeof(MerchantRelicEntry), "OnTryPurchaseWrapper", nameof(BeforeBuyRelic));
        PatchRecordMethod(typeof(MerchantPotionEntry), "OnTryPurchaseWrapper", nameof(BeforeBuyPotion));
        PatchRecordMethod(typeof(MerchantCardRemovalEntry), "OnTryPurchaseWrapper", nameof(BeforeRemoveCard));
        PatchRecordMethod(typeof(NMerchantCard), "OnTryPurchase", nameof(BeforeMerchantCardTryPurchaseUi));
        PatchRecordMethod(typeof(NMerchantRelic), "OnTryPurchase", nameof(BeforeMerchantRelicTryPurchaseUi));
        PatchRecordMethod(typeof(NMerchantPotion), "OnTryPurchase", nameof(BeforeMerchantPotionTryPurchaseUi));
        PatchRecordMethod(typeof(NMerchantCardRemoval), "OnTryPurchase", nameof(BeforeMerchantCardRemovalTryPurchaseUi));
        PatchRecordMethod(typeof(NChooseARelicSelection), "SelectHolder", nameof(BeforeSelectBossRelic));
        PatchRecordMethod(typeof(NChooseARelicSelection), "OnSkipButtonReleased", nameof(BeforeBossRelicSkip));

        PatchTraceMethod(typeof(NProceedButton), "OnRelease", nameof(AfterProceedButtonRelease));
        PatchTraceMethod(typeof(NEndTurnButton), "OnRelease", nameof(AfterEndTurnButtonRelease));
        PatchTraceMethod(typeof(NRewardButton), "OnRelease", nameof(AfterRewardButtonRelease));
        PatchTraceMethod(typeof(NMainMenu), nameof(NMainMenu.OpenSingleplayerSubmenu), nameof(AfterOpenSingleplayerSubmenu));
        PatchTraceMethod(typeof(NSingleplayerSubmenu), "OpenCharacterSelect", nameof(AfterOpenCharacterSelect));
        PatchTraceMethod(typeof(NCharacterSelectScreen), nameof(NCharacterSelectScreen.SelectCharacter), nameof(AfterSelectCharacter));
        PatchTraceMethod(typeof(NCharacterSelectScreen), "StartNewSingleplayerRun", nameof(AfterStartNewSingleplayerRun));
        PatchTraceMethod(typeof(RunManager), nameof(RunManager.EnterMapCoord), nameof(AfterEnterMapCoord));
        PatchTraceMethod(typeof(MegaCrit.Sts2.Core.Nodes.Rooms.NEventRoom), "OptionButtonClicked", nameof(AfterEventOptionClicked));
        BridgeLoop.Log("mod initialized");
        try
        {
            BridgeLoop.Log("menu kickoff begin");
            var automation = MenuAutomation.Instance;
            BridgeLoop.Log("menu instance acquired");
            automation.KickoffFromCurrentUi();
            BridgeLoop.Log("menu kickoff returned");
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"menu kickoff failed: {ex}");
        }
    }

    public static void AfterRunProcess(double delta)
    {
        BridgeLoop.Instance.Tick(delta);
    }

    public static void AfterMainMenuReady(NMainMenu __instance)
    {
        MenuAutomation.Instance.OnMainMenuReady(__instance);
    }

    public static void AfterSingleplayerSubmenuOpened(NSingleplayerSubmenu __instance)
    {
        MenuAutomation.Instance.OnSingleplayerSubmenuOpened(__instance);
    }

    public static void AfterCharacterSelectInitialized(NCharacterSelectScreen __instance)
    {
        MenuAutomation.Instance.OnCharacterSelectReady(__instance);
    }

    public static void AfterCharacterSelectProcess(NCharacterSelectScreen __instance, double delta)
    {
        MenuAutomation.Instance.OnCharacterSelectReady(__instance);
    }

    public static void BeforeAnyClickableReleased(NClickableControl __instance)
    {
        Record("ui.clickable.release", DescribeClickableControl(__instance), includeAvailableControls: true);
    }

    public static void BeforeProceedButtonRelease(NProceedButton __instance)
    {
        Record("button.proceed", new Dictionary<string, object?>
        {
            ["button_type"] = __instance.GetType().FullName,
            ["button_text"] = SafeButtonText(__instance),
            ["is_skip"] = __instance.IsSkip,
            ["enabled"] = __instance.IsEnabled,
        });
    }

    public static void BeforeEndTurnButtonRelease(NEndTurnButton __instance)
    {
        Record("button.end_turn", new Dictionary<string, object?>
        {
            ["button_type"] = __instance.GetType().FullName,
            ["enabled"] = __instance.IsEnabled,
        });
    }

    public static void BeforeRewardButtonRelease(NRewardButton __instance)
    {
        var reward = __instance.Reward;
        Record("button.reward", new Dictionary<string, object?>
        {
            ["reward_type"] = reward?.GetType().FullName ?? "<null>",
            ["reward_text"] = SafeObjectText(reward, "<null>"),
            ["enabled"] = __instance.IsEnabled,
        });
    }

    public static void BeforeButtonPress(NButton __instance)
    {
        var typeName = __instance.GetType().Name;
        if (!typeName.Contains("SkipButton", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        Record("button.skip", new Dictionary<string, object?>
        {
            ["button_type"] = __instance.GetType().FullName,
            ["button_name"] = typeName,
        });
    }

    public static void BeforeOpenSingleplayerSubmenu()
    {
        Record("menu.open_singleplayer_submenu");
    }

    public static void BeforeOpenCharacterSelect()
    {
        Record("menu.open_character_select");
    }

    public static void BeforeSelectCharacter(NCharacterSelectButton button, object? character)
    {
        Record("menu.select_character", new Dictionary<string, object?>
        {
            ["button_character"] = button.Character?.Id.Entry ?? "<null>",
            ["arg"] = character?.ToString(),
        });
    }

    public static void BeforeStartNewSingleplayerRun(string seed)
    {
        Record("menu.start_new_singleplayer_run", new Dictionary<string, object?>
        {
            ["seed"] = seed,
        });
    }

    public static void BeforeEnterMapCoord(MapCoord coord)
    {
        Record("map.choose_path", new Dictionary<string, object?>
        {
            ["row"] = coord.row,
            ["col"] = coord.col,
        });
    }

    public static void BeforeEventOptionClicked(EventOption option, int index)
    {
        Record("event.choose_option", new Dictionary<string, object?>
        {
            ["index"] = index,
            ["text_key"] = option?.TextKey ?? "<null>",
            ["title"] = SafeObjectText(option?.Title, "<null>"),
        });
    }

    public static void BeforeEventOptionButtonRelease(NEventOptionButton __instance)
    {
        var option = __instance.Option;
        var index = SafeGetIntMember(__instance, "Index");
        Record("event.choose_option", new Dictionary<string, object?>
        {
            ["index"] = index,
            ["text_key"] = option?.TextKey ?? "<null>",
            ["title"] = SafeObjectText(option?.Title, "<null>"),
        });
    }

    public static void BeforeTryManualPlay(CardModel __instance, Creature? target)
    {
        Record("combat.play_card", new Dictionary<string, object?>
        {
            ["card_id"] = __instance.Id.Entry,
            ["upgraded"] = __instance.IsUpgraded,
            ["cost"] = __instance.GetStarCostThisCombat(),
            ["target_type"] = __instance.TargetType.ToString(),
            ["target"] = target?.Name,
        });
    }

    public static void BeforeTryPlayCardUi(NCardPlay __instance, Creature? target)
    {
        var card = __instance.Holder?.CardModel;
        if (card is null)
        {
            return;
        }

        Record("combat.play_card", new Dictionary<string, object?>
        {
            ["card_id"] = card.Id.Entry,
            ["upgraded"] = card.IsUpgraded,
            ["cost"] = card.GetStarCostThisCombat(),
            ["target_type"] = card.TargetType.ToString(),
            ["target"] = target?.Name,
            ["card_index"] = GameIntrospection.FindHandCardIndex(card),
            ["source"] = "ui",
        });
    }

    public static void BeforeEnqueueManualUse(PotionModel __instance, Creature? target)
    {
        Record("combat.use_potion", new Dictionary<string, object?>
        {
            ["potion_id"] = __instance.Id.Entry,
            ["target"] = target?.Name,
        });
    }

    public static void BeforeSelectCard(NCardRewardSelectionScreen __instance, NCardHolder cardHolder)
    {
        var card = cardHolder.CardModel;
        Record("card_reward.pick_card", new Dictionary<string, object?>
        {
            ["card_id"] = card?.Id.Entry ?? "<null>",
            ["upgraded"] = card?.IsUpgraded ?? false,
            ["cost"] = card?.GetStarCostThisCombat(),
            ["target_type"] = card?.TargetType.ToString(),
        });
    }

    public static void BeforeChoiceSelectionSkipPress(NChoiceSelectionSkipButton __instance)
    {
        var phase = GameIntrospection.GetTraceContextSnapshot().PhaseHint;
        var action = phase == "card_reward" ? "card_reward.skip" : "selection.skip";
        Record(action, new Dictionary<string, object?>
        {
            ["button_type"] = __instance.GetType().FullName,
            ["phase_hint"] = phase,
        });
    }

    public static void BeforeMapPointSelected(NMapPoint __instance)
    {
        Record("map.choose_path", GameIntrospection.DescribeMapPointSelection(__instance.Point));
    }

    public static void BeforeRestOptionSelect(RestSiteOption __instance)
    {
        Record("rest.select_option", new Dictionary<string, object?>
        {
            ["option_id"] = __instance.OptionId,
            ["title"] = SafeObjectText(__instance.Title, __instance.OptionId),
            ["enabled"] = __instance.IsEnabled,
        });
    }

    public static void BeforeRestSiteButtonSelectOption(NRestSiteButton __instance, RestSiteOption option)
    {
        Record("rest.select_option", new Dictionary<string, object?>
        {
            ["option_id"] = option.OptionId,
            ["title"] = SafeObjectText(option.Title, option.OptionId),
            ["enabled"] = option.IsEnabled,
            ["source"] = "ui",
        });
    }

    public static void BeforeBuyCard(MerchantCardEntry __instance)
    {
        var card = __instance.CreationResult.Card;
        Record("shop.buy_card", new Dictionary<string, object?>
        {
            ["card_id"] = card.Id.Entry,
            ["cost"] = __instance.Cost,
            ["enough_gold"] = __instance.EnoughGold,
            ["stocked"] = __instance.IsStocked,
        });
    }

    public static void BeforeMerchantCardTryPurchaseUi(NMerchantCard __instance, MerchantInventory inventory)
    {
        if (__instance.Entry is not MerchantCardEntry entry)
        {
            return;
        }

        BeforeBuyCard(entry);
    }

    public static void BeforeBuyRelic(MerchantRelicEntry __instance)
    {
        Record("shop.buy_relic", new Dictionary<string, object?>
        {
            ["relic_id"] = __instance.Model.Id.Entry,
            ["cost"] = __instance.Cost,
            ["enough_gold"] = __instance.EnoughGold,
            ["stocked"] = __instance.IsStocked,
        });
    }

    public static void BeforeMerchantRelicTryPurchaseUi(NMerchantRelic __instance, MerchantInventory inventory)
    {
        if (__instance.Entry is not MerchantRelicEntry entry)
        {
            return;
        }

        BeforeBuyRelic(entry);
    }

    public static void BeforeBuyPotion(MerchantPotionEntry __instance)
    {
        Record("shop.buy_potion", new Dictionary<string, object?>
        {
            ["potion_id"] = __instance.Model.Id.Entry,
            ["cost"] = __instance.Cost,
            ["enough_gold"] = __instance.EnoughGold,
            ["stocked"] = __instance.IsStocked,
        });
    }

    public static void BeforeMerchantPotionTryPurchaseUi(NMerchantPotion __instance, MerchantInventory inventory)
    {
        if (__instance.Entry is not MerchantPotionEntry entry)
        {
            return;
        }

        BeforeBuyPotion(entry);
    }

    public static void BeforeRemoveCard(MerchantCardRemovalEntry __instance)
    {
        Record("shop.remove_card", new Dictionary<string, object?>
        {
            ["cost"] = __instance.Cost,
            ["enough_gold"] = __instance.EnoughGold,
            ["used"] = __instance.Used,
            ["stocked"] = __instance.IsStocked,
        });
    }

    public static void BeforeMerchantCardRemovalTryPurchaseUi(NMerchantCardRemoval __instance, MerchantInventory inventory)
    {
        if (__instance.Entry is not MerchantCardRemovalEntry entry)
        {
            return;
        }

        BeforeRemoveCard(entry);
    }

    public static void BeforeSelectBossRelic(NChooseARelicSelection __instance, NRelicBasicHolder relicHolder)
    {
        var relic = relicHolder.Relic?.Model;
        Record("boss_relic.choose", new Dictionary<string, object?>
        {
            ["relic_id"] = relic?.Id.Entry ?? "<null>",
            ["title"] = SafeObjectText(relic?.Title, "<null>"),
        });
    }

    public static void BeforeBossRelicSkip(NChooseARelicSelection __instance, NButton _)
    {
        Record("boss_relic.skip");
    }

    public static void AfterProceedButtonRelease(NProceedButton __instance)
    {
        Trace($"proceed_button release type={__instance.GetType().FullName} text={SafeButtonText(__instance)}");
    }

    public static void AfterEndTurnButtonRelease(NEndTurnButton __instance)
    {
        Trace($"end_turn_button release enabled={__instance.IsEnabled}");
    }

    public static void AfterRewardButtonRelease(NRewardButton __instance)
    {
        var rewardType = __instance.Reward?.GetType().FullName ?? "<null>";
        Trace($"reward_button release reward={rewardType}");
    }

    public static void AfterOpenSingleplayerSubmenu()
    {
        Trace("main_menu open_singleplayer_submenu");
    }

    public static void AfterOpenCharacterSelect()
    {
        Trace("singleplayer_submenu open_character_select");
    }

    public static void AfterSelectCharacter(NCharacterSelectButton button, object? character)
    {
        var buttonCharacter = button.Character?.Id.Entry ?? "<null>";
        Trace($"character_select select_character button={buttonCharacter} arg={character}");
    }

    public static void AfterStartNewSingleplayerRun(string seed)
    {
        Trace($"character_select start_new_singleplayer_run seed={seed}");
    }

    public static void AfterEnterMapCoord(MapCoord coord)
    {
        Trace($"run_manager enter_map_coord row={coord.row} col={coord.col}");
    }

    public static void AfterEventOptionClicked(EventOption option, int index)
    {
        var title = SafeObjectText(option?.Title, "<null>");
        var textKey = option?.TextKey ?? "<null>";
        Trace($"event_room option_clicked index={index} text_key={textKey} title={title}");
    }

    private static void PatchRecordMethod(Type type, string methodName, string prefixName)
    {
        var original = AccessTools.Method(type, methodName);
        var prefix = AccessTools.Method(typeof(ModBootstrap), prefixName);
        if (original is null || prefix is null)
        {
            BridgeLoop.Log($"record patch skipped type={type.FullName} method={methodName} prefix={prefixName}");
            return;
        }

        _harmony!.Patch(original, prefix: new HarmonyMethod(prefix));
    }

    private static void PatchTraceMethod(Type type, string methodName, string postfixName)
    {
        var original = AccessTools.Method(type, methodName);
        var postfix = AccessTools.Method(typeof(ModBootstrap), postfixName);
        if (original is null || postfix is null)
        {
            BridgeLoop.Log($"trace patch skipped type={type.FullName} method={methodName} postfix={postfixName}");
            return;
        }

        _harmony!.Patch(original, postfix: new HarmonyMethod(postfix));
    }

    private static void Trace(string message)
    {
        BridgeLoop.Log($"trace: {message}");
    }

    internal static List<Dictionary<string, object?>> ListVisibleClickableControls()
    {
        try
        {
            var root = GameIntrospection.GetTraceRootNode();
            if (root is null)
            {
                return [];
            }

            return GetChildrenRecursiveIncludingRoot(root)
                .OfType<NClickableControl>()
                .Where(control => control.Visible)
                .Select(DescribeClickableControl)
                .ToList();
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"list visible clickable controls failed: {ex}");
            return [];
        }
    }

    internal static List<Dictionary<string, object?>> ListActionableControls(
        TraceContextSnapshot trace,
        List<Dictionary<string, object?>> availableControls)
    {
        try
        {
            return availableControls
                .Where(control => IsActionableControl(trace.PhaseHint, control))
                .Select(control => DescribeActionableControl(trace.PhaseHint, control))
                .ToList();
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"list actionable controls failed phase={trace.PhaseHint} ex={ex}");
            return [];
        }
    }

    private static void Record(string action, Dictionary<string, object?>? details = null, bool includeAvailableControls = false)
    {
        ClickTraceRecorder.Record(action, details, includeAvailableControls: includeAvailableControls);
    }

    private static Dictionary<string, object?> DescribeActionableControl(
        string phaseHint,
        Dictionary<string, object?> control)
    {
        var actionable = new Dictionary<string, object?>(control)
        {
            ["phase_hint"] = phaseHint,
            ["action_hint"] = DetermineActionHint(phaseHint, control),
        };

        return actionable;
    }

    private static bool IsActionableControl(string phaseHint, Dictionary<string, object?> control)
    {
        if (ReadBool(control, "visible") == false || ReadBool(control, "enabled") == false)
        {
            return false;
        }

        if (ReadBool(control, "stocked") == false || ReadBool(control, "used") == true || ReadBool(control, "enough_gold") == false)
        {
            return false;
        }

        var controlType = ReadString(control, "control_type");
        var nodeName = ReadString(control, "node_name");
        if (IsBlockedControlType(controlType) || IsBlockedNodeName(nodeName))
        {
            return false;
        }

        if (phaseHint == "map")
        {
            return TypeNameContains(controlType, "MapPoint");
        }

        if (HasValue(control, "card_id", "character_id", "option_id", "relic_id", "potion_id", "reward_type"))
        {
            return true;
        }

        if (HasAllowedControlType(controlType) || HasAllowedNodeName(nodeName))
        {
            return true;
        }

        return phaseHint switch
        {
            "treasure" => TypeNameEndsWith(controlType, "NTreasureRoomRelicHolder"),
            "combat" => TypeNameEndsWith(controlType, "NChoiceSelectionSkipButton"),
            _ => false,
        };
    }

    private static string DetermineActionHint(string phaseHint, Dictionary<string, object?> control)
    {
        var controlType = ReadString(control, "control_type");
        var nodeName = ReadString(control, "node_name");

        if (HasValue(control, "character_id"))
        {
            return "menu.select_character";
        }

        if (TypeNameEndsWith(controlType, "NMainMenuTextButton"))
        {
            return nodeName switch
            {
                "SingleplayerButton" => "menu.open_singleplayer_submenu",
                "MultiplayerButton" => "menu.open_multiplayer_submenu",
                "TimelineButton" => "menu.open_timeline",
                "SettingsButton" => "menu.open_settings",
                "CompendiumButton" => "menu.open_compendium",
                "QuitButton" => "menu.quit",
                _ => "menu.click",
            };
        }

        if (TypeNameEndsWith(controlType, "NSubmenuButton"))
        {
            return nodeName switch
            {
                "HostButton" => "menu.multiplayer_host",
                "JoinButton" => "menu.multiplayer_join",
                _ => "menu.submenu",
            };
        }

        if (TypeNameEndsWith(controlType, "NBackButton"))
        {
            return "ui.back";
        }

        if (TypeNameEndsWith(controlType, "NCloseButton"))
        {
            return "ui.close";
        }

        if (TypeNameEndsWith(controlType, "NUnlockConfirmButton"))
        {
            return "timeline.unlock_confirm";
        }

        if (TypeNameEndsWith(controlType, "NEpochSlot"))
        {
            return "timeline.select_epoch";
        }

        return phaseHint switch
        {
            "reward_screen" => DetermineRewardActionHint(controlType, control),
            "card_reward" => DetermineCardRewardActionHint(controlType, control),
            "map" => "map.choose_path",
            "event" => "event.choose_option",
            "rest" => DetermineRestActionHint(controlType, control),
            "shop" => DetermineShopActionHint(controlType, control),
            "combat" => DetermineCombatActionHint(controlType, control),
            "treasure" => DetermineTreasureActionHint(controlType, control),
            "boss_relic" => DetermineBossRelicActionHint(controlType, control),
            _ => DetermineFallbackActionHint(controlType, control),
        };
    }

    private static string DetermineRewardActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (HasValue(control, "reward_type"))
        {
            return "reward.claim";
        }

        if (TypeNameEndsWith(controlType, "NProceedButton"))
        {
            return "reward.proceed";
        }

        return "reward.click";
    }

    private static string DetermineCardRewardActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (HasValue(control, "card_id"))
        {
            return "card_reward.pick";
        }

        if (TypeNameEndsWith(controlType, "NChoiceSelectionSkipButton") || NameEndsWith(ReadString(control, "node_name"), "SkipButton"))
        {
            return "card_reward.skip";
        }

        if (TypeNameEndsWith(controlType, "NCardRewardAlternativeButton"))
        {
            return "card_reward.alternative";
        }

        if (TypeNameEndsWith(controlType, "NConfirmButton"))
        {
            return "card_reward.confirm";
        }

        return "card_reward.click";
    }

    private static string DetermineRestActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (HasValue(control, "option_id"))
        {
            return "rest.select_option";
        }

        if (TypeNameEndsWith(controlType, "NProceedButton"))
        {
            return "rest.proceed";
        }

        return "rest.click";
    }

    private static string DetermineShopActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (HasValue(control, "card_id") && HasValue(control, "cost"))
        {
            return "shop.buy_card";
        }

        if (HasValue(control, "relic_id") && HasValue(control, "cost"))
        {
            return "shop.buy_relic";
        }

        if (HasValue(control, "potion_id") && HasValue(control, "cost"))
        {
            return "shop.buy_potion";
        }

        if (HasValue(control, "cost"))
        {
            return "shop.remove_card";
        }

        if (TypeNameEndsWith(controlType, "NProceedButton"))
        {
            return "shop.proceed";
        }

        return "shop.click";
    }

    private static string DetermineCombatActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (HasValue(control, "card_id"))
        {
            return "combat.play_card_or_select";
        }

        if (HasValue(control, "potion_id"))
        {
            return "combat.use_potion";
        }

        if (TypeNameEndsWith(controlType, "NEndTurnButton"))
        {
            return "combat.end_turn";
        }

        if (TypeNameEndsWith(controlType, "NConfirmButton"))
        {
            return "combat.confirm";
        }

        if (TypeNameEndsWith(controlType, "NChoiceSelectionSkipButton") || NameEndsWith(ReadString(control, "node_name"), "SkipButton"))
        {
            return "combat.skip_selection";
        }

        return "combat.click";
    }

    private static string DetermineTreasureActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (TypeNameEndsWith(controlType, "NTreasureButton"))
        {
            return "treasure.open";
        }

        if (TypeNameEndsWith(controlType, "NTreasureRoomRelicHolder") || HasValue(control, "relic_id"))
        {
            return "treasure.choose_relic";
        }

        if (TypeNameEndsWith(controlType, "NProceedButton"))
        {
            return "treasure.proceed";
        }

        return "treasure.click";
    }

    private static string DetermineBossRelicActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (HasValue(control, "relic_id"))
        {
            return "boss_relic.choose";
        }

        if (TypeNameEndsWith(controlType, "NProceedButton") || NameEndsWith(ReadString(control, "node_name"), "SkipButton"))
        {
            return "boss_relic.skip";
        }

        return "boss_relic.click";
    }

    private static string DetermineFallbackActionHint(string? controlType, Dictionary<string, object?> control)
    {
        if (HasValue(control, "reward_type"))
        {
            return "reward.claim";
        }

        if (HasValue(control, "card_id"))
        {
            return "select_card";
        }

        if (HasValue(control, "option_id"))
        {
            return "choose_option";
        }

        if (HasValue(control, "relic_id"))
        {
            return "select_relic";
        }

        if (HasValue(control, "potion_id"))
        {
            return "select_potion";
        }

        if (TypeNameEndsWith(controlType, "NProceedButton"))
        {
            return "button.proceed";
        }

        if (TypeNameEndsWith(controlType, "NConfirmButton"))
        {
            return "button.confirm";
        }

        return "ui.click";
    }

    private static bool HasAllowedControlType(string? controlType)
    {
        return TypeNameEndsWith(controlType, "NProceedButton")
            || TypeNameEndsWith(controlType, "NEndTurnButton")
            || TypeNameEndsWith(controlType, "NRewardButton")
            || TypeNameEndsWith(controlType, "NEventOptionButton")
            || TypeNameEndsWith(controlType, "NRestSiteButton")
            || TypeNameEndsWith(controlType, "NChoiceSelectionSkipButton")
            || TypeNameEndsWith(controlType, "NConfirmButton")
            || TypeNameEndsWith(controlType, "NTreasureButton")
            || TypeNameEndsWith(controlType, "NTreasureRoomRelicHolder")
            || TypeNameEndsWith(controlType, "NMainMenuTextButton")
            || TypeNameEndsWith(controlType, "NSubmenuButton")
            || TypeNameEndsWith(controlType, "NBackButton")
            || TypeNameEndsWith(controlType, "NCloseButton")
            || TypeNameEndsWith(controlType, "NUnlockConfirmButton")
            || TypeNameEndsWith(controlType, "NEpochSlot")
            || TypeNameEndsWith(controlType, "NCharacterSelectButton")
            || TypeNameEndsWith(controlType, "NCardRewardAlternativeButton")
            || TypeNameContains(controlType, "MapPoint");
    }

    private static bool IsBlockedControlType(string? controlType)
    {
        return TypeNameEndsWith(controlType, "NMapLegendItem")
            || TypeNameEndsWith(controlType, "NAncientDialogueLine")
            || TypeNameEndsWith(controlType, "NPingButton")
            || TypeNameEndsWith(controlType, "NDrawPileButton")
            || TypeNameEndsWith(controlType, "NDiscardPileButton")
            || TypeNameEndsWith(controlType, "NExhaustPileButton")
            || TypeNameEndsWith(controlType, "NPeekButton")
            || TypeNameEndsWith(controlType, "NGoldArrowButton")
            || TypeNameEndsWith(controlType, "NUpgradePreviewTickbox")
            || TypeNameEndsWith(controlType, "NMerchantButton");
    }

    private static bool HasAllowedNodeName(string? nodeName)
    {
        return NameEndsWith(nodeName, "ProceedButton")
            || NameEndsWith(nodeName, "SkipButton")
            || NameEndsWith(nodeName, "ConfirmButton")
            || NameEndsWith(nodeName, "BackButton")
            || NameEndsWith(nodeName, "CloseButton");
    }

    private static bool IsBlockedNodeName(string? nodeName)
    {
        return string.Equals(nodeName, "OutsideClickHitbox", StringComparison.Ordinal)
            || string.Equals(nodeName, "Backstop", StringComparison.Ordinal);
    }

    private static bool HasValue(Dictionary<string, object?> control, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (!control.TryGetValue(key, out var value) || value is null)
            {
                continue;
            }

            switch (value)
            {
                case string text when !string.IsNullOrWhiteSpace(text) && text != "<null>":
                    return true;
                case bool:
                case int:
                case long:
                case float:
                case double:
                case decimal:
                    return true;
                default:
                    var rendered = value.ToString();
                    if (!string.IsNullOrWhiteSpace(rendered) && rendered != "<null>")
                    {
                        return true;
                    }
                    break;
            }
        }

        return false;
    }

    private static string? ReadString(Dictionary<string, object?> control, string key)
    {
        if (!control.TryGetValue(key, out var value) || value is null)
        {
            return null;
        }

        return NullIfWhitespace(value.ToString());
    }

    private static bool? ReadBool(Dictionary<string, object?> control, string key)
    {
        if (!control.TryGetValue(key, out var value) || value is null)
        {
            return null;
        }

        return value switch
        {
            bool boolValue => boolValue,
            string text when bool.TryParse(text, out var parsed) => parsed,
            _ => null,
        };
    }

    private static bool TypeNameEndsWith(string? value, string suffix)
    {
        return value?.EndsWith(suffix, StringComparison.Ordinal) == true;
    }

    private static bool TypeNameContains(string? value, string fragment)
    {
        return value?.Contains(fragment, StringComparison.Ordinal) == true;
    }

    private static bool NameEndsWith(string? value, string suffix)
    {
        return value?.EndsWith(suffix, StringComparison.Ordinal) == true;
    }

    private static string SafeButtonText(object button)
    {
        var type = button.GetType();
        var names = new[] { "ProceedLoc", "SkipLoc", "Text", "Label", "Title" };
        foreach (var name in names)
        {
            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property is null)
            {
                continue;
            }

            var value = property.GetValue(button);
            var text = SafeObjectText(value, null);
            if (!string.IsNullOrWhiteSpace(text))
            {
                return text;
            }
        }

        return button.GetType().Name;
    }

    private static string SafeObjectText(object? value, string? fallback)
    {
        if (value is null)
        {
            return fallback ?? string.Empty;
        }

        try
        {
            var text = value.ToString();
            return string.IsNullOrWhiteSpace(text) ? (fallback ?? string.Empty) : text;
        }
        catch
        {
            return fallback ?? string.Empty;
        }
    }

    private static int? SafeGetIntMember(object target, string memberName)
    {
        try
        {
            var type = target.GetType();
            var property = type.GetProperty(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property is not null)
            {
                var value = property.GetValue(target);
                if (value is int intValue)
                {
                    return intValue;
                }
            }

            var field = type.GetField($"<{memberName}>k__BackingField", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                ?? type.GetField(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field is not null)
            {
                var value = field.GetValue(target);
                if (value is int intValue)
                {
                    return intValue;
                }
            }
        }
        catch
        {
        }

        return null;
    }

    private static Dictionary<string, object?> DescribeClickableControl(NClickableControl control)
    {
        var details = new Dictionary<string, object?>
        {
            ["control_type"] = control.GetType().FullName,
            ["node_name"] = control.Name.ToString(),
            ["node_path"] = SafeNodePath(control),
            ["parent_type"] = control.GetParent()?.GetType().FullName,
            ["visible"] = control.Visible,
            ["enabled"] = SafeGetBoolMember(control, "IsEnabled"),
            ["text"] = FindControlText(control),
            ["tooltip_text"] = NullIfWhitespace(control.TooltipText),
        };

        switch (control)
        {
            case NProceedButton proceedButton:
                details["is_skip"] = proceedButton.IsSkip;
                break;
            case NRewardButton rewardButton:
                details["reward_type"] = rewardButton.Reward?.GetType().FullName ?? "<null>";
                break;
            case NCharacterSelectButton characterSelectButton:
                details["character_id"] = characterSelectButton.Character?.Id.Entry ?? "<null>";
                break;
            case NMapPoint mapPoint:
                foreach (var pair in GameIntrospection.DescribeMapPointSelection(mapPoint.Point))
                {
                    details[pair.Key] = pair.Value;
                }
                break;
            case NEventOptionButton eventOptionButton:
                details["index"] = SafeGetIntMember(eventOptionButton, "Index");
                details["text_key"] = eventOptionButton.Option?.TextKey ?? "<null>";
                details["title"] = SafeObjectText(eventOptionButton.Option?.Title, "<null>");
                break;
            case NRestSiteButton restSiteButton:
                details["option_id"] = restSiteButton.Option?.OptionId ?? "<null>";
                details["title"] = SafeObjectText(restSiteButton.Option?.Title, "<null>");
                break;
            case NRelicBasicHolder relicHolder:
                details["relic_id"] = relicHolder.Relic?.Model?.Id.Entry ?? "<null>";
                break;
        }

        AppendSemanticDetailsFromHierarchy(control, details);
        return details;
    }

    private static IEnumerable<Godot.Node> GetChildrenRecursiveIncludingRoot(Godot.Node root)
    {
        yield return root;
        foreach (var child in root.GetChildren())
        {
            if (child is not Godot.Node node)
            {
                continue;
            }

            foreach (var nested in GetChildrenRecursiveIncludingRoot(node))
            {
                yield return nested;
            }
        }
    }

    private static string? SafeNodePath(NClickableControl control)
    {
        try
        {
            return control.GetPath().ToString();
        }
        catch
        {
            return null;
        }
    }

    private static bool? SafeGetBoolMember(object target, string memberName)
    {
        try
        {
            var type = target.GetType();
            var property = type.GetProperty(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property is not null)
            {
                var value = property.GetValue(target);
                if (value is bool boolValue)
                {
                    return boolValue;
                }
            }

            var field = type.GetField($"<{memberName}>k__BackingField", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                ?? type.GetField(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field is not null)
            {
                var value = field.GetValue(target);
                if (value is bool boolValue)
                {
                    return boolValue;
                }
            }
        }
        catch
        {
        }

        return null;
    }

    private static void AppendSemanticDetailsFromHierarchy(Godot.Node node, Dictionary<string, object?> details)
    {
        foreach (var current in EnumerateSelfAndParents(node, maxDepth: 6))
        {
            if (!details.ContainsKey("card_id"))
            {
                var cardModel = SafeGetObjectMember(current, "CardModel");
                if (cardModel is CardModel card)
                {
                    details["card_id"] = card.Id.Entry;
                    details["card_upgraded"] = card.IsUpgraded;
                    details["card_cost"] = card.GetStarCostThisCombat();
                }
            }

            if (!details.ContainsKey("character_id"))
            {
                var characterId = TryGetIdEntry(SafeGetObjectMember(current, "Character"));
                if (!string.IsNullOrWhiteSpace(characterId))
                {
                    details["character_id"] = characterId;
                }
            }

            if (!details.ContainsKey("reward_type"))
            {
                var reward = SafeGetObjectMember(current, "Reward");
                if (reward is not null)
                {
                    details["reward_type"] = reward.GetType().FullName;
                }
            }

            if (!details.ContainsKey("option_id"))
            {
                var option = SafeGetObjectMember(current, "Option");
                switch (option)
                {
                    case EventOption eventOption:
                        details["option_id"] = eventOption.TextKey;
                        details["text_key"] = eventOption.TextKey;
                        details["title"] = SafeObjectText(eventOption.Title, "<null>");
                        break;
                    case RestSiteOption restOption:
                        details["option_id"] = restOption.OptionId;
                        details["title"] = SafeObjectText(restOption.Title, "<null>");
                        break;
                }
            }

            if (!details.ContainsKey("room_type"))
            {
                var point = SafeGetObjectMember(current, "Point");
                if (point is MapPoint mapPoint)
                {
                    foreach (var pair in GameIntrospection.DescribeMapPointSelection(mapPoint))
                    {
                        details[pair.Key] = pair.Value;
                    }
                }
            }

            if (!details.ContainsKey("cost"))
            {
                var entry = SafeGetObjectMember(current, "Entry");
                if (entry is MerchantEntry merchantEntry)
                {
                    details["cost"] = merchantEntry.Cost;
                    details["enough_gold"] = merchantEntry.EnoughGold;
                    details["stocked"] = merchantEntry.IsStocked;
                    switch (entry)
                    {
                        case MerchantCardEntry cardEntry:
                            details["card_id"] = cardEntry.CreationResult.Card.Id.Entry;
                            break;
                        case MerchantRelicEntry relicEntry:
                            details["relic_id"] = relicEntry.Model.Id.Entry;
                            break;
                        case MerchantPotionEntry potionEntry:
                            details["potion_id"] = potionEntry.Model.Id.Entry;
                            break;
                        case MerchantCardRemovalEntry removalEntry:
                            details["used"] = removalEntry.Used;
                            break;
                    }
                }
            }

            if (!details.ContainsKey("relic_id"))
            {
                var relicId = TryGetIdEntry(SafeGetObjectMember(current, "Relic"))
                    ?? TryGetIdEntry(SafeGetObjectMember(SafeGetObjectMember(current, "Relic") ?? current, "Model"));
                if (!string.IsNullOrWhiteSpace(relicId))
                {
                    details["relic_id"] = relicId;
                }
            }
        }
    }

    private static IEnumerable<Godot.Node> EnumerateSelfAndParents(Godot.Node node, int maxDepth)
    {
        Godot.Node? current = node;
        for (var depth = 0; depth <= maxDepth && current is not null; depth++)
        {
            yield return current;
            current = current.GetParent();
        }
    }

    private static string? TryGetIdEntry(object? value)
    {
        if (value is null)
        {
            return null;
        }

        var id = SafeGetObjectMember(value, "Id");
        if (id is null)
        {
            return null;
        }

        var entry = SafeGetObjectMember(id, "Entry");
        return entry is null ? null : NullIfWhitespace(entry.ToString());
    }

    private static string? FindControlText(object control)
    {
        var members = new[] { "Text", "Label", "Title", "Description", "ProceedLoc", "SkipLoc", "Header", "TooltipText" };
        foreach (var memberName in members)
        {
            var value = SafeGetObjectMember(control, memberName);
            var text = NullIfWhitespace(SafeObjectText(value, null));
            if (!string.IsNullOrWhiteSpace(text))
            {
                return text;
            }
        }

        return null;
    }

    private static object? SafeGetObjectMember(object target, string memberName)
    {
        try
        {
            var type = target.GetType();
            var property = type.GetProperty(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property is not null)
            {
                return property.GetValue(target);
            }

            var field = type.GetField($"<{memberName}>k__BackingField", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                ?? type.GetField(memberName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field is not null)
            {
                return field.GetValue(target);
            }
        }
        catch
        {
        }

        return null;
    }

    private static string? NullIfWhitespace(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }

        return value;
    }
}
