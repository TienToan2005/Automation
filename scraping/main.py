import asyncio
import json
import os
from src.automation.auth_bot import UniversalAuthBot
from src.utils.file_helper import read_users_from_excel

def load_config(config_file_name):
    config_path = os.path.join(os.path.dirname(__file__), "configs", config_file_name)
    with open(config_path, 'r', encoding='utf-8') as file:
        return json.load(file)

async def main():
    config = load_config("login_ecom.json")
    bot = UniversalAuthBot(config)
    
    # 1. Đọc danh sách User từ file Excel trong data/input
    user_list = read_users_from_excel("accounts.xlsx")
    
    if not user_list:
        print("Không có dữ liệu để chạy. Dừng chương trình.")
        return

    try:
        await bot.setup(headless_mode=False)
        
        # 2. Chạy vòng lặp qua từng user trong danh sách
        for index, user_data in enumerate(user_list, start=1):
            print(f"\n--- Đang xử lý dòng {index}/{len(user_list)} ---")
            # Đảm bảo sạch sẽ trước khi bắt đầu acc mới
            await bot.context.clear_cookies()
            # Chạy luồng Đăng nhập
            await bot.login(user_data)
            
            # Đợi một chút giữa các lần đăng nhập (Tránh bị web block vì thao tác quá nhanh)
            await asyncio.sleep(2) 
            
            await bot.logout() 
            
    finally:
        await bot.teardown()

if __name__ == "__main__":
    asyncio.run(main())