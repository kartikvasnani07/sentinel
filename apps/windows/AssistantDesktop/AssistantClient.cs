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
        string? mode = null,
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
        if (!string.IsNullOrWhiteSpace(mode))
        {
            payload["mode"] = mode;
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

    public async Task<AssistantHistory?> GetHistoryAsync()
    {
        try
        {
            var response = await _http.GetAsync("/api/history");
            var body = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<AssistantHistory>(body);
        }
        catch
        {
            return null;
        }
    }

    public async Task<AssistantVoicePresets?> GetVoicePresetsAsync()
    {
        try
        {
            var response = await _http.GetAsync("/api/voices");
            var body = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<AssistantVoicePresets>(body);
        }
        catch
        {
            return null;
        }
    }

    public async Task<AssistantConversation?> GetConversationAsync(string conversationId)
    {
        try
        {
            var response = await _http.GetAsync($"/api/history/{conversationId}");
            var body = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<AssistantConversation>(body);
        }
        catch
        {
            return null;
        }
    }

    public async Task<AssistantHistoryAction?> OpenConversationAsync(string conversationId)
    {
        try
        {
            var payload = new Dictionary<string, object?> { ["conversation_id"] = conversationId };
            var json = JsonSerializer.Serialize(payload);
            var response = await _http.PostAsync("/api/history/open", new StringContent(json, Encoding.UTF8, "application/json"));
            var body = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<AssistantHistoryAction>(body);
        }
        catch
        {
            return null;
        }
    }

    public async Task<AssistantHistoryAction?> DeleteConversationAsync(string conversationId)
    {
        try
        {
            var payload = new Dictionary<string, object?> { ["conversation_id"] = conversationId };
            var json = JsonSerializer.Serialize(payload);
            var response = await _http.PostAsync("/api/history/delete", new StringContent(json, Encoding.UTF8, "application/json"));
            var body = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<AssistantHistoryAction>(body);
        }
        catch
        {
            return null;
        }
    }

    public async Task<bool> SpeakAsync(string text, bool fast = false)
    {
        var payload = new Dictionary<string, object?> { ["text"] = text, ["fast"] = fast };
        var json = JsonSerializer.Serialize(payload);
        var response = await _http.PostAsync("/api/speak", new StringContent(json, Encoding.UTF8, "application/json"));
        return response.IsSuccessStatusCode;
    }

    public async Task<AssistantTranscription?> TranscribeAsync(string mode)
    {
        try
        {
            var payload = new Dictionary<string, object?> { ["mode"] = mode };
            var json = JsonSerializer.Serialize(payload);
            var response = await _http.PostAsync("/api/transcribe", new StringContent(json, Encoding.UTF8, "application/json"));
            var body = await response.Content.ReadAsStringAsync();
            return JsonSerializer.Deserialize<AssistantTranscription>(body);
        }
        catch
        {
            return null;
        }
    }

    public async Task<bool> UpdateSettingsAsync(Dictionary<string, object?> payload)
    {
        var json = JsonSerializer.Serialize(payload ?? new Dictionary<string, object?>());
        var response = await _http.PostAsync("/api/settings", new StringContent(json, Encoding.UTF8, "application/json"));
        return response.IsSuccessStatusCode;
    }
}

public record AssistantResponse(
    string response,
    bool needs_confirmation,
    [property: JsonPropertyName("model_used")] string? ModelUsed = null,
    [property: JsonPropertyName("weather")] AssistantWeather? Weather = null,
    [property: JsonPropertyName("exit_app")] bool? ExitApp = null);

public record AssistantWeather(
    [property: JsonPropertyName("location")] string? Location,
    [property: JsonPropertyName("lat")] double? Lat,
    [property: JsonPropertyName("lon")] double? Lon,
    [property: JsonPropertyName("condition")] string? Condition,
    [property: JsonPropertyName("description")] string? Description,
    [property: JsonPropertyName("kind")] string? Kind,
    [property: JsonPropertyName("temp_c")] double? TempC,
    [property: JsonPropertyName("feels_like_c")] double? FeelsLikeC,
    [property: JsonPropertyName("humidity")] double? Humidity,
    [property: JsonPropertyName("wind_kph")] double? WindKph,
    [property: JsonPropertyName("pressure_hpa")] double? PressureHpa,
    [property: JsonPropertyName("hourly")] List<AssistantWeatherHour>? Hourly,
    [property: JsonPropertyName("link")] string? Link,
    [property: JsonPropertyName("source")] string? Source);

public record AssistantWeatherHour(
    [property: JsonPropertyName("time")] string? Time,
    [property: JsonPropertyName("temp_c")] double? TempC,
    [property: JsonPropertyName("condition")] string? Condition,
    [property: JsonPropertyName("kind")] string? Kind,
    [property: JsonPropertyName("precip_mm")] double? PrecipMm);

public record AssistantStatus(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("cloud_ready")] bool CloudReady,
    [property: JsonPropertyName("model_preference")] string? ModelPreference,
    [property: JsonPropertyName("access_level")] string? AccessLevel,
    [property: JsonPropertyName("assistant_name")] string? AssistantName,
    [property: JsonPropertyName("default_create_path")] string? DefaultCreatePath,
    [property: JsonPropertyName("open_on_startup")] bool OpenOnStartup,
    [property: JsonPropertyName("clap_launch_enabled")] bool ClapLaunchEnabled,
    [property: JsonPropertyName("startup_commands")] List<string>? StartupCommands,
    [property: JsonPropertyName("models")] List<AssistantModelOption>? Models);

public record AssistantModelOption(
    string id,
    string label);

public record AssistantHistory(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("current")] string? Current,
    [property: JsonPropertyName("conversations")] List<AssistantHistoryItem>? Conversations);

public record AssistantHistoryItem(
    [property: JsonPropertyName("id")] string id,
    [property: JsonPropertyName("title")] string title,
    [property: JsonPropertyName("updated_at")] string updated_at,
    [property: JsonPropertyName("message_count")] int message_count,
    [property: JsonPropertyName("is_current")] bool is_current);

public record AssistantVoicePresets(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("current")] string? Current,
    [property: JsonPropertyName("presets")] List<AssistantVoicePreset>? Presets);

public record AssistantVoicePreset(
    string id,
    string label);

public record AssistantConversation(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("conversation")] AssistantConversationDetails? Conversation);

public record AssistantTranscription(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("text")] string? Text);

public record AssistantConversationDetails(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("created_at")] string CreatedAt,
    [property: JsonPropertyName("updated_at")] string UpdatedAt,
    [property: JsonPropertyName("messages")] List<AssistantConversationMessage>? Messages);

public record AssistantConversationMessage(
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("text")] string Text);

public record AssistantHistoryAction(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("message")] string? Message,
    [property: JsonPropertyName("conversation")] AssistantConversationDetails? Conversation);
