"""Platform-specific system actions."""


def get_platform_actions(host):
    """Return a platform adapter bound to the provided SystemActions host."""
    if getattr(host, "is_windows", False):
        from .windows.actions import WindowsPlatformActions

        return WindowsPlatformActions(host)
    from .linux.actions import LinuxPlatformActions

    return LinuxPlatformActions(host)
