import asyncio
import logging
from playwright.async_api import async_playwright, expect, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

class UniversalAuthBot:
    def __init__(self, config_data):
        self.config = config_data
        self.playwright = None
        self.browser = None
        self.page = None

    async def setup(self, headless_mode=False):
        logging.info(f"🚀 Khởi tạo Bot cho dự án: {self.config.get('project_name')}")
        self.playwright = await async_playwright().start()
        # Thêm slow_mo=500 (delay 0.5s mỗi thao tác) để bạn dễ quan sát
        self.browser = await self.playwright.chromium.launch(headless=headless_mode, slow_mo=800)
        # Khởi tạo context mới cho mỗi phiên
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

    async def auto_fill_form(self, action_type, user_data):
        """Hàm dùng chung để điền form (áp dụng cho cả login và register)"""
        locators = self.config["locators"][action_type]
        
        # Tự động quét và điền dữ liệu dựa trên key của JSON
        for field_key, selector in locators.items():
            if field_key == "submit_btn":
                continue # Bỏ qua nút submit, ta sẽ click sau
                
            if field_key in user_data:
                logging.info(f"[*] Đang điền trường '{field_key}'...")
                await self.page.locator(selector).fill(user_data[field_key])

    async def login(self, user_data):
        logging.info("--- BẮT ĐẦU LUỒNG ĐĂNG NHẬP ---")
        try:
            # Xóa session cũ hoàn toàn
            await self.context.clear_cookies()
            await self.page.goto(self.config["urls"]["login"], wait_until="networkidle")

            # Điền form
            await self.auto_fill_form("login", user_data)
            
            # Click Login và chờ đợi sự thay đổi
            submit_btn_selector = self.config["locators"]["login"]["submit_btn"]
            await self.page.locator(submit_btn_selector).click()

            # --- THAY ĐỔI Ở ĐÂY ---
            # Thay vì chờ chữ "TienToan", ta chờ cho đến khi nút Login BIẾN MẤT 
            # Hoặc URL không còn chứa chữ "/login" nữa
            try:
                await expect(self.page.locator(submit_btn_selector)).to_be_hidden(timeout=10000)
                logging.info(f"[+] Đăng nhập THÀNH CÔNG cho: {user_data.get('username')}")
            except:
                logging.error(f"[-] Đăng nhập thất bại hoặc sai mật khẩu cho: {user_data.get('username')}")
            # ----------------------

        except Exception as e:
            logging.error(f"[-] Lỗi hệ thống khi đăng nhập: {e}")

    async def logout(self):
        logging.info("--- BẮT ĐẦU ĐĂNG XUẤT ---")
        try:
            cfg = self.config["locators"].get("logout")
            
            # 1. Di chuột vào khu vực Tài khoản (để menu xổ xuống)
            menu_trigger = self.page.locator(cfg["user_menu_btn"]).last
            await menu_trigger.hover() 
            await asyncio.sleep(1) # Đợi 1s cho menu hiện ra hẳn
            
            # 2. Click vào nút "Tài khoản" để chắc chắn menu được kích hoạt
            await menu_trigger.click(force=True)
            
            # 3. Tìm nút Đăng xuất và click
            logout_btn = self.page.locator(cfg["submit_btn"])
            # Đợi nó visible nhưng không đợi quá lâu
            await logout_btn.wait_for(state="visible", timeout=3000)
            await logout_btn.click(force=True)
            
            # 4. Xác nhận quay về trang login
            await self.page.wait_for_url("**/login", timeout=5000)
            logging.info("[+] Đăng xuất THÀNH CÔNG.\n")
            
        except Exception as e:
            logging.warning(f"[-] Logout lỗi, đang thực hiện xóa Session cứng...")
            # CHIÊU CUỐI: Xóa sạch Cookie và nhảy về trang login
            await self.context.clear_cookies()
            await self.page.goto(self.config["urls"]["login"])
            await self.page.wait_for_load_state("networkidle")

    async def teardown(self):
        """Dọn dẹp tài nguyên"""
        logging.info("Đang đóng trình duyệt...")
        if self.page: await self.page.close()
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()