using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Speech.Recognition;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Diagnostics;
using System.IO;
using Microsoft.Win32;
using Forms = System.Windows.Forms;

namespace AssistantDesktop;

public partial class MainWindow : Window, INotifyPropertyChanged
{
    private readonly AssistantClient _client = new("http://127.0.0.1:8765");
    private bool _awaitingConfirmation;
    private readonly HashSet<string> _pinnedLookup = new(StringComparer.OrdinalIgnoreCase);
    private SpeechRecognitionEngine? _speechEngine;
    private bool _isListening;
    private string _micButtonText = "Mic";
    private string _lastVoiceText = "";
    private DateTime _lastVoiceCommandAt = DateTime.MinValue;
    private bool _voiceReplyEnabled;
    private bool _isMenuOpen;
    private bool _isSettingsActive;
    private string? _currentConversationId;
    private string _assistantNameInput = "";
    private string _voiceModelInput = "";
    private double _voiceAuthSensitivityValue = 60;
    private double _micSensitivityValue = 60;
    private double _wakeSensitivityValue = 60;
    private double _chatFontSizeValue = 14;
    private bool _voiceAuthEnabled;
    private DateTime _lastVoiceAuthSentAt = DateTime.MinValue;
    private DateTime _lastMicSentAt = DateTime.MinValue;
    private DateTime _lastWakeSentAt = DateTime.MinValue;

    public ObservableCollection<ChatMessage> Messages { get; } = new();
    public ObservableCollection<string> PinnedItems { get; } = new();
    public ObservableCollection<ConversationItem> Conversations { get; } = new();
    public ObservableCollection<UiOption> ModelOptions { get; } = new();
    public ObservableCollection<UiOption> AccessOptions { get; } = new()
    {
        new UiOption("read", "Read-only"),
        new UiOption("write", "Write"),
        new UiOption("full", "Full access"),
    };
    public ObservableCollection<UiOption> ModeOptions { get; } = new()
    {
        new UiOption("execute", "Execute"),
        new UiOption("respond", "Respond"),
        new UiOption("plan", "Plan"),
    };
    public ObservableCollection<UiOption> VoicePresetOptions { get; } = new();

    private UiOption? _selectedModelOption;
    public UiOption? SelectedModelOption
    {
        get => _selectedModelOption;
        set
        {
            _selectedModelOption = value;
            OnPropertyChanged();
        }
    }

    private UiOption? _selectedAccessOption;
    public UiOption? SelectedAccessOption
    {
        get => _selectedAccessOption;
        set
        {
            _selectedAccessOption = value;
            OnPropertyChanged();
        }
    }
    private UiOption? _selectedModeOption;
    public UiOption? SelectedModeOption
    {
        get => _selectedModeOption;
        set
        {
            _selectedModeOption = value;
            OnPropertyChanged();
        }
    }
    private UiOption? _selectedVoicePreset;
    public UiOption? SelectedVoicePreset
    {
        get => _selectedVoicePreset;
        set
        {
            _selectedVoicePreset = value;
            OnPropertyChanged();
        }
    }
    private string _statusText = "Connecting...";
    private string _lastStatus = "Connecting...";
    public string StatusText
    {
        get => _statusText;
        set
        {
            _statusText = value;
            OnPropertyChanged();
        }
    }
    public bool IsListening
    {
        get => _isListening;
        set
        {
            _isListening = value;
            OnPropertyChanged();
        }
    }
    public string MicButtonText
    {
        get => _micButtonText;
        set
        {
            _micButtonText = value;
            OnPropertyChanged();
        }
    }
    public bool IsMenuOpen
    {
        get => _isMenuOpen;
        set
        {
            _isMenuOpen = value;
            OnPropertyChanged();
        }
    }
    public bool IsSettingsActive
    {
        get => _isSettingsActive;
        set
        {
            _isSettingsActive = value;
            OnPropertyChanged();
        }
    }
    public string AssistantNameInput
    {
        get => _assistantNameInput;
        set
        {
            _assistantNameInput = value;
            OnPropertyChanged();
        }
    }

