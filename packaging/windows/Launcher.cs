using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Launcher
{
    // Windows CommandLineToArgvW / C runtime quoting, including trailing backslashes.
    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1).Append(c);
            else result.Append('\\', slashes).Append(c);
            slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        bool test = args.Length == 2 && args[0] == "--self-test";
        try
        {
            if (args.Length != 0 && !test) throw new ArgumentException("不支持的启动参数。");
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string data = test ? Path.Combine(Path.GetFullPath(args[1]), "application-data")
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PneumoconiosisAnnotator");
            string slicer = Path.Combine(root, "Slicer", "Slicer.exe");
            if (!File.Exists(slicer) || !File.Exists(Path.Combine(root, "runtime", "python", "python.exe")))
                throw new FileNotFoundException("程序文件不完整，请重新安装尘肺胸片标注。");
            Directory.CreateDirectory(data);
            string logs = Path.Combine(data, "logs");
            Directory.CreateDirectory(logs);
            string script = Path.Combine(root, test ? "qa\\slicer_package_check.py" : "scripts\\start.py");
            string arguments = "--disable-settings --ignore-slicerrc --no-splash "
                + (test ? "--testing " : "") + "--additional-module-path "
                + Quote(Path.Combine(root, "PneumoconiosisAnnotator")) + " --python-script " + Quote(script);
            var info = new ProcessStartInfo(slicer, arguments);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WorkingDirectory = data;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            foreach (string name in new[] { "PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH" })
                info.EnvironmentVariables.Remove(name);
            info.EnvironmentVariables["PNEUMO_DATA_ROOT"] = data;
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            if (test) info.EnvironmentVariables["PNEUMO_TEST_OUTPUT"] = Path.GetFullPath(args[1]);
            string path = Path.Combine(logs, "launch-" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff") + "-" + Process.GetCurrentProcess().Id + ".log");
            using (var log = new StreamWriter(path, false, new UTF8Encoding(false)))
            using (var process = new Process())
            {
                object gate = new object();
                log.AutoFlush = true;
                process.StartInfo = info;
                DataReceivedEventHandler receive = (sender, e) => { if (e.Data != null) lock (gate) log.WriteLine(e.Data); };
                process.OutputDataReceived += receive;
                process.ErrorDataReceived += receive;
                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            if (!test) MessageBox.Show(error.Message, "尘肺胸片标注", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
