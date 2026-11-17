"""Hold a Windows process handle so PID reuse cannot confuse lifecycle cleanup."""
import ctypes
from ctypes import wintypes


class WindowsProcess:
    def __init__(self, pid):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            error = ctypes.get_last_error()
            if error == 87:
                raise ProcessLookupError(pid)
            raise ctypes.WinError(error)
        times = [wintypes.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in times]):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        self.birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def alive(self):
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 258

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def process_birth(pid):
    process = WindowsProcess(pid)
    try:
        return process.birth
    finally:
        process.close()


def watch_owned_processes(owner_pid, owner_birth, worker_pid, worker_birth, cleanup, ready, sleep):
    handles = []
    try:
        for pid, birth in ((owner_pid, owner_birth), (worker_pid, worker_birth)):
            try:
                process = WindowsProcess(pid)
            except ProcessLookupError:
                cleanup()
                return
            handles.append(process)
            if process.birth != birth:
                cleanup()
                return
        ready()
        while all(process.alive() for process in handles):
            sleep(0.5)
        cleanup()
    finally:
        for process in handles:
            process.close()
