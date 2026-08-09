ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.20
FROM ${BUILD_FROM}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The shared ariacast_core package (single source of truth, also used by the
# HACS custom_component) is staged into ./app/ariacast_core by
# `scripts/build_addon.sh` before `docker build` runs, because the Home
# Assistant Supervisor build context is this add-on directory only — it
# cannot reach ../../custom_components directly.
COPY app /app

ENV PYTHONUNBUFFERED=1
ENV ARIACAST_DB_PATH=/data/ariacast.db
ENV ARIACAST_WEB_PORT=8099

EXPOSE 8099/tcp
EXPOSE 12888/udp

COPY addon/ariacast_core/run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
