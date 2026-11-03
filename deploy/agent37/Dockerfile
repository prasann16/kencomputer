# Ken on its own computer in the cloud: an Agent37 template image.
# One person, one instance. Ken's web app is the instance's main URL (default port 7777).
#
# Build and register from this folder (see README.md here):
#   npx agent37 templates build . --name ken --default-port 7777
#
# Everything under /home/node is the instance's persistent disk and is hidden at runtime, so the
# image only carries what doesn't change per person: Python, Ken's Python packages, a first copy
# of Ken's code, Chromium and ffmpeg. Ken's own code, memory and settings live in ~/.ken on the
# disk, where git-pull updates survive the image being replaced.
FROM ghcr.io/agent37-platform/base:2026.10.24a

USER root
# ffmpeg for Ken's video skills; Chromium for its own headless browser (only plans that include
# a browser turn it on, with KEN_BROWSER=1); tzdata so a person's time zone can be set.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg chromium fonts-dejavu-core fonts-noto-color-emoji tzdata \
  && rm -rf /var/lib/apt/lists/*
# Google's Chrome DevTools MCP server, which Ken drives the browser through.
RUN npm install -g --prefix /usr/local --no-fund --no-audit chrome-devtools-mcp@1.10.1 \
  && mkdir -p /opt/ken && chown node:node /opt/ken
COPY --chmod=0755 ken-start ken-supervise /usr/local/bin/

USER node
ARG KEN_REPO=https://github.com/prasann16/kencomputer.git
ARG KEN_REF=main
ENV UV_PYTHON_INSTALL_DIR=/opt/ken/python UV_CACHE_DIR=/tmp/uv-cache
# Python 3.13 and Ken's packages in /opt, without voice: the local transcription model needs about
# 1.5 GB of memory (pins.py leaves it, and what only it needs, out).
RUN git clone -q --branch "$KEN_REF" "$KEN_REPO" /opt/ken/app-seed \
  && uv venv -q --seed --managed-python --python 3.13 /opt/ken/venv \
  && /opt/ken/venv/bin/python /opt/ken/app-seed/pins.py /opt/ken/app-seed/requirements.txt > /tmp/requirements.txt \
  && /opt/ken/venv/bin/pip install -q --no-cache-dir -r /tmp/requirements.txt \
  && rm -rf /tmp/uv-cache /tmp/requirements.txt

ENV KEN_HOSTED=1 \
    KEN_PLATFORM=agent37 \
    KEN_VOICE=0 \
    KEN_WEB_HOST=0.0.0.0 \
    KEN_WEB_PORT=7777 \
    KEN_CHROMIUM=/usr/bin/chromium \
    KEN_NODE=/usr/local/bin/node \
    KEN_BROWSER_MCP=/usr/local/lib/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js

EXPOSE 7777
# ken-start runs Ken's supervisor next to the base image's own boot (boot hooks and a terminal on 7681).
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/ken-start"]
