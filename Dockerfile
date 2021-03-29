FROM debian:10-slim

RUN apt-get update

RUN apt-get install -y --no-install-recommends \
    build-essential time \
    python3 python3-pip
RUN pip3 install anytree

RUN apt-get install -y --no-install-recommends \
    libfuse3-dev libboost-dev

WORKDIR /app
ENTRYPOINT /app/test.sh
