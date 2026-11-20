namespace WeChatResearch.Workbench;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        var workspace = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "WeChatResearch");
        if (args.Length == 2 && args[0] == "--workspace")
        {
            workspace = args[1];
        }
        else if (args.Length != 0)
        {
            MessageBox.Show("用法：WeChatResearch.Workbench.exe [--workspace 资料目录]", "微信调查工作台");
            return;
        }

        Application.Run(new WorkbenchWindow(Path.GetFullPath(workspace)));
    }
}
