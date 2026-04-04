using System.Globalization;
using System.Reflection;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;

namespace Sts2RlBridge;

internal sealed class MenuAutomation
{
    private static readonly FieldInfo StandardButtonField =
        typeof(NSingleplayerSubmenu).GetField("_standardButton", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly FieldInfo SelectedButtonField =
        typeof(NCharacterSelectScreen).GetField("_selectedButton", BindingFlags.Instance | BindingFlags.NonPublic)!;

    private static readonly MethodInfo OpenCharacterSelectMethod =
        typeof(NSingleplayerSubmenu).GetMethod("OpenCharacterSelect", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;

    private static readonly MethodInfo StartNewSingleplayerRunMethod =
        typeof(NCharacterSelectScreen).GetMethod("StartNewSingleplayerRun", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;

    public static MenuAutomation Instance { get; } = new();

    private DateTime _nextAttemptUtc = DateTime.MinValue;
    private DateTime _nextStatusLogUtc = DateTime.MinValue;
    private DateTime _nextCharacterLogUtc = DateTime.MinValue;
    private Task? _pendingStartTask;
    private string? _pendingCharacter;

    private MenuAutomation()
    {
    }

    public bool Tick()
    {
        if (BridgeLoop.LoadPausedFlag())
        {
            ResetPending();
            MaybeLogStatus("paused");
            return true;
        }

        if (HasActiveRun())
        {
            ResetPending();
            return false;
        }

        MaybeLogStatus("waiting_for_menu");
        if (DateTime.UtcNow < _nextAttemptUtc)
        {
            return true;
        }

        if (_pendingStartTask is not null)
        {
            if (!_pendingStartTask.IsCompleted)
            {
                return true;
            }

            if (_pendingStartTask.IsFaulted)
            {
                BridgeLoop.Log($"menu start failed: {_pendingStartTask.Exception?.GetBaseException()}");
            }

            _pendingStartTask = null;
            _nextAttemptUtc = DateTime.UtcNow.AddSeconds(1);
            return true;
        }

        var game = NGame.Instance;
        var mainMenu = game?.MainMenu;
        if (mainMenu is null)
        {
            MaybeLogStatus("main_menu_missing");
            return false;
        }

        var desiredCharacter = BridgeLoop.LoadDesiredCharacter() ?? "Ironclad";
        var activeScreen = GetCurrentScreenSafe("tick");
        if (activeScreen is NCharacterSelectScreen charScreen)
        {
            MaybeLogStatus("character_select_visible");
            return EnsureCharacterAndStart(charScreen, desiredCharacter);
        }

        var submenuStack = mainMenu.SubmenuStack;
        if (submenuStack is null)
        {
            return false;
        }

        var singleplayer = submenuStack.GetSubmenuType<NSingleplayerSubmenu>();
        if (singleplayer is null)
        {
            mainMenu.OpenSingleplayerSubmenu();
            BridgeLoop.Log("menu automation: opened singleplayer submenu");
            _nextAttemptUtc = DateTime.UtcNow.AddSeconds(1);
            return true;
        }

        var standardButton = (NButton?)StandardButtonField.GetValue(singleplayer);
        OpenCharacterSelectMethod.Invoke(singleplayer, [standardButton]);
        BridgeLoop.Log("menu automation: opened character select");
        _nextAttemptUtc = DateTime.UtcNow.AddSeconds(1);
        return true;
    }

    public void OnMainMenuReady(NMainMenu mainMenu)
    {
        if (BridgeLoop.LoadPausedFlag())
        {
            ResetPending();
            return;
        }

        if (HasActiveRun())
        {
            return;
        }

        try
        {
            mainMenu.OpenSingleplayerSubmenu();
            BridgeLoop.Log("menu automation: opened singleplayer submenu");
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"menu automation main menu failed: {ex}");
        }
    }

    public void KickoffFromCurrentUi()
    {
        var currentRunNode = NGame.Instance?.CurrentRunNode;
        var mainMenu = NGame.Instance?.MainMenu;
        var activeScreen = GetCurrentScreenSafe("kickoff");
        BridgeLoop.Log(
            $"menu kickoff run_node={(currentRunNode?.GetType().FullName ?? "<null>")} " +
            $"main_menu={(mainMenu?.GetType().FullName ?? "<null>")} " +
            $"active_screen={(activeScreen?.GetType().FullName ?? "<null>")}");

        if (BridgeLoop.LoadPausedFlag())
        {
            ResetPending();
            BridgeLoop.Log("menu automation: kickoff skipped because paused");
            return;
        }

        if (HasActiveRun())
        {
            return;
        }

        if (activeScreen is NCharacterSelectScreen characterSelectScreen)
        {
            OnCharacterSelectReady(characterSelectScreen);
            return;
        }

        if (mainMenu is not null)
        {
            OnMainMenuReady(mainMenu);
        }
    }

    public void OnSingleplayerSubmenuOpened(NSingleplayerSubmenu submenu)
    {
        if (BridgeLoop.LoadPausedFlag())
        {
            ResetPending();
            return;
        }

        if (HasActiveRun())
        {
            return;
        }

        try
        {
            var standardButton = (NButton?)StandardButtonField.GetValue(submenu);
            OpenCharacterSelectMethod.Invoke(submenu, [standardButton]);
            BridgeLoop.Log("menu automation: opened character select");
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"menu automation submenu failed: {ex}");
        }
    }

    public void OnCharacterSelectReady(NCharacterSelectScreen screen)
    {
        if (BridgeLoop.LoadPausedFlag())
        {
            ResetPending();
            return;
        }

        if (HasActiveRun())
        {
            return;
        }

        try
        {
            _ = EnsureCharacterAndStart(screen, BridgeLoop.LoadDesiredCharacter() ?? "Ironclad");
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"menu automation character select failed: {ex}");
        }
    }

