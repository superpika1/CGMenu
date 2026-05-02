import ctypes
import ctypes.wintypes
import queue
import re
import threading
import time

import dearpygui.dearpygui as dpg
import keyboard
import pymem

import antiCheat
import themes
from memory_utils import (
    GAME_ASSEMBLY_MODULE,
    GAME_EXE_NAME,
    UNITY_PLAYER_MODULE,
    PatchSpec,
    PatchState,
    get_module,
    get_process_id,
    is_process_alive,
    open_process,
    read_pointer_chain,
    toggle_patch,
)


MENU_TITLE = "Crab Game Menu"
DEFAULT_THEME_NAME = "Midnight"
VIEWPORT_WIDTH = 480
VIEWPORT_HEIGHT = 435
MENU_TAG = "menu_window"
POSITION_INPUT_TAG = "position_input"
CURRENT_POSITION_TAG = "current_position"
STATUS_TAG = "status_text"
THEME_SELECTOR_TAG = "theme_selector"
INSERT_HINT_TAG = "insert_hint"
INTERACT_HINT_TAG = "interact_hint"
HP_HINT_TAG = "hp_hint"
STATUS_OK_THEME = "status_ok_theme"
STATUS_ERROR_THEME = "status_error_theme"
APPLY_BUTTON_TAG = "apply_position_button"
REFRESH_BUTTON_TAG = "refresh_position_button"
TELEPORT_BUTTON_TAG = "teleport_interact_button"
SET_HP_TOGGLE_TAG = "set_hp_toggle"
INFINITE_JUMP_TOGGLE_TAG = "infinite_jump_toggle"
INFINITE_JUMP_HINT_TAG = "infinite_jump_hint"
NO_KNOCKBACK_TOGGLE_TAG = "no_knockback_toggle"
NO_KNOCKBACK_HINT_TAG = "no_knockback_hint"

PLAYER_STATIC_OFFSET = 0x01A81BA8
INTERACT_TELEPORT_POSITION = (0.678, -18.297, 12.257)
INTERACT_TELEPORT_HOTKEY = "f6"

PLAYER_STATIC_OFFSET_2 = 0X01D83FD0
SET_HP_HOTKEY = "f5"
SET_HP_VALUE = 100
HP_FREEZE_INTERVAL = 0.05
HP_POINTERS = [0x48, 0xB8, 0x28, 0x24]

INFINITE_JUMP_HOTKEY = "f4"
NO_KNOCKBACK_HOTKEY = "f3"

AXIS_POINTERS = {
    "x": [0x480, 0x3A0],
    "y": [0x480, 0x3A4],
    "z": [0x480, 0x3A8],
}
AXIS_ORDER = ("x", "y", "z")
HOTKEY_HINTS = (
    "F7 / F8 = X -/+ 1",
    "F9 / F10 = Y -/+ 1",
    "F11 / F12 = Z -/+ 1",
)

THEMES = themes.THEMES
INFINITE_JUMP_PATCH = PatchSpec(
    name="Infinite jump",
    module_name=GAME_ASSEMBLY_MODULE,
    pattern=rb"\x80\xBB.....\x74\x09\x80\xBB.....",
    patch_bytes=b"\x90\x90\x90\x90\x90\x90\x90\x90\x90",
)
NO_KNOCKBACK_PATCH = PatchSpec(
    name="No knockback",
    module_name=GAME_ASSEMBLY_MODULE,
    pattern=(
        rb"\x48\x89\x5C\x24.\x48\x89\x74\x24.\x57\x48\x83\xEC."
        rb"\x80\x3D.....\x48\x8B\xF2\x48\x8B\xD9\x75.\x48\x8D\x0D...."
        rb"\xE8....\x48\x8D\x0D....\xE8....\xC6\x05.....\x48\x8B\x7B"
    ),
    patch_bytes=b"\xC3\x90\x90\x90\x90",
)

visible = False
pm = None
hp_freeze_enabled = False
hp_freeze_stop_event = threading.Event()
hp_freeze_lock = threading.Lock()
anti_cheat_patched_pid = None
infinite_jump_state = PatchState()
no_knockback_state = PatchState()
ui_status_queue = queue.SimpleQueue()
ui_action_queue = queue.SimpleQueue()
current_theme_name = DEFAULT_THEME_NAME
SW_HIDE = 0
SW_SHOW = 5
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2


