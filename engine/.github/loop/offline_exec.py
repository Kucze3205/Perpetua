#!/usr/bin/env python3
"""Deny socket creation in a script and all its children, including Unix-socket proxy escapes."""
import ctypes
import errno
import os
import platform
import sys


def restrict():
    if sys.platform != "linux":
        raise RuntimeError("offline script enforcement requires Linux")
    # seccomp syscall numbers on the runner architectures we support; unknown architectures fail closed.
    numbers = {"x86_64": (41, 53, 425, 426, 427), "aarch64": (198, 199, 425, 426, 427)}
    blocked = numbers.get(platform.machine())
    if blocked is None:
        raise RuntimeError("unsupported offline script architecture")

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    # Reject alternate x86 syscall ABIs as well as socket/socketpair and io_uring (which can create sockets).
    arch = 0xC000003E if platform.machine() == "x86_64" else 0xC00000B7
    code = [(0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x00000000),
            (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    for number in blocked:
        code += [(0x15, 0, 1, number), (0x06, 0, 0, 0x00050000 | errno.EPERM)]
    code += [(0x06, 0, 0, 0x7FFF0000)]
    filters = (Filter * len(code))(*(Filter(*entry) for entry in code))
    program = Program(len(code), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise OSError(ctypes.get_errno(), "cannot enforce offline script seccomp")


if __name__ == "__main__":
    restrict()
    os.execvp(sys.argv[1], sys.argv[1:])
