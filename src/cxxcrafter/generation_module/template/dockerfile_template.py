dockerfile_template = """
    ```Dockerfile
    FROM ubuntu:{ubuntu_version}

    ENV DEBIAN_FRONTEND=noninteractive

    RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \\
        sed -i 's|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list && \\
        apt-get update && \\
        apt-get upgrade -y

    # Install necessary packages
    RUN apt-get update
    RUN apt-get install -y build-essential
    RUN apt-get install -y software-properties-common

    # Install Dependencies
    RUN apt-get install -y {dependency1}
    RUN apt-get install -y {dependency2}
    ...

    # Build the project with {build_system}
    ...
    ```
    """

build_command_dict = {
    "cmake": """\
RUN set -eux; \
    if [ -f CMakeLists.txt ]; then \
        cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release; \
        cmake --build build --config Release; \
    else \
        echo "CMakeLists.txt not found in the current build root"; \
        find /workspace -name CMakeLists.txt -print; \
        exit 1; \
    fi
""",
    "make": """\
RUN set -eux; \
    if [ -f Makefile ] || [ -f makefile ] || [ -f GNUmakefile ]; then \
        make -j"$(nproc)"; \
    else \
        echo "Makefile not found in the current build root"; \
        find /workspace \( -name Makefile -o -name makefile -o -name GNUmakefile \) -print; \
        exit 1; \
    fi
"""
}