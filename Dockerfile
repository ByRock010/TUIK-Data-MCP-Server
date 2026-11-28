FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
ARG TUIK_EXTRAS=viz
RUN pip install --no-cache-dir ".[${TUIK_EXTRAS}]" \
    && useradd --create-home tuik \
    && install -d -m 0700 -o tuik -g tuik /var/lib/tuik-hosted
USER tuik
ENTRYPOINT ["tuik-mcp"]
