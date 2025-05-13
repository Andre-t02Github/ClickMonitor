#!/usr/bin/env python3
import os
import sys
import threading
import time
import json
import subprocess
from select import select
from evdev import InputDevice, ecodes, list_devices, categorize
from pathlib import Path

# 全局变量
is_touch = False
last_click_position = {"x": 0, "y": 0}
lock = threading.Lock()

# 坐标缓冲
coord_buffer = {"x": 0, "y": 0}

# 屏幕信息
screen_width = 1920  # 默认值
screen_height = 1080  # 默认值

def get_screen_resolution():
    """尝试获取屏幕分辨率"""
    global screen_width, screen_height
    
    try:
        # 尝试从xrandr获取屏幕分辨率
        output = subprocess.check_output(['cat', '/sys/class/graphics/fb0/virtual_size'], 
                                         universal_newlines=True)
        if output:
            values = output.strip().split(',')
            if len(values) == 2:
                screen_width, screen_height = int(values[0]), int(values[1])
                return True
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        pass
    
    # 尝试从文件系统读取
    try:
        fb_path = Path('/sys/class/graphics/fb0/virtual_size')
        if fb_path.exists():
            content = fb_path.read_text().strip().split(',')
            if len(content) == 2:
                screen_width, screen_height = int(content[0]), int(content[1])
                return True
    except Exception:
        pass
    
    # 如果都失败了，尝试从配置文件读取
    try:
        config_path = Path('/etc/X11/xorg.conf')
        if config_path.exists():
            with open(config_path, 'r') as f:
                content = f.read()
                # 简单解析，实际可能需要更复杂的解析器
                if "Modes" in content and "x" in content:
                    for line in content.split('\n'):
                        if "Modes" in line and "x" in line:
                            mode = line.split('"')[1]
                            w, h = mode.split('x')
                            screen_width, screen_height = int(w), int(h)
                            return True
    except Exception:
        pass
    
    print(f"无法检测屏幕分辨率，使用默认值: {screen_width}x{screen_height}")
    return False

def find_input_devices():
    """查找所有输入设备并返回合适的设备"""
    devices = [InputDevice(path) for path in list_devices()]
    mouse_devices = []
    touchscreen_devices = []
    
    for device in devices:
        capabilities = device.capabilities()
        
        # 检查是否为鼠标设备
        is_mouse = False
        if ecodes.EV_KEY in capabilities and ecodes.BTN_MOUSE in capabilities.get(ecodes.EV_KEY, []):
            mouse_devices.append(device)
            is_mouse = True
            print(f"找到鼠标设备: {device.name} (路径: {device.path})")
        
        # 检查是否为触摸屏设备
        is_touchscreen = False
        if ecodes.EV_KEY in capabilities and ecodes.BTN_TOUCH in capabilities.get(ecodes.EV_KEY, []):
            if not is_mouse:  # 避免将鼠标也算作触摸屏
                touchscreen_devices.append(device)
                is_touchscreen = True
                print(f"找到触摸屏设备: {device.name} (路径: {device.path})")
        
        # 打印设备具体支持的事件类型，帮助调试
        if is_mouse or is_touchscreen:
            print(f"  支持的事件类型:")
            if ecodes.EV_KEY in capabilities:
                print(f"    - 按键事件: {capabilities[ecodes.EV_KEY]}")
            if ecodes.EV_ABS in capabilities:
                print(f"    - 绝对坐标: {capabilities[ecodes.EV_ABS]}")
            if ecodes.EV_REL in capabilities:
                print(f"    - 相对坐标: {capabilities[ecodes.EV_REL]}")
    
    # 优先返回触摸屏，因为它提供绝对坐标
    if touchscreen_devices:
        print(f"选择触摸屏设备: {touchscreen_devices[0].name}")
        return touchscreen_devices[0], True
    elif mouse_devices:
        print(f"选择鼠标设备: {mouse_devices[0].name}")
        return mouse_devices[0], False
    else:
        return None, False

def calibrate_device(device, is_touchscreen):
    """获取设备的校准信息，用于转换原始坐标到屏幕坐标"""
    if not is_touchscreen:
        # 鼠标设备使用相对坐标，不需要校准直接使用屏幕分辨率
        return {
            "min_x": 0,
            "max_x": screen_width,
            "min_y": 0,
            "max_y": screen_height
        }
    
    # 触摸屏设备需要获取其坐标范围
    capabilities = device.capabilities()
    abs_info = {}
    
    if ecodes.EV_ABS in capabilities:
        for code, info in capabilities[ecodes.EV_ABS]:
            if code == ecodes.ABS_X:
                abs_info["min_x"] = info.min
                abs_info["max_x"] = info.max
            elif code == ecodes.ABS_Y:
                abs_info["min_y"] = info.min
                abs_info["max_y"] = info.max
    
    # 如果获取不到，使用默认值
    if not abs_info or len(abs_info) < 4:
        print("无法获取触摸屏坐标范围，使用默认值")
        abs_info = {
            "min_x": 0,
            "max_x": 32767,  # 触摸屏常用最大值
            "min_y": 0,
            "max_y": 32767
        }
    
    return abs_info

