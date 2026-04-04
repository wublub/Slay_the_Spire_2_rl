using System.Text.Json.Serialization;

namespace Sts2RlBridge;

public sealed class StateEnvelope
{
    [JsonPropertyName("type")]
    public string Type { get; init; } = "state";

    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; } = 1;

    [JsonPropertyName("request_id")]
    public string RequestId { get; init; } = "";

    [JsonPropertyName("character")]
    public string Character { get; init; } = "";

    [JsonPropertyName("phase")]
    public string Phase { get; init; } = "";

    [JsonPropertyName("run")]
    public RunPayload Run { get; init; } = new();

    [JsonPropertyName("player")]
    public PlayerPayload Player { get; init; } = new();

    [JsonPropertyName("deck")]
    public List<CardPayload> Deck { get; init; } = [];

    [JsonPropertyName("state")]
    public Dictionary<string, object> State { get; init; } = [];
}

public sealed class RunPayload
{
    [JsonPropertyName("act")]
    public int Act { get; init; }

    [JsonPropertyName("floor")]
    public int Floor { get; init; }

    [JsonPropertyName("won")]
    public bool Won { get; init; }
}

public sealed class PlayerPayload
{
    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("hp")]
    public int Hp { get; init; }

    [JsonPropertyName("max_hp")]
    public int MaxHp { get; init; }

    [JsonPropertyName("block")]
    public int Block { get; init; }

    [JsonPropertyName("gold")]
    public int Gold { get; init; }

    [JsonPropertyName("energy")]
    public int Energy { get; init; }

    [JsonPropertyName("energy_per_turn")]
    public int EnergyPerTurn { get; init; }

    [JsonPropertyName("draw_per_turn")]
    public int DrawPerTurn { get; init; } = 5;

    [JsonPropertyName("orb_slots")]
    public int OrbSlots { get; init; }

    [JsonPropertyName("orbs")]
    public List<string> Orbs { get; init; } = [];

    [JsonPropertyName("is_osty_missing")]
    public bool IsOstyMissing { get; init; }

    [JsonPropertyName("hand")]
    public List<CardPayload> Hand { get; init; } = [];

    [JsonPropertyName("draw_pile")]
    public List<CardPayload> DrawPile { get; init; } = [];

    [JsonPropertyName("discard_pile")]
    public List<CardPayload> DiscardPile { get; init; } = [];

    [JsonPropertyName("exhaust_pile")]
    public List<CardPayload> ExhaustPile { get; init; } = [];

    [JsonPropertyName("relics")]
    public List<string> Relics { get; init; } = [];

    [JsonPropertyName("potions")]
    public List<string> Potions { get; init; } = [];

    [JsonPropertyName("powers")]
    public List<PowerPayload> Powers { get; init; } = [];
}

public sealed class CardPayload
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("upgraded")]
    public bool Upgraded { get; init; }

    [JsonPropertyName("cost")]
    public int Cost { get; init; }

    [JsonPropertyName("type")]
    public string Type { get; init; } = "";

    [JsonPropertyName("target")]
    public string Target { get; init; } = "";

    [JsonPropertyName("damage")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? Damage { get; init; }

    [JsonPropertyName("block")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? Block { get; init; }

    [JsonPropertyName("draw")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? Draw { get; init; }

    [JsonPropertyName("keywords")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<string>? Keywords { get; init; }

    [JsonPropertyName("tags")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<string>? Tags { get; init; }

    [JsonPropertyName("pool")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Pool { get; init; }

    [JsonPropertyName("vars")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public Dictionary<string, int>? Vars { get; init; }

    [JsonPropertyName("replay_count")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? ReplayCount { get; init; }

    [JsonPropertyName("retain_this_turn")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? RetainThisTurn { get; init; }

    [JsonPropertyName("sly_this_turn")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? SlyThisTurn { get; init; }

    [JsonPropertyName("affliction_id")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? AfflictionId { get; init; }

    [JsonPropertyName("affliction_amount")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? AfflictionAmount { get; init; }
}

public sealed class PowerPayload
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("amount")]
    public int Amount { get; init; }
}

public sealed class MonsterPayload
{
    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("hp")]
    public int Hp { get; init; }

    [JsonPropertyName("max_hp")]
    public int MaxHp { get; init; }

    [JsonPropertyName("block")]
    public int Block { get; init; }

    [JsonPropertyName("is_dead")]
    public bool IsDead { get; init; }

    [JsonPropertyName("powers")]
    public List<PowerPayload> Powers { get; init; } = [];
}

public sealed class MapChoicePayload
{
    [JsonPropertyName("row")]
    public int Row { get; init; }

    [JsonPropertyName("col")]
    public int Col { get; init; }

    [JsonPropertyName("room_type")]
    public string RoomType { get; init; } = "";

    [JsonPropertyName("children")]
    public List<int> Children { get; init; } = [];

    [JsonPropertyName("enabled")]
    public bool Enabled { get; init; } = true;
}

public sealed class OptionPayload
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("title")]
    public string Title { get; init; } = "";

    [JsonPropertyName("enabled")]
    public bool Enabled { get; init; } = true;
}

public sealed class ShopEntryPayload
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("cost")]
    public int Cost { get; init; }

    [JsonPropertyName("enabled")]
    public bool Enabled { get; init; } = true;
}

public sealed class ResponseEnvelope
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("request_id")]
    public string? RequestId { get; init; }

    [JsonPropertyName("type")]
    public string Type { get; init; } = "";

    [JsonPropertyName("error")]
    public string? Error { get; init; }

    [JsonPropertyName("decision")]
    public DecisionPayload? Decision { get; init; }
}

public sealed class DecisionPayload
{
    [JsonPropertyName("type")]
    public string Type { get; init; } = "";

    [JsonPropertyName("index")]
    public int? Index { get; init; }

    [JsonPropertyName("card_index")]
    public int? CardIndex { get; init; }

    [JsonPropertyName("target_index")]
    public int? TargetIndex { get; init; }

    [JsonPropertyName("potion_index")]
    public int? PotionIndex { get; init; }
}
