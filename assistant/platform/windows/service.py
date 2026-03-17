import subprocess
import sys
from pathlib import Path

import win32event
import win32service
import win32serviceutil


class AssistantService(win32serviceutil.ServiceFramework):
    _svc_name_ = "AIAssistant"
    _svc_display_name_ = "AI Assistant Service"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        root = Path(__file__).resolve().parents[3]
        subprocess.call([sys.executable, "-m", "assistant.main"], cwd=str(root))


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(AssistantService)
