import ctypes
import ctypes.wintypes
import re

import dearpygui.dearpygui as dpg
import keyboard
import pymem
import pymem.process
import time

MENU_TITLE = "Crab Game Menu"
MENU_TAG = "menu_window"
POSITION_INPUT_TAG = "position_input"
CURRENT_POSITION_TAG = "current_position"
STATUS_TAG = "status_text"
STATUS_OK_THEME = "status_ok_theme"
STATUS_ERROR_THEME = "status_error_theme"
APPLY_BUTTON_TAG = "apply_position_button"
REFRESH_BUTTON_TAG = "refresh_position_button"
TELEPORT_BUTTON_TAG = "teleport_interact_button"

PLAYER_STATIC_OFFSET = 0x01A81BA8
INTERACT_TELEPORT_POSITION = (0.678, -18.297, 12.257)
INTERACT_TELEPORT_HOTKEY = "f6"

AXIS_POINTERS = {
    "x": [0x480, 0x3A0],
    "y": [0x480, 0x3A4],
    "z": [0x480, 0x3A8],
}
AXIS_ORDER = ("x", "y", "z")
HOTKEY_HINTS = (
    "Ctrl+Alt+Left / Right = X -/+ 1",
    "Ctrl+Alt+Down / Up = Y -/+ 1",
    "Ctrl+Alt+PageDown / PageUp = Z -/+ 1",
)

visible = False
pm = None
SW_HIDE = 0
SW_SHOW = 5
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2


def set_status(message, is_error=False):
    print(message)
    if dpg.does_item_exist(STATUS_TAG):
        dpg.set_value(STATUS_TAG, message)
        theme = STATUS_ERROR_THEME if is_error else STATUS_OK_THEME
        dpg.bind_item_theme(STATUS_TAG, theme)


def ensure_process():
    global pm

    if pm is not None:
        return pm

    try:
        pm = pymem.Pymem("Crab Game.exe")
        set_status("Attached to Crab Game.")
        return pm
    except pymem.exception.ProcessNotFound:
        pm = None
        set_status("Crab Game.exe not found. Start the game first.",
                   is_error=True)
        return None
    except Exception as exc:
        pm = None
        set_status(f"Could not attach to Crab Game: {exc}", is_error=True)
        return None


def get_hwnd():
    return ctypes.windll.user32.FindWindowW(None, MENU_TITLE)


def hide_window():
    hwnd = get_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)


def toggle_window():
    global visible

    hwnd = get_hwnd()
    if not hwnd:
        return

    visible = not visible

    if visible:
        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(
            fg_hwnd, None)
        our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, True)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, False)

        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        center_x = (rect.left + rect.right) // 2
        center_y = (rect.top + rect.bottom) // 2
        ctypes.windll.user32.SetCursorPos(center_x, center_y)
    else:
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)


def is_interactive_item_hovered():
    interactive_tags = (
        POSITION_INPUT_TAG,
        APPLY_BUTTON_TAG,
        REFRESH_BUTTON_TAG,
        TELEPORT_BUTTON_TAG,
    )
    return any(dpg.is_item_hovered(tag) for tag in interactive_tags if dpg.does_item_exist(tag))


def begin_native_window_drag():
    hwnd = get_hwnd()
    if not hwnd:
        return

    ctypes.windll.user32.ReleaseCapture()
    ctypes.windll.user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)


def read_pointer_chain(process, base_addr, static_offset, offsets):
    address = base_addr + static_offset
    for offset in offsets:
        address = process.read_longlong(address)
        address += offset
    return address


def get_axis_addresses():
    global pm

    process = ensure_process()
    if process is None:
        raise RuntimeError("Crab Game is not running.")

    try:
        module = pymem.process.module_from_name(
            process.process_handle, "UnityPlayer.dll")
    except Exception as exc:
        pm = None
        raise RuntimeError(f"Lost connection to Crab Game: {exc}") from exc

    if module is None:
        raise RuntimeError("UnityPlayer.dll was not found.")

    base_address = module.lpBaseOfDll
    return {
        axis: read_pointer_chain(
            process, base_address, PLAYER_STATIC_OFFSET, offsets)
        for axis, offsets in AXIS_POINTERS.items()
    }


