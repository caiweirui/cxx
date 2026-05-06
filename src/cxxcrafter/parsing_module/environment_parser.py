import os
import platform
import shutil
import subprocess
from typing import Dict, List

def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def _detect_gpu() -> bool:
    if not _which("nvidia-smi"):
        return False
    try:
        subprocess.run(["nvidia-smi", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception:
        return False

def detect_environment(project_dir: str = "") -> Dict:
    os_name = platform.system().lower()
    arch = platform.machine()
    release = platform.release()

    package_managers = []
    for cmd in ["apt", "apt-get", "dnf", "yum", "pacman", "apk", "brew"]:
        if _which(cmd):
            package_managers.append(cmd)

    docker_available = _which("docker")
    git_available = _which("git")

    if os_name == "linux":
        if _which("apt") or _which("apt-get"):
            base_image = "ubuntu:22.04"
        elif _which("dnf") or _which("yum"):
            base_image = "centos:stream9"
        elif _which("apk"):
            base_image = "alpine:3.20"
        else:
            base_image = "debian:12"
    elif os_name == "windows":
        base_image = "ubuntu:22.04"
    else:
        base_image = "ubuntu:22.04"

    return {
        "os": os_name,
        "release": release,
        "arch": arch,
        "has_gpu": _detect_gpu(),
        "package_managers": package_managers,
        "docker_available": docker_available,
        "git_available": git_available,
        "recommended_base_image": base_image,
        "project_dir": os.path.abspath(project_dir) if project_dir else "",
    }