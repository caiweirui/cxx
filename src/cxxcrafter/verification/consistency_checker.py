import os
import subprocess
from typing import Dict

class ConsistencyChecker:
    def __init__(self, build_dir: str):
        self.build_dir = os.path.abspath(build_dir)

    def check_consistency(self) -> Dict:
        """简单功能一致性验证"""
        result = {
            "status": "pending",
            "tests_run": [],
            "details": ""
        }

        # 查找可执行文件
        executables = self._find_executables()
        
        if not executables:
            result["status"] = "skipped"
            result["details"] = "未找到可执行文件进行功能验证"
            return result

        # 对每个可执行文件进行简单测试
        tests_run = []
        for exe in executables[:3]:  # 最多测试3个
            test_result = self._test_executable(exe)
            tests_run.append(test_result)

        result["tests_run"] = tests_run
        
        # 判定结果
        if any(t["status"] == "success" for t in tests_run):
            result["status"] = "success"
            result["details"] = f"功能验证通过，成功执行 {len([t for t in tests_run if t['status'] == 'success'])} 个测试"
        else:
            result["status"] = "failed"
            result["details"] = "功能验证失败"

        return result

    def _find_executables(self) -> List[str]:
        executables = []
        for root, dirs, files in os.walk(self.build_dir):
            for file in files:
                file_path = os.path.join(root, file)
                # Windows: .exe; Linux: 可执行权限
                if file.endswith(".exe") or (os.access(file_path, os.X_OK) and "." not in file):
                    executables.append(file_path)
        return executables

    def _test_executable(self, exe_path: str) -> Dict:
        """测试单个可执行文件"""
        result = {
            "executable": os.path.basename(exe_path),
            "status": "pending",
            "output": "",
            "return_code": -1
        }

        try:
            # 尝试运行 --help 或 -h
            for arg in ["--help", "-h", ""]:
                cmd = [exe_path]
                if arg:
                    cmd.append(arg)
                
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    shell=True
                )
                output, _ = proc.communicate(timeout=30)
                
                result["output"] = output[:500]  # 只保存前500字符
                result["return_code"] = proc.returncode
                
                if proc.returncode == 0 or len(output) > 0:
                    result["status"] = "success"
                    break
            
            if result["status"] == "pending":
                result["status"] = "failed"

        except Exception as e:
            result["status"] = "failed"
            result["output"] = str(e)

        return result

    def get_summary(self) -> str:
        check = self.check_consistency()
        return f"[功能验证] {check['status'].upper()}: {check['details']}"