def map_coordinates(x, y, calib, is_touchscreen):
    """将设备坐标映射到屏幕坐标"""
    if is_touchscreen:
        # 触摸屏使用绝对坐标，需要映射
        screen_x = int((x - calib["min_x"]) / (calib["max_x"] - calib["min_x"]) * screen_width)
        screen_y = int((y - calib["min_y"]) / (calib["max_y"] - calib["min_y"]) * screen_height)
    else:
        # 对于鼠标，我们已经在累积相对移动，确保在屏幕范围内
        screen_x = max(0, min(x, screen_width))
        screen_y = max(0, min(y, screen_height))
    
    return screen_x, screen_y

def collect_events(device, timeout=0.1):
    """收集设备的所有事件，直到没有更多事件或超时"""
    events = []
    start_time = time.time()
    
    while True:
        r, w, x = select([device], [], [], timeout)
        if not r or time.time() - start_time > timeout:
            break
            
        try:
            for event in device.read():
                events.append(event)
        except Exception as e:
            print(f"读取事件出错: {e}")
            break
    
    return events

def process_events(events, is_touchscreen, calibration):
    """处理收集到的事件"""
    global is_touch, last_click_position, coord_buffer
    
    # 按时间顺序排序事件
    events.sort(key=lambda e: e.timestamp())
    
    # 先处理所有坐标事件
    x_updated = False
    y_updated = False
    touch_event = None
    
    for event in events:
        # 首先检查和处理所有坐标更新
        if event.type == ecodes.EV_ABS:
            if event.code == ecodes.ABS_X:
                coord_buffer["x"] = event.value
                x_updated = True
            elif event.code == ecodes.ABS_Y:
                coord_buffer["y"] = event.value
                y_updated = True
        elif event.type == ecodes.EV_REL:
            if event.code == ecodes.REL_X:
                coord_buffer["x"] += event.value
                x_updated = True
            elif event.code == ecodes.REL_Y:
                coord_buffer["y"] += event.value
                y_updated = True
        # 记录按键事件，稍后处理
        elif event.type == ecodes.EV_KEY and event.code in [ecodes.BTN_LEFT, ecodes.BTN_TOUCH]:
            touch_event = event
    
    # 如果有坐标更新，映射到屏幕坐标
    if x_updated or y_updated:
        screen_x, screen_y = map_coordinates(coord_buffer["x"], coord_buffer["y"], 
                                         calibration, is_touchscreen)
        print(f"坐标更新: ({screen_x}, {screen_y})")
    
    # 最后处理按键事件
    if touch_event:
        # 再次映射当前最新的坐标
        screen_x, screen_y = map_coordinates(coord_buffer["x"], coord_buffer["y"], 
                                         calibration, is_touchscreen)
        
        if touch_event.value == 1:  # 按下
            with lock:
                is_touch = True
                last_click_position["x"] = screen_x
                last_click_position["y"] = screen_y
            print(f"点击事件: ({screen_x}, {screen_y})")
        
        elif touch_event.value == 0:  # 释放
            with lock:
                is_touch = False
            print("释放事件")

def monitor_input_device(device, is_touchscreen, calibration):
    """监控输入设备事件的线程函数"""
    global coord_buffer
    
    # 当前坐标初始化为屏幕中心
    coord_buffer = {"x": screen_width // 2, "y": screen_height // 2}
    
    print(f"开始监控设备: {device.name}")
    print(f"屏幕分辨率: {screen_width}x{screen_height}")
    print(f"设备类型: {'触摸屏' if is_touchscreen else '鼠标'}")
    print(f"校准信息: {json.dumps(calibration, indent=2)}")
    
    # 强化调试信息
    print("开始非独占方式监控设备，系统鼠标功能将正常工作")
    print("改进的事件处理流程: 收集所有事件 -> 先处理坐标事件 -> 再处理点击事件")
    
    # 主事件循环
    while True:
        # 收集所有可用事件
        events = collect_events(device)
        if events:
            # 批量处理收集到的事件
            process_events(events, is_touchscreen, calibration)
        else:
            # 如果没有事件，短暂休眠以减少CPU使用
            time.sleep(0.01)

def get_click_status():
    """获取当前点击状态和位置"""
    with lock:
        status = {
            "is_touch": is_touch,
            "position": last_click_position.copy()
        }
    return status

def write_status_to_file(status_file, interval=0.1):
    """将点击状态写入文件，以便其他程序读取"""
    while True:
        status = get_click_status()
        with open(status_file, 'w') as f:
            json.dump(status, f)
        time.sleep(interval)

def ClickResult():
    # 获取屏幕分辨率
    get_screen_resolution()
    print(f"屏幕分辨率设置为: {screen_width}x{screen_height}")
    
    # 查找输入设备
    device, is_touchscreen = find_input_devices()
    if not device:
        print("未找到鼠标或触摸屏设备")
        sys.exit(1)
    
    # 获取设备校准信息
    calibration = calibrate_device(device, is_touchscreen)
    
    # 启动监控线程
    monitor_thread = threading.Thread(target=monitor_input_device, 
                                      args=(device, is_touchscreen, calibration))
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # 创建状态文件，以便其他程序读取
    status_file = "/home/autotest/autotestrobot/tools/CoordiTrans/mouse_click_status.json"
    status_thread = threading.Thread(target=write_status_to_file, args=(status_file,))
    status_thread.daemon = True
    status_thread.start()
    
    print(f"点击状态将被写入: {status_file}")
    print("按Ctrl+C终止程序")
    
    try:
        # 主循环 - 只用于保持程序运行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n程序终止")
        sys.exit(0)

if __name__ == "__main__":
    ClickResult()