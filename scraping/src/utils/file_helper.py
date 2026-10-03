import pandas as pd
import os
import logging

def get_data_input_path(file_name):
    """Hàm lấy đường dẫn tuyệt đối đến thư mục data/input"""
    # Lấy thư mục gốc (AUTO-SCRAP) bằng cách lùi lại 3 cấp từ file_helper.py
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, "data", "input", file_name)

def read_users_from_excel(file_name):
    """Đọc file Excel và trả về danh sách các Dictionary"""
    file_path = get_data_input_path(file_name)
    
    if not os.path.exists(file_path):
        logging.error(f"[-] Không tìm thấy file: {file_path}")
        return []

    try:
        logging.info(f"[*] Đang đọc dữ liệu từ: {file_name}...")
        # Đọc toàn bộ file Excel
        df = pd.read_excel(file_path)
        
        # Xử lý dữ liệu rác: Xóa các dòng trống hoàn toàn và thay thế giá trị NaN thành chuỗi rỗng
        df = df.dropna(how='all').fillna("")
        
        # Chuyển đổi Dataframe thành List of Dictionaries
        users_list = df.to_dict(orient='records')
        
        logging.info(f"[+] Đã tải thành công {len(users_list)} dòng dữ liệu.")
        return users_list

    except Exception as e:
        logging.error(f"[-] Lỗi khi đọc file Excel: {e}")
        return []