using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Lab1;

/// <summary>
/// Foundry-managed scoring: a judge model rates each recorded answer with Foundry's built-in evaluators. The agents
/// aren't called again; Foundry receives the answers the runner already recorded.
/// </summary>
internal static class ManagedScoring
{
    public const string PolicyVersion = "1";
    public const int MinimumScore = 4;
    public static readonly string[] Evaluators = ["groundedness", "relevance"];

    // How often to check on the run, and when to give up. Tests shorten these.
    internal static TimeSpan PollInterval = TimeSpan.FromSeconds(5);
    internal static TimeSpan Timeout = TimeSpan.FromMinutes(10);

    private static readonly object StringField = new { type = "string" };

    /// <summary>
    /// Scores the recorded answers and fills in <paramref name="result"/>. <paramref name="save"/> runs after each
    /// step, so the evaluation and run IDs are in the Evidence Pack even if the run later fails or times out.
    /// </summary>
    public static async Task Run(Foundry foundry, ManagedEvaluation result, List<EvidenceItem> items,
        Dictionary<string, string> instructions, string runId, Action save)
    {
        using var timeout = new CancellationTokenSource(Timeout);
        var cancel = timeout.Token;
        using var span = Telemetry.Source.StartActivity("lab1.managed_evaluation");

        // 1. Register the evaluation: the evaluators, their judge model, and which fields each one reads.
        var evaluation = await foundry.Post("openai/v1/evals", Definition(result.JudgeModelDeployment), cancel);
        result.EvaluationId = Json.Text(evaluation["id"]);
        save();

        // 2. Start a run over the recorded answers. The judge sees each agent's instructions, its input and its
        //    answer, but never the dataset's expected answer.
        var rows = items.Select(item => new
        {
            item = new
            {
                id = item.Id,
                query = $"Agent instructions:\n{instructions[item.Agent]}\n\nTask input:\n{item.Input}",
                context = item.Input,
                response = item.Output
            }
        });
        var run = await foundry.Post($"openai/v1/evals/{result.EvaluationId}/runs", new
        {
            name = $"Lab 1 {runId}",
            metadata = new { lab_run_id = runId },
            data_source = new { type = "jsonl", source = new { type = "file_content", content = rows } }
        }, cancel);
        result.RunId = Json.Text(run["id"]);
        span?.SetTag("foundry.eval.id", result.EvaluationId);
        span?.SetTag("foundry.eval.run_id", result.RunId);

        // 3. Wait for the run to finish.
        var runPath = $"openai/v1/evals/{result.EvaluationId}/runs/{result.RunId}";
        while (true)
        {
            result.Status = Json.Text(run["status"]);
            var reportUrl = Json.Text(run["report_url"]);
            result.ReportUrl = reportUrl.Length > 0 ? reportUrl : null;
            save();
            if (result.Status == "completed")
                break;
            if (result.Status is not ("queued" or "in_progress"))
                throw new InvalidOperationException($"Foundry evaluation run {result.RunId} ended as {result.Status}. Open its report in Foundry.");
            await Task.Delay(PollInterval, cancel);
            run = await foundry.Get(runPath, cancel);
        }

        // 4. Read every scored row. Foundry returns them in pages of up to 100.
        var scored = new List<JsonObject>();
        var page = await foundry.Get($"{runPath}/output_items?limit=100&order=asc", cancel);
        scored.AddRange((page["data"] as JsonArray ?? []).OfType<JsonObject>());
        while (page["has_more"]?.GetValueKind() == JsonValueKind.True)
        {
            var after = Uri.EscapeDataString(Json.Text(page["last_id"]));
            page = await foundry.Get($"{runPath}/output_items?limit=100&order=asc&after={after}", cancel);
            scored.AddRange((page["data"] as JsonArray ?? []).OfType<JsonObject>());
        }

        // 5. Apply the lab's threshold to every score.
        result.Items = Score(scored, items.Select(item => item.Id));
        result.Gate = result.Items.All(item => item.Passed) ? "pass" : "fail";
        span?.SetTag("lab1.managed_gate", result.Gate);
        save();
    }

    /// <summary>
    /// Turns Foundry's scored rows into per-item results. An item passes when every evaluator scores at least
    /// <see cref="MinimumScore"/> out of 5 and Foundry also marks the score as passed. A missing, duplicate or
    /// unreadable score means the scoring is incomplete, so no gate can be computed.
    /// </summary>
    public static List<ManagedItem> Score(List<JsonObject> rows, IEnumerable<string> itemIds)
    {
        var unscored = itemIds.ToHashSet();
        var results = new List<ManagedItem>();
        foreach (var row in rows)
        {
            var id = Json.Text(row["datasource_item"]?["id"]);
            Require(unscored.Remove(id), $"Foundry returned an unknown or duplicate item: {id}.");
            Require(row["sample"]?["error"] is null, $"Foundry couldn't score {id}.");

            var metrics = (row["results"] as JsonArray ?? []).OfType<JsonObject>().Select(score => Metric(id, score)).ToList();
            Require(metrics.Select(m => m.Name).Order().SequenceEqual(Evaluators.Order()),
                $"Foundry didn't return exactly one {string.Join(" and ", Evaluators)} score for {id}.");
            results.Add(new ManagedItem(id, metrics.All(m => m.Passed), metrics));
        }
        Require(unscored.Count == 0, $"Foundry didn't score {string.Join(", ", unscored)}.");
        return results;
    }

    /// <summary>Reads one evaluator's score, and applies the lab's threshold on top of Foundry's own pass mark.</summary>
    private static ManagedMetric Metric(string itemId, JsonObject result)
    {
        var name = Json.Text(result["name"]);
        var score = result["score"]?.GetValueKind() == JsonValueKind.Number
            ? double.Parse(result["score"]!.ToJsonString(), CultureInfo.InvariantCulture)
            : double.NaN;
        var passedKind = result["passed"]?.GetValueKind();
        Require(score is >= 1 and <= 5 && passedKind is JsonValueKind.True or JsonValueKind.False,
            $"Foundry returned an unreadable {name} score for {itemId}.");
        var servicePassed = passedKind == JsonValueKind.True;
        var reason = Json.Text(result["reason"]);
        return new ManagedMetric(name, score, servicePassed, servicePassed && score >= MinimumScore, reason.Length > 0 ? reason : null);
    }

    /// <summary>
    /// The evaluation definition. Both evaluators read the query and the answer; Groundedness also reads the case
    /// file as context, to judge whether the answer is supported by it.
    /// </summary>
    private static object Definition(string judgeModel) => new
    {
        name = $"Lab 1 evidence quality (policy {PolicyVersion})",
        data_source_config = new
        {
            type = "custom",
            item_schema = new
            {
                type = "object",
                properties = new { id = StringField, query = StringField, context = StringField, response = StringField },
                required = new[] { "id", "query", "context", "response" }
            }
        },
        testing_criteria = Evaluators.Select(name => new
        {
            type = "azure_ai_evaluator",
            name,
            evaluator_name = $"builtin.{name}",
            initialization_parameters = new { model = judgeModel },
            data_mapping = name == "groundedness"
                ? new Dictionary<string, string> { ["query"] = "{{item.query}}", ["context"] = "{{item.context}}", ["response"] = "{{item.response}}" }
                : new Dictionary<string, string> { ["query"] = "{{item.query}}", ["response"] = "{{item.response}}" }
        })
    };

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}
