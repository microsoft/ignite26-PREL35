// Lab 1 evaluation runner (.NET). The Python version in src\lab1\python behaves the same way.
//
// For every case in the evaluation dataset, the runner asks the triage agent for a recommendation and the reviewer
// agent to check it, then gives the reviewer some deliberately flawed recommendations ("challenges"). It checks
// every answer with deterministic rules, asks Foundry to score the answers with a judge model, and writes an
// Evidence Pack with the release decision.
//
//   dotnet run --project .\src\lab1\dotnet -- --triage-version 4 --reviewer-version 4
//   dotnet run --project .\src\lab1\dotnet -- --replay .\responses.json

using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using Lab1;

const string Usage = """
    Lab 1 evaluation runner

    Live run:  --triage-version <version> --reviewer-version <version>
    Replay:    --replay <responses.json>     Deterministic checks on recorded answers; no Azure calls.

    Options:   --scoring managed|local       Live default: managed. A replay is always local.
               --judge-model <deployment>    Default: MODEL_DEPLOYMENT from .env.
               --dataset <dataset.json>      Default: data\lab1-evaluations.json.
               --output <new-directory>      Default: a new folder under .artifacts\lab1.

    Exit codes: 0 = the selected checks passed, 1 = a quality check failed, 2 = the run couldn't complete.
    Only a live run with managed scoring can pass the release gate.
    """;

if (args.Length == 0 || args is ["--help"])
{
    Console.WriteLine(Usage);
    return args.Length == 0 ? 2 : 0;
}

string[] agents = ["triage", "reviewer"];
var evidence = new EvidencePack();
var exitCode = 2;
string? outputDirectory = null;
Telemetry? telemetry = null;
Activity? runSpan = null;

