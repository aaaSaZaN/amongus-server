FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY protocol18.py crypto.py enet.py fusion_plugin.py photon.py dns.py ./

# Fusion: name, master, game. Voice: name, master, game.
EXPOSE 27000/udp 27001/udp 27002/udp 5058/udp 5055/udp 5056/udp
USER nobody
ENTRYPOINT ["python3", "-u", "photon.py"]
