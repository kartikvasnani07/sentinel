namespace AssistantDesktop.Linux;

public class ChatMessage
{
    public ChatMessage(string text, bool isUser)
    {
        Text = text;
        IsUser = isUser;
    }

    public string Text { get; }
    public bool IsUser { get; }
}
