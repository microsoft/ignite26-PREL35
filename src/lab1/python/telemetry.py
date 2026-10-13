"""Sends traces and metrics to Application Insights. Mirrors Telemetry.cs.

Telemetry carries IDs, timings, gate states and scores; never case files, prompts, answers, judge explanations,
or credentials.
"""
import logging
import os
import re

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

SERVICE_NAME = "ignite-lab1-evaluation"

# The tracer and the metric instruments are created at import, so code can record telemetry without checking
# whether it's switched on. Until start() runs (and never, in a replay), they record nothing.
tracer = trace.get_tracer("Ignite.Lab1")
_meter = metrics.get_meter("Ignite.Lab1")
_agent_duration = _meter.create_histogram("lab1.agent.duration", unit="ms")
_local_checks = _meter.create_counter("lab1.local.checks")
_managed_score = _meter.create_histogram("lab1.managed.score")
_runs = _meter.create_counter("lab1.runs")


class _ExporterErrors(logging.Handler):
    """Counts warnings and errors that the Azure Monitor exporter logs, such as an upload that failed."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record):
        self.count += 1


_errors = _ExporterErrors()


def start(connection_string, foundry_endpoint):
    """Starts sending this process's spans and metrics to the Application Insights resource."""
    # Name the service, and keep every trace: a lab run is small, and a sampled-out run would be missing evidence.
    # Trace HTTP calls to the Foundry project only: every other URL, such as sign-in, is excluded by a pattern
    # that matches anything not starting with the project endpoint.
    os.environ["OTEL_SERVICE_NAME"] = SERVICE_NAME
    os.environ["OTEL_TRACES_SAMPLER"] = "always_on"
    os.environ["OTEL_PYTHON_REQUESTS_EXCLUDED_URLS"] = f"^(?!{re.escape(foundry_endpoint)})"

    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(
        connection_string=connection_string,
        disable_logging=True,
        # No offline storage: telemetry that can't be sent now is reported as a failure, not retried later.
        disable_offline_storage=True,
        enable_live_metrics=False,
        # This runner makes its HTTP calls with requests; switch off the other HTTP instrumentations.
        instrumentation_options={name: {"enabled": False} for name in ("azure_sdk", "httpx", "urllib", "urllib3")},
    )
    logging.getLogger("azure.monitor.opentelemetry.exporter").addHandler(_errors)


def record_item(run_id, item_id, agent, duration_ms, checks):
    """Records one agent answer: a span with its failed-check count, its duration, and its check results."""
    failed = sum(not c["passed"] for c in checks)
    with tracer.start_as_current_span("lab1.local_checks") as span:
        span.set_attributes({"lab1.run_id": run_id, "lab1.item_id": item_id, "gen_ai.agent.name": agent,
                             "lab1.failed_checks": failed})
        if failed:
            span.set_status(Status(StatusCode.ERROR, "Local quality checks failed."))

    # Metric dimensions stay low-cardinality (agent, pass/fail); run and item IDs go on spans only.
    _agent_duration.record(duration_ms, {"agent": agent})
    for c in checks:
        _local_checks.add(1, {"agent": agent, "passed": c["passed"]})


def record_score(run_id, item_id, agent, score):
    """Records one managed score: a span with the score and verdict, and the score as a metric."""
    with tracer.start_as_current_span("lab1.managed_score") as span:
        span.set_attributes({"lab1.run_id": run_id, "lab1.item_id": item_id, "gen_ai.agent.name": agent,
                             "lab1.evaluator": score["name"], "lab1.score": score["score"],
                             "lab1.passed": score["passed"]})
        if not score["passed"]:
            span.set_status(Status(StatusCode.ERROR, "Managed score below the required quality gate."))
    _managed_score.record(score["score"], {"agent": agent, "evaluator": score["name"]})


def record_run(gate):
    """Counts one finished run by its release gate."""
    _runs.add(1, {"gate": gate})


def flush():
    """Sends everything still buffered.

    Returns False if sending timed out or the exporter reported a problem; True means sending succeeded, not that
    Application Insights has finished ingesting it.
    """
    metrics_sent = metrics.get_meter_provider().force_flush(30_000)
    traces_sent = trace.get_tracer_provider().force_flush(30_000)
    return bool(metrics_sent and traces_sent and _errors.count == 0)
