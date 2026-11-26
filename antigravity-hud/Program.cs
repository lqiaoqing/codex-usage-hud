using System.Text.Json;

namespace AntigravityUsageHud;

internal static class Program
{
    [MTAThread]
    private static void Main(string[] args)
    {
        if (args.FirstOrDefault() == "conversation")
        {
            Console.WriteLine(JsonSerializer.Serialize(new { ConversationId = DesktopQuota.FindCurrentConversation() }));
            return;
        }
        var action = args.FirstOrDefault() switch
        {
            "open" => QuotaAction.OpenPage,
            "refresh" or "refresh-open" => QuotaAction.RefreshOpenPage,
            _ => QuotaAction.Read
        };
        Console.WriteLine(JsonSerializer.Serialize(DesktopQuota.Read(action)));
    }
}
