using System.Text;
using System.Text.Json;

namespace Lab1;

/// <summary>
/// The Evidence Pack: everything an approver or auditor needs to check one run. It's written as evidence.json (all
/// details) and summary.md (readable), and saved again after every step, so even a failed run leaves evidence.
/// </summary>
internal sealed class EvidencePack
{
    public string SchemaVersion { get; set; } = "3";
    public string Runner { get; set; } = "dotnet";
    public string RunId { get; set; } = Guid.NewGuid().ToString("N");
    public string StartedAt { get; set; } = DateTimeOffset.UtcNow.ToString("O");
    public string? FinishedAt { get; set; }

    // What was evaluated: live agents or recorded answers, which checks ran, and fingerprints of every input.
    public string Execution { get; set; } = "not_started";
    public string Mode { get; set; } = "local_deterministic";
    public string? DatasetVersion { get; set; }
    public string? DatasetSha256 { get; set; }
    public string? CasesSha256 { get; set; }
    public string? RunnerSha256 { get; set; }
    public string? ReplaySha256 { get; set; }
    public Dictionary<string, AgentInfo>? Agents { get; set; }
    public string? TraceId { get; set; }

    // The verdicts. Only a live run with both gates passed and its telemetry sent is release-ready.
    public string LocalGate { get; set; } = "execution_error";
    public string ManagedEvaluationStatus { get; set; } = "not_started";
    public ManagedEvaluation? ManagedEvaluation { get; set; }
    public string ReleaseGate { get; set; } = "not_evaluated";
    public bool ReleaseReady { get; set; }
    public string TelemetryStatus { get; set; } = "disabled";

    // Always false: the runner can't see whether Application Insights ingested the telemetry. Check it there.
    public bool TelemetryIngestionVerified { get; set; }
    public int ExitCode { get; set; } = 2;
    public string? Error { get; set; }
    public Summary? Summary { get; set; }
    public List<EvidenceItem> Items { get; set; } = [];

    /// <summary>Writes evidence.json and summary.md to the run's folder, replacing the previous save.</summary>
    public void Save(string directory)
    {
        var json = JsonSerializer.Serialize(this, Json.Options);
        var markdown = SummaryMarkdown();
        try
        {
            // Each replacement is atomic. Commit the authoritative JSON verdict only after the summary succeeds.
            WriteAtomic(Path.Combine(directory, "summary.md"), markdown);
            WriteAtomic(Path.Combine(directory, "evidence.json"), json);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            ReleaseReady = false;
            ReleaseGate = "execution_error";
            ExitCode = 2;
            Error = $"{Error} Evidence Pack save failed: {error.Message}".Trim();

            // Either file may be unwritable. Try each independently to leave a failed verdict wherever possible.
            foreach (var (name, content) in new[]
            {
                ("summary.md", SummaryMarkdown()),
                ("evidence.json", JsonSerializer.Serialize(this, Json.Options))
            })
            {
                try
                {
                    WriteAtomic(Path.Combine(directory, name), content);
                }
                catch (Exception recoveryError) when (recoveryError is IOException or UnauthorizedAccessException)
                {
                    Console.Error.WriteLine($"Could not record the save failure in {name}: {recoveryError.Message}");
                }
            }
            throw;
        }
    }

    private static void WriteAtomic(string path, string content)
    {
        var temporary = $"{path}.{Guid.NewGuid():N}.tmp";
        try
        {
            File.WriteAllText(temporary, content);
            File.Move(temporary, path, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary))
                File.Delete(temporary);
        }
    }

    private string SummaryMarkdown()
    {
        var text = new StringBuilder("# Lab 1 Evidence Pack\n\n")
            .AppendLine($"- Runner: {Runner}")
            .AppendLine($"- Execution: {Execution}")
            .AppendLine($"- Local gate: {LocalGate}")
            .AppendLine($"- Managed scoring: {ManagedEvaluationStatus}")
            .AppendLine($"- Release gate: {ReleaseGate}")
            .AppendLine($"- Release ready: {(ReleaseReady ? "yes" : "no")}")
            .AppendLine($"- Telemetry: {TelemetryStatus} (confirm it arrived in Application Insights)")
            .AppendLine($"- Run ID: {RunId}")
            .AppendLine($"- Trace ID: {TraceId ?? "none"}")
            .AppendLine($"- Dataset version: {DatasetVersion ?? "not loaded"}")
            .AppendLine()
            .AppendLine("| Item | Agent | Passed checks | Total checks | Duration (ms) |")
            .AppendLine("|---|---|---:|---:|---:|");
        foreach (var item in Items)
            text.AppendLine($"| {item.Id} | {item.Agent} | {item.Checks.Count(c => c.Passed)} | {item.Checks.Count} | {item.DurationMs:F0} |");

        if (ManagedEvaluation is { } managed)
        {
            text.AppendLine()
                .AppendLine("## Managed scores")
                .AppendLine()
                .AppendLine($"Every evaluator must score at least {managed.MinimumScore}/5 on every item. Judge: {managed.JudgeModelDeployment}.")
                .AppendLine()
                .AppendLine($"Foundry report: {managed.ReportUrl ?? "not available"}")
                .AppendLine()
                .AppendLine("| Item | Evaluator | Score | Passed |")
                .AppendLine("|---|---|---:|---|");
            foreach (var item in managed.Items)
                foreach (var metric in item.Metrics)
                    text.AppendLine($"| {item.ItemId} | {metric.Name} | {metric.Score:0.##} | {(metric.Passed ? "yes" : "no")} |");
        }
        text.AppendLine().AppendLine("Inputs, answers, check details and correlation IDs are in [evidence.json](./evidence.json).");
        return text.ToString();
    }
}

/// <summary>The exact agent version that was evaluated, the model it ran on, and a fingerprint of its instructions.</summary>
internal sealed record AgentInfo(string Version, string ModelDeployment, string InstructionsSha256);

/// <summary>One agent answer, its input, its deterministic check results, and IDs to find it again.</summary>
internal sealed record EvidenceItem(
    string Id, string Agent, string AlertId, string Input, string Output,
    string? ResponseId, string? RequestId, double DurationMs, string? TraceId, string? SpanId, List<Check> Checks);

/// <summary>Counts across all deterministic checks in the run.</summary>
internal sealed record Summary(int Items, int Checks, int Passed, int Failed);

/// <summary>The managed scoring settings, the Foundry evaluation it created, and the result.</summary>
internal sealed class ManagedEvaluation
{
    public string PolicyVersion { get; set; } = ManagedScoring.PolicyVersion;
    public string JudgeModelDeployment { get; set; } = "";
    public string[] Evaluators { get; set; } = ManagedScoring.Evaluators;
    public int MinimumScore { get; set; } = ManagedScoring.MinimumScore;
    public string? EvaluationId { get; set; }
    public string? RunId { get; set; }
    public string Status { get; set; } = "not_started";
    public string? ReportUrl { get; set; }
    public string Gate { get; set; } = "execution_error";
    public List<ManagedItem> Items { get; set; } = [];
}

/// <summary>One answer's managed scores. It passes only when every evaluator passes.</summary>
internal sealed record ManagedItem(string ItemId, bool Passed, List<ManagedMetric> Metrics);

/// <summary>One evaluator's score (1 to 5), Foundry's own verdict, the lab's verdict, and the judge's explanation.</summary>
internal sealed record ManagedMetric(string Name, double Score, bool ServicePassed, bool Passed, string? Reason);
