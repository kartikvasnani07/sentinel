using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace AssistantDesktop;

public class ChatMessage : INotifyPropertyChanged
{
    private bool _isJsonVisible;
    private string _displayText;
    private bool _isWeatherExpanded;

    public ChatMessage(string text, bool isUser, string? jsonText = null, AssistantWeather? weather = null)
    {
        Text = text;
        IsUser = isUser;
        JsonText = jsonText ?? string.Empty;
        Weather = weather;
        _displayText = text;
    }

    public string Text { get; }
    public string JsonText { get; }
    public bool IsUser { get; }
    public AssistantWeather? Weather { get; }
    public bool HasWeather => Weather is not null;
    public bool IsWeatherVisible => HasWeather && !IsJsonVisible;
    public bool IsWeatherExpanded
    {
        get => _isWeatherExpanded;
        private set
        {
            _isWeatherExpanded = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(WeatherToggleLabel));
        }
    }
    public string WeatherToggleLabel => IsWeatherExpanded ? "Collapse" : "Expand";
    public string WeatherLocation => Weather?.Location ?? "";
    public string WeatherCondition => Weather?.Description ?? Weather?.Condition ?? "";
    public string WeatherTempLabel => Weather?.TempC is double temp ? $"{temp:0.#}°C" : "N/A";
    public string WeatherFeelsLikeLabel => Weather?.FeelsLikeC is double temp ? $"{temp:0.#}°C" : "N/A";
    public string WeatherHumidityLabel => Weather?.Humidity is double value ? $"{value:0.#}%" : "N/A";
    public string WeatherWindLabel => Weather?.WindKph is double value ? $"{value:0.#} km/h" : "N/A";
    public string WeatherPressureLabel => Weather?.PressureHpa is double value ? $"{value:0} hPa" : "N/A";
    public string WeatherLink => Weather?.Link ?? "";

    public string DisplayText
    {
        get => _displayText;
        private set
        {
            _displayText = value;
            OnPropertyChanged();
        }
    }

    public bool IsJsonVisible
    {
        get => _isJsonVisible;
        private set
        {
            _isJsonVisible = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(IsWeatherVisible));
        }
    }

    public void ToggleJson()
    {
        if (string.IsNullOrWhiteSpace(JsonText))
        {
            return;
        }
        IsJsonVisible = !IsJsonVisible;
        DisplayText = IsJsonVisible ? JsonText : Text;
    }

    public void ToggleWeatherExpanded()
    {
        if (!HasWeather)
        {
            return;
        }
        IsWeatherExpanded = !IsWeatherExpanded;
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged([CallerMemberName] string? name = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
