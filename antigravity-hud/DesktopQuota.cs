using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Windows.Automation;

namespace AntigravityUsageHud;

internal enum QuotaState { NoWindow, PageClosed, Waiting, Available, Unavailable }
internal enum QuotaAction { Read, OpenPage, RefreshOpenPage }
internal sealed record QuotaWindow(decimal Remaining, string? Reset);
internal sealed record ModelQuota(string Name, QuotaWindow? FiveHours, QuotaWindow? Weekly);
internal sealed record QuotaReading(QuotaState State, ModelQuota[] Groups, bool CanRefresh = false, string? ConversationId = null);

internal static partial class DesktopQuota
{
    public static string? FindCurrentConversation()
    {
        var processes = Process.GetProcessesByName("Antigravity");
        try
        {
            foreach (var process in processes)
            {
                var handle = process.MainWindowHandle;
                if (handle != IntPtr.Zero && !IsIconic(handle))
                {
                    var id = CurrentConversation(AutomationElement.FromHandle(handle));
                    if (id is not null)
                        return id;
                }
            }
            return null;
        }
        catch (Exception e) when (e is ElementNotAvailableException or InvalidOperationException
            or COMException or System.ComponentModel.Win32Exception)
        {
            return null;
        }
        finally
        {
            foreach (var process in processes)
                process.Dispose();
        }
    }

    public static QuotaReading Read(QuotaAction action)
    {
        var processes = Process.GetProcessesByName("Antigravity");
        try
        {
            return ReadWindows(processes, action);
        }
        catch (Exception e) when (e is ElementNotAvailableException or InvalidOperationException
            or System.Runtime.InteropServices.COMException or System.ComponentModel.Win32Exception)
        {
            return new(QuotaState.Unavailable, []);
        }
        finally
        {
            foreach (var process in processes)
                process.Dispose();
        }
    }

