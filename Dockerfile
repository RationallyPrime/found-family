FROM python:3.13-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
RUN uv sync

COPY src/ src/

CMD ["uv", "run", "uvicorn", "memory_palace.main:app", "--host", "0.0.0.0", "--reload"]