def set_status(message, is_error=False):
    if threading.current_thread() is not threading.main_thread():
        queue_status(message, is_error=is_error)
        return

    print(message)
    if dpg.does_item_exist(STATUS_TAG):
        dpg.set_value(STATUS_TAG, message)
        theme = STATUS_ERROR_THEME if is_error else STATUS_OK_THEME
        dpg.bind_item_theme(STATUS_TAG, theme)


def queue_status(message, is_error=False):
    ui_status_queue.put((message, is_error))


def flush_status_updates():
    while True:
        try:
            message, is_error = ui_status_queue.get_nowait()
        except queue.Empty:
            return
        set_status(message, is_error=is_error)


def queue_ui_action(action, payload=None):
    ui_action_queue.put((action, payload))


def flush_ui_actions():
    while True:
        try:
            action, payload = ui_action_queue.get_nowait()
        except queue.Empty:
            return
        if action == "sync_position":
            sync_position_ui(show_status=payload)
        elif action == "sync_hp_toggle":
            update_hp_toggle()
        elif action == "sync_infinite_jump_toggle":
            update_infinite_jump_toggle()
        elif action == "sync_no_knockback_toggle":
            update_no_knockback_toggle()


def reset_process_cache():
    global pm
    global anti_cheat_patched_pid

    if pm is not None:
        try:
            pm.close_process()
        except Exception:
            pass

    pm = None
    anti_cheat_patched_pid = None


def ensure_anti_cheat_patch(process_id):
    global anti_cheat_patched_pid

    if anti_cheat_patched_pid == process_id:
        return

    antiCheat.run()
    anti_cheat_patched_pid = process_id


def ensure_process():
    global pm

    if pm is not None and is_process_alive(pm):
        return pm

    reset_process_cache()

    try:
        pm = open_process(GAME_EXE_NAME)
        process_id = get_process_id(pm)
        status_message = "Attached to Crab Game."
        try:
            ensure_anti_cheat_patch(process_id)
            status_message += " Anti-cheat patch applied."
            set_status(status_message)
        except Exception as exc:
            set_status(f"{status_message} Anti-cheat patch failed: {exc}",
                       is_error=True)
        return pm
    except pymem.exception.ProcessNotFound:
        reset_process_cache()
        set_status(f"{GAME_EXE_NAME} not found. Start the game first.",
                   is_error=True)
        return None
    except Exception as exc:
        reset_process_cache()
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
        THEME_SELECTOR_TAG,
        APPLY_BUTTON_TAG,
        REFRESH_BUTTON_TAG,
        TELEPORT_BUTTON_TAG,
        SET_HP_TOGGLE_TAG,
        INFINITE_JUMP_TOGGLE_TAG,
        NO_KNOCKBACK_TOGGLE_TAG,
    )
    return any(dpg.is_item_hovered(tag) for tag in interactive_tags if dpg.does_item_exist(tag))


def begin_native_window_drag():
    hwnd = get_hwnd()
    if not hwnd:
        return

    ctypes.windll.user32.ReleaseCapture()
    ctypes.windll.user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)


def resolve_pointer_chain(module_name, static_offset, offsets):
    process = ensure_process()
    if process is None:
        raise RuntimeError("Crab Game is not running.")

    try:
        module = get_module(process, module_name)
    except Exception as exc:
        reset_process_cache()
        raise RuntimeError(f"Lost connection to Crab Game: {exc}") from exc

    return read_pointer_chain(
        process,
        module.lpBaseOfDll,
        static_offset,
        offsets,
    )


def get_hp_address():
    return resolve_pointer_chain(
        GAME_ASSEMBLY_MODULE,
        PLAYER_STATIC_OFFSET_2,
        HP_POINTERS,
    )


def read_hp():
    process = ensure_process()
    if process is None:
        raise RuntimeError("Crab Game is not running.")

    return process.read_int(get_hp_address())


def write_hp(value, update_status=True):
    process = ensure_process()
    address = get_hp_address()
    process.write_int(address, int(value))
    if update_status:
        set_status(f"HP set to {value}")


