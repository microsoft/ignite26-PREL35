using System.Reflection;
using System.Text.Json;

// Exercise the runner's internal Evidence Pack without exposing it as a public API just for this check.
var packType = Assembly.Load("Evaluate").GetType("Lab1.EvidencePack", throwOnError: true)!;
var save = packType.GetMethod("Save")!;
var directory = Path.Combine(Path.GetTempPath(), $"lab1-evidence-check-{Guid.NewGuid():N}");
Directory.CreateDirectory(directory);
try
{
    var pack = Activator.CreateInstance(packType)!;
    save.Invoke(pack, [directory]);
    var evidencePath = Path.Combine(directory, "evidence.json");
    var summaryPath = Path.Combine(directory, "summary.md");
    File.Delete(summaryPath);
    Directory.CreateDirectory(summaryPath);
    Set("ReleaseReady", true);
    Set("ReleaseGate", "pass");
    Set("ExitCode", 0);
    var failed = false;
    try
    {
        save.Invoke(pack, [directory]);
    }
    catch (TargetInvocationException error) when (error.InnerException is IOException or UnauthorizedAccessException)
    {
        failed = true;
    }
    Require(failed, "A blocked summary must fail the save.");
    using var saved = JsonDocument.Parse(File.ReadAllText(evidencePath));
    Require(!saved.RootElement.GetProperty("release_ready").GetBoolean()
        && saved.RootElement.GetProperty("exit_code").GetInt32() == 2,
        "A failed summary save left a release-ready JSON file.");
    Require(saved.RootElement.GetProperty("release_gate").GetString() == "execution_error"
        && !string.IsNullOrWhiteSpace(saved.RootElement.GetProperty("error").GetString()),
        "The save failure must be recorded in the JSON.");
    Require(!Directory.EnumerateFiles(directory, "*.tmp").Any(), "Handled failures must remove temporary files.");
    Console.WriteLine("PASS .NET summary save failure");

    Directory.Delete(summaryPath);
    save.Invoke(pack, [directory]);
    var summary = File.ReadAllText(summaryPath);
    Require(summary.Contains("- Release ready: no") && summary.Contains("- Release gate: execution_error"),
        "A subsequent save must retain the failed verdict.");
    Console.WriteLine("PASS .NET recovery retains failure");
    return 0;

    void Set(string name, object value) => packType.GetProperty(name)!.SetValue(pack, value);
}
catch (Exception error)
{
    Console.Error.WriteLine($"FAIL {error.Message}");
    return 1;
}
finally
{
    Directory.Delete(directory, recursive: true);
}

static void Require(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}
