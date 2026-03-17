using Avalonia.Controls;
using Avalonia.Input;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace AssistantDesktop.Linux;

public partial class MainWindow : Window, INotifyPropertyChanged
{
    private readonly AssistantClient _client = new("http://127.0.0.1:8765");
    private bool _awaitingConfirmation;

    public ObservableCollection<ChatMessage> Messages { get; } = new();
    private string _statusText = "Connecting...";
    public string StatusText
    {
        get => _statusText;
        set
        {
            _statusText = value;
            OnPropertyChanged();
        }
    }

    public MainWindow()
    {
        InitializeComponent();
        DataContext = this;

        Opened += async (_, _) =>
        {
            var ok = await _client.PingAsync();
            StatusText = ok ? "Connected" : "Offline";
        };
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged([CallerMemberName] string? name = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }

    private async void Send_Click(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    {
        await SendCurrentInputAsync();
    }

    private async void InputBox_OnKeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && e.KeyModifiers == KeyModifiers.None)
        {
            e.Handled = true;
            await SendCurrentInputAsync();
        }
    }

    private async Task SendCurrentInputAsync()
    {
        var text = InputBox.Text?.Trim() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }

        Messages.Add(new ChatMessage(text, true));
        InputBox.Text = string.Empty;

        bool? confirm = null;
        if (_awaitingConfirmation)
        {
            var lowered = text.ToLowerInvariant();
            if (lowered is "yes" or "y" or "sure" or "ok" or "okay")
            {
                confirm = true;
                _awaitingConfirmation = false;
            }
            else if (lowered is "no" or "n" or "cancel")
            {
                confirm = false;
                _awaitingConfirmation = false;
            }
        }

        var response = await _client.SendAsync(text, confirm);
        Messages.Add(new ChatMessage(response.response, false));
        _awaitingConfirmation = response.needs_confirmation;
    }
}
