"""Race-resistant Windows workspace opens using NT handle-relative traversal.

Each child is opened relative to an already-open directory handle with
FILE_OPEN_REPARSE_POINT. Directory handles deny delete sharing while descendants
are opened, preventing rename/replacement of an inspected path component.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

import msvcrt

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_APPEND_DATA = 0x0004
FILE_READ_ATTRIBUTES = 0x0080
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_OPEN = 1
FILE_CREATE = 2
FILE_OPEN_IF = 3
FILE_OVERWRITE_IF = 5
FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
OBJ_CASE_INSENSITIVE = 0x00000040
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                ("Buffer", wintypes.LPWSTR)]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
                ("Attributes", wintypes.ULONG), ("SecurityDescriptor", wintypes.LPVOID),
                ("SecurityQualityOfService", wintypes.LPVOID)]


class IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   wintypes.LPVOID, wintypes.DWORD]
kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
ntdll.NtCreateFile.restype = ctypes.c_long
ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG


def _last_error(message: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), message)


def _check_handle(handle, label: str, *, directory: bool) -> None:
    info = FILE_ATTRIBUTE_TAG_INFO()
    if not kernel32.GetFileInformationByHandleEx(
        handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise _last_error(f"cannot inspect {label} handle")
    attrs = info.FileAttributes
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        raise PermissionError(f"workspace reparse-point traversal is forbidden: {label}")
    if directory and not attrs & FILE_ATTRIBUTE_DIRECTORY:
        raise NotADirectoryError(f"workspace parent is not a directory: {label}")
    if not directory and attrs & FILE_ATTRIBUTE_DIRECTORY:
        raise PermissionError("workspace target must be a regular file")


def _open_relative(parent, name: str, *, directory: bool, access: int,
                   disposition: int):
    encoded = ctypes.create_unicode_buffer(name)
    byte_length = len(name.encode("utf-16-le"))
    name_string = UNICODE_STRING(byte_length, byte_length + 2, encoded)
    attributes = OBJECT_ATTRIBUTES(
        ctypes.sizeof(OBJECT_ATTRIBUTES), parent, ctypes.pointer(name_string),
        OBJ_CASE_INSENSITIVE, None, None
    )
    status_block = IO_STATUS_BLOCK()
    handle = wintypes.HANDLE()
    options = FILE_FLAG_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT
    options |= FILE_DIRECTORY_FILE if directory else FILE_NON_DIRECTORY_FILE
    status = ntdll.NtCreateFile(
        ctypes.byref(handle), access | SYNCHRONIZE | FILE_READ_ATTRIBUTES,
        ctypes.byref(attributes), ctypes.byref(status_block), None,
        FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ | FILE_SHARE_WRITE,
        disposition, options, None, 0
    )
    if status < 0:
        error = ntdll.RtlNtStatusToDosError(status)
        raise ctypes.WinError(error, f"cannot open workspace component {name!r}")
    try:
        _check_handle(handle, name, directory=directory)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    return handle


def open_workspace_file(root: Path, parts: tuple[str, ...], mode: str,
                        create_parents: bool):
    """Open a workspace file without pathname re-resolution after root open."""
    if mode not in {"r", "rt", "rb", "w", "wt", "wb", "a", "at", "ab", "x", "xt", "xb"}:
        raise ValueError("unsupported workspace file mode")
    root_handle = kernel32.CreateFileW(
        str(root), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None
    )
    if root_handle == INVALID_HANDLE_VALUE:
        raise _last_error("cannot open workspace root handle")

    directory_handles = [root_handle]
    leaf_handle = None
    try:
        _check_handle(root_handle, "workspace root", directory=True)
        writing = mode[0] in {"w", "a", "x"}
        for part in parts[:-1]:
            disposition = FILE_OPEN_IF if writing and create_parents else FILE_OPEN
            child = _open_relative(
                directory_handles[-1], part, directory=True,
                access=FILE_READ_DATA, disposition=disposition
            )
            directory_handles.append(child)

        if mode[0] == "r":
            access, disposition, fd_flags = FILE_READ_DATA, FILE_OPEN, os.O_RDONLY
        elif mode[0] == "w":
            access, disposition, fd_flags = FILE_WRITE_DATA, FILE_OVERWRITE_IF, os.O_WRONLY
        elif mode[0] == "a":
            access, disposition, fd_flags = FILE_APPEND_DATA, FILE_OPEN_IF, os.O_WRONLY | os.O_APPEND
        else:
            access, disposition, fd_flags = FILE_WRITE_DATA, FILE_CREATE, os.O_WRONLY

        leaf_handle = _open_relative(
            directory_handles[-1], parts[-1], directory=False,
            access=access, disposition=disposition
        )
        fd = msvcrt.open_osfhandle(leaf_handle.value, fd_flags | getattr(os, "O_BINARY", 0))
        leaf_handle = None  # The CRT descriptor now owns the native handle.
        return os.fdopen(fd, mode) if "b" in mode else os.fdopen(fd, mode, encoding="utf-8")
    finally:
        if leaf_handle is not None:
            kernel32.CloseHandle(leaf_handle)
        for handle in reversed(directory_handles):
            kernel32.CloseHandle(handle)