    private static QuotaReading ReadWindows(Process[] processes, QuotaAction action)
    {
        var state = QuotaState.NoWindow;
        var canRefresh = false;
        string? conversation = null;
        foreach (var process in processes)
        {
            var handle = process.MainWindowHandle;
            if (handle == IntPtr.Zero)
                continue;

            if (state == QuotaState.NoWindow)
                state = QuotaState.PageClosed;
            if (IsIconic(handle))
            {
                if (action != QuotaAction.OpenPage)
                    continue;
                ShowWindowAsync(handle, 9);
                var restore = Stopwatch.StartNew();
                while (IsIconic(handle) && restore.ElapsedMilliseconds < 1000)
                    Thread.Sleep(50);
                if (IsIconic(handle))
                    continue;
            }
            var root = AutomationElement.FromHandle(handle);
            conversation ??= CurrentConversation(root);
            var settings = Find(root, ControlType.Window, "Settings");
            var refresh = settings is null ? null : Find(settings, ControlType.Button, "Refresh quota and credits data");
            var popup = refresh is null ? Find(root, ControlType.Menu, "View Usage") : null;
            var opened = false;
            if (action == QuotaAction.OpenPage && refresh is null && popup is null)
            {
                if (settings is null && Invoke(Find(root, ControlType.Button, "Settings")))
                    settings = WaitFor(root, ControlType.Window, "Settings");
                if (settings is not null && Invoke(WaitFor(settings, ControlType.Button, "Models")))
                {
                    refresh = WaitFor(settings, ControlType.Button, "Refresh quota and credits data");
                    opened = refresh is not null;
                }
            }
            var requested = action == QuotaAction.RefreshOpenPage && Invoke(refresh);

            // 只读取用量页或用量菜单中的文本，避免把会话里的数字识别成额度。
            var scope = refresh is not null ? settings : popup;
            if (scope is null)
                continue;

            var groups = ReadPage(scope, refresh, opened || requested);
            if (groups.Length > 0)
                return new(QuotaState.Available, groups, refresh is not null, conversation);
            state = QuotaState.Waiting;
            canRefresh |= refresh is not null;
        }
        return new(state, [], canRefresh, conversation);
    }

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr window);

    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr window, int command);

    private static ModelQuota[] ReadPage(AutomationElement scope, AutomationElement? refresh, bool wait)
    {
        var timer = Stopwatch.StartNew();
        var cache = new CacheRequest();
        cache.Add(AutomationElement.NameProperty);
        cache.AutomationElementMode = AutomationElementMode.None;
        if (wait)
            Thread.Sleep(200);
        do
        {
            if (refresh?.Current.IsEnabled != false)
            {
                using var active = cache.Activate();
                var text = scope.FindAll(TreeScope.Descendants, new PropertyCondition(
                    AutomationElement.ControlTypeProperty, ControlType.Text));
                var groups = Parse(text.Cast<AutomationElement>().Select(element => element.Cached.Name));
                if (groups.Length > 0)
                    return groups;
            }
            if (!wait)
                break;
            Thread.Sleep(125);
        } while (timer.ElapsedMilliseconds < 3500);
        return [];
    }

    private static string? CurrentConversation(AutomationElement root)
    {
        var documents = root.FindAll(TreeScope.Descendants, new AndCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Document),
            new PropertyCondition(AutomationElement.AutomationIdProperty, "RootWebArea")));
        foreach (AutomationElement doc in documents)
        {
            if (doc.Current.IsOffscreen
                || doc.GetCurrentPropertyValue(ValuePattern.ValueProperty, true) is not string value
                || !Uri.TryCreate(value, UriKind.Absolute, out var address)
                || !address.IsLoopback)
                continue;
            var parts = address.AbsolutePath.Trim('/').Split('/');
            if (parts.Length == 2 && parts[0] == "c" && Guid.TryParseExact(parts[1], "D", out var id))
                return id.ToString("D");
        }
        return null;
    }

    private static AutomationElement? Find(AutomationElement scope, ControlType type, string name) =>
        scope.FindFirst(TreeScope.Descendants, new AndCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty, type),
            new PropertyCondition(AutomationElement.NameProperty, name)));

    private static AutomationElement? WaitFor(AutomationElement scope, ControlType type, string name)
    {
        for (var i = 0; i < 8; i++)
        {
            var element = Find(scope, type, name);
            if (element is not null)
                return element;
            Thread.Sleep(125);
        }
        return null;
    }

    private static bool Invoke(AutomationElement? element)
    {
        if (element is null || !element.Current.IsEnabled
            || !element.TryGetCurrentPattern(InvokePattern.Pattern, out var pattern))
            return false;
        ((InvokePattern)pattern).Invoke();
        return true;
    }

    internal static ModelQuota[] Parse(IEnumerable<string> text)
    {
        var lines = text.Select(line => line.Trim()).Where(line => line.Length > 0).ToArray();
        var groups = new List<ModelQuota>();
        var name = "";
        QuotaWindow? fiveHours = null;
        QuotaWindow? weekly = null;
        for (var i = 0; i < lines.Length; i++)
        {
            var line = lines[i];
            if (GroupName().IsMatch(line))
            {
                if (fiveHours is not null || weekly is not null)
                    groups.Add(new(name, fiveHours, weekly));
                (name, fiveHours, weekly) = (line, null, null);
                continue;
            }

            if (name.Length == 0 || WindowKind(line) is not { } isWeekly)
                continue;

            decimal? percent = null;
            string? reset = null;
            while (i + 1 < lines.Length && !GroupName().IsMatch(lines[i + 1])
                && WindowKind(lines[i + 1]) is null)
            {
                var value = lines[++i];
                var match = Percent().Match(value);
                if (!match.Success && i + 1 < lines.Length
                    && lines[i + 1].StartsWith("%", StringComparison.Ordinal))
                    match = Percent().Match(value + lines[i + 1]);
                if (percent is null && match.Success && decimal.TryParse(match.Groups[1].Value.Replace(',', '.'),
                    NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out var amount)
                    && amount is >= 0 and <= 100)
                    percent = amount;

                var countdown = ResetDuration().Match(value);
                if (countdown.Success)
                    reset = RelativeDuration().Replace(countdown.Groups[1].Value, part =>
                    {
                        var unit = char.ToLowerInvariant(part.Groups[2].Value[0]);
                        return part.Groups[1].Value + (unit switch
                        {
                            'd' => " 天", 'h' => " 小时", 'm' => " 分钟", _ => " 秒",
                        });
                    });
            }

            if (percent is not { } remaining)
                continue;
            if (isWeekly)
                weekly = new(remaining, reset);
            else
                fiveHours = new(remaining, reset);
        }
        if (fiveHours is not null || weekly is not null)
            groups.Add(new(name, fiveHours, weekly));
        return groups.ToArray();
    }

    private static bool? WindowKind(string line) => line.ToLowerInvariant() switch
    {
        "weekly limit remaining" or "weekly limit" => true,
        "five hour limit remaining" or "5h limit" => false,
        _ => null,
    };

    [GeneratedRegex(@"^(?:Gemini Models|Claude (?:and|\+) GPT Models|Gemini 4 Argon(?: Models)?)$", RegexOptions.IgnoreCase)]
    private static partial Regex GroupName();

    [GeneratedRegex(@"^(\d{1,3}(?:[.,]\d+)?)\s*%(?:\s+left)?$", RegexOptions.IgnoreCase)]
    private static partial Regex Percent();

    [GeneratedRegex(@"(?:resets? in|refresh(?:es)? in)\s+((?:\d+\s*(?:days?|hours?|minutes?|seconds?|[dhms])\s*)+)", RegexOptions.IgnoreCase)]
    private static partial Regex ResetDuration();

    [GeneratedRegex(@"(\d+)\s*(days?|hours?|minutes?|seconds?|[dhms])", RegexOptions.IgnoreCase)]
    private static partial Regex RelativeDuration();
}
