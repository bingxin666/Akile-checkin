import configparser
import os
import re
import shutil
import subprocess
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

        options = uc.ChromeOptions()
        options.add_argument("--lang=zh-CN")
        options.add_experimental_option("prefs", {"intl.accept_languages": "zh-CN,zh"})
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        )

        # 在 CI 中显式指定 Chrome 二进制与主版本，避免多版本并存导致的版本错配
        chrome_path, chrome_major = self._get_chrome_info()
        if chrome_path:
            options.binary_location = chrome_path
            print(f"Using Chrome binary: {chrome_path} (major={chrome_major})")

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

    def login(self):
        # 直接访问登录页面
        self.browser.get("https://akile.ai/login")
        self.browser.maximize_window()
        time.sleep(2)

        # 键入邮箱和密码
        try:
            email_input = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'input[placeholder*="邮箱"]')
                )
            )
            email_input.send_keys(self.email)
            password_input = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'input[placeholder*="密码"]')
                )
            )
            password_input.send_keys(self.password)
        except TimeoutException as e:
            self.browser.save_screenshot("邮箱.png")
            print(f"邮箱或密码输入框没有加载出来: {e}")
            msg = f"邮箱或密码输入框没有加载出来: {e}\n签到失败"
            Notice.serverJ(self.push_key, "Akile签到", msg)
            sys.exit(1)

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
            sys.exit(1)

        # 处理二次验证（TOTP）
        self._fill_totp()

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
        checkin_page = "https://akile.ai/console/ak-coin-shop"
        self.browser.get(checkin_page)
        time.sleep(5)  # 增加等待时间

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
            sys.exit(0)

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
                sys.exit(0)
            except Exception as e:
                print(f"查找已签到按钮失败: {e}")
                # 保存截图用于调试
                self.browser.save_screenshot("debug.png")
                msg = "签到按钮和已签到按钮都无法加载出来, 可能是网络原因, 可以等待一会再执行脚本"
                print(msg)
                Notice.serverJ(self.push_key, "Akile签到", msg)
                sys.exit(1)

    def __del__(self):
        if self.browser:
            self.browser.quit()


if __name__ == "__main__":
    akile = AkileCheckin()
    try:
        akile.login()
        time.sleep(3)  # 防止执行太快导致需要二次登录
        akile.check_in()
    finally:
        if akile.browser:
            akile.browser.quit()
