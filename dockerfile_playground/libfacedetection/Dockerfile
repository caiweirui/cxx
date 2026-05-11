FROM ubuntu:20.04

# Setting the environment variable to avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Change to Aliyun mirrors for faster installation
# Update package sources to use Aliyun mirrors (mirrors.aliyun.com)
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list
RUN sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list

# Update the package list
RUN apt-get update

# Upgrade the system
RUN apt-get upgrade -y

# Install build-essential (GCC, G++, make, etc.)
RUN apt-get install -y build-essential

# Install cmake
RUN apt-get install -y cmake

# Install software-properties-common
RUN apt-get install -y software-properties-common

# Install optional OpenCV dependency if needed
RUN apt-get install -y libopencv-dev

# Install GStreamer dependencies if needed
RUN apt-get install -y libgstreamer1.0-dev
RUN apt-get install -y libgstreamer-plugins-base1.0-dev
RUN apt-get install -y libgstreamer-plugins-good1.0-dev

# Create and set the working directory
WORKDIR /tmp/libfacedetection_build

# Copy the project source code into the container
COPY ./libfacedetection /tmp/libfacedetection

# Create a build directory
RUN mkdir -p /tmp/libfacedetection/build

# Change to the build directory
WORKDIR /tmp/libfacedetection/build

# Configure the project with CMake
RUN cmake .. -DCMAKE_BUILD_TYPE=Release

# Build the project
RUN cmake --build . --config Release