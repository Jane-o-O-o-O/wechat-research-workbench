using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace WeChatResearch.Workbench;

internal sealed class TaskClient(string workspace)
{
    private readonly string directory = Path.Combine(workspace, ".private", "tasks");
    private string? lastSnapshot;
    private string? lastProjects;

    public JsonObject[]? RefreshProjects(bool force)
    {
        var projects = Path.Combine(workspace, ".private", "projects");
        var result = new List<JsonObject>();
        if (Directory.Exists(projects))
        {
            foreach (var folder in Directory.EnumerateDirectories(projects))
            {
                var id = Path.GetFileName(folder);
                if (id.Length != 32 || !id.All(Uri.IsHexDigit)) continue;
                try
                {
                    if ((File.GetAttributes(folder) & FileAttributes.ReparsePoint) != 0) continue;
                    var project = ReadObject(Path.Combine(folder, "project.json"));
                    if (project is null) continue;
                    project.Remove("account");
                    project["progress"] = ReadObject(Path.Combine(folder, "status.json"));
                    project["analysis"] = ReadObject(Path.Combine(folder, "analysis.json"));
                    result.Add(project);
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException) { }
            }
        }
        var sorted = result.OrderByDescending(project => (string?)project["updated_at"]).ToArray();
        var snapshot = JsonSerializer.Serialize(sorted);
        if (!force && snapshot == lastProjects) return null;
        lastProjects = snapshot;
        return sorted;
    }

    private static JsonObject? ReadObject(string path)
    {
        if (!File.Exists(path)) return null;
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        return JsonNode.Parse(stream) as JsonObject;
    }

    public JsonElement[]? Refresh(bool force)
    {
        var tasks = new List<JsonElement>();
        if (Directory.Exists(directory))
        {
            foreach (var folder in Directory.EnumerateDirectories(directory))
            {
                try
                {
                    if ((File.GetAttributes(folder) & FileAttributes.ReparsePoint) != 0) continue;
                    using var stream = new FileStream(Path.Combine(folder, "status.json"), FileMode.Open, FileAccess.Read,
                        FileShare.ReadWrite | FileShare.Delete);
                    using var document = JsonDocument.Parse(stream);
                    var root = document.RootElement;
                    if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("id", out _)) continue;
                    tasks.Add(root.Clone());
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException) { }
            }
        }

        var current = tasks.OrderByDescending(task => GetString(task, "created_at")).ToArray();
        var snapshot = JsonSerializer.Serialize(current);
        if (!force && snapshot == lastSnapshot) return null;
        lastSnapshot = snapshot;
        return current;
    }

    public async Task<JsonElement> Execute(string command, string? taskId = null, string? request = null, string group = "task")
    {
        var config = Path.Combine(AppContext.BaseDirectory, "runtime.json");
        if (!File.Exists(config)) throw new InvalidOperationException("task_runtime_missing");
        using var settings = JsonDocument.Parse(await File.ReadAllTextAsync(config));
        var python = GetString(settings.RootElement, "python");
        var cli = GetString(settings.RootElement, "cli");
        if (!Path.IsPathFullyQualified(python) || !File.Exists(python) || !Path.IsPathFullyQualified(cli) || !File.Exists(cli))
            throw new InvalidOperationException("task_runtime_missing");

        var start = new ProcessStartInfo(python) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardInputEncoding = new UTF8Encoding(false),
            WorkingDirectory = workspace };
        foreach (var arg in new[] { "-B", cli, group, command, "--workspace", workspace }) start.ArgumentList.Add(arg);
        if (taskId is not null) { start.ArgumentList.Add("--id"); start.ArgumentList.Add(taskId); }
        using var process = Process.Start(start) ?? throw new InvalidOperationException("task_runtime_start_failed");
        var output = process.StandardOutput.ReadToEndAsync();
        var errors = process.StandardError.ReadToEndAsync();
        if (request is not null) await process.StandardInput.WriteAsync(request);
        process.StandardInput.Close();
        await process.WaitForExitAsync();
        await errors;
        using var result = JsonDocument.Parse(await output);
        return result.RootElement.GetProperty("data").Clone();
    }

    public static string GetString(JsonElement data, string key) =>
        data.ValueKind == JsonValueKind.Object && data.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? "" : "";
}
