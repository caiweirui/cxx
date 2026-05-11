# Use Ubuntu 20.10 (Groovy Gorilla) as the base image
FROM ubuntu:20.10

# Set environment variable to avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Use Aliyun mirror for faster package updates
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \
    sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \
    apt-get update && \
    apt-get upgrade -y

# Install necessary tools, build essentials, and project dependencies
RUN apt-get install -y \
    build-essential \
    software-properties-common \
    python3-pip \
    git \
    cmake \
    libxcb1-dev \
    libxcb-util0-dev \
    libxcb-randr0-dev \
    libxcb-composite0-dev \
    python3-sphinx \
    libxcb-xkb-dev \
    libxcb-cursor-dev \
    libasound2-dev \
    libmpdclient-dev \
    libiw-dev \
    libcurl4-openssl-dev \
    libnl-genl-3-dev \
    libpulse-dev && \
    apt-get autoremove -y && apt-get clean

# Install Python dependencies for building documentation
RUN pip3 install \
    sphinx==7.2.6 \
    sphinx-rtd-theme==2.0.0rc2 \
    sphinx-notfound-page==1.0.0 \
    readthedocs-sphinx-search==0.3.2

# Create a temporary directory for building
WORKDIR /tmp/build

# Copy the source code into the container
COPY ./polybar /tmp/build

# Create a build directory for cmake
RUN mkdir build

# Switch to the build directory
WORKDIR /tmp/build/build

# Configure the project using CMake, build, and install
RUN cmake .. && \
    make -j$(nproc) && \
    make install

# Clean up build directories and unnecessary packages
RUN rm -rf /tmp/build && \
    apt-get autoremove -y && \
    apt-get clean

# Set default command
CMD ["bash"]