def get_axis_addresses():
    return {
        axis: resolve_pointer_chain(
            UNITY_PLAYER_MODULE,
            PLAYER_STATIC_OFFSET,
            offsets,
        )
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


def toggle_feature_patch(patch_spec, patch_state):
    try:
        return toggle_patch(patch_spec, patch_state)
    except pymem.exception.ProcessNotFound:
        return False, f"{GAME_EXE_NAME} not found. Start the game first."
    except Exception as exc:
        return False, f"{patch_spec.name} failed: {exc}"


def toggle_infinite_jump_patch():
    return toggle_feature_patch(INFINITE_JUMP_PATCH, infinite_jump_state)


def toggle_no_knockback_patch():
    return toggle_feature_patch(NO_KNOCKBACK_PATCH, no_knockback_state)


def teleport_and_press_interact(update_status=True, sync_ui=False):
    write_position(INTERACT_TELEPORT_POSITION)
    time.sleep(0.25)
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
    if threading.current_thread() is not threading.main_thread():
        queue_ui_action("sync_position", show_status)
        return

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
        queue_status(
            f"Hotkey teleport -> {format_position(INTERACT_TELEPORT_POSITION)} and pressed E."
        )
    except Exception as exc:
        queue_status(f"Hotkey teleport failed: {exc}", is_error=True)


def update_hp_toggle():
    if threading.current_thread() is not threading.main_thread():
        queue_ui_action("sync_hp_toggle")
        return

    if not dpg.does_item_exist(SET_HP_TOGGLE_TAG):
        return

    dpg.set_value(SET_HP_TOGGLE_TAG, hp_freeze_enabled)


def update_infinite_jump_toggle():
    if threading.current_thread() is not threading.main_thread():
        queue_ui_action("sync_infinite_jump_toggle")
        return

    if not dpg.does_item_exist(INFINITE_JUMP_TOGGLE_TAG):
        return

    dpg.set_value(INFINITE_JUMP_TOGGLE_TAG, infinite_jump_state.enabled)


def update_no_knockback_toggle():
    if threading.current_thread() is not threading.main_thread():
        queue_ui_action("sync_no_knockback_toggle")
        return

    if not dpg.does_item_exist(NO_KNOCKBACK_TOGGLE_TAG):
        return

    dpg.set_value(NO_KNOCKBACK_TOGGLE_TAG, no_knockback_state.enabled)


def hotkey_toggle_infinite_jump():
    try:
        success, message = toggle_infinite_jump_patch()
        update_infinite_jump_toggle()
        if not success:
            queue_status(message, is_error=True)
            return

        queue_status(message)
    except Exception as exc:
        queue_status(f"Hotkey infinite jump failed: {exc}", is_error=True)


def hotkey_toggle_no_knockback():
    try:
        success, message = toggle_no_knockback_patch()
        update_no_knockback_toggle()
        if not success:
            queue_status(message, is_error=True)
            return

        queue_status(message)
    except Exception as exc:
        queue_status(f"Hotkey no knockback failed: {exc}", is_error=True)


def hp_freeze_worker():
    global hp_freeze_enabled

    while not hp_freeze_stop_event.is_set():
        try:
            write_hp(SET_HP_VALUE, update_status=False)
        except Exception as exc:
            hp_freeze_enabled = False
            hp_freeze_stop_event.set()
            update_hp_toggle()
            queue_status(f"HP freeze failed: {exc}", is_error=True)
            return

        hp_freeze_stop_event.wait(HP_FREEZE_INTERVAL)


def set_hp_freeze_enabled(enabled, update_status=True):
    global hp_freeze_enabled

    with hp_freeze_lock:
        if enabled == hp_freeze_enabled:
            update_hp_toggle()
            return hp_freeze_enabled

        if not enabled:
            hp_freeze_enabled = False
            hp_freeze_stop_event.set()
            update_hp_toggle()
            if update_status:
                set_status("HP freeze disabled.")
            return False

        write_hp(SET_HP_VALUE, update_status=False)
        hp_freeze_stop_event.clear()
        hp_freeze_enabled = True
        threading.Thread(target=hp_freeze_worker, daemon=True).start()
        update_hp_toggle()
        if update_status:
            set_status(f"HP freeze enabled at {SET_HP_VALUE}.")
        return True


def toggle_hp_freeze(update_status=True):
    return set_hp_freeze_enabled(not hp_freeze_enabled, update_status=update_status)


def apply_toggle_hp_freeze(sender=None, app_data=None, user_data=None):
    try:
        set_hp_freeze_enabled(bool(app_data), update_status=True)
    except Exception as exc:
        update_hp_toggle()
        set_status(f"Unable to toggle HP freeze: {exc}", is_error=True)


def hotkey_toggle_hp_freeze():
    try:
        enabled = toggle_hp_freeze(update_status=False)
        state = "enabled" if enabled else "disabled"
        queue_status(f"Hotkey HP freeze {state} at {SET_HP_VALUE}.")
    except Exception as exc:
        queue_status(f"Hotkey HP freeze failed: {exc}", is_error=True)


def apply_infinite_jump(sender=None, app_data=None, user_data=None):
    desired_state = bool(app_data)
    if desired_state == infinite_jump_state.enabled:
        update_infinite_jump_toggle()
        return

    success, message = toggle_infinite_jump_patch()
    if success:
        update_infinite_jump_toggle()
        set_status(message)
        return

    update_infinite_jump_toggle()
    set_status(message, is_error=True)


def apply_no_knockback(sender=None, app_data=None, user_data=None):
    desired_state = bool(app_data)
    if desired_state == no_knockback_state.enabled:
        update_no_knockback_toggle()
        return

    success, message = toggle_no_knockback_patch()
    if success:
        update_no_knockback_toggle()
        set_status(message)
        return

    update_no_knockback_toggle()
    set_status(message, is_error=True)


def nudge_axis(axis, delta):
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


def hotkey_nudge_axis(axis, delta):
    nudge_axis(axis, delta)


def handle_mouse_down(sender, app_data):
    if app_data != 0 or not visible:
        return

    if dpg.is_item_hovered(MENU_TAG) and not is_interactive_item_hovered():
        begin_native_window_drag()


def apply_theme(theme_name, update_status=False):
    global current_theme_name

    if theme_name not in THEMES:
        theme_name = DEFAULT_THEME_NAME

    current_theme_name = theme_name
    theme = THEMES[theme_name]
    dpg.bind_theme(theme["tag"])

    if dpg.does_item_exist(THEME_SELECTOR_TAG):
        current_value = dpg.get_value(THEME_SELECTOR_TAG)
        if current_value != theme_name:
            dpg.set_value(THEME_SELECTOR_TAG, theme_name)

    hint_items = [
        INSERT_HINT_TAG,
        INTERACT_HINT_TAG,
        HP_HINT_TAG,
        INFINITE_JUMP_HINT_TAG,
        NO_KNOCKBACK_HINT_TAG,
    ]
    hint_items.extend(
        f"hotkey_hint_{index}" for index in range(len(HOTKEY_HINTS)))
    for item_tag in hint_items:
        if dpg.does_item_exist(item_tag):
            dpg.configure_item(item_tag, color=theme["hint"])

    if update_status:
        set_status(f"Theme changed to {theme_name}.")


def apply_selected_theme(sender=None, app_data=None, user_data=None):
    apply_theme(str(app_data), update_status=True)


def build_theme():
    for theme in THEMES.values():
        with dpg.theme(tag=theme["tag"]):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,
                                    theme["window_bg"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,
                                    theme["child_bg"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_PopupBg,
                                    theme["popup_bg"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Text,
                                    theme["text"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Button,
                                    theme["button"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                    theme["button_hovered"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,
                                    theme["button_active"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg,
                                    theme["frame_bg"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,
                                    theme["frame_bg_hovered"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,
                                    theme["frame_bg_active"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Border,
                                    theme["border"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark,
                                    theme["checkmark"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Header,
                                    theme["header"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,
                                    theme["header_hovered"], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,
                                    theme["header_active"], category=dpg.mvThemeCat_Core)
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
        width=VIEWPORT_WIDTH,
        height=VIEWPORT_HEIGHT,
    ):
        dpg.add_text("CGMenu")
        dpg.add_text(
            "Insert toggles the menu.",
            tag=INSERT_HINT_TAG,
            color=THEMES[current_theme_name]["hint"],
        )

        dpg.add_text("Waiting for input.", tag=STATUS_TAG)
        dpg.bind_item_theme(STATUS_TAG, STATUS_OK_THEME)

        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_text("Theme")
            dpg.add_combo(
                list(THEMES.keys()),
                default_value=current_theme_name,
                tag=THEME_SELECTOR_TAG,
                width=170,
                callback=apply_selected_theme,
            )
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
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Ready Up",
                tag=TELEPORT_BUTTON_TAG,
                width=210,
                height=38,
                callback=apply_interact_teleport,
            )
            dpg.add_checkbox(
                label=f"Freeze HP ({SET_HP_VALUE})",
                tag=SET_HP_TOGGLE_TAG,
                callback=apply_toggle_hp_freeze,
            )
            dpg.add_checkbox(
                label="Infinite Jump",
                tag=INFINITE_JUMP_TOGGLE_TAG,
                callback=apply_infinite_jump,
            )
        dpg.add_checkbox(
            label="No Knockback",
            tag=NO_KNOCKBACK_TOGGLE_TAG,
            callback=apply_no_knockback,
        )

        dpg.add_separator()
        dpg.add_text(
            f"{NO_KNOCKBACK_HOTKEY.upper()} = Toggle no knockback",
            tag=NO_KNOCKBACK_HINT_TAG,
            color=THEMES[current_theme_name]["hint"],
        )
        dpg.add_text(
            f"{INTERACT_TELEPORT_HOTKEY.upper()} = Ready Up",
            tag=INTERACT_HINT_TAG,
            color=THEMES[current_theme_name]["hint"],
        )
        dpg.add_text(
            f"{SET_HP_HOTKEY.upper()} = Toggle HP freeze at {SET_HP_VALUE}",
            tag=HP_HINT_TAG,
            color=THEMES[current_theme_name]["hint"],
        )
        dpg.add_text(
            f"{INFINITE_JUMP_HOTKEY.upper()} = Toggle infinite jump",
            tag=INFINITE_JUMP_HINT_TAG,
            color=THEMES[current_theme_name]["hint"],
        )
        dpg.add_text("Global Hotkeys")
        for index, hint in enumerate(HOTKEY_HINTS):
            dpg.add_text(
                hint,
                tag=f"hotkey_hint_{index}",
                color=THEMES[current_theme_name]["hint"],
            )


def main():
    dpg.create_context()
    build_theme()
    build_ui()
    update_hp_toggle()
    update_infinite_jump_toggle()
    update_no_knockback_toggle()

    dpg.create_viewport(
        title=MENU_TITLE,
        width=VIEWPORT_WIDTH,
        height=VIEWPORT_HEIGHT,
    )
    dpg.set_viewport_decorated(False)
    dpg.set_viewport_resizable(False)
    dpg.set_viewport_always_top(True)
    dpg.setup_dearpygui()
    apply_theme(current_theme_name)
    dpg.show_viewport()
    dpg.set_primary_window(MENU_TAG, True)

    hide_window()

    with dpg.handler_registry():
        dpg.add_mouse_down_handler(callback=handle_mouse_down)

    insert_hotkey = keyboard.add_hotkey("insert", callback=toggle_window)
    interact_hotkey = keyboard.add_hotkey(
        INTERACT_TELEPORT_HOTKEY,
        callback=hotkey_interact_teleport,
    )
    set_hp_hotkey = keyboard.add_hotkey(
        SET_HP_HOTKEY,
        callback=hotkey_toggle_hp_freeze,
    )
    infinite_jump_hotkey = keyboard.add_hotkey(
        INFINITE_JUMP_HOTKEY,
        callback=hotkey_toggle_infinite_jump
    )
    no_knockback_hotkey = keyboard.add_hotkey(
        NO_KNOCKBACK_HOTKEY,
        callback=hotkey_toggle_no_knockback,
    )
    nudge_hotkeys = [
        keyboard.add_hotkey(
            "f7", callback=lambda: hotkey_nudge_axis("x", -1.0)),
        keyboard.add_hotkey(
            "f8", callback=lambda: hotkey_nudge_axis("x", 1.0)),
        keyboard.add_hotkey(
            "f9", callback=lambda: hotkey_nudge_axis("y", -1.0)),
        keyboard.add_hotkey(
            "f10", callback=lambda: hotkey_nudge_axis("y", 1.0)),
        keyboard.add_hotkey(
            "f11", callback=lambda: hotkey_nudge_axis("z", 1.0)),
        keyboard.add_hotkey(
            "f12", callback=lambda: hotkey_nudge_axis("z", -1.0)),
    ]
    try:
        while dpg.is_dearpygui_running():
            flush_ui_actions()
            flush_status_updates()
            dpg.render_dearpygui_frame()
    finally:
        hp_freeze_stop_event.set()
        keyboard.remove_hotkey(insert_hotkey)
        keyboard.remove_hotkey(interact_hotkey)
        keyboard.remove_hotkey(set_hp_hotkey)
        keyboard.remove_hotkey(infinite_jump_hotkey)
        keyboard.remove_hotkey(no_knockback_hotkey)
        for hotkey in nudge_hotkeys:
            keyboard.remove_hotkey(hotkey)
        dpg.destroy_context()


if __name__ == "__main__":
    main()
