from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

@dataclass
class BuildExecutionResult:
    success: bool
    exit_code: int
    image_tag: str
    log_path: str
    dockerfile_path: str
    context_dir: str
    message: str = ""
    timed_out: bool = False
    duration_seconds: float = 0.0
    command: List[str] = None
    status: str = "unknown"

@dataclass
class VerificationExecutionResult:
    success: bool
    exit_code: int
    log_path: str
    message: str = ""
    skipped: bool = False
    timed_out: bool = False
    duration_seconds: float = 0.0
    command: List[str] = None
    status: str = "unknown"

class DockerExecutor:
    def __init__(self, use_buildkit: bool = True, buildkit_progress: str = "plain") -> None:
        self.use_buildkit = use_buildkit
        self.buildkit_progress = buildkit_progress

    def build(
        self,
        dockerfile_path: str,
        context_dir: str,
        image_tag: Optional[str],
        log_path: str,
        extra_args: Optional[List[str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> BuildExecutionResult:
        dockerfile_path = str(Path(dockerfile_path))
        context_dir = str(Path(context_dir))
        image_tag = image_tag or self._default_image_tag(dockerfile_path)

        cmd = [
            "docker",
            "build",
            "-f",
            dockerfile_path,
            "-t",
            image_tag,
        ]

        if extra_args:
            cmd.extend(extra_args)

        cmd.append(context_dir)

        env = os.environ.copy()
        if self.use_buildkit:
            env["DOCKER_BUILDKIT"] = "1"
            env["BUILDKIT_PROGRESS"] = self.buildkit_progress

        started = time.monotonic()
        result = self._run_streaming_command(
            cmd=cmd,
            log_path=log_path,
            env=env,
            cwd=None,
            timeout_seconds=timeout_seconds,
        )
        duration = time.monotonic() - started

        return BuildExecutionResult(
            success=(result["exit_code"] == 0 and not result["timed_out"]),
            exit_code=result["exit_code"],
            image_tag=image_tag,
            log_path=log_path,
            dockerfile_path=dockerfile_path,
            context_dir=context_dir,
            message=result["message"],
            timed_out=result["timed_out"],
            duration_seconds=duration,
            command=cmd,
            status=result.get("status", "unknown"),
        )

    def verify(
        self,
        image_tag: str,
        run_command: str,
        log_path: str,
        timeout_seconds: Optional[float] = None,
    ) -> VerificationExecutionResult:
        if not run_command or not str(run_command).strip():
            return VerificationExecutionResult(
                success=False,
                exit_code=0,
                log_path=log_path,
                message="verification skipped: empty run command",
                skipped=True,
                timed_out=False,
                duration_seconds=0.0,
                command=[],
                status="skipped",
            )

        cmd = ["docker", "run", "--rm", image_tag, "sh", "-lc", run_command]

        started = time.monotonic()
        result = self._run_streaming_command(
            cmd=cmd,
            log_path=log_path,
            env=None,
            cwd=None,
            timeout_seconds=timeout_seconds,
        )
        duration = time.monotonic() - started

        return VerificationExecutionResult(
            success=(result["exit_code"] == 0 and not result["timed_out"]),
            exit_code=result["exit_code"],
            log_path=log_path,
            message=result["message"],
            skipped=False,
            timed_out=result["timed_out"],
            duration_seconds=duration,
            command=cmd,
            status=result.get("status", "unknown"),
        )

    def _run_streaming_command(
        self,
        cmd: List[str],
        log_path: str,
        env: Optional[dict],
        cwd: Optional[str],
        timeout_seconds: Optional[float],
    ) -> dict:
        """
        流式执行命令：
        - 实时把 stdout/stderr 写入日志
        - 支持超时
        - 超时后杀掉进程树
        - 自动识别 Docker 不可用
        """
        log_file_path = Path(log_path)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

        creationflags = 0
        preexec_fn = None

        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            preexec_fn = os.setsid

        lines: List[str] = []
        timed_out = False
        status = "failed"

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
                cwd=cwd,
                env=env,
                creationflags=creationflags,
                preexec_fn=preexec_fn,
            )
        except FileNotFoundError as e:
            self._write_text(log_file_path, f"[executor] {e}\n")
            return {
                "exit_code": 127,
                "timed_out": False,
                "message": f"docker unavailable: {e}",
                "status": "docker_unavailable",
            }
        except PermissionError as e:
            self._write_text(log_file_path, f"[executor] {e}\n")
            return {
                "exit_code": 126,
                "timed_out": False,
                "message": f"docker unavailable: {e}",
                "status": "docker_unavailable",
            }
        except OSError as e:
            self._write_text(log_file_path, f"[executor] {e}\n")
            return {
                "exit_code": 126,
                "timed_out": False,
                "message": f"docker unavailable: {e}",
                "status": "docker_unavailable",
            }

        def reader() -> None:
            try:
                assert proc.stdout is not None
                with log_file_path.open("w", encoding="utf-8", errors="ignore") as f:
                    for line in iter(proc.stdout.readline, ""):
                        if not line:
                            break
                        lines.append(line)
                        f.write(line)
                        f.flush()
            except Exception as e:
                lines.append(f"\n[executor-reader-error] {e}\n")

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_tree(proc)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass

        t.join(timeout=10)

        exit_code = proc.returncode if proc.returncode is not None else (-1 if timed_out else 1)
        log_text = "".join(lines).lower()

        if timed_out:
            message = f"command timed out after {timeout_seconds} seconds"
            status = "timeout"
            if exit_code == 0:
                exit_code = 124
        else:
            message = "command ok" if exit_code == 0 else "command failed"
            status = "passed" if exit_code == 0 else "failed"

        # Docker daemon / socket / registry 异常识别
        docker_unavailable_patterns = [
            "cannot connect to the docker daemon",
            "is the docker daemon running",
            "failed to connect to the docker daemon",
            "permission denied while trying to connect to the docker daemon socket",
            "error during connect",
            "dockerdesktoplinuxengine/_ping",
            "500 internal server error for api route and version",
        ]
        if any(p in log_text for p in docker_unavailable_patterns):
            status = "docker_unavailable"
            message = "docker unavailable or docker daemon connection failed"

        if not log_file_path.exists():
            self._write_text(log_file_path, "".join(lines))

        return {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "message": message,
            "status": status,
        }

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", errors="ignore")

    def _terminate_process_tree(self, proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @staticmethod
    def _default_image_tag(dockerfile_path: str) -> str:
        name = Path(dockerfile_path).parent.name.lower().replace(" ", "_")
        return f"cxxcrafter/{name}:latest"