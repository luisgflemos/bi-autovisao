FROM python:3.12-slim
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN pip install --no-cache-dir playwright==1.64.1 openpyxl==3.1.5 \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright
WORKDIR /app
COPY app/ /app/
ENV PYTHONDONTWRITEBYTECODE=1 BI_HOST=0.0.0.0 BI_PORT=8765 BI_DATABASE=/data/vendas.sqlite3
USER 10001:10001
EXPOSE 8765
CMD ["python", "painel.py"]
