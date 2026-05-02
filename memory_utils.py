import ctypes
import re
from dataclasses import dataclass

import pymem
import pymem.process


GAME_EXE_NAME = "Crab Game.exe"
GAME_ASSEMBLY_MODULE = "GameAssembly.dll"
UNITY_PLAYER_MODULE = "UnityPlayer.dll"
STILL_ACTIVE = 259


@dataclass(frozen=True)
class PatchSpec:
    name: str
    module_name: str
    pattern: bytes
    patch_bytes: bytes


@dataclass
class PatchState:
    enabled: bool = False
    address: int | None = None
    original_bytes: bytes | None = None
    pid: int | None = None


def open_process(process_name: str = GAME_EXE_NAME) -> pymem.Pymem:
    return pymem.Pymem(process_name)


def close_process(process: pymem.Pymem | None) -> None:
    if process is not None:
        process.close_process()


def get_process_id(process: pymem.Pymem) -> int:
    return ctypes.windll.kernel32.GetProcessId(process.process_handle)


def is_process_alive(process: pymem.Pymem | None) -> bool:
    if process is None:
        return False

    exit_code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(
        process.process_handle,
        ctypes.byref(exit_code),
    )
    return bool(ok) and exit_code.value == STILL_ACTIVE


def get_module(process: pymem.Pymem, module_name: str):
    module = pymem.process.module_from_name(process.process_handle, module_name)
    if module is None:
        raise RuntimeError(f"{module_name} was not found.")
    return module


def scan_pattern_address(
    process: pymem.Pymem,
    module_name: str,
    pattern: bytes,
    label: str,
) -> int:
    module = get_module(process, module_name)
    module_bytes = process.read_bytes(module.lpBaseOfDll, module.SizeOfImage)
    match = re.search(pattern, module_bytes)
    if match is None:
        raise RuntimeError(f"{label} pattern was not found.")
    return module.lpBaseOfDll + match.start()


def read_pointer_chain(
    process: pymem.Pymem,
    base_address: int,
    static_offset: int,
    offsets: list[int] | tuple[int, ...],
) -> int:
    address = base_address + static_offset
    for offset in offsets:
        address = process.read_longlong(address)
        address += offset
    return address


def apply_patch_once(spec: PatchSpec) -> int:
    process = None
    try:
        process = open_process()
        address = scan_pattern_address(
            process,
            spec.module_name,
            spec.pattern,
            spec.name,
        )
        process.write_bytes(address, spec.patch_bytes, len(spec.patch_bytes))
        return address
    finally:
        close_process(process)


def toggle_patch(spec: PatchSpec, state: PatchState) -> tuple[bool, str]:
    process = None
    try:
        process = open_process()
        current_pid = get_process_id(process)

        if state.enabled:
            if state.address is None or state.original_bytes is None or state.pid is None:
                state.enabled = False
                state.address = None
                state.original_bytes = None
                state.pid = None
                return False, f"{spec.name} state was lost. Enable it again."

            if current_pid != state.pid:
                state.enabled = False
                state.address = None
                state.original_bytes = None
                state.pid = None
                return False, f"{GAME_EXE_NAME} restarted. Enable {spec.name.lower()} again."

            process.write_bytes(
                state.address,
                state.original_bytes,
                len(state.original_bytes),
            )
            state.enabled = False
            state.address = None
            state.original_bytes = None
            state.pid = None
            return True, f"{spec.name} disabled."

        address = scan_pattern_address(
            process,
            spec.module_name,
            spec.pattern,
            spec.name,
        )
        original_bytes = process.read_bytes(address, len(spec.patch_bytes))
        process.write_bytes(address, spec.patch_bytes, len(spec.patch_bytes))
        state.enabled = True
        state.address = address
        state.original_bytes = original_bytes
        state.pid = current_pid
        return True, f"{spec.name} enabled."
    finally:
        close_process(process)
