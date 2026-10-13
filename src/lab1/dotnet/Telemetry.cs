using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Diagnostics.Tracing;
using Azure.Monitor.OpenTelemetry.Exporter;
using OpenTelemetry;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

namespace Lab1;

/// <summary>
/// Sends traces and metrics to Application Insights. Telemetry carries IDs, timings, gate states and scores; never
/// case files, prompts, answers, judge explanations, or credentials.
/// </summary>
internal sealed class Telemetry : IDisposable
{
    public const string ServiceName = "ignite-lab1-evaluation";

    // The span source and the metric instruments are static, so code can record telemetry without checking whether
    // it's switched on. Until a Telemetry object exists (and never, in a replay), they record nothing.
    public static readonly ActivitySource Source = new("Ignite.Lab1");
    private static readonly Meter Meter = new("Ignite.Lab1");
    private static readonly Histogram<double> AgentDuration = Meter.CreateHistogram<double>("lab1.agent.duration", "ms");
    private static readonly Counter<long> LocalChecks = Meter.CreateCounter<long>("lab1.local.checks");
    private static readonly Histogram<double> ManagedScore = Meter.CreateHistogram<double>("lab1.managed.score");
    private static readonly Counter<long> Runs = Meter.CreateCounter<long>("lab1.runs");

    private readonly TracerProvider traces;
    private readonly MeterProvider metrics;
    private readonly ExporterErrors errors = new();

    /// <summary>Starts sending this process's spans and metrics to the Application Insights resource.</summary>
    public Telemetry(string connectionString, Uri foundryEndpoint)
    {
        var resource = ResourceBuilder.CreateDefault().AddService(ServiceName);
        traces = Sdk.CreateTracerProviderBuilder()
            .SetResourceBuilder(resource)
            .AddSource(Source.Name)
            // Trace calls to the Foundry project as dependencies, but not sign-in or other HTTP traffic.
            .AddHttpClientInstrumentation(options => options.FilterHttpRequestMessage = request =>
                request.RequestUri?.AbsoluteUri.StartsWith(foundryEndpoint.AbsoluteUri, StringComparison.OrdinalIgnoreCase) == true)
            // No offline storage: telemetry that can't be sent now is reported as a failure, not retried later.
            .AddAzureMonitorTraceExporter(options => { options.ConnectionString = connectionString; options.DisableOfflineStorage = true; })
            // Keep every trace (set after the exporter, which would otherwise choose its own sampling). A lab run is
            // small, and a sampled-out run would be missing evidence.
            .SetSampler(new AlwaysOnSampler())
            .Build();
        metrics = Sdk.CreateMeterProviderBuilder()
            .SetResourceBuilder(resource)
            .AddMeter(Meter.Name)
            .AddAzureMonitorMetricExporter(options => { options.ConnectionString = connectionString; options.DisableOfflineStorage = true; })
            .Build();
    }

    /// <summary>Records one agent answer: a span with its failed-check count, its duration, and its check results.</summary>
    public static void RecordItem(string runId, string itemId, string agent, double durationMs, List<Check> checks)
    {
        var failed = checks.Count(check => !check.Passed);
        using var span = Source.StartActivity("lab1.local_checks");
        span?.SetTag("lab1.run_id", runId);
        span?.SetTag("lab1.item_id", itemId);
        span?.SetTag("gen_ai.agent.name", agent);
        span?.SetTag("lab1.failed_checks", failed);
        if (failed > 0)
            span?.SetStatus(ActivityStatusCode.Error, "Local quality checks failed.");

        // Metric dimensions stay low-cardinality (agent, pass/fail); run and item IDs go on spans only.
        AgentDuration.Record(durationMs, new KeyValuePair<string, object?>("agent", agent));
        foreach (var check in checks)
            LocalChecks.Add(1, new("agent", agent), new("passed", check.Passed));
    }

    /// <summary>Records one managed score: a span with the score and verdict, and the score as a metric.</summary>
    public static void RecordScore(string runId, string itemId, string agent, ManagedMetric score)
    {
        using var span = Source.StartActivity("lab1.managed_score");
        span?.SetTag("lab1.run_id", runId);
        span?.SetTag("lab1.item_id", itemId);
        span?.SetTag("gen_ai.agent.name", agent);
        span?.SetTag("lab1.evaluator", score.Name);
        span?.SetTag("lab1.score", score.Score);
        span?.SetTag("lab1.passed", score.Passed);
        if (!score.Passed)
            span?.SetStatus(ActivityStatusCode.Error, "Managed score below the required quality gate.");
        ManagedScore.Record(score.Score, new("agent", agent), new("evaluator", score.Name));
    }

    /// <summary>Counts one finished run by its release gate.</summary>
    public static void RecordRun(string gate) => Runs.Add(1, new KeyValuePair<string, object?>("gate", gate));

    /// <summary>
    /// Sends everything still buffered. Returns false if sending timed out or the exporter reported an error; a
    /// true result means sending succeeded, not that Application Insights has finished ingesting it.
    /// </summary>
    public bool Flush()
    {
        var metricsSent = metrics.ForceFlush(30_000);
        var tracesSent = traces.ForceFlush(30_000);
        return metricsSent && tracesSent && errors.Count == 0;
    }

    public void Dispose()
    {
        metrics.Dispose();
        traces.Dispose();
        errors.Dispose();
    }

    /// <summary>Counts errors that the Azure Monitor exporter reports, such as an upload that failed.</summary>
    private sealed class ExporterErrors : EventListener
    {
        private int count;

        public int Count => Volatile.Read(ref count);

        protected override void OnEventSourceCreated(EventSource source)
        {
            if (source.Name == "OpenTelemetry-AzureMonitor-Exporter")
                EnableEvents(source, EventLevel.Error);
        }

        protected override void OnEventWritten(EventWrittenEventArgs e)
        {
            if (e.EventSource.Name == "OpenTelemetry-AzureMonitor-Exporter" && e.Level <= EventLevel.Error)
                Interlocked.Increment(ref count);
        }
    }
}