    public string VoiceModelInput
    {
        get => _voiceModelInput;
        set
        {
            _voiceModelInput = value;
            OnPropertyChanged();
        }
    }

    public bool VoiceAuthEnabled
    {
        get => _voiceAuthEnabled;
        set
        {
            _voiceAuthEnabled = value;
            OnPropertyChanged();
        }
    }

    public double VoiceAuthSensitivityValue
    {
        get => _voiceAuthSensitivityValue;
        set
        {
            _voiceAuthSensitivityValue = value;
            OnPropertyChanged();
        }
    }

    public double MicSensitivityValue
    {
        get => _micSensitivityValue;
        set
        {
            _micSensitivityValue = value;
            OnPropertyChanged();
        }
    }

    public double WakeSensitivityValue
    {
        get => _wakeSensitivityValue;
        set
        {
            _wakeSensitivityValue = value;
            OnPropertyChanged();
        }
    }

    public double ChatFontSizeValue
    {
        get => _chatFontSizeValue;
        set
        {
            _chatFontSizeValue = value;
            OnPropertyChanged();
        }
    }

    public MainWindow()
    {
        InitializeComponent();
        DataContext = this;
        Loaded += async (_, _) =>
        {
            BringToFront();
            await LoadStatusAsync();
        };
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged([CallerMemberName] string? name = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }

    private async void Send_Click(object sender, RoutedEventArgs e)
    {
        await SendCurrentInputAsync();
    }

    private async void InputBox_OnKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == Key.Enter && Keyboard.Modifiers == ModifierKeys.None)
        {
            e.Handled = true;
            await SendCurrentInputAsync();
        }
    }

    private async Task SendCurrentInputAsync()
    {
        var text = InputBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }

        InputBox.Text = string.Empty;
        await SendTextAsync(text);
    }

    private async Task SendTextAsync(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        Messages.Add(new ChatMessage(text, true));
        ScrollChatToEnd();
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

        try
        {
            var response = await _client.SendAsync(
                text,
                confirm,
                SelectedModelOption?.Id,
                SelectedAccessOption?.Id,
                SelectedModeOption?.Id,
                PinnedItems);
            var assistantText = response.ModelUsed is { Length: > 0 }
                ? $"{response.response}\n\n(Model: {response.ModelUsed})"
                : response.response;
            var jsonPayload = JsonSerializer.Serialize(
                new
                {
                    response = response.response,
                    model_used = response.ModelUsed,
                    mode = SelectedModeOption?.Id,
                    needs_confirmation = response.needs_confirmation,
                    weather = response.Weather,
                    timestamp = DateTime.UtcNow.ToString("o")
                },
                new JsonSerializerOptions { WriteIndented = true });
            jsonPayload = $"JSON response\n{jsonPayload}\n";
            var assistantMessage = new ChatMessage(assistantText, false, jsonPayload, response.Weather);
            Messages.Add(assistantMessage);
            ScrollChatToEnd();
            _awaitingConfirmation = response.needs_confirmation;
            if (_voiceReplyEnabled && !_awaitingConfirmation)
            {
                await _client.SpeakAsync(response.response);
                if (!IsListening)
                {
                    _voiceReplyEnabled = false;
                }
            }
        }
        catch (Exception exc)
        {
            Messages.Add(new ChatMessage($"Bridge error: {exc.Message}", false));
            StatusText = "Bridge offline";
        }
    }

    private async Task SendSilentAsync(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        try
        {
            await _client.SendAsync(
                text,
                null,
                SelectedModelOption?.Id,
                SelectedAccessOption?.Id,
                SelectedModeOption?.Id,
                PinnedItems);
        }
        catch
        {
        }
    }

    private async Task LoadStatusAsync()
    {
        AssistantStatus? status;
        try
        {
            status = await _client.GetStatusAsync();
        }
        catch (Exception exc)
        {
            Messages.Add(new ChatMessage($"Status error: {exc.Message}", false));
            StatusText = "Bridge offline";
            return;
        }
        if (status is null)
        {
            StatusText = "Bridge offline";
            var started = await TryStartBridgeAsync();
            if (!started)
            {
                return;
            }
            status = await _client.GetStatusAsync();
            if (status is null)
            {
                StatusText = "Bridge offline";
                return;
            }
        }

        StatusText = status.CloudReady ? "Online" : "Bridge online (cloud not configured)";

        ModelOptions.Clear();
        if (status.Models is not null)
        {
            foreach (var model in status.Models)
            {
                ModelOptions.Add(new UiOption(model.id, model.label));
            }
        }

        if (ModelOptions.Count == 0)
        {
            ModelOptions.Add(new UiOption("auto", "Auto"));
        }

        SelectedModelOption = ModelOptions.FirstOrDefault(option => option.Id == status.ModelPreference)
            ?? ModelOptions.FirstOrDefault();
        SelectedAccessOption = AccessOptions.FirstOrDefault(option => option.Id == status.AccessLevel)
            ?? AccessOptions.LastOrDefault();
        SelectedModeOption = ModeOptions.FirstOrDefault();
        await LoadVoicePresetsAsync();
    }

    private async Task LoadVoicePresetsAsync()
    {
        VoicePresetOptions.Clear();
        var presets = await _client.GetVoicePresetsAsync();
        if (presets?.Presets is not null)
        {
            foreach (var preset in presets.Presets)
            {
                VoicePresetOptions.Add(new UiOption(preset.id, preset.label));
            }
        }
        if (VoicePresetOptions.Count == 0)
        {
            VoicePresetOptions.Add(new UiOption("default", "Default"));
        }
        SelectedVoicePreset = VoicePresetOptions.FirstOrDefault(option => option.Id == presets?.Current)
            ?? VoicePresetOptions.FirstOrDefault();
    }

    private void BringToFront()
    {
        WindowState = WindowState.Normal;
        ShowInTaskbar = true;
        Activate();
        Topmost = true;
        Topmost = false;
        Focus();
    }

    private async Task<bool> TryStartBridgeAsync()
    {
        var started = false;
        var repoRoot = FindRepoRoot();
        if (repoRoot is not null)
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "python",
                    Arguments = "-m assistant.gui_server",
                    UseShellExecute = true,
                    CreateNoWindow = true,
                    WorkingDirectory = repoRoot
                });
                started = true;
            }
            catch
            {
                started = false;
            }
        }
        if (!started)
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "assistant-gui",
                    UseShellExecute = true,
                    CreateNoWindow = true
                });
                started = true;
            }
            catch
            {
                started = false;
            }
        }
        if (!started)
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "python",
                    Arguments = "-m assistant.gui_server",
                    UseShellExecute = true,
                    CreateNoWindow = true
                });
                started = true;
            }
            catch
            {
                return false;
            }
        }
        await Task.Delay(1200);
        return (await _client.GetStatusAsync()) is not null;
    }

    private string? FindRepoRoot()
    {
        try
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            for (var i = 0; i < 8 && dir is not null; i++)
            {
                var candidate = Path.Combine(dir.FullName, "assistant", "gui_server.py");
                if (File.Exists(candidate))
                {
                    return dir.FullName;
                }
                dir = dir.Parent;
            }
        }
        catch
        {
            return null;
        }
        return null;
    }

    private void Search_Click(object sender, RoutedEventArgs e)
    {
        SeedInput("search for ");
    }

    private void OpenApp_Click(object sender, RoutedEventArgs e)
    {
        SeedInput("open ");
    }

    private void SystemSettings_Click(object sender, RoutedEventArgs e)
    {
        SeedInput("open settings ");
    }

    private void Summarize_Click(object sender, RoutedEventArgs e)
    {
        SeedInput(PinnedItems.Count > 0 ? "summarize the pinned items" : "summarize ");
    }

    private void SeedInput(string text)
    {
        InputBox.Text = text;
        InputBox.Focus();
        InputBox.CaretIndex = InputBox.Text.Length;
    }

    private void Pin_Click(object sender, RoutedEventArgs e)
    {
        if (PinButton.ContextMenu is null)
        {
            return;
        }
        PinButton.ContextMenu.IsOpen = true;
    }

    private void PinFile_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Multiselect = true,
            Title = "Select files to pin"
        };
        if (dialog.ShowDialog() == true)
        {
            foreach (var file in dialog.FileNames)
            {
                AddPinnedItem(file);
            }
        }
    }

    private void PinFolder_Click(object sender, RoutedEventArgs e)
    {
        using var dialog = new Forms.FolderBrowserDialog();
        dialog.Description = "Select folder to pin";
        if (dialog.ShowDialog() == Forms.DialogResult.OK)
        {
            AddPinnedItem(dialog.SelectedPath);
        }
    }

    private void ClearPins_Click(object sender, RoutedEventArgs e)
    {
        _pinnedLookup.Clear();
        PinnedItems.Clear();
    }

    private void RemovePin_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && element.Tag is string path)
        {
            _pinnedLookup.Remove(path);
            PinnedItems.Remove(path);
        }
    }

    private void CopyPrompt_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && element.Tag is ChatMessage message)
        {
            System.Windows.Clipboard.SetText(message.Text);
        }
    }

    private void CopyResponse_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && element.Tag is ChatMessage message)
        {
            System.Windows.Clipboard.SetText(message.Text);
        }
    }

    private void EditPrompt_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && element.Tag is ChatMessage message)
        {
            InputBox.Text = message.Text;
            InputBox.Focus();
            InputBox.CaretIndex = InputBox.Text.Length;
        }
    }

    private void AddPinnedItem(string path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return;
        }
        if (_pinnedLookup.Add(path))
        {
            PinnedItems.Add(path);
        }
    }

    private async void Menu_Click(object sender, RoutedEventArgs e)
    {
        IsMenuOpen = !IsMenuOpen;
        if (IsMenuOpen)
        {
            await RefreshHistoryAsync();
        }
    }

    private async void RefreshHistory_Click(object sender, RoutedEventArgs e)
    {
        await RefreshHistoryAsync();
    }

    private void OpenSettings_Click(object sender, RoutedEventArgs e)
    {
        IsSettingsActive = true;
    }

    private void CloseSettings_Click(object sender, RoutedEventArgs e)
    {
        IsSettingsActive = false;
    }

    private void ChatMenu_Click(object sender, RoutedEventArgs e)
    {
        if (sender is System.Windows.Controls.Button button &&
            button.ContextMenu is System.Windows.Controls.ContextMenu menu)
        {
            menu.DataContext = button.Tag;
            menu.PlacementTarget = button;
            menu.IsOpen = true;
        }
    }

    private async Task RefreshHistoryAsync()
    {
        var history = await _client.GetHistoryAsync();
        if (history is null)
        {
            return;
        }
        Conversations.Clear();
        _currentConversationId = history.Current;
        if (history.Conversations is null)
        {
            return;
        }
        foreach (var item in history.Conversations)
        {
            Conversations.Add(new ConversationItem(item.id, item.title, item.is_current));
        }
        if (!string.IsNullOrWhiteSpace(_currentConversationId))
        {
            await LoadConversationAsync(_currentConversationId);
        }
    }

    private async void ApplyAssistantName_Click(object sender, RoutedEventArgs e)
    {
        var name = AssistantNameInput.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            return;
        }
        var ok = await _client.UpdateSettingsAsync(new Dictionary<string, object?> { ["assistant_name"] = name });
        if (ok)
        {
            Messages.Add(new ChatMessage($"Assistant name set to {name}.", false));
            ScrollChatToEnd();
        }
        else
        {
            Messages.Add(new ChatMessage("Failed to update assistant name.", false));
            ScrollChatToEnd();
        }
    }

    private async void ApplyVoiceModel_Click(object sender, RoutedEventArgs e)
    {
        var model = SelectedVoicePreset?.Id ?? VoiceModelInput.Trim();
        if (string.IsNullOrWhiteSpace(model))
        {
            return;
        }
        await SendTextAsync($"change voice model to {model}");
    }

    private async void VoiceAuth_Toggled(object sender, RoutedEventArgs e)
    {
        var command = VoiceAuthEnabled ? "enable voice authentication" : "disable voice authentication";
        await SendTextAsync(command);
    }

    private async void VoiceAuthSensitivity_Changed(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if ((DateTime.UtcNow - _lastVoiceAuthSentAt).TotalMilliseconds < 400)
        {
            return;
        }
        _lastVoiceAuthSentAt = DateTime.UtcNow;
        var percent = (int)Math.Round(VoiceAuthSensitivityValue);
        await SendSilentAsync($"set voice authentication to {percent} percent");
    }

    private async void MicSensitivity_Changed(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if ((DateTime.UtcNow - _lastMicSentAt).TotalMilliseconds < 400)
        {
            return;
        }
        _lastMicSentAt = DateTime.UtcNow;
        var percent = (int)Math.Round(MicSensitivityValue);
        await SendSilentAsync($"set microphone sensitivity to {percent} percent");
    }

    private async void WakeSensitivity_Changed(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if ((DateTime.UtcNow - _lastWakeSentAt).TotalMilliseconds < 400)
        {
            return;
        }
        _lastWakeSentAt = DateTime.UtcNow;
        var percent = (int)Math.Round(WakeSensitivityValue);
        await SendSilentAsync($"set wake word sensitivity to {percent} percent");
    }

    private async void NewChat_Click(object sender, RoutedEventArgs e)
    {
        await _client.SendAsync("new conversation");
        Messages.Clear();
        IsSettingsActive = false;
        await RefreshHistoryAsync();
        if (!string.IsNullOrWhiteSpace(_currentConversationId))
        {
            await LoadConversationAsync(_currentConversationId);
        }
    }

    private async void OpenChat_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && element.Tag is ConversationItem item)
        {
            var result = await _client.OpenConversationAsync(item.Id);
            if (result?.Conversation is not null)
            {
                _currentConversationId = result.Conversation.Id;
                LoadConversationFromDetails(result.Conversation);
            }
            IsSettingsActive = false;
            await RefreshHistoryAsync();
        }
    }

    private async Task LoadConversationAsync(string conversationId)
    {
        var conversation = await _client.GetConversationAsync(conversationId);
        if (conversation?.Conversation is null)
        {
            return;
        }
        LoadConversationFromDetails(conversation.Conversation);
    }

    private void LoadConversationFromDetails(AssistantConversationDetails details)
    {
        if (details.Messages is null)
        {
            return;
        }
        Messages.Clear();
        foreach (var message in details.Messages)
        {
            var isUser = string.Equals(message.Role, "user", StringComparison.OrdinalIgnoreCase);
            Messages.Add(new ChatMessage(message.Text, isUser));
        }
        ScrollChatToEnd();
    }

    private async void ExportChat_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && (element.Tag as ConversationItem ?? element.DataContext as ConversationItem) is ConversationItem item)
        {
            var conversation = await _client.GetConversationAsync(item.Id);
            if (conversation?.Conversation is null)
            {
                return;
            }
            var dialog = new Microsoft.Win32.SaveFileDialog
            {
                FileName = $"conversation_{item.Id}.json",
                Filter = "JSON files (*.json)|*.json|All files (*.*)|*.*",
            };
            if (dialog.ShowDialog() == true)
            {
                var json = JsonSerializer.Serialize(conversation.Conversation, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(dialog.FileName, json);
            }
        }
    }

    private async void DeleteChat_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && (element.Tag as ConversationItem ?? element.DataContext as ConversationItem) is ConversationItem item)
        {
            var confirm = System.Windows.MessageBox.Show(
                "All of the data from this chat will be lost. Delete it?",
                "Delete chat",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning);
            if (confirm != MessageBoxResult.Yes)
            {
                return;
            }
            var result = await _client.DeleteConversationAsync(item.Id);
            if (result?.Status == "ok")
            {
                Conversations.Remove(item);
            }
            await RefreshHistoryAsync();
            if (!string.IsNullOrWhiteSpace(_currentConversationId))
            {
                await LoadConversationAsync(_currentConversationId);
            }
        }
    }

    private void ToggleWeather_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && element.Tag is ChatMessage message)
        {
            message.ToggleWeatherExpanded();
        }
    }

    private void ToggleJson_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement element && element.Tag is ChatMessage message)
        {
            message.ToggleJson();
        }
    }

    private void ScrollChatToEnd()
    {
        _ = Dispatcher.InvokeAsync(() =>
        {
            ChatScrollViewer?.ScrollToEnd();
        });
    }

    private async void Mic_Click(object sender, RoutedEventArgs e)
    {
        if (IsListening)
        {
            StopListening();
            return;
        }
        _lastStatus = StatusText;
        StartListening();
        try
        {
            await _client.SpeakAsync("Listening.");
        }
        catch (Exception exc)
        {
            Messages.Add(new ChatMessage($"Mic prompt failed: {exc.Message}", false));
        }
    }

    private void StartListening()
    {
        if (IsListening)
        {
            return;
        }
        EnsureSpeechEngine();
        try
        {
            _speechEngine?.RecognizeAsync(RecognizeMode.Multiple);
            IsListening = true;
            MicButtonText = "Stop";
            StatusText = "Listening";
            _voiceReplyEnabled = true;
        }
        catch (Exception exc)
        {
            Messages.Add(new ChatMessage($"Mic error: {exc.Message}", false));
            StopListening();
        }
    }

    private void StopListening()
    {
        if (!IsListening)
        {
            return;
        }
        try
        {
            _speechEngine?.RecognizeAsyncCancel();
            _speechEngine?.RecognizeAsyncStop();
        }
        catch
        {
            // ignore stop errors
        }
        IsListening = false;
        MicButtonText = "Mic";
        StatusText = _lastStatus;
    }

    private void EnsureSpeechEngine()
    {
        if (_speechEngine is not null)
        {
            return;
        }
        _speechEngine = new SpeechRecognitionEngine();
        _speechEngine.SetInputToDefaultAudioDevice();
        _speechEngine.LoadGrammar(new DictationGrammar());
        _speechEngine.SpeechRecognized += OnSpeechRecognized;
    }

    private void OnSpeechRecognized(object? sender, SpeechRecognizedEventArgs e)
    {
        if (!IsListening)
        {
            return;
        }
        var result = e.Result;
        if (result is null)
        {
            return;
        }
        var text = result.Text?.Trim();
        if (string.IsNullOrWhiteSpace(text) || result.Confidence < 0.6)
        {
            return;
        }
        var now = DateTime.UtcNow;
        if (string.Equals(text, _lastVoiceText, StringComparison.OrdinalIgnoreCase) &&
            (now - _lastVoiceCommandAt).TotalSeconds < 1.2)
        {
            return;
        }
        _lastVoiceText = text;
        _lastVoiceCommandAt = now;
        _ = Dispatcher.InvokeAsync(() =>
        {
            var current = InputBox.Text.Trim();
            if (string.IsNullOrWhiteSpace(current))
            {
                InputBox.Text = text;
            }
            else if (!current.EndsWith(text, StringComparison.OrdinalIgnoreCase))
            {
                InputBox.Text = $"{current} {text}";
            }
            InputBox.Focus();
            InputBox.CaretIndex = InputBox.Text.Length;
        });
    }
}
