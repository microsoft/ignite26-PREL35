using System.Text.Json;
using System.Text.Json.Nodes;

namespace Lab1;

/// <summary>What an approved triage answer must contain for one alert.</summary>
internal sealed record CaseExpectation(string AlertId, List<string> AcceptedDispositions, List<List<string>> RequiredCitationGroups);

/// <summary>A deliberately authored triage recommendation, and the verdict the reviewer should reach on it.</summary>
internal sealed record ReviewerChallenge(string Id, string AlertId, JsonObject Recommendation, string ExpectedVerdict);

/// <summary>
/// The reviewed evaluation dataset (data\lab1-evaluations.json). The expected answers are only used for scoring;
/// they're never sent to the agents or the judge.
/// </summary>
internal sealed record Dataset(string Version, string LocalGate, List<CaseExpectation> Cases, List<ReviewerChallenge> ReviewerChallenges)
{
    public static Dataset Load(string path) =>
        JsonSerializer.Deserialize<Dataset>(File.ReadAllText(path), Json.Options)
            ?? throw new ArgumentException($"{path} doesn't contain a dataset.");

    // Item IDs name each agent answer in the Evidence Pack, in replay files, and in telemetry.
    public static string TriageItem(string alertId) => $"triage/{alertId}";
    public static string WorkflowReviewItem(string alertId) => $"reviewer/workflow-{alertId}";
    public static string ChallengeItem(string challengeId) => $"reviewer/{challengeId}";

    /// <summary>Every item ID, in the order the runner evaluates them.</summary>
    public IEnumerable<string> ItemIds() =>
        Cases.SelectMany(c => new[] { TriageItem(c.AlertId), WorkflowReviewItem(c.AlertId) })
            .Concat(ReviewerChallenges.Select(c => ChallengeItem(c.Id)));

    /// <summary>Rejects a dataset that couldn't produce a meaningful score.</summary>
    public void Validate(Dictionary<string, JsonObject> caseFiles)
    {
        Require(Version.Length > 0 && LocalGate == "all_checks_pass" && Cases.Count > 0 && ReviewerChallenges.Count > 0,
            "The dataset needs a version, local_gate=all_checks_pass, and at least one case and reviewer challenge.");
        Require(Cases.Select(c => c.AlertId).Distinct().Count() == Cases.Count, "Each alert can appear only once in the dataset.");
        foreach (var expected in Cases)
        {
            Require(caseFiles.ContainsKey(expected.AlertId), $"Unknown alert {expected.AlertId}.");
            Require(expected.AcceptedDispositions.Count > 0 && expected.AcceptedDispositions.All(Checks.Dispositions.Contains),
                $"{expected.AlertId} needs at least one supported disposition.");
            var validIds = Checks.ValidIds(caseFiles[expected.AlertId]);
            Require(expected.RequiredCitationGroups.Count > 0
                    && expected.RequiredCitationGroups.All(group => group.Count > 0 && group.All(validIds.Contains)),
                $"Required evidence for {expected.AlertId} must reference IDs in its supplied case or screening lists.");
        }
        Require(ReviewerChallenges.Select(c => c.Id).Distinct().Count() == ReviewerChallenges.Count,
            "Each reviewer challenge needs a unique ID.");
        foreach (var challenge in ReviewerChallenges)
        {
            Require(challenge.Id.Length > 0 && caseFiles.ContainsKey(challenge.AlertId), $"Reviewer challenge {challenge.Id} needs a known alert.");
            Require(challenge.ExpectedVerdict is "agree" or "disagree", $"Reviewer challenge {challenge.Id} needs an expected verdict of agree or disagree.");
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new ArgumentException(message);
    }
}

/// <summary>The synthetic case files in data\cases.json.</summary>
internal static class CaseData
{
    /// <summary>Case files by alert ID. Each includes the bank's screening lists, as the agents receive it.</summary>
    public static Dictionary<string, JsonObject> Load(string path)
    {
        var data = JsonNode.Parse(File.ReadAllText(path))!;
        var cases = new Dictionary<string, JsonObject>();
        foreach (var item in data["cases"]!.AsArray())
        {
            var caseFile = item!.DeepClone().AsObject();
            caseFile["screening_lists"] = data["screening_lists"]!.DeepClone();
            cases[Json.Text(caseFile["alert"]!["id"])] = caseFile;
        }
        return cases;
    }
}

/// <summary>Recorded agent answers, used to rerun the deterministic checks without calling Azure.</summary>
internal static class Replay
{
    /// <summary>Answers by item ID. The file must contain a nonempty answer for every item in the dataset.</summary>
    public static Dictionary<string, string> Load(string path, Dataset dataset)
    {
        var json = JsonNode.Parse(File.ReadAllText(path)) as JsonObject
            ?? throw new ArgumentException($"{path} must contain a JSON object.");
        var answers = new Dictionary<string, string>();
        foreach (var id in dataset.ItemIds())
        {
            answers[id] = Json.Text(json[id]);
            if (answers[id].Trim().Length == 0)
                throw new ArgumentException($"The replay file needs a nonempty answer for {id}.");
        }
        return answers;
    }
}
