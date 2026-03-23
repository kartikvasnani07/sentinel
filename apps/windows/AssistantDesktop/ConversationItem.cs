namespace AssistantDesktop;

public class ConversationItem
{
    public ConversationItem(string id, string title, bool isCurrent)
    {
        Id = id;
        Title = title;
        IsCurrent = isCurrent;
    }

    public string Id { get; }
    public string Title { get; }
    public bool IsCurrent { get; }
}
