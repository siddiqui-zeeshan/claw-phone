FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

VOLUME /root/.spare-paw

EXPOSE 8080

ENTRYPOINT ["python", "-m", "spare_paw", "gateway"]
