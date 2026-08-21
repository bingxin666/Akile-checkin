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


class CheckinError(Exception):
    """签到流程中无法自动恢复的错误。"""


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

    def _fail(self, msg, screenshot=None):
        """统一失败处理：截图、打印、推送并抛出异常。"""
        if screenshot:
            try:
                self.browser.save_screenshot(screenshot)
            except Exception:
                pass
        print(msg)
        Notice.serverJ(self.push_key, "Akile签到", msg)
        raise CheckinError(msg)

    def _page_text(self):
        try:
            return (
                self.browser.execute_script(
                    "return document.body ? document.body.innerText : '';"
                )
                or ""
            )
        except Exception:
            return ""

    def _detect_login_blockers(self):
        """检测登录后无法自动处理的安全拦截。"""
        text = self._page_text()
        url = (self.browser.current_url or "").lower()

        # 强制改密（需要邮箱验证码，自动化无法完成）
        if (
            "验证邮箱并修改密码" in text
            or "本次登录需要修改密码" in text
            or ("验证码已发送至" in text and "新密码" in text)
            or "修改密码并登录" in text
        ):
            return (
                "forced_password_change",
                "登录被拦截：平台要求强制修改密码。"
                "请先在官网手动完成邮箱验证码改密，"
                "并同步更新 config.ini 或 GitHub Secrets 中的 AKILE_PASSWORD。",
            )

        # 验证器 TOTP（若已配置 TOTP 密钥则可自动填入，由调用方处理）
        if (
            "使用验证器应用验证身份" in text
            or "验证器应用生成的6位验证码" in text
        ):
            return (
                "totp_required",
                "登录被拦截：需要验证器应用二次验证。"
                "若已开启验证器，请在 config.ini 的 totp 或环境变量 AKILE_TOTP "
                "中配置密钥后重试。",
            )

        # 必须完成 Passkey 验证才能继续
        if "需要 Passkey 验证" in text and "立即验证" in text:
            return (
                "passkey_required",
                "登录被拦截：需要 Passkey 验证。"
                "自动签到无法完成 Passkey，请在官网取消强制 Passkey 后再试。",
            )

        # 常见密码错误提示
        if any(
            tip in text
            for tip in (
                "密码错误",
                "账号或密码错误",
                "邮箱或密码错误",
                "用户名或密码错误",
            )
        ):
            return (
                "bad_credentials",
                "登录失败：账号或密码错误，请检查 AKILE_EMAIL / AKILE_PASSWORD。",
            )

        # 仍在登录页且没有明显可跳过提示
        if "/login" in url and "控制台" not in text:
            # 若页面只有登录表单，可能是失败但无明确文案
            if "登录您的帐户" in text or "请输入邮箱" in text:
                return (
                    "still_on_login",
                    "登录失败：提交后仍停留在登录页。"
                    "可能是密码错误、触发安全校验或网络异常。",
                )

        return None, None

    def _dismiss_skippable_dialogs(self):
        """关闭可跳过的弹窗（Passkey 提示、公告等），但保留 TOTP 验证弹窗"""
        # 优先点「下次一定」，对应可选的 Passkey 绑定提示
        try:
            later_buttons = self.browser.find_elements(
                By.XPATH, '//button[contains(., "下次一定")]'
            )
            for btn in later_buttons:
                try:
                    if btn.is_displayed():
                        self.browser.execute_script("arguments[0].click();", btn)
                        time.sleep(0.4)
                except Exception:
                    continue
        except Exception:
            pass

        # 点关闭按钮（排除 TOTP 验证弹窗内的关闭按钮）
        try:
            close_buttons = self.browser.find_elements(
                By.CSS_SELECTOR, ".arco-modal-close-btn, .arco-modal-close"
            )
            for btn in close_buttons:
                try:
                    if not btn.is_displayed():
                        continue
                    modal = btn.find_element(
                        By.XPATH, "ancestor::*[contains(@class, 'arco-modal')]"
                    )
                    if "验证器" in modal.text or "验证码" in modal.text:
                        continue
                except Exception:
                    pass
                self.browser.execute_script("arguments[0].click();", btn)
                time.sleep(0.3)
        except Exception:
            pass

        # 兜底移除残留遮罩，避免挡住点击（保留 TOTP 验证弹窗）
        try:
            self.browser.execute_script(
                """
                document.querySelectorAll(
                    '.arco-modal-wrapper, .arco-modal-mask, .arco-modal, .arco-modal-container'
                ).forEach(function (m) {
                    try {
                        if (m.innerText && (m.innerText.includes('验证器') || m.innerText.includes('验证码'))) {
                            return;
                        }
                        if (m && m.parentNode) {
                            m.parentNode.removeChild(m);
                        }
                    } catch (e) {}
                });
                document.body.style.overflow = '';
                """
            )
        except Exception:
            pass

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

        # 登录前清理无关遮挡
        self._dismiss_skippable_dialogs()

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
            email_input.clear()
            email_input.send_keys(self.email)
            password_input = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        'input[name="password"], input[placeholder*="密码"]',
                    )
                )
            )
            password_input.clear()
            password_input.send_keys(self.password)
        except TimeoutException as e:
            self._fail(
                f"邮箱或密码输入框没有加载出来: {e}\n签到失败",
                screenshot="login_form.png",
            )

        try:
            submit_button = WebDriverWait(self.browser, 10).until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        'form button[type="submit"], form .arco-btn-primary',
                    )
                )
            )
            submit_button.click()
        except TimeoutException as e:
            self._fail(
                f"登录按钮没有加载出来: {e}\n签到失败",
                screenshot="login_button.png",
            )

        # 等待登录结果：离开登录页，或出现安全拦截弹窗
        deadline = time.time() + 20
        while time.time() < deadline:
            blocker, blocker_msg = self._detect_login_blockers()
            if blocker == "totp_required" and self.totp:
                # 已配置 TOTP 密钥，尝试自动填入后继续等待
                if self._fill_totp():
                    time.sleep(0.5)
                    continue
                self._fail(
                    "TOTP 验证失败，请检查 AKILE_TOTP 配置\n签到失败",
                    screenshot="login_totp.png",
                )
            if blocker and blocker != "still_on_login":
                self._fail(blocker_msg + "\n签到失败", screenshot="login_blocked.png")

            url = (self.browser.current_url or "").lower()
            text = self._page_text()
            if "/login" not in url or "控制台" in text or "AVIP" in text:
                break
            time.sleep(0.5)
        else:
            blocker, blocker_msg = self._detect_login_blockers()
            if blocker:
                self._fail(
                    (blocker_msg or "登录失败") + "\n签到失败",
                    screenshot="login_timeout.png",
                )
            self._fail(
                "登录超时：未能确认登录成功\n签到失败",
                screenshot="login_timeout.png",
            )

        # 登录成功后可能弹出可选 Passkey 绑定提示
        self._dismiss_skippable_dialogs()

        # 复核是否真正进入控制台（未登录访问控制台会被重定向到首页）
        if not self._is_logged_in():
            self._fail(
                "登录后仍未进入控制台, 请检查账号密码或 TOTP 配置\n签到失败",
                screenshot="login_verify.png",
            )

        print("登录成功")
        return True

    def _get_ak_coins(self):
        """获取当前AK币数量"""
        try:
            element = self.browser.find_element(By.CSS_SELECTOR, ".coin-balance-value")
            text = element.text.strip()
            return int(re.search(r"(\d+)", text).group(1))
        except Exception:
            return -1

    def _is_logged_out(self):
        url = (self.browser.current_url or "").lower()
        text = self._page_text()
        # 未登录访问控制台会被重定向到首页或登录页
        return "/console" not in url or "/login" in url or "您还没有登录" in text

    # 签到主逻辑
    def check_in(self):
        checkin_page = "https://akile.ai/console/ak-coin-shop"
        self.browser.get(checkin_page)
        time.sleep(5)

        # 若被踢回登录页，说明会话无效
        if self._is_logged_out():
            # 再检查是否其实是强制改密等拦截
            blocker, blocker_msg = self._detect_login_blockers()
            if blocker and blocker != "still_on_login":
                self._fail(blocker_msg + "\n签到失败", screenshot="checkin_blocked.png")
            self._fail(
                "访问签到页失败：未登录或登录态失效。"
                "若近期平台要求强制改密，请先手动改密并更新密码配置。\n签到失败",
                screenshot="checkin_login_required.png",
            )

        # 关闭公告 / Passkey 等可跳过弹窗
        self._dismiss_skippable_dialogs()

        prev_points_num = self._get_ak_coins()
        print(f"当前AK币: {prev_points_num}")

        # 已签到（页面文案可能是「今日已签到」或「已签到」）
        try:
            done_button = self.browser.find_element(
                By.XPATH, '//button[contains(., "已签到")]'
            )
            if done_button.is_displayed():
                msg = f"今日已签到, 现在有{prev_points_num}AK币"
                print(msg)
                Notice.serverJ(self.push_key, "Akile签到", msg)
                return True
        except Exception:
            pass

        # 尝试签到
        try:
            checkin_button = WebDriverWait(self.browser, 15).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//button[contains(., "每日签到")]')
                )
            )
            print("找到签到按钮，正在点击...")
            self.browser.execute_script("arguments[0].click();", checkin_button)
            time.sleep(3)

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
            print("未找到签到按钮，再次检查是否已签到...")
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
                self._fail(
                    "签到按钮和已签到按钮都无法加载出来。"
                    "可能是网络波动、页面改版，或登录后被安全弹窗拦截。"
                    "请稍后重试；若出现强制改密提示，请先手动改密。\n签到失败",
                    screenshot="debug.png",
                )

    def __del__(self):
        if self.browser:
            try:
                self.browser.quit()
            except Exception:
                pass


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
    """执行一次签到流程，返回是否成功"""
    akile = AkileCheckin()
    try:
        if not akile.login():
            return False
        return akile.check_in()
    except CheckinError:
        return False
    finally:
        if akile.browser:
            try:
                akile.browser.quit()
            except Exception:
                pass


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

        try:
            _run_checkin_once()
        except Exception as e:
            # 定时模式下单次失败不应终止守护进程
            print(f"本次签到出现异常: {e}")
