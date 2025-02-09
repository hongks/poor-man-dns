FROM python:3.11-slim

WORKDIR /poor-main-dns/
COPY requirements.txt .

RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY certs/ certs/

WORKDIR /poor-main-dns/run/
COPY ../config.yml .

EXPOSE 53 5050 5053

CMD [ "python",  "-u", "../app/main.py" ]
