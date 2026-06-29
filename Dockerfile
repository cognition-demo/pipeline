FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e ".[dev]" 2>/dev/null || pip install fastapi uvicorn[standard] httpx pydantic aiosqlite rich click
COPY . .
RUN pip install -e .
EXPOSE 8765
ENV PIPELINE_MODE=replay
CMD ["pipeline", "dashboard"]
