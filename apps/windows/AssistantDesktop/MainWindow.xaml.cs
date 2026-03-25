using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Speech.Recognition;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using System.Diagnostics;
using System.IO;
using System.Globalization;
using Microsoft.Win32;
using Forms = System.Windows.Forms;

namespace AssistantDesktop;

public partial class MainWindow : Window, INotifyPropertyChanged
{
    private readonly AssistantClient _client = new("http://127.0.0.1:8765");
    private bool _awaitingConfirmation;
    private readonly HashSet<string> _pinnedLookup = new(StringComparer.OrdinalIgnoreCase);
    private SpeechRecognitionEngine? _speechEngine;
    private Grammar? _wakeGrammar;
    private Grammar? _dictationGrammar;
    private bool _recognitionStarted;
    private bool _isListening;
    private string _micButtonText = "Mic";
    private string _lastVoiceText = "";
    private DateTime _lastVoiceCommandAt = DateTime.MinValue;
    private bool _voiceReplyEnabled;
    private bool _manualMicMode;
    private bool _wakeTriggeredSession;
    private bool _autoSendBlocked;
    private bool _isUpdatingFromSpeech;
    private string _speechBuffer = "";
    private DispatcherTimer? _autoSendTimer;
    private string _wakeWord = "assistant";
    private string? _lastWakeAck;
    private DateTime _lastWakeAckAt = DateTime.MinValue;
    private bool _suppressVoiceForNextSend;
    private readonly Random _random = new();
    private bool _isServerTranscribing;
    private bool _cancelTranscription;
    private bool _isWakeAnimating;
    private static readonly string[] WakeReplies = new[]
    {
        "I'm here.",
        "Listening.",
        "You called?",
        "What's up?",
        "Huh?"
    };
    private bool _isMenuOpen;
    private bool _isSettingsActive;
    private string? _currentConversationId;
    private string _assistantNameInput = "";
    private string _voiceModelInput = "";
    private string _defaultCreatePathInput = "";
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

