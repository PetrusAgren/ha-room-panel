ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip tzdata \
    && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY roompanel ./roompanel
COPY DOCS.md ./DOCS.md

EXPOSE 8099 8443
CMD ["/command/with-contenv", "python3", "-m", "roompanel"]
