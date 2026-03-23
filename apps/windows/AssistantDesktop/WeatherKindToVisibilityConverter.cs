using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace AssistantDesktop;

public class WeatherKindToVisibilityConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var kind = (value as string ?? string.Empty).Trim().ToLowerInvariant();
        var target = (parameter as string ?? string.Empty).Trim().ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(target))
        {
            return Visibility.Collapsed;
        }
        return kind == target ? Visibility.Visible : Visibility.Collapsed;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
    {
        return System.Windows.Data.Binding.DoNothing;
    }
}
