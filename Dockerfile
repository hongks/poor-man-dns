FROM python:3.12

WORKDIR /poor-main-dns/
COPY requirements.txt .

RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY certs/ certs/

COPY run/config.yml run/

HEALTHCHECK CMD curl -fks http://localhost:5050/ || exit 1

EXPOSE 53 5050 5053

STOPSIGNAL SIGINT

CMD [ "python",  "-X", "dev", "app/main.py" ]
