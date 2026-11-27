"""Shared helpers for the live integration tests and benchmarks."""

import ctypes
import shutil
import time
from ctypes import wintypes
from pathlib import Path


def wipe(store: Path) -> None:
    """
    Delete a test memory folder. The server can hold the SQLite file for
    a moment after it exits, so retry, and FAIL loudly instead of letting
    the next run measure on stale memory.
    """
    for _ in range(20):
        shutil.rmtree(store, ignore_errors=True)
        if not store.exists():
            return
        time.sleep(0.5)
    raise RuntimeError(f"could not delete the test memory {store}")


# ---------------- process CPU / RAM (Windows, no extra package) ----------------

class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]


class ProcessMeter:
    """CPU time and working set of a process (own process when pid is None)."""

    def __init__(self, pid: int | None = None) -> None:
        if pid is None:
            self.handle = _kernel32.GetCurrentProcess()
        else:
            # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
            self.handle = _kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
            if not self.handle:
                raise OSError(f"cannot open process {pid}")
        self.t0 = time.perf_counter()
        self.c0 = self.cpu_seconds()
        self.peak_rss = self.rss()

    def cpu_seconds(self) -> float:
        creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
        _kernel32.GetProcessTimes(self.handle, ctypes.byref(creation), ctypes.byref(exit_),
                                  ctypes.byref(kernel), ctypes.byref(user))
        to_s = lambda ft: ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7
        return to_s(kernel) + to_s(user)

    def rss(self) -> int:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        _psapi.GetProcessMemoryInfo(self.handle, ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize

    def sample(self) -> None:
        self.peak_rss = max(self.peak_rss, self.rss())

    def cpu_percent(self) -> float:
        """Average CPU since creation, in % of ONE core."""
        wall = time.perf_counter() - self.t0
        return 100.0 * (self.cpu_seconds() - self.c0) / wall if wall > 0 else 0.0


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]


_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def server_pid(pid: int) -> int:
    """
    The process that actually runs the server. A venv's python.exe on
    Windows is a launcher that starts the real interpreter as a child.
    """
    snapshot = _kernel32.CreateToolhelp32Snapshot(0x2, 0)       # TH32CS_SNAPPROCESS
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    child = None
    try:
        ok = _kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == pid and entry.szExeFile.lower().startswith(b"python"):
                child = entry.th32ProcessID
            ok = _kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return child or pid


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def foreign_script(text: str) -> set[str]:
    """Letters that are neither Arabic nor Latin (an answer must be speakable)."""
    out = set()
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        arabic = 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF
        latin = cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF
        if not (arabic or latin):
            out.add(ch)
    return out

