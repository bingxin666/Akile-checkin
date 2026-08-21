import configparser
import os
import re
import shutil
import subprocess
import datetime
import random
import sys
import time

import pyotp
import undetected_chromedriver as uc
from notice import Notice
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class AkileCheckin:
    def __init__(self):
        self.browser = None

        # 优先读取环境变量（便于在 GitHub Actions 中直接运行）
        self.email = os.getenv("AKILE_EMAIL", "").strip()
        self.password = os.getenv("AKILE_PASSWORD", "").strip()
        self.totp = os.getenv("AKILE_TOTP", "").strip()
        self.push_key = os.getenv("AKILE_PUSH_KEY", "").strip()
        self.session_dir = os.getenv("AKILE_SESSION_DIR", "").strip()

        # 若环境变量未配置则回退到配置文件
        if not self.email or not self.password:
            config = configparser.ConfigParser()
            config.read("config.ini", encoding="utf-8")
            self.email = self.email or config.get("akile", "email")
            self.password = self.password or config.get("akile", "password")
            self.totp = self.totp or config.get("akile", "totp", fallback="")
            self.push_key = self.push_key or config.get(
                "akile", "push_key", fallback=""
            )
            self.session_dir = self.session_dir or config.get(
                "akile", "session_dir", fallback=""
            )

        # 默认使用本地 chrome_session 目录保存登录状态
        if not self.session_dir:
            self.session_dir = os.path.abspath("chrome_session")

        options = uc.ChromeOptions()
        options.add_argument("--lang=zh-CN")
        options.add_experimental_option("prefs", {"intl.accept_languages": "zh-CN,zh"})
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(f"--user-data-dir={self.session_dir}")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        )

        # 在 CI 中显式指定 Chrome 二进制与主版本，避免多版本并存导致的版本错配
        chrome_path, chrome_major = self._get_chrome_info()
        if chrome_path:
            options.binary_location = chrome_path
            print(f"Using Chrome binary: {chrome_path} (major={chrome_major})")

        print(f"Using session directory: {self.session_dir}")

        if chrome_major:
            self.browser = uc.Chrome(options=options, version_main=chrome_major)
        else:
            self.browser = uc.Chrome(options=options)

    @staticmethod
    def _get_chrome_info():
        candidates = [
            "google-chrome",
            "google-chrome-stable",
            "chromium-browser",
            "chromium",
        ]

        for binary in candidates:
            binary_path = shutil.which(binary)
            if not binary_path:
                continue

            try:
                output = subprocess.check_output(
                    [binary_path, "--version"], stderr=subprocess.STDOUT, text=True
                ).strip()
                match = re.search(r"(\d+)\.", output)
                if match:
                    return binary_path, int(match.group(1))
            except Exception:
                continue

        return None, None

    def _dismiss_dialogs(self):
        """关闭所有可能的弹窗和遮挡层，但保留 TOTP 验证弹窗"""
        # 尝试点击关闭按钮（排除 TOTP 验证弹窗内的关闭按钮）
        try:
            close_btns = self.browser.find_elements(
                By.CSS_SELECTOR,
                '.arco-modal-close-btn, .arco-modal-close, [class*="close"]',
            )
            for close_btn in close_btns:
                try:
                    modal = close_btn.find_element(By.XPATH, "ancestor::*[contains(@class, 'arco-modal')]")
                    if "验证器" in modal.text or "验证码" in modal.text:
                        continue
                except Exception:
                    pass
                self.browser.execute_script("arguments[0].click();", close_btn)
                time.sleep(0.5)
                break
        except Exception:
            pass

        # 强制移除所有可能的遮挡层，但保留 TOTP 验证弹窗
        self.browser.execute_script("""
            document.querySelectorAll(
                '.arco-modal-wrapper, .arco-modal-mask, .arco-modal, .arco-modal-container'
            ).forEach(m => {
                if (m.innerText && (m.innerText.includes('验证器') || m.innerText.includes('验证码'))) {
                    return;
                }
                m.remove();
            });
            document.body.style.overflow = '';
        """)

    def _fill_totp(self):
        """如果页面要求 TOTP 验证码，则自动生成并填入"""
        if not self.totp:
            return False

        # 等待 TOTP 验证码输入框出现
        try:
            WebDriverWait(self.browser, 8).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'input[name="ak-code-0"]')
                )
            )
        except TimeoutException:
            # 未出现 TOTP 输入框，视为无需验证
            return False

        totp = pyotp.TOTP(self.totp)
        code = totp.now()
        print(f"检测到 TOTP 验证，正在填入验证码: {code}")

        for i, digit in enumerate(code):
            try:
                digit_input = self.browser.find_element(
                    By.NAME, f"ak-code-{i}"
                )
                digit_input.clear()
                digit_input.send_keys(digit)
            except Exception as e:
                print(f"填入 TOTP 第 {i + 1} 位失败: {e}")
                return False

        # 等待“继续”按钮可用并点击
        try:
            continue_button = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//div[contains(@class, "verification-code")]//button[contains(., "继续")]')
                )
            )
            continue_button.click()
        except TimeoutException as e:
            print(f"TOTP 继续按钮未启用或不可点击: {e}")
            return False

        return True

    def _is_logged_in(self):
        """通过访问控制台页面判断当前是否已登录"""
        self.browser.get("https://akile.ai/console/ak-coin-shop")
        time.sleep(3)
        current_url = self.browser.current_url
        print(f"检测登录状态，当前 URL: {current_url}")
        # 新版页面未登录访问控制台会被重定向到首页或登录页
        if "/console" not in current_url:
            return False
        # 页面中存在 AK 币余额元素说明已登录
        try:
            self.browser.find_element(By.CSS_SELECTOR, ".coin-balance-value")
            return True
        except Exception:
            pass
        # 兜底：若页面中存在邮箱输入框，说明停留在登录页
        try:
            self.browser.find_element(By.CSS_SELECTOR, 'input[name="email"]')
            return False
        except Exception:
            return True

    def login(self):
        # 先尝试直接访问控制台页面，若已登录则跳过登录流程
        if self._is_logged_in():
            print("检测到已有登录 session，跳过登录")
            return True

        # 需要重新登录
        print("未检测到登录 session，执行登录...")
        self.browser.get("https://akile.ai/login")
        self.browser.maximize_window()
        time.sleep(2)

        # 键入邮箱和密码
        try:
            email_input = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        'input[name="email"], input[placeholder*="邮箱"]',
                    )
                )
            )
            email_input.send_keys(self.email)
            password_input = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        'input[name="password"], input[placeholder*="密码"]',
                    )
                )
            )
            password_input.send_keys(self.password)
        except TimeoutException as e:
            self.browser.save_screenshot("邮箱.png")
            print(f"邮箱或密码输入框没有加载出来: {e}")
            msg = f"邮箱或密码输入框没有加载出来: {e}\n签到失败"
            Notice.serverJ(self.push_key, "Akile签到", msg)
            return False

        try:
            submit_button = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'form button[type="submit"], form .arco-btn-primary')
                )
            )
            submit_button.click()
        except TimeoutException as e:
            print(f"登录按钮没有加载出来: {e}")
            msg = f"登录按钮没有加载出来: {e}\n签到失败"
            Notice.serverJ(self.push_key, "Akile签到", msg)
            return False

        # 处理二次验证（TOTP）
        self._fill_totp()

        # 等待登录完成，确保 session 已写入
        time.sleep(3)
        # 登录后校验是否真正进入控制台
        if not self._is_logged_in():
            msg = "登录后仍未进入控制台, 请检查账号密码或 TOTP 配置\n签到失败"
            print(msg)
            Notice.serverJ(self.push_key, "Akile签到", msg)
            return False
        return True

    def _get_ak_coins(self):
        """获取当前AK币数量"""
        try:
            element = self.browser.find_element(By.CSS_SELECTOR, '.coin-balance-value')
            text = element.text.strip()
            return int(re.search(r'(\d+)', text).group(1))
        except Exception:
            return -1

    # 签到主逻辑
    def check_in(self):
        # 确保当前在签到页面
        if "/console/ak-coin-shop" not in self.browser.current_url:
            self.browser.get("https://akile.ai/console/ak-coin-shop")
            time.sleep(5)

        # 关闭可能出现的弹窗
        self._dismiss_dialogs()

        # 签到前的积分
        prev_points_num = self._get_ak_coins()
        print(f"当前AK币: {prev_points_num}")

        # 尝试签到 - 使用更简单的选择器
        try:
            # 等待页面加载完成
            checkin_button = WebDriverWait(self.browser, 15).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//button[contains(., "每日签到")]')
                )
            )
            print("找到签到按钮，正在点击...")
            self.browser.execute_script("arguments[0].click();", checkin_button)
            time.sleep(3)  # 防止点击签到动作未发出

            # 检查签到结果
            cur_points_num = self._get_ak_coins()

            if prev_points_num == -1:
                msg = f"签到成功, 当前有{cur_points_num}个AK币"
            else:
                gain = cur_points_num - prev_points_num if cur_points_num > 0 else 0
                msg = f"签到成功, 获得{gain}个AK币, 当前有{cur_points_num}个AK币"

            print(msg)
            Notice.serverJ(self.push_key, "Akile签到", msg)
            return True

        except TimeoutException:
            print("未找到签到按钮，检查是否已签到...")
            # 签到按钮没有加载出来，检查是否已经签到过
            try:
                self.browser.find_element(
                    By.XPATH, '//button[contains(., "已签到")]'
                )
                msg = f"今日已签到, 现在有{prev_points_num}AK币"
                print(msg)
                Notice.serverJ(self.push_key, "Akile签到", msg)
                return True
            except Exception as e:
                print(f"查找已签到按钮失败: {e}")
                # 保存截图用于调试
                self.browser.save_screenshot("debug.png")
                msg = "签到按钮和已签到按钮都无法加载出来, 可能是网络原因, 可以等待一会再执行脚本"
                print(msg)
                Notice.serverJ(self.push_key, "Akile签到", msg)
                return False

    def __del__(self):
        if self.browser:
            self.browser.quit()


