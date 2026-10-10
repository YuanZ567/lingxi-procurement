FROM python:3.14-slim

WORKDIR /app

# 先只拷依赖清单，利用 Docker 层缓存：代码改动不用重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/


COPY src ./src
# build_kb.py 的语料路径写死为 seed_data/kb，两个目录都需要存在
COPY seed_data ./data
COPY seed_data ./seed_data
COPY init_db.py .

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
