import os
from typing import Dict, List

class ProductChecker:
    def __init__(self, build_dir: str):
        self.build_dir = os.path.abspath(build_dir)
        self.common_exts = [".exe", ".dll", ".so", ".a", ".lib", ".dylib"]
        self.common_names = ["a.out", "main", "test", "demo", "example"]

    def check_products(self) -> Dict:
        """检查构建产物"""
        result = {
            "status": "pending",
            "products_found": [],
            "total_size_mb": 0.0,
            "details": ""
        }

        if not os.path.exists(self.build_dir):
            result["status"] = "failed"
            result["details"] = f"构建目录不存在: {self.build_dir}"
            return result

        # 遍历查找构建产物
        products = []
        total_size = 0

        for root, dirs, files in os.walk(self.build_dir):
            for file in files:
                # 检查扩展名或常见名称
                if any(file.endswith(ext) for ext in self.common_exts) or \
                   any(name in file.lower() for name in self.common_names):
                    file_path = os.path.join(root, file)
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    
                    products.append({
                        "path": file_path,
                        "name": file,
                        "size_mb": round(size_mb, 2)
                    })
                    total_size += size_mb

        result["products_found"] = products
        result["total_size_mb"] = round(total_size, 2)

        if products:
            result["status"] = "success"
            result["details"] = f"找到 {len(products)} 个构建产物，总大小 {result['total_size_mb']} MB"
        else:
            result["status"] = "failed"
            result["details"] = "未找到任何构建产物"

        return result

    def get_summary(self) -> str:
        check = self.check_products()
        return f"[产物检查] {check['status'].upper()}: {check['details']}"