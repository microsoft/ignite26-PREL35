using System.Diagnostics;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Azure.Core;
using Azure.Identity;

namespace Lab1;

/// <summary>A published agent version: the model deployment it runs on, and its instructions.</summary>
internal sealed record AgentVersion(string Model, string Instructions);

/// <summary>An agent's answer, with the IDs that find it again in Foundry and Application Insights.</summary>
internal sealed record AgentReply(
    string Text, string? ResponseId = null, string? RequestId = null, double DurationMs = 0,
    string? TraceId = null, string? SpanId = null);

/// <summary>
/// Calls the Foundry project REST API with your Entra ID token. The runner calls Foundry directly rather than
/// through the API Management gateway, because the gateway always uses an agent's latest version and an
/// evaluation must pin exact versions.
/// </summary>
internal sealed class Foundry : IDisposable
{
    private readonly HttpClient http;
    private readonly TokenCredential credential;

    public Foundry(string endpoint)
        : this(new HttpClient { BaseAddress = ProjectUri(endpoint), Timeout = TimeSpan.FromMinutes(5) }, new DefaultAzureCredential())
    {
    }

    /// <summary>Lets tests replace the network and the sign-in.</summary>
    internal Foundry(HttpClient http, TokenCredential credential)
    {
        this.http = http;
        this.credential = credential;
    }

    /// <summary>The project endpoint, ending in a slash.</summary>
    public Uri Endpoint => http.BaseAddress!;

    /// <summary>Reads a published agent version, and checks that it's a prompt agent with a model and instructions.</summary>
    public async Task<AgentVersion> GetAgentVersion(string agent, string version)
    {
        var (body, _, _) = await Send(HttpMethod.Get, $"agents/{agent}/versions/{Uri.EscapeDataString(version)}?api-version=v1", null);
        var definition = body["definition"];
        var published = new AgentVersion(Json.Text(definition?["model"]), Json.Text(definition?["instructions"]));
        if (Json.Text(body["version"]) != version || Json.Text(definition?["kind"]) != "prompt"
            || published.Model.Length == 0 || published.Instructions.Length == 0)
            throw new InvalidOperationException($"Agent {agent} version {version} isn't a prompt agent with a model and instructions.");
        return published;
    }

    /// <summary>Sends one input to an exact agent version and returns the text of its answer.</summary>
    public async Task<AgentReply> Ask(string agent, string version, string input)
    {
        var request = new { agent_reference = new { type = "agent_reference", name = agent, version }, input, stream = false };
        var (body, requestId, durationMs) = await Send(HttpMethod.Post, "openai/v1/responses", request);
        if (Json.Text(body["status"]) != "completed")
            throw new InvalidOperationException($"Agent {agent} didn't complete its response.");

        // The answer is the text of the message items. Other items, such as reasoning, are skipped.
        var text = string.Concat((body["output"] as JsonArray ?? [])
            .OfType<JsonObject>()
            .Where(item => Json.Text(item["type"]) == "message")
            .SelectMany(item => (item["content"] as JsonArray ?? []).OfType<JsonObject>())
            .Where(part => Json.Text(part["type"]) == "output_text")
            .Select(part => Json.Text(part["text"])));
        if (text.Trim().Length == 0)
            throw new InvalidOperationException($"Agent {agent} returned no text.");
        return new AgentReply(text, Json.Text(body["id"]), requestId, durationMs);
    }

    public async Task<JsonObject> Get(string path, CancellationToken cancel) =>
        (await Send(HttpMethod.Get, path, null, cancel)).Body;

    public async Task<JsonObject> Post(string path, object body, CancellationToken cancel) =>
        (await Send(HttpMethod.Post, path, body, cancel)).Body;

    /// <summary>Sends one request with a fresh token, and returns the JSON body, request ID and duration.</summary>
    private async Task<(JsonObject Body, string? RequestId, double DurationMs)> Send(
        HttpMethod method, string path, object? body, CancellationToken cancel = default)
    {
        var token = await credential.GetTokenAsync(new TokenRequestContext(["https://ai.azure.com/.default"]), cancel);
        using var request = new HttpRequestMessage(method, path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.Token);
        if (body is not null)
            request.Content = JsonContent.Create(body);

        var timer = Stopwatch.StartNew();
        using var response = await http.SendAsync(request, cancel);
        var content = await response.Content.ReadAsStringAsync(cancel);
        timer.Stop();

        // Foundry returns a request ID that support can use to find the call.
        var requestId = response.Headers.TryGetValues("apim-request-id", out var ids) ? ids.First() : null;
        if (!response.IsSuccessStatusCode)
            throw new HttpRequestException(
                $"Foundry returned HTTP {(int)response.StatusCode} for {method} {path}; request ID: {requestId ?? "none"}.");
        var json = JsonNode.Parse(content) as JsonObject
            ?? throw new InvalidOperationException($"Foundry returned something other than a JSON object for {path}.");
        return (json, requestId, timer.Elapsed.TotalMilliseconds);
    }

    /// <summary>Checks the endpoint from .env and adds the trailing slash that relative paths need.</summary>
    private static Uri ProjectUri(string endpoint)
    {
        if (!Uri.TryCreate(endpoint, UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttps
            || uri.UserInfo.Length > 0 || uri.Query.Length > 0 || uri.Fragment.Length > 0)
            throw new ArgumentException("FOUNDRY_PROJECT_ENDPOINT must be an HTTPS URL without credentials, query, or fragment.");
        return new Uri(endpoint.TrimEnd('/') + "/");
    }

    public void Dispose() => http.Dispose();
}
