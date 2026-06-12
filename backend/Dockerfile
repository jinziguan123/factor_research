FROM python:3.10-slim

# lightgbm 需要 libgomp（OpenMP）
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# WORKDIR = 项目根；backend/ 作为子包存在，保持 import backend.xxx 能跑通
WORKDIR /project

# 1) 依赖声明先拷，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 2) 只装依赖，不装项目本身（代码还没拷进来）
RUN uv sync --frozen --no-install-project --no-dev

# 3) 拷全部后端代码到 /project/backend/
COPY . ./backend/

# 4) 安装项目包（依赖已缓存，这步很快）
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