def format_position(position):
    return ", ".join(f"{value:.3f}" for value in position)


def read_position():
    process = ensure_process()
    if process is None:
        raise RuntimeError("Crab Game is not running.")

    addresses = get_axis_addresses()
    return tuple(process.read_float(addresses[axis]) for axis in AXIS_ORDER)


def write_position(position):
    process = ensure_process()
    if process is None:
        raise RuntimeError("Crab Game is not running.")

    addresses = get_axis_addresses()
    for axis, value in zip(AXIS_ORDER, position):
        process.write_float(addresses[axis], float(value))


def teleport_and_press_interact(update_status=True, sync_ui=False):
    write_position(INTERACT_TELEPORT_POSITION)
    time.sleep(0.5)
    keyboard.press_and_release("e")

    if sync_ui:
        sync_position_ui(show_status=False)

    if update_status:
        set_status(
            f"Teleported to {format_position(INTERACT_TELEPORT_POSITION)} and pressed E."
        )


def parse_position_input(raw_value):
    parts = [part for part in re.split(r"[\s,]+", raw_value.strip()) if part]
    if len(parts) != 3:
        raise ValueError("Enter exactly three numbers: x, y, z")
    return tuple(float(part) for part in parts)


def sync_position_ui(show_status=True):
    if not dpg.does_item_exist(CURRENT_POSITION_TAG):
        return

    try:
        position = read_position()
        position_text = format_position(position)
        dpg.set_value(CURRENT_POSITION_TAG,
                      f"Current Position: {position_text}")
        dpg.set_value(POSITION_INPUT_TAG, position_text)
        if show_status:
            set_status("Position refreshed.")
    except Exception as exc:
        set_status(f"Unable to read position: {exc}", is_error=True)


def apply_position(sender=None, app_data=None, user_data=None):
    try:
        position = parse_position_input(dpg.get_value(POSITION_INPUT_TAG))
        write_position(position)
        sync_position_ui(show_status=False)
        set_status(f"Moved to {format_position(position)}.")
    except Exception as exc:
        set_status(f"Unable to update position: {exc}", is_error=True)


def apply_interact_teleport(sender=None, app_data=None, user_data=None):
    try:
        teleport_and_press_interact(sync_ui=True)
    except Exception as exc:
        set_status(f"Unable to run teleport action: {exc}", is_error=True)


def hotkey_interact_teleport():
    try:
        teleport_and_press_interact(update_status=False, sync_ui=False)
        print(
            f"Hotkey teleport -> {format_position(INTERACT_TELEPORT_POSITION)} and pressed E."
        )
    except Exception as exc:
        print(f"Hotkey teleport failed: {exc}")


def nudge_axis(axis, delta):
    if not visible:
        return

    try:
        current_position = list(read_position())
        axis_index = AXIS_ORDER.index(axis)
        current_position[axis_index] += delta
        write_position(tuple(current_position))
        sync_position_ui(show_status=False)
        sign = "+" if delta > 0 else "-"
        set_status(
            f"{axis.upper()} {sign}{abs(delta):.0f} -> {format_position(current_position)}")
    except Exception as exc:
        set_status(f"Unable to nudge {axis.upper()}: {exc}", is_error=True)


