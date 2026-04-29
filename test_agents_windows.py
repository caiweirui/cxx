import os
import sys

# 添加src到路径
sys.path.insert(0, os.path.abspath('./src'))

from cxxcrafter.agents import AgentCoordinator

def test_agents():
    print("="*50)
    print("Windows多智能体协作层测试")
    print("="*50)
    
    # 配置API（请确保已设置环境变量）
    if not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = "sk_praWrYfE0pzxL3aaO67kkgUk3GOpH3Q_T5AJyOJ5t4s"
        os.environ["OPENAI_BASE_URL"] = "https://api.jiekou.ai/openai"
    
    # 初始化调度器
    coordinator = AgentCoordinator()
    
    # 模拟项目上下文
    test_context = {
        "project_path": "./project/8cc",
        "build_system": "Makefile",
        "docs": "标准C项目，使用make构建，依赖gcc"
    }
    
    # 运行构建流水线
    result = coordinator.run_build_pipeline(test_context)
    
    # 输出结果
    print("\n最终结果:")
    print(f"依赖解析: {result.get('dependencies', {})}")
    print(f"构建命令: {result.get('build_commands', {})}")
    
    print("\n✅ 多智能体协作层测试完成！")

if __name__ == "__main__":
    test_agents()