from cx_Freeze import setup, Executable

build_exe_options = {
    "packages": ["dearpygui", "keyboard", "pymem", "ctypes"],
    "includes": ["antiCheat", "memory_utils", "themes"],
}

setup(
    name="CrabGameMenu",
    version="1.0.0",
    description="A Dear PyGui-based Crab Game utility menu.",
    options={"build_exe": build_exe_options},
    executables=[Executable("main.py", base="gui")],
)