    private bool EnsureCharacterAndStart(NCharacterSelectScreen screen, string desiredCharacter)
    {
        if (BridgeLoop.LoadPausedFlag())
        {
            ResetPending();
            return true;
        }

        var buttons = GetChildrenRecursive(screen).OfType<NCharacterSelectButton>().ToList();
        if (DateTime.UtcNow >= _nextCharacterLogUtc)
        {
            _nextCharacterLogUtc = DateTime.UtcNow.AddSeconds(2);
            BridgeLoop.Log($"menu automation: character buttons count={buttons.Count} desired={desiredCharacter}");
        }
        var targetButton = buttons.FirstOrDefault(x =>
            x.Character?.Id.Entry.Equals(desiredCharacter, StringComparison.OrdinalIgnoreCase) == true);

        if (targetButton is null)
        {
            BridgeLoop.Log($"menu automation: character button not found for {desiredCharacter}");
            _nextAttemptUtc = DateTime.UtcNow.AddSeconds(2);
            return true;
        }

        var selectedButton = (NCharacterSelectButton?)SelectedButtonField.GetValue(screen);
        if (!ReferenceEquals(selectedButton, targetButton))
        {
            screen.SelectCharacter(targetButton, targetButton.Character);
            BridgeLoop.Log($"menu automation: selected character {targetButton.Character.Id.Entry}");
            _nextAttemptUtc = DateTime.UtcNow.AddMilliseconds(800);
            return true;
        }

        if (_pendingStartTask is not null || _pendingCharacter == desiredCharacter)
        {
            _nextAttemptUtc = DateTime.UtcNow.AddSeconds(1);
            return true;
        }

        var seed = BuildRunSeed();
        var acts = ModelDb.Acts.ToList();
        _pendingCharacter = desiredCharacter;
        _pendingStartTask = (Task)StartNewSingleplayerRunMethod.Invoke(screen, [seed, acts])!;
        _pendingStartTask.ContinueWith(task =>
        {
            if (task.IsFaulted)
            {
                BridgeLoop.Log($"menu start task faulted: {task.Exception?.GetBaseException()}");
            }
        }, TaskScheduler.Default);
        BridgeLoop.Log($"menu automation: starting singleplayer run character={desiredCharacter} seed={seed}");
        _nextAttemptUtc = DateTime.UtcNow.AddSeconds(2);
        return true;
    }

    public void ResetPending()
    {
        _pendingStartTask = null;
        _pendingCharacter = null;
        _nextAttemptUtc = DateTime.UtcNow.AddMilliseconds(250);
    }

    private static bool HasActiveRun()
    {
        return NGame.Instance?.CurrentRunNode is not null;
    }

    private static string BuildRunSeed()
    {
        return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds().ToString(CultureInfo.InvariantCulture);
    }

    private void MaybeLogStatus(string status)
    {
        if (DateTime.UtcNow < _nextStatusLogUtc)
        {
            return;
        }

        _nextStatusLogUtc = DateTime.UtcNow.AddSeconds(3);
        BridgeLoop.Log($"menu automation status={status}");
    }

    private static object? GetCurrentScreenSafe(string origin)
    {
        try
        {
            var context = ActiveScreenContext.Instance;
            if (context is null)
            {
                BridgeLoop.Log($"menu automation: active screen context missing origin={origin}");
                return null;
            }

            return context.GetCurrentScreen();
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"menu automation: get current screen failed origin={origin} ex={ex}");
            return null;
        }
    }

    private static IEnumerable<Godot.Node> GetChildrenRecursive(Godot.Node root)
    {
        foreach (var child in root.GetChildren())
        {
            if (child is not Godot.Node node)
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
}
