# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY finance_agent ./finance_agent
COPY migrations ./migrations
COPY alembic.ini ./

RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then \
        PIP_CERT=/run/secrets/pip_ca python -m pip install --no-cache-dir .; \
    else \
        python -m pip install --no-cache-dir .; \
    fi && \
    useradd --create-home --uid 10001 finance

USER finance
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "finance_agent.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
