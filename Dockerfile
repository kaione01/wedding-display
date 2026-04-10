FROM python:3.11-slim

WORKDIR /app

# 安裝套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製程式碼
COPY . .

# 建立必要資料夾
RUN mkdir -p uploads static/wedding_bg

# 啟動
CMD ["python", "main.py"]
