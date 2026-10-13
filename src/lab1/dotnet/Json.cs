using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Lab1;

/// <summary>JSON settings and small helpers shared by the runner.</summary>
internal static class Json
{
    /// <summary>
    /// Reads and writes snake_case JSON (the style of the dataset and the Evidence Pack). Input that's missing a
    /// required field is rejected instead of silently becoming null.
    /// </summary>
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        RespectNullableAnnotations = true,
        RespectRequiredConstructorParameters = true,
        WriteIndented = true
    };

    /// <summary>The value of a JSON string, or "" when the node is missing or isn't a string.</summary>
    public static string Text(JsonNode? node) =>
        node?.GetValueKind() == JsonValueKind.String ? node.GetValue<string>() : "";

    /// <summary>
    /// A JSON array of nonempty strings. An empty array gives an empty list; anything else gives null.
    /// </summary>
    public static List<string>? TextList(JsonNode? node)
    {
        if (node is not JsonArray array)
            return null;
        var values = array.Select(Text).ToList();
        return values.All(value => !string.IsNullOrWhiteSpace(value)) ? values : null;
    }
}
