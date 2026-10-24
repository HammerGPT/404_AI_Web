FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/prepare_pdf_font.py /tmp/prepare_pdf_font.py
RUN pip install --no-cache-dir 'fonttools>=4.55,<5' \
    && python /tmp/prepare_pdf_font.py /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc /usr/local/share/fonts/ai404/report.otf \
    && fc-cache -f && pip uninstall -y fonttools && rm /tmp/prepare_pdf_font.py
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -r -m tester
COPY app ./app
RUN mkdir /data && chown -R tester:tester /data /srv
USER tester
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1","--no-access-log"]