def _get_next_checkin_datetime(checkin_time_str, random_delay_minutes):
    """计算下一次签到时间，并在目标时间基础上增加随机延迟"""
    hour, minute = map(int, checkin_time_str.split(":"))
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)

    if random_delay_minutes:
        delay_seconds = random.randint(-random_delay_minutes, random_delay_minutes) * 60
        target += datetime.timedelta(seconds=delay_seconds)

    if target <= now:
        target = now + datetime.timedelta(seconds=1)

    return target


def _run_checkin_once():
    """执行一次签到流程"""
    akile = AkileCheckin()
    try:
        if not akile.login():
            return False
        return akile.check_in()
    finally:
        if akile.browser:
            akile.browser.quit()


if __name__ == "__main__":
    scheduled = os.getenv("RUN_SCHEDULED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not scheduled:
        success = _run_checkin_once()
        sys.exit(0 if success else 1)

    checkin_time = os.getenv("AKILE_CHECKIN_TIME", "10:00").strip()
    random_delay_minutes = int(
        os.getenv("AKILE_RANDOM_DELAY_MINUTES", "5").strip()
    )

    print(
        f"已进入定时签到模式，每日 {checkin_time} 左右"
        f"（±{random_delay_minutes} 分钟）尝试签到"
    )

    while True:
        next_checkin = _get_next_checkin_datetime(checkin_time, random_delay_minutes)
        sleep_seconds = (next_checkin - datetime.datetime.now()).total_seconds()
        print(
            f"下次签到时间: {next_checkin.strftime('%Y-%m-%d %H:%M:%S')}，"
            f"等待 {int(sleep_seconds)} 秒"
        )
        time.sleep(max(sleep_seconds, 1))

        _run_checkin_once()
