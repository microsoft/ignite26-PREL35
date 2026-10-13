using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Lab1;

/// <summary>The result of one deterministic check on one agent answer.</summary>
internal sealed record Check(string Name, bool Passed, string Detail);

/// <summary>
/// Deterministic checks. They use no model, so the same answer always gets the same verdict. They check the
/// answer's form and its citations; whether the evidence supports the reasoning is left to managed scoring.
/// </summary>
internal static partial class Checks
{
    public static readonly string[] Dispositions = ["escalate", "close_false_positive", "request_info"];
    private static readonly string[] Priorities = ["critical", "high", "medium", "low"];

    /// <summary>
    /// Checks a triage answer: valid JSON, allowed values, an approved disposition, a citation on every reason,
    /// only IDs that exist in the case, and the evidence the dataset requires.
    /// </summary>
    public static List<Check> Triage(string answer, CaseExpectation expected, HashSet<string> validIds)
    {
        var checks = new List<Check>();
        var json = ParseObject(answer, checks);
        if (json is null)
            return checks;

        var disposition = Json.Text(json["disposition"]);
        var reasons = Json.TextList(json["reasons"]);
        checks.Add(new("disposition", Dispositions.Contains(disposition), "Disposition must be a supported value."));
        checks.Add(new("priority", Priorities.Contains(Json.Text(json["priority"])), "Priority must be a supported value."));
        checks.Add(new("reasons", reasons is { Count: > 0 }, "Reasons must be a nonempty array of nonempty strings."));
        checks.Add(new("expected_disposition", expected.AcceptedDispositions.Contains(disposition),
            "Disposition must match an approved expectation."));
        if (reasons is not { Count: > 0 })
            return checks;

        // Collect the IDs cited in each reason, and note whether any reason has none.
        var cited = new HashSet<string>();
        var everyReasonCited = true;
        foreach (var reason in reasons)
        {
            var ids = CitedIds(reason);
            everyReasonCited &= ids.Count > 0;
            cited.UnionWith(ids);
        }
        checks.Add(new("citations_present", everyReasonCited, "Every reason must cite at least one evidence ID in brackets."));
        checks.Add(new("citations_valid", cited.All(validIds.Contains),
            "All cited IDs must exist in the supplied case or screening lists."));

        // Each required group lists alternatives: citing any one of them satisfies the group.
        foreach (var group in expected.RequiredCitationGroups)
            checks.Add(new($"required_evidence:{string.Join("|", group)}", group.Any(cited.Contains),
                "At least one ID from this approved evidence group must be cited."));
        return checks;
    }

    /// <summary>
    /// Checks a reviewer answer: valid JSON, a verdict that matches its issues, and, for a reviewer challenge, the
    /// verdict the dataset expects. Without an expected verdict, agreeing with triage proves nothing on its own.
    /// </summary>
    public static List<Check> Reviewer(string answer, string? expectedVerdict)
    {
        var checks = new List<Check>();
        var json = ParseObject(answer, checks);
        if (json is null)
            return checks;

        var verdict = Json.Text(json["verdict"]);
        var issues = Json.TextList(json["issues"]);
        checks.Add(new("verdict", verdict is "agree" or "disagree", "Verdict must be agree or disagree."));
        checks.Add(new("issues", issues is not null, "Issues must be an array of nonempty strings; an empty array is allowed."));
        checks.Add(new("verdict_consistent",
            issues is not null && (verdict == "agree" && issues.Count == 0 || verdict == "disagree" && issues.Count > 0),
            "Agreement must have no issues; disagreement must explain at least one issue."));
        if (expectedVerdict is not null)
            checks.Add(new("expected_verdict", verdict == expectedVerdict, "Verdict must match the approved challenge expectation."));
        return checks;
    }

    /// <summary>Every "id" value in a case file, including its screening lists: the IDs an answer may cite.</summary>
    public static HashSet<string> ValidIds(JsonNode? node, HashSet<string>? ids = null)
    {
        ids ??= [];
        if (node is JsonObject obj)
        {
            if (Json.Text(obj["id"]) is { Length: > 0 } id)
                ids.Add(id);
            foreach (var (_, value) in obj)
                ValidIds(value, ids);
        }
        else if (node is JsonArray array)
        {
            foreach (var value in array)
                ValidIds(value, ids);
        }
        return ids;
    }

    /// <summary>The IDs inside the brackets of a reason, such as [T-00018] or [DOC-201, T-00018].</summary>
    private static List<string> CitedIds(string reason) =>
        BracketedGroup().Matches(reason)
            .SelectMany(group => EvidenceId().Matches(group.Groups[1].Value))
            .Select(match => match.Value)
            .ToList();

    /// <summary>Parses the answer and records whether it's a JSON object; returns null when it isn't.</summary>
    private static JsonObject? ParseObject(string answer, List<Check> checks)
    {
        JsonObject? json = null;
        try
        {
            json = JsonNode.Parse(answer) as JsonObject;
        }
        catch (System.Text.Json.JsonException)
        {
        }
        checks.Add(new("json_object", json is not null, "Agent must return a JSON object without Markdown fences."));
        return json;
    }

    [GeneratedRegex(@"\[([^\[\]\r\n]+)\]")]
    private static partial Regex BracketedGroup();

    [GeneratedRegex(@"\b[A-Z]+-\d+\b")]
    private static partial Regex EvidenceId();
}
