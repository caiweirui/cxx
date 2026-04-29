import os
import subprocess
from typing import Dict, List

class TestRunner:
    def __init__(self, build_dir: str):
        self.build_dir = os.path.abspath(build_dir)

    def run_tests(self) -> Dict:
        """自动运行单元测试"""
        result = {
            "status": "pending",
            "test_type": "",
            "total_tests": 0,
            "passed_tests": 0,
            "failed_tests": 0,
            "output": "",
            "details": ""
        }

        if not os.path.exists(self.build_dir):
            result["status"] = "skipped"
            result["details"] = "构建目录不存在，跳过测试"
            return result

        # 尝试不同的测试命令
        test_commands = [
            ("make test", ["make", "test"]),
            ("ctest", ["ctest", "--output-on-failure"]),
            ("pytest", ["python", "-m", "pytest", "-v"]),
            ("ninja test", ["ninja", "test"])
        ]

        for test_name, cmd in test_commands:
            try:
                # Windows兼容：使用shell=True
                proc = subprocess.Popen(
                    cmd,
                    cwd=self.build_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    shell=True
                )
                output, _ = proc.communicate(timeout=300)
                
                result["test_type"] = test_name
                result["output"] = output
                
                # 简单解析测试结果
                if proc.returncode == 0:
                    result["status"] = "success"
                    result["passed_tests"] = self._count_passed(output)
                    result["total_tests"] = max(result["passed_tests"], 1)
                    result["details"] = f"{test_name} 执行成功，通过 {result['passed_tests']} 个测试"
                else:
                    result["status"] = "failed"
                    result["failed_tests"] = self._count_failed(output)
                    result["details"] = f"{test_name} 执行失败，失败 {result['failed_tests']} 个测试"
                
                return result

            except Exception as e:
                continue

        result["status"] = "skipped"
        result["details"] = "未找到可执行的测试命令"
        return result

    def _count_passed(self, output: str) -> int:
        keywords = ["passed", "PASSED", "ok", "OK", "success"]
        return sum(output.lower().count(k) for k in keywords)

    def _count_failed(self, output: str) -> int:
        keywords = ["failed", "FAILED", "error", "ERROR", "fail"]
        return sum(output.lower().count(k) for k in keywords)

    def get_summary(self) -> str:
        test = self.run_tests()
        return f"[单元测试] {test['status'].upper()}: {test['details']}"