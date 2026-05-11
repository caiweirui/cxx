# Use Ubuntu 14 as the base image since it's recommended for 8cc
FROM ubuntu:14.04

# Set environment variable to avoid interactive prompts during builds
ENV DEBIAN_FRONTEND=noninteractive

# Update package repositories to use mirrors.aliyun.com for faster downloads
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \
    sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list

# Update the package list
RUN apt-get update

# Upgrade installed packages
RUN apt-get upgrade -y

# Install required development tools (build-essential, software-properties-common)
RUN apt-get install -y build-essential

# Install additional dependencies (gcc and make)
RUN apt-get install -y gcc
RUN apt-get install -y make

# Copy the 8cc source code to the Docker container
COPY ./8cc /tmp/8cc

# Set the working directory inside the container
WORKDIR /tmp/8cc

# Build the 8cc project using the Make build system
RUN make

# Run unit tests to validate the build
RUN make test

# (Optional) Add more testing or verification commands as needed
# Uncomment the following line to include full testing:
# RUN make fulltest