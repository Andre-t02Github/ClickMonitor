import threading
import time
import os
import re
import queue
import struct
import fcntl

# Global variables
is_touch = False
is_touch_lock = threading.Lock()
click_queue = queue.Queue()

class MouseMonitor:
    def __init__(self):
        self.running = True
        self.device_path = None
        self.prev_state = None  # 用于跟踪前一个状态

    def find_mouse_device(self):
        """尝试找到鼠标输入设备的路径"""
        try:
            # 先尝试直接使用/dev/input/mice（适用于大多数系统）
            if os.path.exists('/dev/input/mice') and os.access('/dev/input/mice', os.R_OK):
                print("使用/dev/input/mice作为鼠标设备")
                return '/dev/input/mice'
            
            # 如果mice不可用，尝试查找特定的鼠标设备
            with open('/proc/bus/input/devices', 'r') as f:
                content = f.read()
            
            # 查找可能的鼠标设备
            mouse_patterns = ['mouse', 'Mouse']
            devices = content.split('\n\n')
            
            for device in devices:
                if any(pattern in device for pattern in mouse_patterns):
                    event_match = re.search(r'event(\d+)', device)
                    if event_match:
                        event_num = event_match.group(1)
                        device_path = f'/dev/input/event{event_num}'
                        if os.path.exists(device_path) and os.access(device_path, os.R_OK):
                            print(f"找到鼠标设备: {device_path}")
                            return device_path
            
            print("未找到可读取的鼠标设备")
            return None
        except Exception as e:
            print(f"查找鼠标设备时出错: {e}")
            return None

    def monitor_clicks_mice(self):
        """使用/dev/input/mice监控鼠标点击"""
        try:
            print(f"开始监控鼠标设备: {self.device_path}")
            with open(self.device_path, 'rb') as device:
                # 设置非阻塞模式
                fd = device.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                
                # 跟踪鼠标状态
                prev_buttons = 0
                abs_x, abs_y = 500, 500  # 初始坐标
                
                while self.running:
                    try:
                        # 非阻塞读取，如果没有数据会抛出异常
                        data = device.read(3)
                        if not data or len(data) < 3:
                            time.sleep(0.01)  # 短暂休眠避免CPU占用过高
                            continue
                        
                        buttons = data[0]
                        dx = data[1]
                        dy = data[2]
                        
                        # 处理鼠标移动
                        if dx > 127:
                            dx -= 256
                        if dy > 127:
                            dy -= 256
                        
                        # 累积坐标
                        abs_x += dx
                        abs_y += dy
                        
                        # 限制坐标范围（模拟屏幕边界）
                        abs_x = max(0, min(abs_x, 1920))
                        abs_y = max(0, min(abs_y, 1080))
                        
                        # 检测左键点击（从未按下到按下的变化）
                        left_button = buttons & 0x1
                        left_button_was_pressed = prev_buttons & 0x1
                        
                        if left_button and not left_button_was_pressed:
                            # 检测到新的点击
                            with is_touch_lock:
                                global is_touch
                                is_touch = True
                            
                            # 将点击坐标放入队列
                            click_queue.put((abs_x, abs_y))
                            print(f"检测到点击，位置: ({abs_x}, {abs_y})，设置is_touch=True")
                        
                        # 更新按钮状态
                        prev_buttons = buttons
                        
                    except IOError:
                        # 非阻塞模式下没有数据可读
                        time.sleep(0.01)
                    except Exception as e:
                        print(f"读取鼠标事件数据错误: {e}")
                        time.sleep(0.1)
                        
        except Exception as e:
            print(f"监控鼠标时出错: {e}")

    def monitor_clicks_event(self):
        """使用/dev/input/eventX监控鼠标点击（适用于event设备）"""
        try:
            print(f"开始监控鼠标设备: {self.device_path}")
            with open(self.device_path, 'rb') as device:
                # 设置非阻塞模式
                fd = device.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                
                abs_x, abs_y = 500, 500  # 初始坐标
                btn_left_pressed = False
                
                # 尝试不同的事件格式
                event_formats = [
                    {"size": 16, "format": "llHHI", "name": "16字节格式 (32位系统常见)"},
                    {"size": 24, "format": "llHHq", "name": "24字节格式 (64位系统常见)"},
                    {"size": 24, "format": "2IHHq", "name": "24字节格式 (备选格式1)"},
                    {"size": 24, "format": "QQHHq", "name": "24字节格式 (备选格式2)"}
                ]
                
                # 尝试确定正确的格式
                event_format = None
                for fmt in event_formats:
                    try:
                        # 尝试读一次数据看能否成功解析
                        for _ in range(10):  # 多尝试几次以确保有数据可读
                            try:
                                event_data = device.read(fmt["size"])
                                if event_data and len(event_data) == fmt["size"]:
                                    struct.unpack(fmt["format"], event_data)
                                    event_format = fmt
                                    print(f"成功识别事件格式: {fmt['name']}")
                                    break
                            except struct.error:
                                continue
                            except IOError:  # 无数据可读
                                time.sleep(0.1)
                        
                        if event_format:
                            break
                    except Exception:
                        continue
                
                if not event_format:
                    print("无法确定事件格式，使用默认16字节格式")
                    event_format = event_formats[0]
                
                while self.running:
                    try:
                        # 非阻塞读取事件数据
                        event = device.read(event_format["size"])
                        if not event or len(event) != event_format["size"]:
                            time.sleep(0.01)  # 短暂休眠避免CPU占用过高
                            continue
                        
                        # 尝试解析事件
                        try:
                            values = struct.unpack(event_format["format"], event)
                            # 根据不同格式提取type、code和value
                            if len(values) >= 5:
                                type_ = values[2] if event_format["format"] in ["2IHHq", "QQHHq"] else values[3]
                                code = values[3] if event_format["format"] in ["2IHHq", "QQHHq"] else values[4]
                                value = values[4] if event_format["format"] in ["2IHHq", "QQHHq"] else values[5]
                            else:
                                continue
                            
                            # 处理按键事件 (EV_KEY=1)
                            if type_ == 1:
                                # BTN_LEFT 通常是272或256，我们检查两者
                                if code in [272, 256]:
                                    if value == 1 and not btn_left_pressed:
                                        btn_left_pressed = True
                                        with is_touch_lock:
                                            global is_touch
                                            is_touch = True
                                        
                                        click_queue.put((abs_x, abs_y))
                                        print(f"检测到点击，位置: ({abs_x}, {abs_y})，设置is_touch=True")
                                    elif value == 0:
                                        btn_left_pressed = False
                            
                            # 处理相对移动事件 (EV_REL=2)
                            elif type_ == 2:
                                if code == 0:  # X轴
                                    abs_x += value
                                    abs_x = max(0, min(abs_x, 1920))
                                elif code == 1:  # Y轴
                                    abs_y += value
                                    abs_y = max(0, min(abs_y, 1080))
                        
                        except struct.error as e:
                            print(f"解析事件格式错误: {e}")
                            time.sleep(0.1)
                    
                    except IOError:
                        # 非阻塞模式下没有数据可读
                        time.sleep(0.01)
                    except Exception as e:
                        print(f"读取鼠标事件数据错误: {e}")
                        time.sleep(0.1)
                        
        except Exception as e:
            print(f"监控鼠标时出错: {e}")

    def monitor_clicks(self):
        """根据设备类型选择合适的监控方法"""
        self.device_path = self.find_mouse_device()
        
        if not self.device_path:
            print("无法找到鼠标设备，程序终止")
            return
        
        try:
            # 设置非阻塞模式，避免读取时卡住
            with open(self.device_path, 'rb') as device:
                fd = device.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except Exception as e:
            print(f"设置非阻塞模式失败: {e}")
            
        # 根据设备路径选择合适的监控方法
        if 'mice' in self.device_path:
            self.monitor_clicks_mice()
        else:  # event设备
            self.monitor_clicks_event()
    
    def stop(self):
        """停止监控"""
        self.running = False

