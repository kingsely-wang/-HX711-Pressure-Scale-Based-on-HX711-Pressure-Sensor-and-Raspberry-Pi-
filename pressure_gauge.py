import RPi.GPIO as GPIO
import time
import datetime  # 新增：用于获取当前时间
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from luma.core.render import canvas
from PIL import ImageFont
from ftplib import FTP, error_perm
import os
from collections import deque



# ==========================================
# 第一部分：HX711 驱动类 (无外部库)
# ==========================================
class HX711:
    def __init__(self, dt_pin, sck_pin):
        self.DT_PIN = dt_pin
        self.SCK_PIN = sck_pin
        self.offset = 0
        self.scale_ratio = 1
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.DT_PIN, GPIO.IN)
        GPIO.setup(self.SCK_PIN, GPIO.OUT)
        GPIO.output(self.SCK_PIN, GPIO.LOW)

    def is_ready(self):
        return GPIO.input(self.DT_PIN) == GPIO.LOW

    def read_raw_data(self):
        while not self.is_ready():
            pass
        data_buffer = 0
        for _ in range(24):
            GPIO.output(self.SCK_PIN, GPIO.HIGH)
            data_buffer = data_buffer << 1
            if GPIO.input(self.DT_PIN):
                data_buffer = data_buffer + 1
            GPIO.output(self.SCK_PIN, GPIO.LOW)
        
        # 第25个脉冲，选择通道A 增益128
        GPIO.output(self.SCK_PIN, GPIO.HIGH)
        GPIO.output(self.SCK_PIN, GPIO.LOW)

        if data_buffer >= 0x800000:
            data_buffer = data_buffer - 0x1000000
        return data_buffer


    # ==========================================
    # 在 HX711 类中去抖动滤波函数
    # ==========================================
        # ==========================================
    # 在 HX711 类中添加此方法
    # ==========================================
    def get_weight_fast(self, times=5):
        """
        快速获取重量并去除离群值（中值滤波）。
        采样次数少（5次），反应极快，能有效过滤偶然的离谱波动。
        """
        values = []
        for _ in range(times):
            # 尽可能快地读取，不加额外的 sleep，依赖 HX711 本身的速率
            values.append(self.read_raw_data())
        
        # 排序
        values.sort()
        
        # 取中位数（去除最大和最小的干扰）
        median_val = values[times // 2]
        
        # 转换为重量
        weight = (median_val - self.offset) / self.scale_ratio
        return weight


    #  这是多次取均值的读数
    def read_average(self, times=10):
        total = 0
        for _ in range(times):
            total += self.read_raw_data()
            time.sleep(0.01)
        return int(total / times)


    def tare(self, times=15):
        raw_value = self.read_average(times)
        self.offset = raw_value
        return self.offset

    def set_scale(self, scale_ratio):
        self.scale_ratio = scale_ratio

    def calibrate(self, known_mass):
        raw_with_mass = self.read_average(times=20)
        if raw_with_mass == self.offset:
            return False
        self.scale_ratio = (raw_with_mass - self.offset) / known_mass
        return True

    def get_weight(self, times=10):
        val = self.read_average(times)
        weight = (val - self.offset) / self.scale_ratio
        return weight

# ==========================================
# 第二部分：OLED 显示辅助函数
# ==========================================
oled_device = None 

def init_oled():
    global oled_device
    try:
        serial = i2c(port=1, address=0x3C)
        oled_device = ssd1306(serial)
        print("OLED 屏幕检测成功。")
        return True
    except Exception as e:
        print(f"OLED 初始化失败 (不影响程序运行): {e}")
        return False

def show_on_oled(line1, line2=""):
    if oled_device is None:
        return

    try:
        with canvas(oled_device) as draw:
            # 加载系统字体，设置大小为14
            try:
                font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                font = ImageFont.truetype(font_path, 14)
            except:
                font = ImageFont.load_default()
            
            bbox1 = draw.textbbox((0, 0), line1, font=font)
            w1 = bbox1[2] - bbox1[0]
            h1 = bbox1[3] - bbox1[1]
            x1 = (oled_device.width - w1) // 2
            
            bbox2 = draw.textbbox((0, 0), line2, font=font)
            w2 = bbox2[2] - bbox2[0]
            x2 = (oled_device.width - w2) // 2
            y2 = h1 + 5
            
            draw.text((x1, 2), line1, font=font, fill="white")
            draw.text((x2, y2), line2, font=font, fill="white")
            
    except Exception:
        pass

# ==========================================
# 第三部分：上传本地数据到云端
# ==========================================

def upload_file_via_ftp(file_path, server_ip, username, password, port=21, remote_dir="/"):
    """
    使用 FTP 协议将本地文件上传到服务器
    """
    if not os.path.exists(file_path):
        print(f"错误：文件 {file_path} 不存在，无法上传。")
        return False

    print(f"正在连接 FTP 服务器 {server_ip} ...")
    ftp = None
    try:
        # 1. 建立连接
        ftp = FTP()
        ftp.connect(server_ip, port, timeout=10) # 10秒连接超时
        print("连接成功！")
        
        # 2. 登录
        ftp.login(username, password)
        print("登录成功！")
        
        # 3. 切换到二进制传输模式 (必须，否则文本文件可能乱码或损坏)
        ftp.voidcmd('TYPE I')
        
        # 3. 切换到远程目录 
        print(f"正在切换到远程目录: {remote_dir}")
        try:
            ftp.cwd(remote_dir)
        except error_perm as e:
            # 如果目录不存在或没有权限，这里会报错
            print(f"错误：无法切换到目录 '{remote_dir}'。")
            print(f"原因: {e}")
            print("请检查服务器路径是否正确，或者用户是否有权限访问该目录。")
            return False
        
        # 4. 切换到二进制传输模式
        ftp.voidcmd('TYPE I')
        
        # 5. 上传文件
        with open(file_path, 'rb') as f:
            file_size = os.path.getsize(file_path)
            print(f"开始上传: {os.path.basename(file_path)} ({file_size} bytes)...")
            
            # storbinary 会将文件上传到当前 ftp.cwd() 指定的目录下
            ftp.storbinary(f'STOR {os.path.basename(file_path)}', f, 1024)
            
        print("上传成功！")
        return True

    except Exception as e:
        print(f"上传过程中发生错误: {e}")
        return False
    finally:
        if ftp:
            try:
                ftp.quit()
            except:
                ftp.close()

# ==========================================
# 第四部分：主程序 
# ==========================================

def main():
    # --- 引脚配置 ---
    DT_PIN = 5
    SCK_PIN = 6

    scale = HX711(DT_PIN, SCK_PIN)
    has_oled = init_oled()
    
    if has_oled:
        show_on_oled("Scale System", "Initializing...")
        time.sleep(1)
    
    # --- FTP 服务器配置  ---
    FTP_CONFIG = {
        "server": "100.1.1.1",  # 服务器IP
        "user": "Lab111",            # FTP用户名
        "pwd": "12345678",            # FTP密码
        "port": 21,                 # 通常是21
        "remote_dir":"/Weight",     # 远程文件路径
    }
    
    print("========================================")
    print("操作指南：")
    print("1. 确保传感器已连接，且未放置重物。")
    print("2. 首次使用必须先执行 't' (去皮)。")
    print("3. 去皮后，执行 'c' (校准)，输入已知重量。")
    print("4. 校准后，输入 'w' 开始称重并记录数据。")
    print("5. 输入 'u' 上传数据到 FTP 服务器。") 
    print("6. 输入 'q' 退出。")
    print("========================================")

    try:
        while True:
            if has_oled:
                show_on_oled("Ready", "Waiting Cmd")

            user_input = input("\n请输入命令 > ").strip().lower()

            if user_input == 't':
                print("正在去皮 (归零中)...")
                if has_oled:
                    show_on_oled("Status:", "Taring...")
                scale.tare()
                print(f"去皮完成。当前零点原始值: {scale.offset}")
                if has_oled:
                    show_on_oled("Tare Done", "Offset Set")
                    time.sleep(1.5)

            elif user_input == 'c':
                try:
                    w = float(input("请输入已知物体的重量 (例如: 100.0): "))
                except ValueError:
                    print("输入无效，请输入数字。")
                    continue

                print(f"请将 {w}g 物体放在秤上，然后按回车...")
                if has_oled:
                    show_on_oled("Put Object", f"Weight: {w}g")
                input() 

                if has_oled:
                    show_on_oled("Status:", "Calibrating...")
                
                if scale.calibrate(w):
                    print(f"校准成功！比例系数: {scale.scale_ratio:.4f}")
                    if has_oled:
                        show_on_oled("Calib Success!", f"Ratio: {scale.scale_ratio:.2f}")
                else:
                    print("错误：检测不到重量变化，无法校准。")
                    if has_oled:
                        show_on_oled("Error", "No Change")
                time.sleep(2)
                                   
            elif user_input == 'w':
                if scale.scale_ratio == 1:
                    print("警告：尚未校准，使用默认比例系数！")
                
                print("开始称重... (放置物体后请等待数值稳定显示)")
                print("按 Ctrl+C 停止。")
                
                filename = "record_weight.txt"
                
                # --- 配置参数 ---
                STABLE_THRESHOLD = 0.5  # 稳定阈值：波动小于 0.5g 视为稳定
                CHECK_WINDOW = 5        # 检查窗口：连续 5 次采样都在阈值内才算稳定
                
                try:
                    with open(filename, "a", encoding="utf-8") as f:
                        print(f"正在将数据保存到: {filename}")
                        
                        # 用于记录最近几次的重量，判断是否稳定
                        recent_weights = []
                        last_displayed_weight = 0.0
                        display_mode = "stabilizing" # 状态：stabilizing(稳定中) 或 stable(已稳定)
                        
                        while True:
                            # 1. 使用快速中值滤波获取当前重量（无延迟）
                            current_weight = scale.get_weight_fast(times=5)
                            recent_weights.append(current_weight)
                            
                            # 保持队列长度
                            if len(recent_weights) > CHECK_WINDOW:
                                recent_weights.pop(0)
                            
                            # 2. 判断是否稳定
                            is_stable = False
                            if len(recent_weights) == CHECK_WINDOW:
                                # 计算最近5次读数的最大差值
                                max_w = max(recent_weights)
                                min_w = min(recent_weights)
                                if (max_w - min_w) < STABLE_THRESHOLD:
                                    is_stable = True
                            
                            # 3. 显示与记录逻辑
                            now_str = datetime.datetime.now().strftime("%H:%M:%S")
                            
                            if is_stable:
                                # --- 状态：已稳定 ---
                                # 计算这几次的平均值作为最终显示值，更加平滑
                                stable_weight = sum(recent_weights) / len(recent_weights)
                                # 只有当稳定值比上次显示值变化超过 0.1g 时才更新屏幕（防止无意义的微跳）
                                if abs(stable_weight - last_displayed_weight) > 0.2:
                                    last_displayed_weight = stable_weight
                                    
                                    # 更新 OLED 和 终端
                                    print(f"\r[{now_str}] 稳定重量: {stable_weight:.2f} g", end="")
                                    if has_oled:
                                        show_on_oled("Weight:", f"{stable_weight:.2f} g")
                                        
                                    # 写入文件（只记录稳定后的数据）
                                    log_line = f"时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}   重量：{stable_weight:.2f} g (稳定)\n"
                                    f.write(log_line)
                                    f.flush()
                            else:
                                # --- 状态：正在波动/稳定中 ---
                                # 这里我们选择：不更新屏幕显示（保持上一个稳定值），或者显示 "..."
                                # 这样您就不会看到中间的爬升过程，实现了“不要中间过渡”
                                if has_oled:
                                    # 可选：在 OLED 上显示正在计算中
                                    show_on_oled("Measuring...", "Wait...")
                                else:
                                    # 终端仅显示光标闪烁，不刷屏数字
                                    pass 
                                    
                            time.sleep(0.05) # 控制循环速度，给 CPU 休息，约每秒20次检测
                            
                except KeyboardInterrupt:
                    print("\n称重停止，文件已自动关闭。")
                    continue

           
            # --- 上传功能 ---
            elif user_input == 'u':
                filename = "2531907.txt"
                
                # 检查文件是否有内容
                if not os.path.exists(filename):
                    print("本地暂无记录文件，请先进行称重记录。")
                    continue
                
                print(f"准备上传 {filename} ...")
                if has_oled:
                    show_on_oled("Uploading...", "Please Wait")
                
                # 调用上传函数
                success = upload_file_via_ftp(
                    file_path=filename,
                    server_ip=FTP_CONFIG["server"],
                    username=FTP_CONFIG["user"],
                    password=FTP_CONFIG["pwd"],
                    port=FTP_CONFIG["port"],
                    remote_dir=FTP_CONFIG["remote_dir"],  # <--- 传入配置的路径
                )
                
                if success:
                    if has_oled:
                        show_on_oled("Upload", "Success!")
                else:
                    if has_oled:
                        show_on_oled("Upload", "Failed!")
                
                time.sleep(2)

            elif user_input == 'q':
                break
            
            elif user_input == 'r':
                print(f"当前原始数据: {scale.read_average()}")

            else:
                print("未知命令。")

    finally:
        GPIO.cleanup()
        if has_oled:
            show_on_oled("System", "Stopped")
        print("程序退出，GPIO已清理。")

if __name__ == "__main__":
    main()
