"""Read process identity without sending signals or terminating another process."""
import os
from pathlib import Path


def process_identity(pid):
    """Creation token, 'absent', or None when access is denied/unsupported."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return 'absent' if ctypes.get_last_error() == 87 else None
        try:
            created, exited, system, user = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *[ctypes.byref(x) for x in [created, exited, system, user]]):
                return None
            if exited.dwHighDateTime or exited.dwLowDateTime:
                return 'absent'
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    path = Path(f'/proc/{pid}/stat')
    try:
        # Fields after the final ')' start at field 3; starttime is field 22.
        return path.read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return 'absent' if Path('/proc').is_dir() else None
    except (OSError, IndexError):
        return None
