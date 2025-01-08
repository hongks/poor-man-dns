FROM python:3.11

RUN mkdir /poor-main-dns/run

WORKDIR /poor-main-dns

COPY config.yml ./run

COPY requirements.txt .

COPY app app

COPY certs certs

RUN pip3 install --no-cache-dir --upgrade pip wheel

RUN pip3 install --no-cache-dir -r requirements.txt

EXPOSE 53

EXPOSE 5050

EXPOSE 5053

WORKDIR /poor-main-dns/run

CMD [ "python",  "-u", "../app/main.py" ]
