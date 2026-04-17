FROM python:3.10-slim

WORKDIR /app

# 安装 uv 包管理器
RUN pip install --no-cache-dir uv

# 先复制依赖声明文件以利用 Docker 缓存层
COPY pyproject.toml uv.lock /app/

# 安装 Python 依赖到系统环境
RUN uv pip install --system -r pyproject.toml

# 复制全部后端代码
COPY . /app/

EXPOSE 8000

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
