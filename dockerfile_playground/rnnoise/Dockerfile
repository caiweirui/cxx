FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive

# Update sources list to use Aliyun mirrors
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \
    sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \
    apt-get update && apt-get upgrade -y

# Install necessary tools and dependencies separately
RUN apt-get install -y build-essential
RUN apt-get install -y software-properties-common
RUN apt-get install -y autoconf
RUN apt-get install -y automake
RUN apt-get install -y libtool
RUN apt-get install -y gcc
RUN apt-get install -y make
RUN apt-get install -y python3

# Avoid duplicating identical RUN commands (consolidate if appropriate)

# Copy RNNoise source code to the container
COPY ./rnnoise /tmp/rnnoise

# Set work directory to the RNNoise source location
WORKDIR /tmp/rnnoise

# Build RNNoise
RUN ./autogen.sh
RUN ./configure
RUN make