try
{
    // 1. Read the options and input files, and reject anything that couldn't produce a meaningful result.
    var options = Options.Parse(args);
    var root = FindRepositoryRoot();
    var datasetPath = Path.GetFullPath(options.Dataset ?? Path.Combine(root, "data", "lab1-evaluations.json"));
    var casesPath = Path.Combine(root, "data", "cases.json");
    var dataset = Dataset.Load(datasetPath);
    var caseFiles = CaseData.Load(casesPath);
    dataset.Validate(caseFiles);
    var replay = options.Replay is null ? null : Replay.Load(options.Replay, dataset);

    // 2. Create a new Evidence Pack folder. An existing folder is refused, so earlier evidence is never overwritten.
    var folder = Path.GetFullPath(options.Output ?? Path.Combine(root, ".artifacts", "lab1", $"{DateTime.UtcNow:yyyyMMddTHHmmssZ}-{evidence.RunId}"));
    if (Path.Exists(folder))
        throw new ArgumentException("The output path must not already exist; choose a new directory.");
    Directory.CreateDirectory(folder);
    outputDirectory = folder;

    evidence.Execution = replay is null ? "live_pinned_foundry" : "offline_replay";
    evidence.Mode = options.Scoring == "managed" ? "local_and_managed" : "local_deterministic";
    evidence.ManagedEvaluationStatus = options.Scoring == "managed" ? "not_started" : "skipped";
    evidence.DatasetVersion = dataset.Version;
    evidence.DatasetSha256 = Sha256(File.ReadAllBytes(datasetPath));
    evidence.CasesSha256 = Sha256(File.ReadAllBytes(casesPath));
    evidence.RunnerSha256 = Sha256(File.ReadAllBytes(typeof(Program).Assembly.Location));
    evidence.ReplaySha256 = options.Replay is null ? null : Sha256(File.ReadAllBytes(options.Replay));
    evidence.Save(folder);

    // 3. Live run: connect to Foundry and Application Insights, and pin the exact agent versions to evaluate.
    Foundry? foundry = null;
    string? judgeModel = null;
    var instructions = new Dictionary<string, string>();
    if (replay is null)
    {
        var settings = DotEnv.Load(Path.Combine(root, ".env"));
        foundry = new Foundry(settings.Required("FOUNDRY_PROJECT_ENDPOINT"));
        if (options.Scoring == "managed")
            judgeModel = options.JudgeModel ?? settings.Required("MODEL_DEPLOYMENT");
        telemetry = new Telemetry(settings.Required("APPLICATIONINSIGHTS_CONNECTION_STRING"), foundry.Endpoint);
        evidence.TelemetryStatus = "enabled";

        // Every span in this run belongs to one trace. Its ID is in the Evidence Pack, for Application Insights.
        runSpan = Telemetry.Source.StartActivity("lab1.evaluation_run");
        runSpan?.SetTag("lab1.run_id", evidence.RunId);
        runSpan?.SetTag("lab1.runner", evidence.Runner);
        runSpan?.SetTag("lab1.dataset_version", dataset.Version);
        runSpan?.SetTag("lab1.scoring", options.Scoring);
        evidence.TraceId = runSpan?.TraceId.ToString();

        evidence.Agents = [];
        foreach (var agent in agents)
        {
            var version = options.VersionOf(agent);
            var published = await foundry.GetAgentVersion(agent, version);
            instructions[agent] = published.Instructions;
            evidence.Agents[agent] = new AgentInfo(version, published.Model, Sha256(Encoding.UTF8.GetBytes(published.Instructions)));
            runSpan?.SetTag($"lab1.{agent}.version", version);
        }
        evidence.Save(folder);
    }

    // Asks an agent for an answer, or reads its recorded answer in a replay. Each live call gets its own span.
    async Task<AgentReply> Ask(string itemId, string agent, string input)
    {
        if (replay is not null)
            return new AgentReply(replay[itemId]);

        using var span = Telemetry.Source.StartActivity("lab1.agent");
        span?.SetTag("lab1.run_id", evidence.RunId);
        span?.SetTag("lab1.item_id", itemId);
        span?.SetTag("gen_ai.agent.name", agent);
        span?.SetTag("gen_ai.agent.version", options.VersionOf(agent));
        try
        {
            var reply = await foundry!.Ask(agent, options.VersionOf(agent), input);
            span?.SetTag("gen_ai.response.id", reply.ResponseId);
            span?.SetTag("foundry.request_id", reply.RequestId);
            return reply with { TraceId = span?.TraceId.ToString(), SpanId = span?.SpanId.ToString() };
        }
        catch
        {
            span?.SetStatus(ActivityStatusCode.Error, "Agent call failed.");
            throw;
        }
    }

    // Records an answer and its check results, prints the verdict, and saves the Evidence Pack so far.
    void Record(string itemId, string agent, string alertId, string input, AgentReply reply, List<Check> checks)
    {
        evidence.Items.Add(new EvidenceItem(itemId, agent, alertId, input, reply.Text, reply.ResponseId, reply.RequestId,
            reply.DurationMs, reply.TraceId, reply.SpanId, checks));
        var passed = checks.Count(check => check.Passed);
        Console.WriteLine($"{(passed == checks.Count ? "PASS" : "FAIL")} {itemId}: {passed}/{checks.Count} checks, {reply.DurationMs:F0} ms");
        foreach (var check in checks.Where(check => !check.Passed))
            Console.WriteLine($"  {check.Name}: {check.Detail}");
        Telemetry.RecordItem(evidence.RunId, itemId, agent, reply.DurationMs, checks);
        evidence.Save(folder);
    }

    // 4. Run every case: triage recommends, then the reviewer checks that recommendation.
    foreach (var expected in dataset.Cases)
    {
        var caseFile = caseFiles[expected.AlertId];
        var input = caseFile.ToJsonString();
        var triageId = Dataset.TriageItem(expected.AlertId);
        var triage = await Ask(triageId, "triage", input);
        Record(triageId, "triage", expected.AlertId, input, triage, Checks.Triage(triage.Text, expected, Checks.ValidIds(caseFile)));

        var reviewInput = ReviewerInput(input, triage.Text);
        var reviewId = Dataset.WorkflowReviewItem(expected.AlertId);
        var review = await Ask(reviewId, "reviewer", reviewInput);
        Record(reviewId, "reviewer", expected.AlertId, reviewInput, review, Checks.Reviewer(review.Text, expectedVerdict: null));
    }

    // 5. Run the reviewer challenges: authored recommendations where the right verdict is known in advance.
    foreach (var challenge in dataset.ReviewerChallenges)
    {
        var input = ReviewerInput(caseFiles[challenge.AlertId].ToJsonString(), challenge.Recommendation.ToJsonString());
        var itemId = Dataset.ChallengeItem(challenge.Id);
        var review = await Ask(itemId, "reviewer", input);
        Record(itemId, "reviewer", challenge.AlertId, input, review, Checks.Reviewer(review.Text, challenge.ExpectedVerdict));
    }

    // 6. Local gate: every deterministic check must pass.
    var allChecks = evidence.Items.SelectMany(item => item.Checks).ToList();
    var passedChecks = allChecks.Count(check => check.Passed);
    evidence.Summary = new Summary(evidence.Items.Count, allChecks.Count, passedChecks, allChecks.Count - passedChecks);
    evidence.LocalGate = passedChecks == allChecks.Count ? "pass" : "fail";
    exitCode = evidence.LocalGate == "pass" ? 0 : 1;
    Console.WriteLine($"Local gate: {evidence.LocalGate.ToUpperInvariant()}.");

    // 7. Managed gate: Foundry scores the recorded answers. The release gate needs both gates to pass.
    if (options.Scoring == "managed")
    {
        Console.WriteLine("Scoring the recorded answers in Foundry (the agents aren't called again).");
        var managed = evidence.ManagedEvaluation = new ManagedEvaluation { JudgeModelDeployment = judgeModel! };
        evidence.ManagedEvaluationStatus = "running";
        await ManagedScoring.Run(foundry!, managed, evidence.Items, instructions, evidence.RunId, () => evidence.Save(folder));
        evidence.ManagedEvaluationStatus = managed.Status;
        foreach (var item in managed.Items)
        {
            var agent = evidence.Items.First(recorded => recorded.Id == item.ItemId).Agent;
            foreach (var score in item.Metrics)
                Telemetry.RecordScore(evidence.RunId, item.ItemId, agent, score);
        }
        evidence.ReleaseGate = evidence.LocalGate == "pass" && managed.Gate == "pass" ? "pass" : "fail";
        exitCode = evidence.ReleaseGate == "pass" ? 0 : 1;
        Console.WriteLine($"Managed gate: {managed.Gate.ToUpperInvariant()}; every evaluator must score at least {ManagedScoring.MinimumScore}/5 on every item.");
    }
    else
    {
        Console.WriteLine("Local-only run: it can't approve a release.");
    }
}
catch (Exception error)
{
    // Anything unexpected (bad input, sign-in, network, Foundry) means no valid assessment: never a pass.
    exitCode = 2;
    evidence.Error = error.Message;
    evidence.ReleaseGate = "execution_error";
    if (evidence.Summary is null)
        evidence.LocalGate = "execution_error";
    if (evidence.ManagedEvaluationStatus == "running")
        evidence.ManagedEvaluationStatus = "execution_error";
    Console.Error.WriteLine($"Evaluation could not complete: {error.Message}");
}
finally
{
    // 8. Close the run's trace and send all telemetry before the process exits. If it can't be sent, the run
    //    can't pass: its traces would be missing.
    Telemetry.RecordRun(evidence.ReleaseGate);
    runSpan?.SetTag("lab1.local_gate", evidence.LocalGate);
    runSpan?.SetTag("lab1.release_gate", evidence.ReleaseGate);
    if (exitCode != 0)
        runSpan?.SetStatus(ActivityStatusCode.Error, exitCode == 1 ? "Quality gate failed." : "Run couldn't complete.");
    runSpan?.Dispose();
    if (telemetry is not null)
    {
        var sent = telemetry.Flush();
        telemetry.Dispose();
        evidence.TelemetryStatus = sent ? "flush_completed" : "flush_failed";
        if (!sent)
        {
            exitCode = 2;
            evidence.ReleaseGate = "execution_error";
            evidence.Error ??= "Telemetry couldn't be sent to Application Insights.";
            Console.Error.WriteLine("Telemetry couldn't be sent to Application Insights, so this run can't pass.");
        }
    }

    // Only a complete live run, with both gates passed and its telemetry sent, is release-ready.
    evidence.ReleaseReady = exitCode == 0 && evidence.ReleaseGate == "pass"
        && evidence.Execution == "live_pinned_foundry" && evidence.TelemetryStatus == "flush_completed";
    evidence.ExitCode = exitCode;
    evidence.FinishedAt = DateTimeOffset.UtcNow.ToString("O");
    if (outputDirectory is not null)
    {
        try
        {
            evidence.Save(outputDirectory);
            Console.WriteLine($"Evidence Pack: {outputDirectory}");
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            exitCode = 2;
            Console.Error.WriteLine($"Could not save the Evidence Pack: {error.Message}");
        }
    }
}
return exitCode;

