from memory_utils import GAME_ASSEMBLY_MODULE, PatchSpec, apply_patch_once


ANTI_CHEAT_PATCH = PatchSpec(
    name="Anti-cheat",
    module_name=GAME_ASSEMBLY_MODULE,
    pattern=rb"\x40\x53\x48\x83\xEC\x20\x48\x8B\xD9\x48\x85\xC9\x74\x71",
    patch_bytes=b"\xC3\x90",
)


def run():
    return apply_patch_once(ANTI_CHEAT_PATCH)
