from .product_checker import ProductChecker
from .test_runner import TestRunner
from .consistency_checker import ConsistencyChecker
from typing import Dict

class VerificationJudge:
    def __init__(self, build_dir: str):
        self.build_dir = build_dir
        self.product_checker = ProductChecker(build_dir)
        self.test_runner = TestRunner(build_dir)
        self.consistency_checker = ConsistencyChecker(build_dir)

    def full_verification(self) -> Dict:
        """执行完整的多维度验证"""
        print("="*50)
        print("多维度验证模块启动")
        print("="*50)

        # 1. 构建产物检查
        print("\n[1/3] 执行构建产物检查...")
        product_result = self.product_checker.check_products()
        print(self.product_checker.get_summary())

        # 2. 单元测试执行
        print("\n[2/3] 执行单元测试...")
        test_result = self.test_runner.run_tests()
        print(self.test_runner.get_summary())

        # 3. 功能一致性验证
        print("\n[3/3] 执行功能一致性验证...")
        consistency_result = self.consistency_checker.check_consistency()
        print(self.consistency_checker.get_summary())

        # 综合判定
        final_verdict = self._make_verdict(product_result, test_result, consistency_result)

        print("\n" + "="*50)
        print("多维度验证完成")
        print("="*50)
        print(f"\n最终判定: {final_verdict['verdict'].upper()}")
        print(f"置信度: {final_verdict['confidence']}%")
        print(f"判定理由: {final_verdict['reason']}")

        return {
            "product_check": product_result,
            "test_run": test_result,
            "consistency_check": consistency_result,
            "final_verdict": final_verdict
        }

    def _make_verdict(self, product: Dict, test: Dict, consistency: Dict) -> Dict:
        """综合判定逻辑"""
        score = 0
        reasons = []

        # 产物检查权重：40分
        if product["status"] == "success":
            score += 40
            reasons.append("构建产物检查通过")
        else:
            reasons.append("构建产物检查失败")

        # 单元测试权重：35分
        if test["status"] == "success":
            score += 35
            reasons.append("单元测试执行成功")
        elif test["status"] == "skipped":
            score += 15
            reasons.append("单元测试跳过")
        else:
            reasons.append("单元测试执行失败")

        # 功能验证权重：25分
        if consistency["status"] == "success":
            score += 25
            reasons.append("功能一致性验证通过")
        elif consistency["status"] == "skipped":
            score += 10
            reasons.append("功能验证跳过")
        else:
            reasons.append("功能一致性验证失败")

        # 最终判定
        if score >= 70:
            verdict = "success"
        elif score >= 40:
            verdict = "partial"
        else:
            verdict = "failed"

        return {
            "verdict": verdict,
            "confidence": score,
            "reason": "; ".join(reasons)
        }