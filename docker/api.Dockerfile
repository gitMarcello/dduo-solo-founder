FROM postgres:16-bookworm
ENTRYPOINT []
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/dduo-venv
ENV PATH="/opt/dduo-venv/bin:$PATH"
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY alembic.ini ./
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/pip pip install .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn dduo_solo_founder.main:app --app-dir backend --host 0.0.0.0 --port 8000"]
