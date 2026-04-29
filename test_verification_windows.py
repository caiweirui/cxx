import os
import sys

# 添加src到路径
sys.path.insert(0, os.path.abspath('./src'))

from cxxcrafter.verification import VerificationJudge

def test_verification():
    print("="*50)
    print("Windows多维度验证模块测试")
    print("="*50)
    
    # 测试目录（使用项目中的一个示例目录）
    test_build_dir = "./project/8cc"
    
    if not os.path.exists(test_build_dir):
        print(f"❌ 测试目录不存在: {test_build_dir}")
        print("请先运行项目克隆脚本下载测试项目")
        return
    
    # 初始化验证器
    judge = VerificationJudge(test_build_dir)
    
    # 执行完整验证
    result = judge.full_verification()
    
    print("\n✅ 多维度验证模块测试完成！")

if __name__ == "__main__":
    test_verification()