#!/usr/bin/env python3
import os
import sys
import threading
import time
from select import select
from evdev import InputDevice, ecodes, list_devices

# 全局变量
is_touch = False
last_click_position = {"x": 0, "y": 0}
lock = threading.Lock()

def find_mouse_device():
    """查找鼠标输入设备"""
    devices = [InputDevice(path) for path in list_devices()]
    for device in devices:
        # 查找鼠标或触摸屏设备
        # 可以根据设备名称或功能进行筛选
        capabilities = device.capabilities()
        if ecodes.EV_KEY in capabilities and ecodes.BTN_MOUSE in capabilities[ecodes.EV_KEY]:
            print(f"找到鼠标设备: {device.name}")
            return device
        # 检查是否为触摸屏设备
        if ecodes.EV_KEY in capabilities and ecodes.BTN_TOUCH in capabilities[ecodes.EV_KEY]:
            print(f"找到触摸屏设备: {device.name}")
            return device
    
    return None

def monitor_mouse_clicks(device):
    """监控鼠标点击事件的线程函数"""
    global is_touch, last_click_position
    
    # 当前坐标
    current_x = 0
    current_y = 0
    
    print(f"开始监控设备: {device.name}")
    
    while True:
        r, w, x = select([device], [], [], 0.1)
        if r:
            for event in device.read():
                # 处理绝对坐标 (触摸屏通常使用绝对坐标)
                if event.type == ecodes.EV_ABS:
                    if event.code == ecodes.ABS_X:
                        current_x = event.value
                    elif event.code == ecodes.ABS_Y:
                        current_y = event.value
                
                # 处理相对坐标 (鼠标通常使用相对坐标)
                elif event.type == ecodes.EV_REL:
                    if event.code == ecodes.REL_X:
                        current_x += event.value
                    elif event.code == ecodes.REL_Y:
                        current_y += event.value
                
                # 处理按钮事件
                elif event.type == ecodes.EV_KEY:
                    # 鼠标左键按下
                    if (event.code == ecodes.BTN_LEFT or event.code == ecodes.BTN_TOUCH) and event.value == 1:
                        with lock:
                            is_touch = True
                            last_click_position["x"] = current_x
                            last_click_position["y"] = current_y
                            print(f"鼠标点击: ({current_x}, {current_y})")
                    
                    # 鼠标左键松开
                    elif (event.code == ecodes.BTN_LEFT or event.code == ecodes.BTN_TOUCH) and event.value == 0:
                        with lock:
                            is_touch = False
                            print("鼠标释放")

def get_click_status():
    """获取当前点击状态和位置"""
    with lock:
        status = {
            "is_touch": is_touch,
            "position": last_click_position.copy()
        }
    return status

def main():
    # 检查权限
    if os.geteuid() != 0:
        print("需要root权限来访问输入设备")
        print("请尝试使用 sudo 运行此脚本")
        sys.exit(1)
    
    # 查找鼠标设备
    mouse_device = find_mouse_device()
    if not mouse_device:
        print("未找到鼠标或触摸屏设备")
        sys.exit(1)
    
    # 启动监控线程
    monitor_thread = threading.Thread(target=monitor_mouse_clicks, args=(mouse_device,))
    monitor_thread.daemon = True
    monitor_thread.start()
    
    try:
        # 主循环 - 可以在这里添加你的主程序逻辑
        while True:
            # 例如，每秒钟获取并显示点击状态
            status = get_click_status()
            if status["is_touch"]:
                print(f"当前正在点击: ({status['position']['x']}, {status['position']['y']})")
            time.sleep(1)
    except KeyboardInterrupt:
        print("程序终止")
        sys.exit(0)

if __name__ == "__main__":
    main()