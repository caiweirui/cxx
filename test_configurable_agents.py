import os
import sys

# 添加src到路径
sys.path.insert(0, os.path.abspath('./src'))

from cxxcrafter.config import CXXCrafterConfig
from cxxcrafter.agents import AgentCoordinator

def test_configurable_system():
    print("="*50)
    print("可配置多智能体系统测试")
    print("="*50)
    
    # 1. 初始化配置
    config = CXXCrafterConfig()
    
    # 2. 用户输入API Key
    print("\n🔑 请输入你的API Key:")
    api_key = input("> ").strip()
    try:
        config.set_api_key(api_key)
    except ValueError as e:
        print(f"❌ {e}")
        return
    
    # 3. 可选：设置Base URL
    print("\n🌐 是否使用自定义API Base URL? (默认: https://api.jiekou.ai/openai)")
    use_custom_url = input("输入y确认，其他跳过: ").strip().lower()
    if use_custom_url == 'y':
        base_url = input("请输入Base URL: ").strip()
        config.set_base_url(base_url)
    
    # 4. 可选：自定义智能体模型
    print("\n🤖 是否自定义智能体模型? (y/n)")
    customize_models = input("> ").strip().lower()
    
    if customize_models == 'y':
        print("\n可用的智能体类型: dependency, build, error, coordinator")
        print("输入 'done' 结束设置")
        
        while True:
            agent_type = input("\n请输入智能体类型 (或 'done'): ").strip().lower()
            if agent_type == 'done':
                break
            
            print(f"\n请输入 {agent_type} 智能体的模型:")
            model = input("> ").strip()
            
            config.set_agent_model(agent_type, model)
    
    # 5. 显示配置摘要
    print(config.get_config_summary())
    
    # 6. 确认并运行
    print("\n✅ 配置完成！是否开始测试? (y/n)")
    confirm = input("> ").strip().lower()
    
    if confirm != 'y':
        print("测试已取消")
        return
    
    # 7. 初始化调度器并运行
    try:
        coordinator = AgentCoordinator(config)
        
        # 模拟项目上下文
        test_context = {
            "project_path": "./project/8cc",
            "build_system": "Makefile",
            "docs": "标准C项目，使用make构建，依赖gcc"
        }
        
        # 运行构建流水线
        result = coordinator.run_build_pipeline(test_context)
        
        print("\n✅ 可配置多智能体系统测试完成！")
        
    except Exception as e:
        print(f"\n❌ 运行失败: {e}")

if __name__ == "__main__":
    test_configurable_system()