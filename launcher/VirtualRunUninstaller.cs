using System;
using System.IO;
using System.Diagnostics;
using System.Reflection;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Management;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using Microsoft.Win32;

[assembly: AssemblyTitle("卸载虚拟跑步")]
[assembly: AssemblyVersion("1.4.3.0")]
[assembly: AssemblyFileVersion("1.4.3.0")]

internal static class VirtualRunUninstaller
{
    static string Root, TempDirectory;
    static Dictionary<string, object> Record;
    static JavaScriptSerializer Json = new JavaScriptSerializer();
    static string Quote(string value) { return "\"" + value + "\""; }
    static string PsQuote(string value) { return "'" + value.Replace("'", "''") + "'"; }
    static string Canonical(string value) { return Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar); }
    static bool Within(string path, string directory)
    {
        return Canonical(path).StartsWith(Canonical(directory) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }
    static void Guard(string path)
    {
        if (!Within(path, Root)) throw new IOException("清理目标不在本软件数据目录内：" + path);
        string parent = Canonical(path);
        while (!String.Equals(parent, Root, StringComparison.OrdinalIgnoreCase))
        {
            if ((File.Exists(parent) || Directory.Exists(parent)) &&
                (File.GetAttributes(parent) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("清理路径被重定向，未卸载：" + parent);
            parent = Path.GetDirectoryName(parent);
        }
    }
    static void NoLinks(string path)
    {
        if (!File.Exists(path) && !Directory.Exists(path)) return;
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("目录含有链接或重定向，未清理：" + path);
        if (Directory.Exists(path)) foreach (var child in Directory.GetFileSystemEntries(path)) NoLinks(child);
    }
    static void DeleteOwned(string path)
    {
        Guard(path); NoLinks(path);
        if (Directory.Exists(path)) Directory.Delete(path, true);
        else if (File.Exists(path)) File.Delete(path);
    }
    static void LoadRecord()
    {
        Root = Canonical(Root);
        if (Root.Length < 8 || String.Equals(Root, Path.GetPathRoot(Root).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
            throw new IOException("安装目录无效。");
        if ((File.GetAttributes(Root) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("安装目录被重定向，未卸载。");
        Guard(Path.Combine(Root, ".installation.json"));
        Record = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(Root, ".installation.json")));
        if ((string)Record["product"] != "VirtualRunDesktop" || Canonical((string)Record["data_directory"]) != Root)
            throw new IOException("无法确认安装身份，未卸载。");
        string expected = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\VirtualRunDesktop";
        string key = (string)Record["registry_path"];
        bool test = (bool)Record["test_mode"];
        if ((!test && key != expected) || (test && !key.StartsWith(expected + "-QA-", StringComparison.Ordinal)))
            throw new IOException("卸载登记信息无效。");
        string desktop = test ? Path.Combine(Root, "test-shortcuts", "Desktop") : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string menu = test ? Path.Combine(Root, "test-shortcuts", "StartMenu") : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "虚拟跑步");
        if (Canonical((string)Record["desktop_link"]) != Canonical(Path.Combine(desktop, "虚拟跑步.lnk")) ||
            Canonical((string)Record["menu_directory"]) != Canonical(menu))
            throw new IOException("快捷方式登记目录无效。");
        using (var registered = Registry.CurrentUser.OpenSubKey(key))
            if (registered != null && Canonical((string)registered.GetValue("VirtualRunDataDirectory", "")) != Root)
                throw new IOException("登记目录与当前安装不一致。");
    }
    static List<string> Plan(bool clear)
    {
        var result = new List<string>();
        string app = Path.Combine(Root, "app");
        Guard(app);
        if (Directory.Exists(app))
        {
            if ((File.GetAttributes(app) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("程序目录被重定向，未卸载。");
            foreach (string directory in Directory.GetDirectories(app))
                if (Regex.IsMatch(Path.GetFileName(directory), @"^\d+\.\d+\.\d+(?:-folder)?-[0-9a-f]{16}$") &&
                    File.Exists(Path.Combine(directory, ".complete")) &&
                    File.Exists(Path.Combine(directory, "android_location_test.py"))) result.Add(directory);
        }
        result.Add(Path.Combine(Root, "tools", "platform-tools"));
        if (clear)
        {
            foreach (var name in new[] { "morning", "logs", "click-tests", "settings.json" })
                result.Add(Path.Combine(Root, name));
        }
        foreach (string path in result) { Guard(path); NoLinks(path); }
        return result;
    }
    static void CheckProcesses()
    {
        using (var search = new ManagementObjectSearcher("SELECT ExecutablePath, CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe' OR Name='adb.exe'"))
        using (var processes = search.Get())
            foreach (ManagementObject process in processes)
            {
                string executable = process["ExecutablePath"] as string;
                string command = process["CommandLine"] as string;
                if ((!String.IsNullOrEmpty(executable) &&
                     (Within(executable, Path.Combine(Root, "app")) || Within(executable, Path.Combine(Root, "tools")))) ||
                    (!String.IsNullOrEmpty(command) && command.IndexOf(Path.Combine(Root, "morning") + "\\", StringComparison.OrdinalIgnoreCase) >= 0))
                    throw new IOException("软件或晨跑仍在运行，请先停止晨跑并关闭软件，再重新卸载。");
            }
    }
    static string RemoveTask()
    {
        string name = (string)Record["task_name"];
        if (!(name == "VirtualRun-Morning-0700" || ((bool)Record["test_mode"] && name.StartsWith("VirtualRun-Uninstall-QA-"))))
            throw new IOException("晨跑任务名称无效。");
        string script = Path.Combine(TempDirectory, "remove-task.ps1");
        string launcher = Path.Combine(Root, "morning", "启动晨跑.ps1");
        File.WriteAllText(script, "$ErrorActionPreference='Stop'\n" +
            "$task=Get-ScheduledTask -TaskName " + PsQuote(name) + " -ErrorAction SilentlyContinue\n" +
            "if (!$task) { Write-Output 'absent'; exit 0 }\n" +
            "$owned=@($task.Actions | Where-Object { $_.Arguments -and $_.Arguments.IndexOf(" + PsQuote("\"" + launcher + "\"") + ", [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0\n" +
            "if (!$owned) { Write-Output 'unrelated'; exit 0 }\n" +
            "if ($task.State -eq 'Running') { throw 'Morning task is still running.' }\n" +
            "Unregister-ScheduledTask -TaskName " + PsQuote(name) + " -Confirm:$false\nWrite-Output 'removed'\n", new System.Text.UTF8Encoding(true));
        var start = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe"),
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(script));
        start.UseShellExecute = false; start.CreateNoWindow = true;
        start.RedirectStandardOutput = true; start.RedirectStandardError = true;
        using (var process = Process.Start(start))
        {
            var output = process.StandardOutput.ReadToEndAsync();
            var errors = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(45000)) throw new IOException("移除晨跑任务超时，未继续卸载。");
            if (process.ExitCode != 0) throw new IOException("无法移除晨跑任务，请先停止任务或检查任务计划程序。" + errors.Result);
            return output.Result.Trim();
        }
    }
    static void RemoveLink(string path)
    {
        if (!File.Exists(path)) return;
        object shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        object link = null;
        try
        {
            link = shell.GetType().InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
            string target = (string)link.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, null, link, null);
            string description = (string)link.GetType().InvokeMember("Description", BindingFlags.GetProperty, null, link, null);
            if (description.Contains("VirtualRunDesktop") &&
                (Within(target, Path.Combine(Root, "app")) || Canonical(target) == Path.Combine(Root, "卸载虚拟跑步.exe")))
                File.Delete(path);
        }
        finally
        {
            if (link != null) Marshal.FinalReleaseComObject(link);
            Marshal.FinalReleaseComObject(shell);
        }
    }
    static object Uninstall(bool clear)
    {
        var paths = Plan(clear);
        CheckProcesses();
        string morning = Path.Combine(Root, "morning"), lockPath = Path.Combine(morning, "morning.lock");
        FileStream guard = null;
        if (Directory.Exists(morning))
        {
            NoLinks(morning);
            guard = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
            try { guard.Lock(0, 1); }
            catch { guard.Dispose(); throw new IOException("晨跑任务仍在运行，请先停止任务再卸载。"); }
        }
        string task;
        try
        {
            task = RemoveTask();
            foreach (var path in paths) if (path != morning) DeleteOwned(path);
        }
        finally
        {
            if (guard != null) { guard.Unlock(0, 1); guard.Dispose(); }
        }
        if (clear && Directory.Exists(morning)) DeleteOwned(morning);
        RemoveLink((string)Record["desktop_link"]);
        string menu = (string)Record["menu_directory"];
        foreach (string name in new[] { "虚拟跑步.lnk", "使用说明.lnk", "卸载虚拟跑步.lnk" }) RemoveLink(Path.Combine(menu, name));
        if (Directory.Exists(menu) && Directory.GetFileSystemEntries(menu).Length == 0) Directory.Delete(menu);
        Registry.CurrentUser.DeleteSubKeyTree((string)Record["registry_path"], false);
        DeleteOwned(Path.Combine(Root, "卸载虚拟跑步.exe"));
        DeleteOwned(Path.Combine(Root, ".installation.json"));
        return new { status = "passed", personal_data_cleared = clear, task = task, registry_removed = true };
    }
    static int Wizard()
    {
        using (var form = new Form())
        {
            form.Text = "卸载虚拟跑步"; form.Width = 640; form.Height = 280;
            form.StartPosition = FormStartPosition.CenterScreen;
            var label = new Label { Left = 24, Top = 20, Width = 575, Height = 90,
                Text = "将移除程序文件、快捷方式、Windows 应用登记及本安装的晨跑任务。\n默认保留个人配置、保存的账号密码和日志。\nAndroid SDK、镜像及虚拟机内的应用和登录状态保留。\n请先停止晨跑并关闭软件。" };
            var clear = new CheckBox { Left = 24, Top = 120, Width = 575, Text = "同时清除本工具的个人配置、保存的账号密码和日志", Checked = false };
            var remove = new Button { Left = 380, Top = 175, Width = 100, Text = "卸载" };
            var cancel = new Button { Left = 490, Top = 175, Width = 100, Text = "取消" };
            cancel.Click += delegate { form.Close(); };
            remove.Click += delegate {
                remove.Enabled = false; cancel.Enabled = false;
                try
                {
                    Uninstall(clear.Checked);
                    MessageBox.Show(clear.Checked ? "卸载完成。已清除本工具的个人数据。" : "卸载完成。个人配置和日志已保留。", "虚拟跑步");
                    form.Close();
                }
                catch (Exception error)
                {
                    MessageBox.Show("卸载未完成：\n" + error.Message, "虚拟跑步", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    remove.Enabled = true; cancel.Enabled = true;
                }
            };
            form.Controls.AddRange(new Control[] { label, clear, remove, cancel });
            Application.Run(form);
        }
        return 0;
    }
    static void CleanupTemporaryWorker()
    {
        if (String.IsNullOrEmpty(TempDirectory) || !Within(TempDirectory, Path.GetTempPath()) ||
            !Path.GetFileName(TempDirectory).StartsWith("VirtualRun-Uninstall-")) return;
        string script = Path.Combine(TempDirectory, "cleanup.ps1");
        File.WriteAllText(script, "$ErrorActionPreference='SilentlyContinue'\n" +
            "while (Get-Process -Id " + Process.GetCurrentProcess().Id + " -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 200 }\n" +
            "Remove-Item -LiteralPath " + PsQuote(Path.Combine(TempDirectory, "worker.exe")) + " -Force\n" +
            "Remove-Item -LiteralPath " + PsQuote(Path.Combine(TempDirectory, "remove-task.ps1")) + " -Force\n" +
            "Remove-Item -LiteralPath " + PsQuote(script) + " -Force\n" +
            "Remove-Item -LiteralPath " + PsQuote(TempDirectory) + " -Force\n", new System.Text.UTF8Encoding(true));
        var start = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe"),
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(script));
        start.UseShellExecute = false; start.CreateNoWindow = true; Process.Start(start);
    }
    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        bool worker = false, clear = false;
        int parentPid = 0;
        string report = null;
        Root = Environment.GetEnvironmentVariable("VIRTUAL_RUN_DATA_DIR") ??
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VirtualRunDesktop");
        try
        {
            for (int i = 0; i < args.Length; ++i)
            {
                if (args[i] == "--data-dir" && i + 1 < args.Length) Root = args[++i];
                else if (args[i] == "--test-uninstall" && i + 1 < args.Length) report = args[++i];
                else if (args[i] == "--worker") worker = true;
                else if (args[i] == "--parent-pid" && i + 1 < args.Length) parentPid = Int32.Parse(args[++i]);
                else if (args[i] == "--clear-data") clear = true;
                else throw new ArgumentException("卸载参数无效。");
            }
            if (worker)
            {
                TempDirectory = Path.GetDirectoryName(Application.ExecutablePath);
                if (!Within(TempDirectory, Path.GetTempPath()) || !Path.GetFileName(TempDirectory).StartsWith("VirtualRun-Uninstall-"))
                    throw new IOException("卸载工作目录无效。");
                try
                {
                    using (var parent = Process.GetProcessById(parentPid))
                        if (!parent.WaitForExit(10000)) throw new IOException("卸载启动器尚未退出。");
                }
                catch (ArgumentException) { }
            }
            LoadRecord();
            if (report != null && !(bool)Record["test_mode"])
                throw new IOException("非交互卸载只允许隔离测试安装。");
            if (!worker)
            {
                string temporary = Path.Combine(Path.GetTempPath(), "VirtualRun-Uninstall-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(temporary);
                string executable = Path.Combine(temporary, "worker.exe");
                File.Copy(Application.ExecutablePath, executable);
                var start = new ProcessStartInfo(executable, "--worker --parent-pid " + Process.GetCurrentProcess().Id + " --data-dir " + Quote(Root) +
                    (report == null ? "" : " --test-uninstall " + Quote(report)) + (clear ? " --clear-data" : ""));
                start.UseShellExecute = false; start.CreateNoWindow = true;
                Process.Start(start);
                return 0;
            }
            if (report == null) return Wizard();
            File.WriteAllText(report, Json.Serialize(Uninstall(clear)));
            return 0;
        }
        catch (Exception error)
        {
            if (report != null) File.WriteAllText(report, Json.Serialize(new { status = "failed", error = error.Message }));
            else MessageBox.Show("无法卸载虚拟跑步：\n" + error.Message, "虚拟跑步", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally { if (worker) CleanupTemporaryWorker(); }
    }
}
