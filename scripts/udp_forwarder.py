"""UDP Forwarder: receives from Pi on port 5006, forwards to Docker RuView on 172.17.0.5:5005"""
import socket
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

LISTEN_PORT = 5006
DOCKER_IP = "127.0.0.1"
DOCKER_PORT = 5005

host_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
host_sock.bind(("0.0.0.0", LISTEN_PORT))

docker_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

logger.info("UDP Forwarder: 0.0.0.0:%d -> %s:%d", LISTEN_PORT, DOCKER_IP, DOCKER_PORT)

count = 0
while True:
    data, addr = host_sock.recvfrom(65535)
    docker_sock.sendto(data, (DOCKER_IP, DOCKER_PORT))
    count += 1
    if count % 100 == 0:
        logger.info("Forwarded %d frames from %s", count, addr[0])
