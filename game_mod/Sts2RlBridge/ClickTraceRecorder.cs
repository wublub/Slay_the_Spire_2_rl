using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sts2RlBridge;

internal sealed class TraceContextSnapshot
{
    [JsonPropertyName("phase_hint")]
    public string PhaseHint { get; init; } = "none";

    [JsonPropertyName("character")]
    public string? Character { get; init; }

    [JsonPropertyName("act")]
    public int? Act { get; init; }

    [JsonPropertyName("floor")]
    public int? Floor { get; init; }

    [JsonPropertyName("context")]
    public string Context { get; init; } = "";
}

internal sealed class ClickTraceEntry
{
    [JsonPropertyName("timestamp")]
    public string Timestamp { get; init; } = "";

    [JsonPropertyName("session_id")]
    public string SessionId { get; init; } = "";

    [JsonPropertyName("kind")]
    public string Kind { get; init; } = "click";

    [JsonPropertyName("action")]
    public string Action { get; init; } = "";

    [JsonPropertyName("trace")]
    public TraceContextSnapshot Trace { get; init; } = new();

    [JsonPropertyName("details")]
    public Dictionary<string, object?> Details { get; init; } = [];

    [JsonPropertyName("available_controls")]
    public List<Dictionary<string, object?>> AvailableControls { get; init; } = [];

    [JsonPropertyName("actionable_controls")]
    public List<Dictionary<string, object?>> ActionableControls { get; init; } = [];

    [JsonPropertyName("state")]
    public StateEnvelope? State { get; init; }
}

internal static class ClickTraceRecorder
{
    private static readonly object Sync = new();
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = null,
        WriteIndented = false,
    };

    private static readonly string SessionId = Guid.NewGuid().ToString("N");
    private static bool _initialized;

    public static void InitializeSession()
    {
        var shouldLog = false;
        lock (Sync)
        {
            if (_initialized)
            {
                return;
            }

            _initialized = true;
            shouldLog = true;
            WriteEntryUnlocked(new ClickTraceEntry
            {
                Timestamp = DateTimeOffset.Now.ToString("O"),
                SessionId = SessionId,
                Kind = "session_start",
                Action = "session_start",
                Trace = GameIntrospection.GetTraceContextSnapshot(),
                Details = new Dictionary<string, object?>
                {
                    ["trace_path"] = GetTracePath(),
                    ["base_dir"] = AppContext.BaseDirectory,
                },
                State = GameIntrospection.TryBuildTraceState(),
            });
        }

        if (shouldLog)
        {
            BridgeLoop.Log($"click trace session={SessionId} path={GetTracePath()}");
        }
    }

    public static void Record(
        string action,
        Dictionary<string, object?>? details = null,
        string kind = "click",
        bool includeAvailableControls = false)
    {
        InitializeSession();

        try
        {
            var trace = GameIntrospection.GetTraceContextSnapshot();
            var availableControls = includeAvailableControls ? ModBootstrap.ListVisibleClickableControls() : [];
            var actionableControls = includeAvailableControls
                ? ModBootstrap.ListActionableControls(trace, availableControls)
                : [];

            var entry = new ClickTraceEntry
            {
                Timestamp = DateTimeOffset.Now.ToString("O"),
                SessionId = SessionId,
                Kind = kind,
                Action = action,
                Trace = trace,
                Details = details ?? [],
                AvailableControls = availableControls,
                ActionableControls = actionableControls,
                State = GameIntrospection.TryBuildTraceState(),
            };

            lock (Sync)
            {
                WriteEntryUnlocked(entry);
            }
        }
        catch (Exception ex)
        {
            BridgeLoop.Log($"click trace failed action={action} ex={ex}");
        }
    }

    public static string GetTracePath() => Path.Combine(AppContext.BaseDirectory, "mods", "Sts2RlBridge", "click_trace.jsonl");

    private static void WriteEntryUnlocked(ClickTraceEntry entry)
    {
        var path = GetTracePath();
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        File.AppendAllText(
            path,
            JsonSerializer.Serialize(entry, JsonOptions) + Environment.NewLine);
    }
}
