using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Input;
using System.Diagnostics;
using Forms = System.Windows.Forms;

namespace AssistantDesktop;

public partial class MainWindow : Window, INotifyPropertyChanged
{
    private readonly AssistantClient _client = new("http://127.0.0.1:8765");
    private bool _awaitingConfirmation;
    private readonly HashSet<string> _pinnedLookup = new(StringComparer.OrdinalIgnoreCase);

    public ObservableCollection<ChatMessage> Messages { get; } = new();
    public ObservableCollection<string> PinnedItems { get; } = new();
    public ObservableCollection<UiOption> ModelOptions { get; } = new();
    public ObservableCollection<UiOption> AccessOptions { get; } = new()
    {
        new UiOption("read", "Read-only"),
        new UiOption("write", "Write"),
        new UiOption("full", "Full access"),
    };

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
        Loaded += async (_, _) =>
        {
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

        try
        {
            var response = await _client.SendAsync(
                text,
                confirm,
                SelectedModelOption?.Id,
                SelectedAccessOption?.Id,
                PinnedItems);
            var assistantText = response.ModelUsed is { Length: > 0 }
                ? $"{response.response}\n\n(Model: {response.ModelUsed})"
                : response.response;
            Messages.Add(new ChatMessage(assistantText, false));
            _awaitingConfirmation = response.needs_confirmation;
        }
        catch (Exception exc)
        {
            Messages.Add(new ChatMessage($"Bridge error: {exc.Message}", false));
            StatusText = "Bridge offline";
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
    }

    private async Task<bool> TryStartBridgeAsync()
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = "assistant-gui",
                UseShellExecute = true,
                CreateNoWindow = true
            });
        }
        catch
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
            }
            catch
            {
                return false;
            }
        }

        await Task.Delay(1200);
        return (await _client.GetStatusAsync()) is not null;
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
}
