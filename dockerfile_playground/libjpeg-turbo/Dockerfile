# Use Ubuntu 20.04 as the base image
FROM ubuntu:20.04

# Set environment variable to avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Update package references and upgrade the image
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list
RUN sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list
RUN apt-get update
RUN apt-get upgrade -y

# Install build essentials for compiling software
RUN apt-get install -y build-essential

# Install software-properties-common for adding repositories easily
RUN apt-get install -y software-properties-common

# Install cmake since it's required for the build
RUN apt-get install -y cmake

# Install necessary compilers for building the project
RUN apt-get install -y gcc g++

# Install nasm for x86/x86-64 SIMD assembly optimizations
RUN apt-get install -y nasm

# Install zlib because it is an optional dependency for the build
RUN apt-get install -y zlib1g-dev

# Copy libjpeg-turbo source code to the container's temp directory
COPY ./libjpeg-turbo /tmp/libjpeg-turbo

# Set the working directory for the build process
WORKDIR /tmp/libjpeg-turbo/build

# Run cmake to configure the build
RUN cmake .. -DWITH_JPEG7=1 -DWITH_JPEG8=1 -DWITH_SIMD=1 -G"Unix Makefiles"

# Build the project using `make` with all available processors
RUN make -j$(nproc)

# Install the compiled binaries to the system
RUN make install

# Clean up unused files and cached data to reduce the image size
RUN apt-get clean
RUN rm -rf /var/lib/apt/lists/*

# Set the default working directory (optional step)
WORKDIR /tmp/libjpeg-turbo/build