def handle_axis_hotkey(sender, app_data):
    if not visible:
        return

    ctrl_down = dpg.is_key_down(
        dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
    alt_down = dpg.is_key_down(
        dpg.mvKey_LAlt) or dpg.is_key_down(dpg.mvKey_RAlt)

    if not (ctrl_down and alt_down):
        return

    keymap = {
        dpg.mvKey_Left: ("x", -1.0),
        dpg.mvKey_Right: ("x", 1.0),
        dpg.mvKey_Down: ("y", -1.0),
        dpg.mvKey_Up: ("y", 1.0),
        dpg.mvKey_Prior: ("z", 1.0),
        dpg.mvKey_Next: ("z", -1.0),
    }

    if app_data in keymap:
        axis, delta = keymap[app_data]
        nudge_axis(axis, delta)


def handle_mouse_down(sender, app_data):
    if app_data != 0 or not visible:
        return

    if dpg.is_item_hovered(MENU_TAG) and not is_interactive_item_hovered():
        begin_native_window_drag()


def build_theme():
    with dpg.theme(tag="main_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,
                                (19, 24, 31), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,
                                (24, 30, 38), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Text,
                                (235, 239, 244), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Button,
                                (56, 102, 214), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                (77, 125, 236), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,
                                (45, 86, 186), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,
                                (31, 39, 49), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,
                                (42, 51, 64), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,
                                (49, 58, 72), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Border,
                                (60, 74, 91), category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,
                                18, 18, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,
                                10, 8, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,
                                10, 10, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,
                                6, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,
                                8, category=dpg.mvThemeCat_Core)

    with dpg.theme(tag=STATUS_OK_THEME):
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text,
                                (123, 220, 147), category=dpg.mvThemeCat_Core)

    with dpg.theme(tag=STATUS_ERROR_THEME):
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text,
                                (255, 115, 115), category=dpg.mvThemeCat_Core)


def build_ui():
    with dpg.window(
        label="Menu",
        tag=MENU_TAG,
        no_title_bar=True,
        no_move=True,
        no_resize=True,
        no_collapse=True,
        width=480,
        height=320,
    ):
        dpg.add_text("Crab Game Position Editor")
        dpg.add_text("Insert toggles the menu.", color=(154, 167, 183))
        dpg.add_separator()

        dpg.add_text("Current Position: unavailable", tag=CURRENT_POSITION_TAG)
        dpg.add_spacer(height=4)

        dpg.add_text("Target Position (x, y, z)")
        dpg.add_input_text(
            tag=POSITION_INPUT_TAG,
            width=430,
            hint="Example: 125.0, 18.5, -44.25",
            on_enter=True,
            callback=apply_position,
        )

        with dpg.group(horizontal=True):
            dpg.add_button(label="Apply Position", tag=APPLY_BUTTON_TAG, width=210,
                           height=34, callback=apply_position)
            dpg.add_button(
                label="Refresh Current",
                tag=REFRESH_BUTTON_TAG,
                width=210,
                height=34,
                callback=lambda sender, app_data, user_data: sync_position_ui(),
            )

        dpg.add_spacer(height=4)
        dpg.add_button(
            label="Teleport To Interact Spot + Press E",
            tag=TELEPORT_BUTTON_TAG,
            width=430,
            height=38,
            callback=apply_interact_teleport,
        )

        dpg.add_separator()
        dpg.add_text(
            f"{INTERACT_TELEPORT_HOTKEY.upper()} = Teleport to preset spot and press E",
            color=(154, 167, 183),
        )
        dpg.add_text("Hotkeys while menu is open")
        for hint in HOTKEY_HINTS:
            dpg.add_text(hint, color=(154, 167, 183))

        dpg.add_spacer(height=8)
        dpg.add_text("Waiting for input.", tag=STATUS_TAG)
        dpg.bind_item_theme(STATUS_TAG, STATUS_OK_THEME)


def main():
    dpg.create_context()
    build_theme()
    build_ui()

    dpg.create_viewport(title=MENU_TITLE, width=480, height=320)
    dpg.set_viewport_decorated(False)
    dpg.set_viewport_resizable(False)
    dpg.set_viewport_always_top(True)
    dpg.setup_dearpygui()
    dpg.bind_theme("main_theme")
    dpg.show_viewport()
    dpg.set_primary_window(MENU_TAG, True)

    hide_window()

    with dpg.handler_registry():
        dpg.add_key_press_handler(callback=handle_axis_hotkey)
        dpg.add_mouse_down_handler(callback=handle_mouse_down)

    insert_hotkey = keyboard.add_hotkey("insert", callback=toggle_window)
    interact_hotkey = keyboard.add_hotkey(
        INTERACT_TELEPORT_HOTKEY,
        callback=hotkey_interact_teleport,
    )

    try:
        dpg.start_dearpygui()
    finally:
        keyboard.remove_hotkey(insert_hotkey)
        keyboard.remove_hotkey(interact_hotkey)
        dpg.destroy_context()


if __name__ == "__main__":
    main()
