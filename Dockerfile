FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY gigaflow gigaflow/
COPY tests tests/

RUN pip install -e ".[dev]"

CMD ["pytest"]
