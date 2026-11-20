using System.Text.Json;

namespace WeChatResearch.Workbench;

internal sealed class ReportCatalog(string workspace)
{
    private readonly string directory = Path.Combine(workspace, ".private", "search", "chains");
    private Dictionary<string, Report> reports = new(StringComparer.OrdinalIgnoreCase);

    private sealed record Report(long Length, DateTime Modified, string CaseId, JsonElement Data);

    public JsonElement[]? Refresh(bool force)
    {
        var next = new Dictionary<string, Report>(StringComparer.OrdinalIgnoreCase);
        if (Directory.Exists(directory))
        {
            foreach (var path in Directory.EnumerateFiles(directory, "*.json", SearchOption.TopDirectoryOnly))
            {
                var file = new FileInfo(path);
                if ((file.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                reports.TryGetValue(path, out var known);
                if (known is not null && file.Length == known.Length && file.LastWriteTimeUtc == known.Modified)
                {
                    next[path] = known;
                    continue;
                }

                var report = ReadReport(file);
                if (report is not null) next[path] = report;
                else if (known is not null) next[path] = known;
            }
        }

        if (!force && next.Count == reports.Count && next.All(pair =>
                reports.TryGetValue(pair.Key, out var known) && ReferenceEquals(known, pair.Value))) return null;
        reports = next;
        return reports.Values.OrderByDescending(report => report.Modified)
            .DistinctBy(report => report.CaseId).Select(report => report.Data).ToArray();
    }

    private static Report? ReadReport(FileInfo file)
    {
        try
        {
            using var stream = new FileStream(file.FullName, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var json = JsonDocument.Parse(stream);
            var row = json.RootElement;
            if (row.ValueKind != JsonValueKind.Object || !row.TryGetProperty("subject", out var subject)
                || subject.ValueKind != JsonValueKind.String || !row.TryGetProperty("claims", out var claims)
                || claims.ValueKind != JsonValueKind.Array) return null;
            var caseId = row.TryGetProperty("investigation_id", out var id) && id.ValueKind == JsonValueKind.String
                ? "case:" + id.GetString() : "file:" + file.Name;
            return new Report(file.Length, file.LastWriteTimeUtc, caseId, row.Clone());
        }
        catch (JsonException) { return null; }
        catch (IOException) { return null; }
    }
}
