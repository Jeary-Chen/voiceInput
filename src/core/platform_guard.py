"""Startup guards for fragile OS interactions.

Python 3.12's ``platform`` module reads the Windows version through WMI
(``platform._wmi_query`` -> ``_wmi.exec_query``). If the WMI subsystem is
unresponsive — e.g. after an audio/COM stall wedges ``WmiPrvSE`` — those calls
block forever with no timeout. Several libraries (notably ``aiohttp``, imported
by ``dashscope``) call ``platform.system()`` at *import* time, so a wedged WMI
would hang application startup indefinitely before any window appears.

``platform`` already has a documented non-WMI fallback (``sys.getwindowsversion``
plus the registry). We force that path by making ``_wmi_query`` fail fast, which
returns equivalent information without ever touching WMI.
"""
import sys

_applied = False


def apply_wmi_hang_guard() -> None:
    """Prevent ``platform``'s WMI queries from hanging on a wedged WMI service.

    Idempotent and Windows-only. Must run before importing any library that
    calls ``platform.system()``/``uname()`` at import time.
    """
    global _applied
    if _applied or sys.platform != "win32":
        return
    _applied = True

    import platform

    def _wmi_query_disabled(*args, **kwargs):
        raise OSError("WMI query disabled to avoid startup hang on wedged WMI")

    platform._wmi_query = _wmi_query_disabled
