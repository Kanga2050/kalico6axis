# Printer twin: real Kalico, Moonraker and Mainsail with simulated MCUs.
# Build from the repo root:  docker build -f twin/Dockerfile -t printer-twin .
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev gcc g++ make libffi-dev pkg-config \
        gcc-arm-none-eabi libnewlib-arm-none-eabi binutils-arm-none-eabi \
        nginx libjpeg-dev zlib1g-dev libsodium-dev libcurl4-openssl-dev liblmdb-dev \
        procps ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

COPY kalico/scripts/klippy-requirements.txt /tmp/klippy-requirements.txt
COPY moonraker/scripts/moonraker-requirements.txt /tmp/moonraker-requirements.txt
RUN python3 -m venv /opt/klippy-env \
    && /opt/klippy-env/bin/pip install --no-cache-dir -r /tmp/klippy-requirements.txt \
    && python3 -m venv /opt/moonraker-env \
    && /opt/moonraker-env/bin/pip install --no-cache-dir -r /tmp/moonraker-requirements.txt

# Windows checkouts may carry CRLF line endings; the build tools need LF
COPY kalico /opt/printer/kalico
RUN find /opt/printer/kalico -type f -not -path '*/.git/*' -exec grep -Il . {} + \
        | xargs sed -i 's/\r$//' \
    && chmod +x /opt/printer/kalico/scripts/*.sh /opt/printer/kalico/scripts/*.py \
    && cd /opt/printer/kalico && /opt/klippy-env/bin/python -c "from klippy import chelper; chelper.get_ffi()"
COPY moonraker /opt/printer/moonraker
RUN find /opt/printer/moonraker -type f -name '*.py' -exec sed -i 's/\r$//' {} +
COPY web/mainsail /opt/printer/mainsail
COPY twin/sim /opt/printer/sim
COPY twin/tests /opt/printer/tests
RUN sed -i 's/\r$//' /opt/printer/sim/*.py /opt/printer/tests/*.py /opt/printer/sim/nginx.conf \
    && cp /opt/printer/sim/nginx.conf /etc/nginx/conf.d/twin.conf

VOLUME ["/data/printer_data/gcodes", "/data/cache"]
EXPOSE 80 7125
ENTRYPOINT ["/opt/klippy-env/bin/python", "/opt/printer/sim/start.py"]
