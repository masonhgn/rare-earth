# Headless authoritative game server for cloud hosting (fly.io / DigitalOcean /
# any VPS with Docker). The client is NOT built here — it ships as a PyInstaller
# exe to players (see DEPLOY.md).
FROM python:3.11-slim

# pygame is imported for pg.Rect/timing; with the dummy SDL drivers it needs no
# display or audio device, just a few SDL runtime libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0 \
        libfreetype6 libportmidi0 \
    && rm -rf /var/lib/apt/lists/*

ENV SDL_VIDEODRIVER=dummy \
    SDL_AUDIODRIVER=dummy \
    PYTHONUNBUFFERED=1 \
    RARE_EARTH_SAVE=/data/server.json

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src

# the persistent world lives on a mounted volume (fly volume / docker -v)
VOLUME ["/data"]
EXPOSE 5555
CMD ["python", "src/server.py"]
