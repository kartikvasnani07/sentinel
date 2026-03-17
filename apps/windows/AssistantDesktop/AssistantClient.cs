using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AssistantDesktop;

public class AssistantClient
{
    private readonly HttpClient _http;

    public AssistantClient(string baseUrl)
    {
        _http = new HttpClient { BaseAddress = new Uri(baseUrl) };
    }

    public async Task<AssistantResponse> SendAsync(
        string text,
        bool? confirm = null,
        string? modelPreference = null,
        string? accessLevel = null,
        IEnumerable<string>? attachments = null)
    {
        var payload = new Dictionary<string, object?> { ["text"] = text };
        if (confirm.HasValue)
        {
            payload["confirm"] = confirm.Value;
        }
        if (!string.IsNullOrWhiteSpace(modelPreference))
        {
            payload["model"] = modelPreference;
        }
        if (!string.IsNullOrWhiteSpace(accessLevel))
        {
            payload["access_level"] = accessLevel;
        }
        if (attachments is not null)
        {
            payload["attachments"] = attachments.ToArray();
        }

        var json = JsonSerializer.Serialize(payload);
        var response = await _http.PostAsync("/api/command", new StringContent(json, Encoding.UTF8, "application/json"));
        var body = await response.Content.ReadAsStringAsync();
        return JsonSerializer.Deserialize<AssistantResponse>(body) ?? new AssistantResponse("No response.", false);
    }

    public async Task<AssistantStatus?> GetStatusAsync()
    {
        try
        {
            var response = await _http.GetAsync("/api/status");
            var body = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<AssistantStatus>(body);
        }
        catch
        {
            return null;
        }
    }
}

public record AssistantResponse(
    string response,
    bool needs_confirmation,
    [property: JsonPropertyName("model_used")] string? ModelUsed = null);

public record AssistantStatus(
    string status,
    [property: JsonPropertyName("cloud_ready")] bool CloudReady,
    [property: JsonPropertyName("model_preference")] string? ModelPreference,
    [property: JsonPropertyName("access_level")] string? AccessLevel,
    [property: JsonPropertyName("models")] List<AssistantModelOption>? Models);

public record AssistantModelOption(
    string id,
    string label);
