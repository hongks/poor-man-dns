# ################################################################################
# builder

FROM python:3.12-slim AS builder
ENV PYTHONUNBUFFERED 1

RUN apt-get update && \
    apt-get install -y gcc && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv/
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /poor-man-dns/

COPY requirements.txt .

RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir --upgrade --requirement requirements.txt

COPY app/ app/
COPY certs/ certs/

COPY app/templates/config.yml run/.


# ################################################################################
# final

FROM python:3.12-slim AS final

RUN apt-get update && \
    apt-get install -y curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /poor-man-dns/

COPY --from=builder /poor-man-dns/ /poor-man-dns/
COPY --from=builder /opt/venv/ /opt/venv/

RUN groupadd -g 1000 -r poor && \
    useradd -u 1000 -m -r -g poor poor
RUN chown -R poor:poor /poor-man-dns/

USER root

ENV PATH="/opt/venv/bin:$PATH"

HEALTHCHECK CMD curl -fks https://localhost:5050/ || exit 1

EXPOSE 53 583 5050 5053

STOPSIGNAL SIGINT

CMD [ "python",  "-uX", "dev", "app/main.py" ]