# 处理点击事件的线程
def click_processor_thread():
    global is_touch
    while True:
        try:
            # 从队列获取最新的点击位置
            x, y = click_queue.get(timeout=1)
            print(f"处理点击事件，实际坐标: ({x}, {y})")
            
            # 处理完成后，重置is_touch状态
            time.sleep(0.5)  # 给予一些处理时间
            with is_touch_lock:
                is_touch = False
            print("处理完成，重置is_touch=False")
            
        except queue.Empty:
            # 队列为空，继续等待
            continue

# 检查点击状态的线程示例
def check_touch_status():
    while True:
        with is_touch_lock:
            status = is_touch
        #DEBUG
        # print(f"当前触摸状态: {'活跃' if status else '无触摸'}")
        time.sleep(2)  # 每2秒检查一次状态

# 主执行
if __name__ == "__main__":
    # 创建并启动鼠标监控器
    monitor = MouseMonitor()
    monitor_thread = threading.Thread(target=monitor.monitor_clicks)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # 启动点击处理线程
    processor_thread = threading.Thread(target=click_processor_thread)
    processor_thread.daemon = True
    processor_thread.start()
    
    # 启动状态检查线程（可选，用于演示）
    status_thread = threading.Thread(target=check_touch_status)
    status_thread.daemon = True
    status_thread.start()
    
    try:
        print("鼠标监控系统已启动，按Ctrl+C退出...")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("正在关闭程序...")
        monitor.stop()