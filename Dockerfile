FROM python:3.11

WORKDIR /poor-main-dns

COPY config.yml .

COPY requirements.txt .

COPY app app

COPY certs certs

RUN pip3 install --no-cache-dir --upgrade pip wheel

RUN pip3 install --no-cache-dir -r requirements.txt

EXPOSE 53

EXPOSE 5053

CMD [ "python",  "-u", "all/main.py" ]
