from cx_Freeze import setup, Executable

build_exe_options = {
    "packages": ["dearpygui"],
}

setup(
    name="CrabGameMenu",
    options={"build_exe": build_exe_options},
    executables=[Executable(
        "main.py",
        base="gui"
    )]
)
