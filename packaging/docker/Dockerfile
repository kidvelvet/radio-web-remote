# Optional: radio-web-remote in a container, Linux hosts only. The normal path
# is the install script (docs/install-linux.md); use this only if you already
# run Docker. See docker/README.md for USB serial and audio passthrough.
FROM python:3.12-slim

# PortAudio for the radio's USB audio (python-sounddevice uses it).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libportaudio2 \
 && rm -rf /var/lib/apt/lists/*

COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src

# Run as an ordinary user. dialout and audio (Debian's groups for serial and
# sound devices) are added by compose's group_add.
RUN useradd --create-home --uid 1000 radio && mkdir /data && chown radio /data
USER radio

# State (config.yaml, users, certificates, logs) lives in a volume.
ENV RADIO_WEB_REMOTE_HOME=/data
VOLUME ["/data"]
EXPOSE 8473
CMD ["radio-web-remote", "serve"]
