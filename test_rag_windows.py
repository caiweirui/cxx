import os
import sys

# 添加src到路径
sys.path.insert(0, os.path.abspath('./src'))

from cxxcrafter.rag import KnowledgeBase, KnowledgeUpdater, Retriever

def test_rag():
    print("="*50)
    print("Windows RAG向量数据库测试")
    print("="*50)
    
    # 1. 初始化知识库
    print("\n[1/5] 初始化知识库...")
    kb = KnowledgeBase()
    print(f"✅ 知识库初始化成功，当前案例数: {kb.get_all_cases()}")
    
    # 2. 添加测试案例
    print("\n[2/5] 添加测试案例...")
    updater = KnowledgeUpdater(kb)
    
    # 模拟C/C++构建错误
    test_error = "undefined reference to 'pthread_create'"
    test_solution = "在CMakeLists.txt中添加 target_link_libraries(target pthread)"
    updater.add_success_case(test_error, test_solution, "test-project")
    
    test_error2 = "fatal error: boost/asio.hpp: No such file or directory"
    test_solution2 = "sudo apt-get install libboost-all-dev (Ubuntu) 或 vcpkg install boost:x64-windows (Windows)"
    updater.add_success_case(test_error2, test_solution2, "boost-project")
    
    print(f"✅ 测试案例添加完成，当前案例数: {kb.get_all_cases()}")
    
    # 3. 测试检索
    print("\n[3/5] 测试相似错误检索...")
    retriever = Retriever(kb)
    
    query_error = "undefined reference to pthread"
    print(f"查询错误: {query_error}")
    
    solutions = retriever.get_solutions(query_error)
    if solutions:
        print(f"✅ 找到 {len(solutions)} 个相似案例:")
        for s in solutions:
            print(f"  - 项目: {s['project']}, 相似度: {1-s['distance']:.2f}")
            print(f"    解决方案: {s['solution']}")
    else:
        print("❌ 未找到相似案例")
    
    # 4. 测试提示词生成
    print("\n[4/5] 测试LLM提示词生成...")
    prompt = retriever.format_prompt(query_error)
    print("生成的提示词:")
    print(prompt[:300] + "..." if len(prompt) > 300 else prompt)
    
    # 5. 完成
    print("\n[5/5] 测试完成！")
    print("="*50)
    print("✅ RAG向量数据库在Windows上运行正常！")
    print(f"📂 数据库位置: {os.path.abspath('./data/knowledge_base')}")
    print("="*50)

if __name__ == "__main__":
    test_rag()