FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY purrmox ./purrmox
RUN pip install --no-cache-dir .
VOLUME ["/data"]
WORKDIR /data
EXPOSE 8080
# Mount your configuration at /data/config.yaml and set listen.host to "0.0.0.0" with auth enabled.
CMD ["purrmox", "--config", "/data/config.yaml"]