// The reviewer's input: the case file, then the recommendation to check.
static string ReviewerInput(string caseFile, string recommendation) =>
    $"Case file:\n{caseFile}\n\nTriage recommendation:\n{recommendation}";

// A SHA-256 fingerprint, so an approver can tell whether an input changed between runs.
static string Sha256(byte[] content) => Convert.ToHexStringLower(SHA256.HashData(content));

// The repository root: the first folder above the runner that contains data\cases.json.
static string FindRepositoryRoot()
{
    for (var folder = new DirectoryInfo(AppContext.BaseDirectory); folder is not null; folder = folder.Parent)
    {
        if (File.Exists(Path.Combine(folder.FullName, "data", "cases.json")))
            return folder.FullName;
    }
    throw new InvalidOperationException("Can't find the repository root. Run the runner from its repository checkout.");
}

/// <summary>The command-line options. Every option takes exactly one value.</summary>
internal sealed record Options(
    string? TriageVersion, string? ReviewerVersion, string? Replay, string Scoring, string? JudgeModel, string? Dataset, string? Output)
{
    private static readonly string[] Names =
        ["--triage-version", "--reviewer-version", "--replay", "--scoring", "--judge-model", "--dataset", "--output"];

    public static Options Parse(string[] args)
    {
        var values = new Dictionary<string, string>();
        for (var i = 0; i < args.Length; i += 2)
        {
            if (!Names.Contains(args[i]) || i + 1 >= args.Length || string.IsNullOrWhiteSpace(args[i + 1]) || args[i + 1].StartsWith("--"))
                throw new ArgumentException($"Invalid or incomplete option: {args[i]}. Use --help for usage.");
            if (!values.TryAdd(args[i], args[i + 1]))
                throw new ArgumentException($"Repeated option: {args[i]}.");
        }

        var replay = values.GetValueOrDefault("--replay");
        var options = new Options(
            values.GetValueOrDefault("--triage-version"),
            values.GetValueOrDefault("--reviewer-version"),
            replay,
            values.GetValueOrDefault("--scoring") ?? (replay is null ? "managed" : "local"),
            values.GetValueOrDefault("--judge-model"),
            values.GetValueOrDefault("--dataset"),
            values.GetValueOrDefault("--output"));

        // A replay has no agents to call and no judge to ask, so it can only run the deterministic checks.
        if (options.Scoring is not ("managed" or "local"))
            throw new ArgumentException("--scoring must be managed or local.");
        if (replay is null && (options.TriageVersion is null || options.ReviewerVersion is null))
            throw new ArgumentException("A live run needs --triage-version and --reviewer-version.");
        if (replay is not null && (options.TriageVersion is not null || options.ReviewerVersion is not null || options.Scoring != "local"))
            throw new ArgumentException("--replay can't be combined with agent versions or managed scoring.");
        if (options.Scoring == "local" && options.JudgeModel is not null)
            throw new ArgumentException("--judge-model needs managed scoring.");
        return options;
    }

    public string VersionOf(string agent) => agent == "triage" ? TriageVersion! : ReviewerVersion!;
}

/// <summary>Reads the KEY=value lines that scripts\env.ps1 writes to .env.</summary>
internal static class DotEnv
{
    public static Dictionary<string, string> Load(string path)
    {
        if (!File.Exists(path))
            throw new ArgumentException(@"No .env yet. Run: .\scripts\env.ps1 -ResourceGroup <your-resource-group>");
        return File.ReadLines(path)
            .Where(line => line.Contains('='))
            .Select(line => line.Split('=', 2))
            .ToDictionary(parts => parts[0], parts => parts[1]);
    }

    public static string Required(this Dictionary<string, string> settings, string name) =>
        settings.TryGetValue(name, out var value) && !string.IsNullOrWhiteSpace(value)
            ? value
            : throw new ArgumentException($@"Missing {name} in .env. Run .\scripts\env.ps1 again.");
}
