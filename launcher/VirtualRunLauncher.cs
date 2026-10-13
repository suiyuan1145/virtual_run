using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Forms;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using Microsoft.Win32;

[assembly: AssemblyTitle("虚拟跑步")]
[assembly: AssemblyDescription("电脑版晨跑、登录与模拟器环境管理")]
[assembly: AssemblyVersion("1.4.5.0")]
[assembly: AssemblyFileVersion("1.4.5.0")]

internal static class VirtualRunLauncher
{
    static bool TestMode { get { return Environment.GetEnvironmentVariable("VIRTUAL_RUN_TEST_MODE") == "1"; } }
    static string DesktopDirectory()
    {
        return TestMode ? Path.Combine(DataDirectory(), "test-shortcuts", "Desktop") :
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
    }
    static string MenuDirectory()
    {
        return TestMode ? Path.Combine(DataDirectory(), "test-shortcuts", "StartMenu") :
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "虚拟跑步");
    }
    static string RegisterInstallation(string directory)
    {
        string data = DataDirectory();
        string root = Path.Combine(data, "app") + Path.DirectorySeparatorChar;
        if (!Path.GetFullPath(directory).StartsWith(root, StringComparison.OrdinalIgnoreCase))
            throw new IOException("程序目录不在本软件安装目录内。");
        Directory.CreateDirectory(data);
        string uninstaller = Path.Combine(data, "卸载虚拟跑步.exe");
        File.Copy(Path.Combine(directory, "卸载虚拟跑步.exe"), uninstaller, true);
        string token;
        using (var stream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(data.ToLowerInvariant())))
            token = Hash(stream).Substring(0, 16);
        string keyName = TestMode ? "VirtualRunDesktop-QA-" + token : "VirtualRunDesktop";
        string registryPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\" + keyName;
        var record = new { product = "VirtualRunDesktop", data_directory = data,
            registry_path = registryPath, test_mode = TestMode, app_directory = directory,
            desktop_link = Path.Combine(DesktopDirectory(), "虚拟跑步.lnk"),
            menu_directory = MenuDirectory(),
            task_name = TestMode ? "VirtualRun-Uninstall-QA-" + token : "VirtualRun-Morning-0700" };
        File.WriteAllText(Path.Combine(data, ".installation.json"), new JavaScriptSerializer().Serialize(record));
        using (var key = Registry.CurrentUser.CreateSubKey(registryPath))
        {
            string previousRoot = key.GetValue("VirtualRunDataDirectory") as string;
            if (previousRoot != null && !String.Equals(Path.GetFullPath(previousRoot), data, StringComparison.OrdinalIgnoreCase))
                throw new IOException("已登记的安装目录不同，未覆盖其他安装的卸载信息。");
            string version = Assembly.GetExecutingAssembly().GetName().Version.ToString(3);
            key.SetValue("DisplayName", TestMode ? "虚拟跑步（卸载验证）" : "虚拟跑步");
            key.SetValue("DisplayVersion", version);
            key.SetValue("Publisher", "VirtualRun Desktop");
            key.SetValue("InstallLocation", directory);
            key.SetValue("DisplayIcon", Path.Combine(directory, "虚拟跑步.exe") + ",0");
            key.SetValue("UninstallString", Quote(uninstaller) + " --data-dir " + Quote(data));
            key.SetValue("VirtualRunDataDirectory", data);
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            long bytes = 0;
            foreach (var file in Directory.GetFiles(directory, "*", SearchOption.AllDirectories)) bytes += new FileInfo(file).Length;
            key.SetValue("EstimatedSize", (int)Math.Min(Int32.MaxValue, (bytes + 1023) / 1024), RegistryValueKind.DWord);
            if (key.GetValue("InstallDate") == null) key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
        }
        return registryPath;
    }
    static string DataDirectory()
    {
        string data = Environment.GetEnvironmentVariable("VIRTUAL_RUN_DATA_DIR");
        if (String.IsNullOrEmpty(data))
            data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VirtualRunDesktop");
        data = Path.GetFullPath(data);
        Environment.SetEnvironmentVariable("VIRTUAL_RUN_DATA_DIR", data);
        return data;
    }

    static string Hash(Stream stream)
    {
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }

    static string InstallFolder()
    {
        string source = Path.GetDirectoryName(Application.ExecutablePath);
        if (File.Exists(Path.Combine(source, ".complete"))) return source;
        string manifestPath = Path.Combine(source, "package-manifest.json");
        if (!File.Exists(manifestPath))
            throw new FileNotFoundException("请完整解压软件包，再启动虚拟跑步.exe。");
        var manifest = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(manifestPath));
        string version = (string)manifest["version"], digest;
        using (var file = File.OpenRead(manifestPath)) digest = Hash(file);
        string baseDirectory = Path.Combine(DataDirectory(), "app");
        string target = Path.Combine(baseDirectory, version + "-folder-" + digest.Substring(0, 16));
        using (var mutex = new Mutex(false, "Local\\VirtualRunFolder-" + digest.Substring(0, 16)))
        {
            bool acquired = false;
            try
            {
                try { acquired = mutex.WaitOne(60000); }
                catch (AbandonedMutexException) { acquired = true; }
                if (!acquired) throw new IOException("另一窗口正在准备软件，请稍后重试。");
                string marker = Path.Combine(target, ".complete");
                if (File.Exists(marker) && File.ReadAllText(marker) == digest) return target;
                if (Directory.Exists(target)) throw new IOException("软件目录不完整：" + target);
                Directory.CreateDirectory(baseDirectory);
                string staging = Path.Combine(baseDirectory, ".preparing-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(staging);
                string safeSource = Path.GetFullPath(source) + Path.DirectorySeparatorChar;
                string safeTarget = Path.GetFullPath(staging) + Path.DirectorySeparatorChar;
                foreach (object item in (System.Collections.IEnumerable)manifest["files"])
                {
                    var entry = (Dictionary<string, object>)item;
                    string relative = (string)entry["path"];
                    string original = Path.GetFullPath(Path.Combine(source, relative));
                    string destination = Path.GetFullPath(Path.Combine(staging, relative));
                    if (!original.StartsWith(safeSource, StringComparison.OrdinalIgnoreCase) ||
                        !destination.StartsWith(safeTarget, StringComparison.OrdinalIgnoreCase))
                        throw new IOException("软件包清单包含无效路径。");
                    using (var file = File.OpenRead(original))
                        if (Hash(file) != (string)entry["sha256"])
                            throw new IOException("软件包文件校验失败：" + relative);
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    File.Copy(original, destination);
                }
                File.Copy(manifestPath, Path.Combine(staging, "package-manifest.json"));
                File.WriteAllText(Path.Combine(staging, ".complete"), digest);
                Directory.Move(staging, target);
                return target;
            }
            finally { if (acquired) mutex.ReleaseMutex(); }
        }
    }

    static string CreateLink(string directory, string name, string target, string workingDirectory, string icon)
    {
        Directory.CreateDirectory(directory);
        string path = Path.Combine(directory, name + ".lnk");
        object shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        object link = null;
        try
        {
            link = shell.GetType().InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
            if (File.Exists(path))
            {
                string previous = (string)link.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, null, link, null);
                string description = (string)link.GetType().InvokeMember("Description", BindingFlags.GetProperty, null, link, null);
                if (!description.Contains("VirtualRunDesktop") &&
                    Path.GetFileName(previous) != "虚拟跑步.exe" && Path.GetFileName(previous) != "启动虚拟跑步.cmd")
                    throw new IOException("已有同名快捷方式属于其他程序，未覆盖：" + path);
            }
            foreach (var property in new Dictionary<string, object> {
                { "TargetPath", target }, { "WorkingDirectory", workingDirectory },
                { "IconLocation", icon + ",0" }, { "WindowStyle", 1 },
                { "Description", "虚拟跑步 · 电脑版晨跑（VirtualRunDesktop）" } })
                link.GetType().InvokeMember(property.Key, BindingFlags.SetProperty, null, link, new object[] { property.Value });
            link.GetType().InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            return path;
        }
        finally
        {
            if (link != null) Marshal.FinalReleaseComObject(link);
            Marshal.FinalReleaseComObject(shell);
        }
    }

    static List<string> CreateShortcuts(string directory, string kind)
    {
        if (kind != "all" && kind != "desktop" && kind != "start-menu")
            throw new ArgumentException("快捷方式选项无效。");
        string target = Path.Combine(directory, "虚拟跑步.exe");
        if (!File.Exists(target)) throw new FileNotFoundException("未找到已安装的启动程序。");
        var paths = new List<string>();
        if (kind == "all" || kind == "desktop")
            paths.Add(CreateLink(DesktopDirectory(), "虚拟跑步", target, directory, target));
        if (kind == "all" || kind == "start-menu")
        {
            string menu = MenuDirectory();
            paths.Add(CreateLink(menu, "虚拟跑步", target, directory, target));
            paths.Add(CreateLink(menu, "使用说明", Path.Combine(directory, "使用方法.txt"), directory, target));
            string uninstaller = Path.Combine(DataDirectory(), "卸载虚拟跑步.exe");
            if (File.Exists(uninstaller))
                paths.Add(CreateLink(menu, "卸载虚拟跑步", uninstaller, DataDirectory(), target));
        }
        return paths;
    }

    static string Quote(string value)
    {
        // Windows command-line quoting, including trailing backslashes.
        var result = new System.Text.StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { ++slashes; continue; }
            result.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
            result.Append(character);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    static string Prepare()
    {
        Assembly assembly = Assembly.GetExecutingAssembly();
        using (Stream payload = assembly.GetManifestResourceStream("VirtualRun.Payload"))
        {
            if (payload == null) return InstallFolder();
            string[] metadata;
            using (var reader = new StreamReader(assembly.GetManifestResourceStream("VirtualRun.Metadata")))
                metadata = reader.ReadToEnd().Trim().Split('\n');
            string version = metadata[0].Trim(), expectedHash = metadata[1].Trim();
            string data = DataDirectory();
            string baseDirectory = Path.Combine(data, "app");
            string target = Path.Combine(baseDirectory, version + "-" + expectedHash.Substring(0, 16));
            using (var mutex = new Mutex(false, "Local\\VirtualRunPrepare-" + expectedHash.Substring(0, 16)))
            {
                bool acquired = false;
                try
                {
                    try { acquired = mutex.WaitOne(60000); }
                    catch (AbandonedMutexException) { acquired = true; }
                    if (!acquired) throw new IOException("另一窗口正在准备软件，请稍后重试。");
                    string marker = Path.Combine(target, ".complete");
                    if (File.Exists(marker) && File.ReadAllText(marker) == expectedHash)
                        return target;
                    if (Directory.Exists(target))
                        throw new IOException("软件目录不完整：" + target);
                    using (var progress = new Form())
                    {
                        progress.Text = "虚拟跑步 · 首次准备";
                        progress.Width = 460; progress.Height = 135;
                        progress.StartPosition = FormStartPosition.CenterScreen;
                        progress.ControlBox = false;
                        var label = new Label { Text = "正在准备内置运行环境，请稍候……", Dock = DockStyle.Top, Height = 40 };
                        var bar = new ProgressBar { Dock = DockStyle.Bottom, Style = ProgressBarStyle.Marquee };
                        progress.Controls.Add(label); progress.Controls.Add(bar);
                        progress.Show(); Application.DoEvents();
                        using (var sha = SHA256.Create())
                        {
                            string actualHash = BitConverter.ToString(sha.ComputeHash(payload)).Replace("-", "").ToLowerInvariant();
                            if (actualHash != expectedHash) throw new IOException("内置软件包校验失败，请重新获取 EXE。");
                        }
                        payload.Position = 0;
                        Directory.CreateDirectory(baseDirectory);
                        string staging = Path.Combine(baseDirectory, ".preparing-" + Guid.NewGuid().ToString("N"));
                        Directory.CreateDirectory(staging);
                        string safeRoot = Path.GetFullPath(staging) + Path.DirectorySeparatorChar;
                        string prefix = "虚拟跑步-" + version + "/";
                        using (var zip = new ZipArchive(payload, ZipArchiveMode.Read, true))
                        {
                            int count = 0;
                            foreach (var entry in zip.Entries)
                            {
                                if (!entry.FullName.StartsWith(prefix, StringComparison.Ordinal))
                                    throw new IOException("内置软件包结构无效。");
                                string relative = entry.FullName.Substring(prefix.Length);
                                if (relative.Length == 0) continue;
                                string destination = Path.GetFullPath(Path.Combine(staging, relative));
                                if (!destination.StartsWith(safeRoot, StringComparison.OrdinalIgnoreCase))
                                    throw new IOException("内置软件包含无效路径。");
                                if (entry.Name.Length == 0) Directory.CreateDirectory(destination);
                                else
                                {
                                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                                    using (Stream input = entry.Open())
                                    using (Stream output = File.Create(destination)) input.CopyTo(output);
                                }
                                if (++count % 30 == 0) Application.DoEvents();
                            }
                        }
                        if (!File.Exists(Path.Combine(staging, "runtime", "pythonw.exe")) ||
                            !File.Exists(Path.Combine(staging, "android_location_test.py")))
                            throw new IOException("内置运行环境不完整。");
                        File.WriteAllText(Path.Combine(staging, ".complete"), expectedHash);
                        Directory.Move(staging, target);
                        progress.Close();
                    }
                    return target;
                }
                finally { if (acquired) mutex.ReleaseMutex(); }
            }
        }
    }

    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        try
        {
            bool shortcuts = args.Length == 3 && args[0] == "--create-shortcuts";
            if (args.Length != 0 && !shortcuts && !(args.Length == 2 &&
                (args[0] == "--ui-smoke" || args[0] == "--diagnostics" || args[0] == "--prepare-only" || args[0] == "--install")))
                throw new ArgumentException("启动参数无效。");
            string directory = Prepare();
            if (args.Length == 0 || shortcuts || (args.Length == 2 && args[0] == "--install"))
            {
                string registered = RegisterInstallation(directory);
                if (args.Length == 2 && args[0] == "--install")
                {
                    var links = CreateShortcuts(directory, "all");
                    File.WriteAllText(args[1], new JavaScriptSerializer().Serialize(new { app_directory = directory,
                        data_directory = DataDirectory(), registry_path = registered, paths = links }));
                    return 0;
                }
            }
            if (shortcuts)
            {
                var paths = CreateShortcuts(directory, args[1]);
                File.WriteAllText(args[2], new JavaScriptSerializer().Serialize(new { paths = paths, app_directory = directory }));
                return 0;
            }
            if (args.Length == 2 && args[0] == "--prepare-only")
            {
                File.WriteAllText(args[1], directory);
                return 0;
            }
            if (args.Length == 0 && !File.Exists(Path.Combine(directory, ".shortcuts-created")))
            {
                try
                {
                    CreateShortcuts(directory, "all");
                    File.WriteAllText(Path.Combine(directory, ".shortcuts-created"), "created");
                }
                catch (Exception error)
                {
                    MessageBox.Show("程序可以继续使用，但快捷方式未创建：\n" + error.Message +
                        "\n可在设置中重新创建。", "虚拟跑步", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
            }
            string runtime = Path.Combine(directory, "runtime");
            string python = Path.Combine(runtime, args.Length == 0 ? "pythonw.exe" : "python.exe");
            string script = Path.Combine(directory, "android_location_test.py");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("缺少软件运行文件。请使用完整 EXE 或完整解压软件包。");
            var start = new ProcessStartInfo(python, "-X utf8 " + Quote(script));
            if (args.Length == 2) start.Arguments += " " + args[0] + " " + Quote(Path.GetFullPath(args[1]));
            start.WorkingDirectory = directory;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["PYTHONHOME"] = runtime;
            start.EnvironmentVariables["TCL_LIBRARY"] = Path.Combine(runtime, "tcl", "tcl8.6");
            start.EnvironmentVariables["TK_LIBRARY"] = Path.Combine(runtime, "tcl", "tk8.6");
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            start.EnvironmentVariables.Remove("PYTHONPATH");
            using (Process child = Process.Start(start))
            {
                if (args.Length != 0) { child.WaitForExit(); return child.ExitCode; }
            }
            return 0;
        }
        catch (Exception error)
        {
            if (args.Length == 3 && args[0] == "--create-shortcuts")
            {
                File.WriteAllText(args[2], new JavaScriptSerializer().Serialize(new { error = error.Message }));
                return 1;
            }
            MessageBox.Show("无法启动虚拟跑步：\n" + error.Message, "虚拟跑步", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
