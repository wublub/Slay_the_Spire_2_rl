using System.Net.WebSockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Sts2RlBridge;

public sealed class BridgeLoop
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = null,
        WriteIndented = false,
    };

    private Uri? _bridgeUri;
    private ClientWebSocket? _socket;
    private Task<string>? _pendingRequest;
    private DateTime _nextConnectAttemptUtc = DateTime.MinValue;
    private DateTime _nextActionUtc = DateTime.MinValue;
    private DateTime _nextPauseLogUtc = DateTime.MinValue;
    private string? _lastStateHash;
    private string? _pendingRequestId;
    private string? _pendingRequestContextKey;

    public static BridgeLoop Instance { get; } = new();

    private BridgeLoop()
    {
    }

    public void Tick(double delta)
    {
        try
        {
            if (LoadPausedFlag())
            {
                EnterPausedState();
                return;
            }

            if (MenuAutomation.Instance.Tick())
            {
                return;
            }

            if (_pendingRequest is { IsCompleted: true })
            {
                FinishRequest();
            }

            if (DateTime.UtcNow < _nextActionUtc)
            {
                return;
            }

            if (!EnsureSocket())
            {
                return;
            }

            if (_pendingRequest is not null)
            {
                return;
            }

            var state = GameIntrospection.TryBuildState();
            if (state is null)
            {
                return;
            }

            var json = JsonSerializer.Serialize(state, JsonOptions);
            var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(json)));
            if (hash == _lastStateHash)
            {
                return;
            }

            _lastStateHash = hash;
            _pendingRequestId = state.RequestId;
            _pendingRequestContextKey = BuildEnvelopeContextKey(state);
            _pendingRequest = SendAndReceiveAsync(json);
        }
        catch (Exception ex)
        {
            Log($"tick failed: {ex.Message}");
            ResetSocket();
            _nextActionUtc = DateTime.UtcNow.AddMilliseconds(500);
        }
    }

    private void EnterPausedState()
    {
        if (DateTime.UtcNow >= _nextPauseLogUtc)
        {
            _nextPauseLogUtc = DateTime.UtcNow.AddSeconds(3);
            Log("automation paused");
        }

        MenuAutomation.Instance.ResetPending();
        _pendingRequest = null;
        _pendingRequestId = null;
        _pendingRequestContextKey = null;
        _lastStateHash = null;
        _nextActionUtc = DateTime.UtcNow.AddMilliseconds(250);
        ResetSocket();
    }

    private void FinishRequest()
    {
        var task = _pendingRequest!;
        _pendingRequest = null;
        var pendingRequestId = _pendingRequestId;
        var pendingContextKey = _pendingRequestContextKey;
        _pendingRequestId = null;
        _pendingRequestContextKey = null;
        string? raw = null;
        ResponseEnvelope? response = null;

        try
        {
            raw = task.GetAwaiter().GetResult();
            response = JsonSerializer.Deserialize<ResponseEnvelope>(raw, JsonOptions);
            if (response is null)
            {
                return;
            }

            if (!response.Ok)
            {
                Log($"bridge error: {response.Type} {response.Error}");
                _nextActionUtc = DateTime.UtcNow.AddMilliseconds(600);
                _lastStateHash = null;
                return;
            }

            if (response.Type is "idle" or "restart_required")
            {
                _nextActionUtc = DateTime.UtcNow.AddMilliseconds(800);
                _lastStateHash = null;
                return;
            }

            if (response.Type == "action" && response.Decision is not null)
            {
                var currentContextKey = GameIntrospection.DescribeBridgeContextKey();
                if (!string.IsNullOrWhiteSpace(pendingRequestId)
                    && !string.Equals(response.RequestId, pendingRequestId, StringComparison.Ordinal))
                {
                    Log(
                        $"stale response ignored request_id={response.RequestId ?? "<null>"} expected={pendingRequestId} " +
                        $"decision={response.Decision.Type ?? "<null>"} sent_context={pendingContextKey ?? "<null>"} current_context={currentContextKey}");
                    _nextActionUtc = DateTime.UtcNow.AddMilliseconds(100);
                    _lastStateHash = null;
                    return;
                }

                if (!string.IsNullOrWhiteSpace(pendingContextKey)
                    && !string.Equals(currentContextKey, pendingContextKey, StringComparison.Ordinal))
                {
                    Log(
                        $"stale response ignored request_id={response.RequestId ?? "<null>"} decision={response.Decision.Type ?? "<null>"} " +
                        $"sent_context={pendingContextKey} current_context={currentContextKey}");
                    _nextActionUtc = DateTime.UtcNow.AddMilliseconds(100);
                    _lastStateHash = null;
                    return;
                }

                var applied = GameIntrospection.TryExecuteDecision(response.Decision);
                Log($"decision={response.Decision.Type} applied={applied}");
                if (!applied)
                {
                    Log($"decision context: {GameIntrospection.DescribeCurrentContext()}");
                    Log($"decision controls: {GameIntrospection.DescribeVisibleControlsSummary(6)}");
                }
                _nextActionUtc = DateTime.UtcNow.AddMilliseconds(applied ? 350 : 650);
                _lastStateHash = null;
            }
        }
        catch (Exception ex)
        {
            var responseType = response?.Type ?? "<null>";
            var decisionType = response?.Decision?.Type ?? "<null>";
            var rawPreview = raw is null
                ? "<null>"
                : raw.Length <= 400
                    ? raw
                    : $"{raw[..400]}...(truncated)";
            Log($"request failed type={responseType} decision={decisionType} raw={rawPreview} ex={ex}");
            ResetSocket();
            _nextActionUtc = DateTime.UtcNow.AddSeconds(1);
            _lastStateHash = null;
        }
    }

    private static string BuildEnvelopeContextKey(StateEnvelope state) =>
        $"{state.Phase}:{state.Run?.Act.ToString() ?? "null"}:{state.Run?.Floor.ToString() ?? "null"}:{GameIntrospection.DescribeCurrentContext()}";

    private bool EnsureSocket()
    {
        _bridgeUri ??= LoadBridgeUri();

        if (_socket is { State: WebSocketState.Open })
        {
            return true;
        }

        if (DateTime.UtcNow < _nextConnectAttemptUtc)
        {
            return false;
        }

        _nextConnectAttemptUtc = DateTime.UtcNow.AddSeconds(3);
        ResetSocket();
        _socket = new ClientWebSocket();
        _socket.ConnectAsync(_bridgeUri, CancellationToken.None).GetAwaiter().GetResult();
        Log($"connected to {_bridgeUri}");
        return true;
    }

    private static Uri LoadBridgeUri()
    {
        try
        {
            var controlStatePath = FindControlStatePath();
            if (controlStatePath is not null && File.Exists(controlStatePath))
            {
                var controlStateJson = JsonDocument.Parse(File.ReadAllText(controlStatePath));
                var host = "127.0.0.1";
                var port = 8765;

                if (controlStateJson.RootElement.TryGetProperty("bridge_host", out var hostElement))
                {
                    var rawHost = hostElement.GetString();
                    if (!string.IsNullOrWhiteSpace(rawHost))
                    {
                        host = rawHost;
                    }
                }

                if (controlStateJson.RootElement.TryGetProperty("bridge_port", out var portElement) && portElement.TryGetInt32(out var parsedPort))
                {
                    port = parsedPort;
                }

                var resolved = new Uri($"ws://{host}:{port}");
                Log($"using control_state uri {resolved}");
                return resolved;
            }

            var configPath = Path.Combine(AppContext.BaseDirectory, "mods", "Sts2RlBridge", "config.json");
            if (!File.Exists(configPath))
            {
                return new Uri("ws://127.0.0.1:8765");
            }

            var json = JsonDocument.Parse(File.ReadAllText(configPath));
            if (json.RootElement.TryGetProperty("bridge_uri", out var uriElement))
            {
                var raw = uriElement.GetString();
                if (!string.IsNullOrWhiteSpace(raw))
                {
                    var resolved = new Uri(raw);
                    Log($"using config uri {resolved}");
                    return resolved;
                }
            }
        }
        catch (Exception ex)
        {
            Log($"load config failed: {ex.Message}");
        }

        var fallback = new Uri("ws://127.0.0.1:8765");
        Log($"using fallback uri {fallback}");
        return fallback;
    }

    public static string? LoadDesiredCharacter()
    {
        try
        {
            var controlStatePath = FindControlStatePath();
            if (controlStatePath is null || !File.Exists(controlStatePath))
            {
                return null;
            }

            var controlStateJson = JsonDocument.Parse(File.ReadAllText(controlStatePath));
            if (!controlStateJson.RootElement.TryGetProperty("desired_character", out var characterElement))
            {
                return null;
            }

            var character = characterElement.GetString();
            return string.IsNullOrWhiteSpace(character) ? null : character;
        }
        catch (Exception ex)
        {
            Log($"load desired character failed: {ex.Message}");
            return null;
        }
    }

    public static bool LoadPausedFlag()
    {
        try
        {
            var controlStatePath = FindControlStatePath();
            if (controlStatePath is null || !File.Exists(controlStatePath))
            {
                return false;
            }

            var controlStateJson = JsonDocument.Parse(File.ReadAllText(controlStatePath));
            if (!controlStateJson.RootElement.TryGetProperty("paused", out var pausedElement))
            {
                return false;
            }

            return pausedElement.ValueKind == JsonValueKind.True;
        }
        catch (Exception ex)
        {
            Log($"load paused flag failed: {ex.Message}");
            return false;
        }
    }

    private static string? FindControlStatePath()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        for (var i = 0; i < 4 && current is not null; i++, current = current.Parent)
        {
            var direct = Path.Combine(current.FullName, "bridge_control_state.json");
            if (File.Exists(direct))
            {
                return direct;
            }

            var repoPath = Path.Combine(current.FullName, "Slay_the_Spire_2_rl-main", "bridge_control_state.json");
            if (File.Exists(repoPath))
            {
                return repoPath;
            }
        }

        return null;
    }

    private async Task<string> SendAndReceiveAsync(string json)
    {
        if (_socket is null)
        {
            throw new InvalidOperationException("socket is null");
        }

        var bytes = Encoding.UTF8.GetBytes(json);
        await _socket.SendAsync(bytes, WebSocketMessageType.Text, true, CancellationToken.None);

        var buffer = new byte[64 * 1024];
        using var ms = new MemoryStream();
        while (true)
        {
            var result = await _socket.ReceiveAsync(buffer, CancellationToken.None);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                throw new WebSocketException("bridge closed connection");
            }

            ms.Write(buffer, 0, result.Count);
            if (result.EndOfMessage)
            {
                break;
            }
        }

        return Encoding.UTF8.GetString(ms.ToArray());
    }

    private void ResetSocket()
    {
        if (_socket is null)
        {
            return;
        }

        try
        {
            _socket.Dispose();
        }
        catch
        {
        }

        _socket = null;
    }

    public static void Log(string message)
    {
        try
        {
            var dir = Path.Combine(AppContext.BaseDirectory, "mods", "Sts2RlBridge");
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, "bridge.log");
            File.AppendAllText(path, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}{Environment.NewLine}");
        }
        catch
        {
        }
    }
}
