import pymem
import pymem.process
import re


def run():
    pm = None
    try:
        try:
            pm = pymem.Pymem("Crab Game.exe")
        except pymem.exception.ProcessNotFound as exc:
            raise RuntimeError("Crab Game.exe not found. Start the game first.") from exc

        client = pymem.process.module_from_name(
            pm.process_handle,
            "GameAssembly.dll",
        )

        if client is None:
            raise RuntimeError("GameAssembly.dll was not found.")

        client_module = pm.read_bytes(client.lpBaseOfDll, client.SizeOfImage)
        match = re.search(
            rb"\x40\x53\x48\x83\xEC\x20\x48\x8B\xD9\x48\x85\xC9\x74\x71",
            client_module,
        )

        if match is None:
            raise RuntimeError("Anti-cheat pattern was not found.")

        address = client.lpBaseOfDll + match.start()
        pm.write_bytes(address, b"\xC3\x90", 2)
        return address
    finally:
        if pm is not None:
            pm.close_process()
