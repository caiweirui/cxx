FROM ubuntu:22.04

# Set noninteractive environment for apt-get to avoid prompts
ENV DEBIAN_FRONTEND=noninteractive

# Update package repositories to use Aliyun mirrors and perform a system upgrade
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list \
    && sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list \
    && apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Install required dependencies in one RUN command for efficiency
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        software-properties-common \
        gtk-3-dev \
        libpango1.0-dev \
        libglib2.0-dev \
        libatk1.0-dev \
        python3-docutils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory to /tmp
WORKDIR /tmp

# Copy source code to container
COPY ./geany /tmp/geany

# Navigate to source code directory and build the project
WORKDIR /tmp/geany
RUN ./autogen.sh \
    && ./configure \
    && make \
    && make check \
    && make install

# Clean up source directory
RUN rm -rf /tmp/geany

# Set the default command to display Geany version for verification
CMD ["geany", "--version"]