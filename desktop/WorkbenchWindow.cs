using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace WeChatResearch.Workbench;

internal sealed class WorkbenchWindow : Form
{
    private const string CanvasHost = "canvas.wechat-research.local";
    private const string CanvasAddress = "https://" + CanvasHost + "/investigation-canvas.html";
    private readonly WebView2 view = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(25, 25, 28) };
    private readonly System.Windows.Forms.Timer refresh = new() { Interval = 1200 };
    private readonly ReportCatalog catalog;
    private readonly TaskClient tasks;
    private readonly HashSet<string> activeTasks = new();
    private readonly SemaphoreSlim readerQueue = new(1);
    private bool creatingTask;
    private string? reportError;

    public WorkbenchWindow(string workspace)
    {
        Text = "微信调查工作台";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        FormBorderStyle = FormBorderStyle.None;
        Padding = new Padding(5);
        ClientSize = new Size(1280, 800);
        MinimumSize = new Size(450, 500);
        BackColor = Color.FromArgb(25, 25, 28);
        StartPosition = FormStartPosition.CenterScreen;
        catalog = new ReportCatalog(workspace);
        tasks = new TaskClient(workspace);
        Controls.Add(view);
        refresh.Tick += (_, _) => PublishWorkspace(false);
        Shown += async (_, _) => await StartCanvas();
        FormClosed += (_, _) => refresh.Dispose();
        Resize += (_, _) => Padding = WindowState == FormWindowState.Maximized ? Padding.Empty : new Padding(5);
    }

    protected override CreateParams CreateParams
    {
        get
        {
            var parameters = base.CreateParams;
            parameters.Style |= 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000;
            return parameters;
        }
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == 0x83 && message.WParam != IntPtr.Zero)
        {
            message.Result = IntPtr.Zero;
            return;
        }
        base.WndProc(ref message);
        if (message.Msg != 0x84 || WindowState == FormWindowState.Maximized) return;
        var point = PointToClient(Cursor.Position);
        var horizontal = point.X < 5 ? -1 : point.X >= ClientSize.Width - 5 ? 1 : 0;
        var vertical = point.Y < 5 ? -1 : point.Y >= ClientSize.Height - 5 ? 1 : 0;
        message.Result = (horizontal, vertical) switch
        {
            (-1, -1) => 13, (1, -1) => 14, (-1, 1) => 16, (1, 1) => 17,
            (-1, 0) => 10, (1, 0) => 11, (0, -1) => 12, (0, 1) => 15, _ => message.Result
        };
    }

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr parameter, IntPtr value);

    private async Task StartCanvas()
    {
        try
        {
            var profile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "WeChatResearch", "WorkbenchProfile");
            var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: profile);
            await view.EnsureCoreWebView2Async(environment);
            if (IsDisposed) return;
            var core = view.CoreWebView2;
            core.SetVirtualHostNameToFolderMapping(CanvasHost, Path.Combine(AppContext.BaseDirectory, "assets"),
                CoreWebView2HostResourceAccessKind.DenyCors);
            core.Settings.AreDefaultScriptDialogsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.NavigationStarting += (_, args) => args.Cancel = args.Uri != CanvasAddress;
            core.NewWindowRequested += (_, args) =>
            {
                args.Handled = true;
                if (!args.IsUserInitiated || !Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri)
                    || uri.Scheme is not ("http" or "https") || !string.IsNullOrEmpty(uri.UserInfo)) return;
                try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
                catch (System.ComponentModel.Win32Exception) { MessageBox.Show(this, "无法打开原文链接。", Text); }
            };
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += (_, args) =>
            {
                if (args.Request.Uri != CanvasAddress)
                    args.Response = environment.CreateWebResourceResponse(null, 403, "Forbidden", "");
            };
            core.WebMessageReceived += async (_, args) =>
            {
                if (args.Source != CanvasAddress) return;
                if (args.WebMessageAsJson == "\"ready\"")
                {
                    PublishWorkspace(true);
                    refresh.Start();
                }
                else
                {
                    await HandleTaskMessage(args.WebMessageAsJson);
                }
            };
            core.Navigate(CanvasAddress);
        }
        catch (Exception ex)
        {
            if (IsDisposed) return;
            MessageBox.Show(this, $"无法启动 WebView2。请检查 WebView2 Runtime 和本机工作台文件。\n{ex.GetType().Name}", Text);
            Close();
        }
    }

    private void PublishWorkspace(bool force)
    {
        try
        {
            var reports = catalog.Refresh(force || reportError is not null);
            var changes = tasks.Refresh(force || reportError is not null);
            var projects = tasks.RefreshProjects(force || reportError is not null);
            reportError = null;
            if (reports is not null) Send(new { type = "reports", reports });
            if (changes is not null) Send(new { type = "tasks", tasks = changes });
            if (projects is not null) Send(new { type = "projects", projects });
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            const string message = "无法读取本机调查报告目录。";
            if (reportError is null || force) Send(new { type = "status", message });
            reportError = message;
        }
    }

    private async Task HandleTaskMessage(string payload)
    {
        if (payload.Length > 8192) return;
        var type = "";
        try
        {
            using var document = JsonDocument.Parse(payload);
            var row = document.RootElement;
            type = TaskClient.GetString(row, "type");
            if (type == "window.action")
            {
                switch (TaskClient.GetString(row, "action"))
                {
                    case "drag":
                        ReleaseCapture();
                        SendMessage(Handle, 0xA1, 2, IntPtr.Zero);
                        break;
                    case "minimize": WindowState = FormWindowState.Minimized; break;
                    case "maximize":
                        MaximizedBounds = Screen.FromControl(this).WorkingArea;
                        WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized;
                        break;
                    case "close": Close(); break;
                }
                return;
            }
            if (type is "project.contacts" or "project.save" or "project.analyze")
            {
                var projectId = TaskClient.GetString(row, "id");
                if (projectId.Length != 0 && (projectId.Length != 32 || !projectId.All(Uri.IsHexDigit))) return;
                var input = row.TryGetProperty("request", out var requestData) ? requestData.GetRawText() : null;
                if (type == "project.analyze") await readerQueue.WaitAsync();
                JsonElement result;
                try
                {
                    result = await tasks.Execute(type[8..], projectId.Length == 0 ? null : projectId, input, group: "project");
                }
                finally
                {
                    if (type == "project.analyze") readerQueue.Release();
                }
                if (!IsDisposed) PublishWorkspace(false);
                Send(new { type, result });
                if (type == "project.save" && TaskClient.GetString(result, "status") == "ok")
                    await HandleLibrary("library.import-project", TaskClient.GetString(result, "id"), null, "");
                return;
            }
            if (type is "library.list" or "library.add" or "library.sync" or "library.search" or "library.import-project")
            {
                var id = TaskClient.GetString(row, "id");
                var input = row.TryGetProperty("request", out var data) ? data.GetRawText() : null;
                await HandleLibrary(type, id.Length == 0 ? null : id, input, TaskClient.GetString(row, "token"));
                return;
            }
            if (type == "task.create" && row.TryGetProperty("request", out var request))
            {
                if (creatingTask) return;
                creatingTask = true;
                try
                {
                    var result = await tasks.Execute("create", request: request.GetRawText());
                    var id = TaskClient.GetString(result, "id");
                    if (id.Length == 0) { TaskError(result); return; }
                    Send(new { type = "task.accepted", id });
                    PublishWorkspace(false);
                    _ = RunTask(id);
                }
                finally { creatingTask = false; }
                return;
            }

            var taskId = TaskClient.GetString(row, "id");
            if (taskId.Length != 32 || !taskId.All(Uri.IsHexDigit)) return;
            if (type == "task.run") await RunTask(taskId);
            if (type == "task.stop")
            {
                var result = await tasks.Execute("stop", taskId);
                if (TaskClient.GetString(result, "error").Length > 0) TaskError(result);
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException or InvalidOperationException or System.ComponentModel.Win32Exception or KeyNotFoundException or UnauthorizedAccessException)
        {
            var code = ex.Message == "task_runtime_missing" ? ex.Message : "task_operation_failed";
            if (type.StartsWith("project.", StringComparison.Ordinal))
                Send(new { type, result = new { status = "failed", error = code } });
            else
                Send(new { type = "task.error", code });
        }
    }

    private async Task HandleLibrary(string type, string? id, string? input, string token)
    {
        var mutating = type is "library.add" or "library.sync" or "library.import-project";
        if (mutating) await readerQueue.WaitAsync();
        try
        {
            if (IsDisposed) return;
            var result = await tasks.Execute(type[8..], id, input, group: "library");
            Send(new { type, token, result });
        }
        catch (Exception ex) when (ex is JsonException or IOException or InvalidOperationException or System.ComponentModel.Win32Exception or KeyNotFoundException or UnauthorizedAccessException)
        {
            Send(new { type, token, result = new { status = "failed", error = "library_operation_failed" } });
        }
        finally
        {
            if (mutating) readerQueue.Release();
        }
    }

    private async Task RunTask(string id)
    {
        if (!activeTasks.Add(id)) return;
        try
        {
            var result = await tasks.Execute("run", id);
            if (TaskClient.GetString(result, "status") == "failed") TaskError(result);
            if (!IsDisposed) PublishWorkspace(false);
        }
        catch (Exception ex) when (ex is JsonException or IOException or InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            Send(new { type = "task.error", code = "task_operation_failed" });
        }
        finally { activeTasks.Remove(id); }
    }

    private void TaskError(JsonElement result) => Send(new { type = "task.error", code = TaskClient.GetString(result, "error") });

    private void Send(object message)
    {
        if (!IsDisposed) view.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(message));
    }
}
