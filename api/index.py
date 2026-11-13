import os
import sys
from pathlib import Path

# Thêm thư mục gốc vào sys.path để import các module app, database, crm_parser...
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import app

# Vercel Serverless Function sẽ tự động nhận diện biến `app` (Flask instance)
# Local test: python api/index.py
if __name__ == "__main__":
    app.run(debug=True)