    public bool IsWakeAnimating
    {
        get => _isWakeAnimating;
        set
        {
            _isWakeAnimating = value;
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

    public string DefaultCreatePathInput
    {
        get => _defaultCreatePathInput;
        set
        {
            _defaultCreatePathInput = value;
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

    private void InputBox_OnTextChanged(object sender, TextChangedEventArgs e)
    {
        if (_isUpdatingFromSpeech)
        {
            return;
        }
        if (!IsListening && string.IsNullOrWhiteSpace(InputBox.Text))
        {
            _suppressVoiceForNextSend = false;
        }
        if (IsListening)
        {
            _autoSendBlocked = true;
            _speechBuffer = InputBox.Text.Trim();
            _autoSendTimer?.Stop();
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
        _speechBuffer = "";
        _autoSendBlocked = false;
        _autoSendTimer?.Stop();
        await SendTextAsync(text);
    }

    private async Task SendTextAsync(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        var shouldReturnToWake = IsListening && !_manualMicMode && _wakeTriggeredSession;
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
            if (_voiceReplyEnabled && !_awaitingConfirmation && !_suppressVoiceForNextSend)
            {
                await _client.SpeakAsync(response.response);
            }
            if (_suppressVoiceForNextSend)
            {
                _suppressVoiceForNextSend = false;
            }
            if (shouldReturnToWake && !_awaitingConfirmation)
            {
                EndWakeSession();
            }
            if (!IsListening)
            {
                _voiceReplyEnabled = false;
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
        if (!string.IsNullOrWhiteSpace(status.AssistantName))
        {
            AssistantNameInput = status.AssistantName.Trim();
            ConfigureWakeWord(status.AssistantName.Trim());
        }
        if (!string.IsNullOrWhiteSpace(status.DefaultCreatePath))
        {
            DefaultCreatePathInput = status.DefaultCreatePath.Trim();
        }
        StartWakeListening();
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
            ConfigureWakeWord(name);
            Messages.Add(new ChatMessage($"Assistant name set to {name}.", false));
            ScrollChatToEnd();
        }
        else
        {
            Messages.Add(new ChatMessage("Failed to update assistant name.", false));
            ScrollChatToEnd();
        }
    }

    private void BrowseDefaultCreatePath_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var dialog = new Forms.FolderBrowserDialog();
            dialog.Description = "Select the default folder for new files/folders.";
            dialog.ShowNewFolderButton = true;
            var result = dialog.ShowDialog();
            if (result == Forms.DialogResult.OK && !string.IsNullOrWhiteSpace(dialog.SelectedPath))
            {
                DefaultCreatePathInput = dialog.SelectedPath.Trim();
            }
        }
        catch
        {
            // ignore browse failures
        }
    }

    private async void ApplyDefaultCreatePath_Click(object sender, RoutedEventArgs e)
    {
        var path = DefaultCreatePathInput.Trim();
        if (string.IsNullOrWhiteSpace(path))
        {
            return;
        }
        var ok = await _client.UpdateSettingsAsync(new Dictionary<string, object?> { ["default_create_path"] = path });
        if (ok)
        {
            Messages.Add(new ChatMessage($"Default create path set to {path}.", false));
            ScrollChatToEnd();
        }
        else
        {
            Messages.Add(new ChatMessage("Failed to update default create path.", false));
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
        if (_isServerTranscribing)
        {
            _cancelTranscription = true;
            EndManualDictation();
            return;
        }
        if (IsListening && _manualMicMode)
        {
            EndManualDictation();
            return;
        }
        if (IsListening && !_manualMicMode)
        {
            EndWakeSession();
            return;
        }
        _lastStatus = StatusText;
        _ = StartServerTranscriptionAsync(sendDirectly: false, fromWake: false);
    }

    private async Task StartServerTranscriptionAsync(bool sendDirectly, bool fromWake)
    {
        if (_isServerTranscribing)
        {
            return;
        }
        _isServerTranscribing = true;
        _cancelTranscription = false;
        StopRecognitionLoop();
        SetGrammarMode(wakeEnabled: false, dictationEnabled: false);
        _speechBuffer = "";
        _autoSendBlocked = true;
        _manualMicMode = !sendDirectly;
        _wakeTriggeredSession = sendDirectly;
        _voiceReplyEnabled = sendDirectly;
        IsListening = true;
        MicButtonText = "Stop";
        StatusText = "Listening";
        try
        {
            var transcript = await _client.TranscribeAsync(sendDirectly ? "wake" : "manual");
            if (_cancelTranscription)
            {
                return;
            }
            var text = transcript?.Text?.Trim() ?? "";
            if (string.IsNullOrWhiteSpace(text))
            {
                if (sendDirectly)
                {
                    EndWakeSession();
                }
                else
                {
                    EndManualDictation();
                }
                return;
            }
            if (sendDirectly)
            {
                await SendTextAsync(text);
                return;
            }
            _autoSendBlocked = false;
            await Dispatcher.InvokeAsync(() => UpdateInputFromSpeech(text));
            EndManualDictation();
        }
        catch
        {
            if (sendDirectly)
            {
                EndWakeSession();
            }
            else
            {
                EndManualDictation();
            }
        }
        finally
        {
            _isServerTranscribing = false;
        }
    }

    private void StartWakeListening()
    {
        _lastStatus = StatusText;
        BeginWakeListening();
    }

    private void BeginWakeListening()
    {
        EnsureSpeechEngine();
        _manualMicMode = false;
        _wakeTriggeredSession = false;
        SetGrammarMode(wakeEnabled: true, dictationEnabled: false);
        StartRecognitionLoop();
        IsListening = false;
        MicButtonText = "Mic";
        StatusText = _lastStatus;
    }

    private void BeginDictationFromWake()
    {
        StartWakeAnimationPulse();
        var ack = WakeReplies[_random.Next(WakeReplies.Length)];
        _ = SpeakWakeAckAsync(ack);
        _ = StartServerTranscriptionAsync(sendDirectly: true, fromWake: true);
    }

    private void StartWakeAnimationPulse()
    {
        IsWakeAnimating = true;
    }

    private void EndWakeSession()
    {
        _wakeTriggeredSession = false;
        StopListening();
    }

    private void EndManualDictation()
    {
        _manualMicMode = false;
        _wakeTriggeredSession = false;
        _autoSendTimer?.Stop();
        _autoSendBlocked = true;
        _suppressVoiceForNextSend = true;
        IsListening = false;
        MicButtonText = "Mic";
        StatusText = _lastStatus;
        BeginWakeListening();
    }

    private void StopListening()
    {
        _manualMicMode = false;
        _wakeTriggeredSession = false;
        _speechBuffer = "";
        _autoSendBlocked = false;
        _autoSendTimer?.Stop();
        IsListening = false;
        MicButtonText = "Mic";
        StatusText = _lastStatus;
        BeginWakeListening();
    }

    private void ConfigureWakeWord(string? name)
    {
        var cleaned = string.IsNullOrWhiteSpace(name) ? "assistant" : name.Trim();
        _wakeWord = cleaned;
        if (_speechEngine is null)
        {
            return;
        }
        BuildGrammars();
        SetGrammarMode(wakeEnabled: !IsListening, dictationEnabled: IsListening);
    }

    private IEnumerable<string> BuildWakePhrases(string wakeWord)
    {
        var baseWord = string.IsNullOrWhiteSpace(wakeWord) ? "assistant" : wakeWord.Trim();
        var phrases = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            baseWord,
            $"hey {baseWord}",
            $"hi {baseWord}",
            $"hello {baseWord}",
            $"ok {baseWord}",
            $"okay {baseWord}",
        };
        return phrases;
    }

    private void EnsureSpeechEngine()
    {
        if (_speechEngine is not null)
        {
            return;
        }
        try
        {
            var recognizers = SpeechRecognitionEngine.InstalledRecognizers();
            var preferred = recognizers.FirstOrDefault(r => r.Culture.Equals(CultureInfo.CurrentUICulture))
                           ?? recognizers.FirstOrDefault();
            _speechEngine = preferred is not null ? new SpeechRecognitionEngine(preferred) : new SpeechRecognitionEngine();
        }
        catch
        {
            _speechEngine = new SpeechRecognitionEngine();
        }
        _speechEngine.SetInputToDefaultAudioDevice();
        BuildGrammars();
        _speechEngine.SpeechRecognized += OnSpeechRecognized;
        _speechEngine.SpeechHypothesized += OnSpeechHypothesized;
        _speechEngine.RecognizeCompleted += (_, _) =>
        {
            _recognitionStarted = false;
            if (!_isServerTranscribing)
            {
                StartRecognitionLoop();
            }
        };
    }

    private void StopRecognitionLoop()
    {
        if (_speechEngine is null)
        {
            return;
        }
        try
        {
            _speechEngine.RecognizeAsyncCancel();
        }
        catch
        {
        }
        try
        {
            _speechEngine.RecognizeAsyncStop();
        }
        catch
        {
        }
        try
        {
            _speechEngine.SetInputToNull();
        }
        catch
        {
        }
        _recognitionStarted = false;
    }

    private void BuildGrammars()
    {
        if (_speechEngine is null)
        {
            return;
        }
        try
        {
            _speechEngine.RequestRecognizerUpdate();
        }
        catch
        {
            // ignore updates when not running
        }
        try
        {
            if (_wakeGrammar is not null)
            {
                _speechEngine.UnloadGrammar(_wakeGrammar);
            }
            if (_dictationGrammar is not null)
            {
                _speechEngine.UnloadGrammar(_dictationGrammar);
            }
        }
        catch
        {
            // ignore unload errors
        }
        var wakeChoices = new Choices(BuildWakePhrases(_wakeWord).ToArray());
        var wakeBuilder = new GrammarBuilder(wakeChoices);
        _wakeGrammar = new Grammar(wakeBuilder) { Name = "wake" };
        _dictationGrammar = new DictationGrammar { Name = "dictation" };
        _speechEngine.LoadGrammar(_wakeGrammar);
        _speechEngine.LoadGrammar(_dictationGrammar);
    }

    private void StartRecognitionLoop()
    {
        if (_speechEngine is null || _recognitionStarted)
        {
            return;
        }
        try
        {
            _speechEngine.SetInputToDefaultAudioDevice();
            _speechEngine.RecognizeAsync(RecognizeMode.Multiple);
            _recognitionStarted = true;
        }
        catch
        {
            _recognitionStarted = false;
        }
    }

    private void SetGrammarMode(bool wakeEnabled, bool dictationEnabled)
    {
        if (_wakeGrammar is not null)
        {
            _wakeGrammar.Enabled = wakeEnabled;
        }
        if (_dictationGrammar is not null)
        {
            _dictationGrammar.Enabled = dictationEnabled;
        }
    }

    private void ResetAutoSendTimer()
    {
        if (_autoSendTimer is null)
        {
            _autoSendTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            _autoSendTimer.Tick += AutoSendTimer_Tick;
        }
        _autoSendTimer.Stop();
        _autoSendTimer.Start();
    }

    private async void AutoSendTimer_Tick(object? sender, EventArgs e)
    {
        _autoSendTimer?.Stop();
        if (!IsListening)
        {
            return;
        }
        if (_manualMicMode)
        {
            EndManualDictation();
            return;
        }
        if (_autoSendBlocked)
        {
            return;
        }
        var text = _speechBuffer.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        _speechBuffer = "";
        await Dispatcher.InvokeAsync(() =>
        {
            _isUpdatingFromSpeech = true;
            InputBox.Text = "";
            _isUpdatingFromSpeech = false;
        });
        await SendTextAsync(text);
    }

    private void UpdateInputFromSpeech(string text)
    {
        if (_autoSendBlocked || (_wakeTriggeredSession && !_manualMicMode))
        {
            return;
        }
        _isUpdatingFromSpeech = true;
        InputBox.Text = text;
        InputBox.Focus();
        InputBox.CaretIndex = InputBox.Text.Length;
        _isUpdatingFromSpeech = false;
    }

    private async Task SpeakWakeAckAsync(string text)
    {
        _lastWakeAck = text;
        _lastWakeAckAt = DateTime.UtcNow;
        try
        {
            await _client.SpeakAsync(text);
        }
        catch
        {
            // ignore ack failures
        }
    }

    private static string NormalizeSpeechSnippet(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return string.Empty;
        }
        return new string(text.ToLowerInvariant()
            .Where(ch => char.IsLetterOrDigit(ch) || char.IsWhiteSpace(ch))
            .ToArray()).Trim();
    }

    private bool IsLikelyWakeAck(string text)
    {
        if (string.IsNullOrWhiteSpace(_lastWakeAck))
        {
            return false;
        }
        if ((DateTime.UtcNow - _lastWakeAckAt).TotalSeconds > 1.5)
        {
            return false;
        }
        var cleaned = NormalizeSpeechSnippet(text);
        var ack = NormalizeSpeechSnippet(_lastWakeAck);
        return !string.IsNullOrWhiteSpace(cleaned) && cleaned == ack;
    }

    private bool IsWakeMatch(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return false;
        }
        var cleaned = NormalizeSpeechSnippet(text);
        var wake = NormalizeSpeechSnippet(_wakeWord);
        if (!string.IsNullOrWhiteSpace(wake) && cleaned.Contains(wake, StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        foreach (var phrase in BuildWakePhrases(_wakeWord))
        {
            var normalized = NormalizeSpeechSnippet(phrase);
            if (!string.IsNullOrWhiteSpace(normalized) && cleaned == normalized)
            {
                return true;
            }
        }
        return false;
    }

    private void OnSpeechHypothesized(object? sender, SpeechHypothesizedEventArgs e)
    {
        if (!IsListening || _autoSendBlocked || (_wakeTriggeredSession && !_manualMicMode))
        {
            return;
        }
        var text = e.Result?.Text?.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        var preview = string.IsNullOrWhiteSpace(_speechBuffer) ? text : $"{_speechBuffer} {text}";
        _ = Dispatcher.InvokeAsync(() => UpdateInputFromSpeech(preview));
    }

    private void OnSpeechRecognized(object? sender, SpeechRecognizedEventArgs e)
    {
        var result = e.Result;
        if (result is null)
        {
            return;
        }
        var text = result.Text?.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        var grammarName = result.Grammar?.Name ?? string.Empty;
        if (grammarName == "wake")
        {
            if (result.Confidence < 0.35 || _manualMicMode || IsListening)
            {
                return;
            }
            BeginDictationFromWake();
            return;
        }
        if (!IsListening && !_manualMicMode && IsWakeMatch(text))
        {
            BeginDictationFromWake();
            return;
        }
        if (!IsListening)
        {
            return;
        }
        if (result.Confidence < 0.45 || _autoSendBlocked)
        {
            return;
        }
        if (IsLikelyWakeAck(text))
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
        _speechBuffer = string.IsNullOrWhiteSpace(_speechBuffer) ? text : $"{_speechBuffer} {text}";
        if (!_wakeTriggeredSession || _manualMicMode)
        {
            _ = Dispatcher.InvokeAsync(() => UpdateInputFromSpeech(_speechBuffer));
        }
        ResetAutoSendTimer();
    }
}
