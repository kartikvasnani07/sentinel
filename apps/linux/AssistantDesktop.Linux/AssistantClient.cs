using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace AssistantDesktop.Linux;

public class AssistantClient
{
    private readonly HttpClient _http;

    public AssistantClient(string baseUrl)
    {
        _http = new HttpClient { BaseAddress = new Uri(baseUrl) };
    }

    public async Task<AssistantResponse> SendAsync(string text, bool? confirm = null)
    {
        var payload = new Dictionary<string, object?> { ["text"] = text };
        if (confirm.HasValue)
        {
            payload["confirm"] = confirm.Value;
        }

        var json = JsonSerializer.Serialize(payload);
        var response = await _http.PostAsync("/api/command", new StringContent(json, Encoding.UTF8, "application/json"));
        var body = await response.Content.ReadAsStringAsync();
        return JsonSerializer.Deserialize<AssistantResponse>(body) ?? new AssistantResponse("No response.", false);
    }

    public async Task<bool> PingAsync()
    {
        try
        {
            var response = await _http.GetAsync("/api/status");
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }
}

public record AssistantResponse(string response, bool needs_confirmation);
