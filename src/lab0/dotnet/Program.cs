// Usage: dotnet run --project .\src\lab0\dotnet -- [ALERT_ID]

using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Azure.Core;
using Azure.Identity;

var root = Path.GetFullPath(@"..\..\..\..\..\..", AppContext.BaseDirectory);
var env = LoadEnv(Path.Combine(root, ".env"));
string[] required = ["APIM_GATEWAY_URL", "ENTRA_TENANT_ID",
    "LAB_CLIENT_ID", "LAB_CLIENT_SECRET", "APIM_API_AUDIENCE"];
var missing = required.Where(name => !env.TryGetValue(name, out var value) ||
    string.IsNullOrWhiteSpace(value) || value.Contains('<')).ToArray();
if (missing.Length > 0)
{
    return Fail($"Lab 0 setup is incomplete: {string.Join(", ", missing)}. " +
        "Run scripts\\env.ps1 for deployment outputs; ask the deployment owner to provision " +
        "the gateway client with scripts\\setup-entra-apps.ps1 on this VM.");
}
var data = JsonNode.Parse(await File.ReadAllTextAsync(Path.Combine(root, "data", "cases.json")))!.AsObject();

var alertId = args.FirstOrDefault() ?? "ALT-1001";
var cases = data["cases"]!.AsArray();
var selectedCase = cases.FirstOrDefault(item => (string?)item!["alert"]!["id"] == alertId);

if (selectedCase is null)
{
    var available = string.Join(", ", cases.Select(item => (string?)item!["alert"]!["id"]));
    return Fail($"Unknown alert {alertId}. Alerts: {available}");
}

var caseFile = selectedCase.DeepClone().AsObject();
caseFile["screening_lists"] = data["screening_lists"]!.DeepClone();
var caseJson = caseFile.ToJsonString();

// Authenticate as the confidential Lab 0 service client.
var credential = new ClientSecretCredential(
    env["ENTRA_TENANT_ID"],
    env["LAB_CLIENT_ID"],
    env["LAB_CLIENT_SECRET"]);
using var client = new HttpClient
{
    BaseAddress = new Uri($"{env["APIM_GATEWAY_URL"].TrimEnd('/')}/")
};

Console.WriteLine($"{alertId}: {selectedCase["alert"]!["title"]} ({selectedCase["customer"]!["name"]})\n");

var triage = await Ask("triage", caseJson);
Console.WriteLine($"Triage recommendation:\n{triage}\n");

var review = await Ask("reviewer", $"Case file:\n{caseJson}\n\nTriage recommendation:\n{triage}");
Console.WriteLine($"Reviewer:\n{review}\n");
Console.WriteLine("The agents recommend. A named analyst decides.");

return 0;

async Task<string> Ask(string agent, string input)
{
    var token = await credential.GetTokenAsync(
        new TokenRequestContext([$"{env["APIM_API_AUDIENCE"]}/.default"]));
    using var request = new HttpRequestMessage(HttpMethod.Post, $"agents/{agent}/responses")
    {
        Content = JsonContent.Create(new { input })
    };
    request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.Token);
    using var response = await client.SendAsync(request);
    response.EnsureSuccessStatusCode();

    var body = JsonNode.Parse(await response.Content.ReadAsStringAsync())!;
    var output = string.Concat(body["output"]!.AsArray()
        .Where(item => (string?)item!["type"] == "message")
        .SelectMany(item => item!["content"]!.AsArray())
        .Where(item => (string?)item!["type"] == "output_text")
        .Select(item => (string?)item!["text"]));
    if (string.IsNullOrWhiteSpace(output))
    {
        throw new InvalidOperationException(
            $"Agent {agent} returned no text. No recommendation is available; try the investigation again.");
    }
    return output;
}

static Dictionary<string, string> LoadEnv(string path)
{
    if (!File.Exists(path))
    {
        Environment.ExitCode = Fail("No .env yet. Run: .\\scripts\\env.ps1 -ResourceGroup <your-resource-group>");
        Environment.Exit(Environment.ExitCode);
    }

    return File.ReadLines(path)
        .Where(line => line.Contains('='))
        .Select(line => line.Split('=', 2))
        .ToDictionary(parts => parts[0], parts => parts[1]);
}

static int Fail(string message)
{
    Console.Error.WriteLine(message);
    return 1;
}
