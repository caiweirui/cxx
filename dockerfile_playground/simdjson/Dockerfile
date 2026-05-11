# Base image with the specified Ubuntu version
FROM ubuntu:22.04

# Set non-interactive environment to prevent issues during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Use a faster mirror and update/upgrade the system
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \
    sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \
    apt-get update && \
    apt-get upgrade -y

# Install essential build tools
RUN apt-get install -y build-essential

# Install software-properties-common to manage additional repositories
RUN apt-get install -y software-properties-common

# Install cmake, a build tool
RUN apt-get install -y cmake

# Install python3, for any scripting needs
RUN apt-get install -y python3

# Install Google Benchmark library dependencies
RUN apt-get install -y libbenchmark-dev

# Install Boost C++ libraries
RUN apt-get install -y libboost-all-dev

# Clean up apt cache to reduce image size
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy the source code into the container
COPY ./simdjson /tmp/simdjson

# Set the working directory to the source code directory in the container
WORKDIR /tmp/simdjson

# Create a build directory and move into it
RUN mkdir -p /tmp/simdjson/build

# Change to the build directory
WORKDIR /tmp/simdjson/build

# Run CMake to configure the build system
RUN cmake ..

# Build the project using CMake
RUN cmake --build . --config Release

# Set the default command to display the build directory contents
CMD ["ls", "/tmp/simdjson/build"]