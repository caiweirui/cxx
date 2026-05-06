#!/usr/bin/env python3
import os
import sys
import argparse

from cxxcrafter.cli import CXXCrafter
from cxxcrafter.config import CXXCrafterConfig

def build_one_repo(repo_path, config):
    try:
        repo_path = os.path.abspath(repo_path)
        cxxcrafter = CXXCrafter(repo_path, config)
        project_name, flag = cxxcrafter.run()
        return project_name, flag
    except Exception as e:
        print(f"处理 {repo_path} 失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return None, False

def run_with_file_list(file_path, config):
    with open(file_path, 'r', encoding='utf-8') as f:
        repos = [line.strip() for line in f if line.strip()]

    print(f"开始串行处理 {len(repos)} 个项目...")
    for repo in repos:
        print(f"\n{'=' * 60}")
        print(f"===== 正在处理: {repo} =====")
        print('=' * 60)
        build_one_repo(repo, config)

def interactive_config():
    """交互式配置"""
    print("=" * 60)
    print("CXXCrafter 交互式配置")
    print("=" * 60)

    config = CXXCrafterConfig()

    print("\n🔑 请输入你的API Key:")
    api_key = input("> ").strip()
    try:
        config.set_api_key(api_key)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print("\n🌐 API Base URL (默认: https://api.jiekou.ai/openai)")
    base_url = input("直接回车使用默认，或输入自定义URL: ").strip()
    if base_url:
        config.set_base_url(base_url)

    print("\n🔁 最大修复轮次 (默认 2):")
    max_rounds = input("> ").strip()
    if max_rounds:
        config.set_max_rounds(max_rounds)

    print("\n📦 缓存目录 (默认 ./data/cache):")
    cache_dir = input("> ").strip()
    if cache_dir:
        config.set_cache_dir(cache_dir)

    print("\n🤖 是否自定义智能体模型? (y/n, 默认n)")
    customize = input("> ").strip().lower()

    if customize == 'y':
        print("\n可用智能体: dependency, build, error, coordinator")
        print("输入 'done' 结束")

        while True:
            agent = input("\n智能体类型 (或 'done'): ").strip().lower()
            if agent == 'done':
                break

            model = input(f"{agent} 模型: ").strip()
            config.set_agent_model(agent, model)

    print(config.get_config_summary())
    return config

def main():
    parser = argparse.ArgumentParser(description="CXXCrafter: 多智能体 C/C++ 项目 Dockerfile 生成系统")
    parser.add_argument('--repo', type=str, help="单个项目路径")
    parser.add_argument('--repo-list', type=str, help="项目列表文件")
    parser.add_argument('--config', action='store_true', help="使用交互式配置")
    parser.add_argument('--api-key', type=str, help="直接指定API Key")
    parser.add_argument('--base-url', type=str, help="直接指定API Base URL")
    parser.add_argument('--max-rounds', type=int, help="最大修复轮次")
    parser.add_argument('--cache-dir', type=str, help="缓存目录")
    parser.add_argument('--base-image', type=str, help="默认基础镜像")

    args = parser.parse_args()

    if args.config:
        config = interactive_config()
    else:
        config = CXXCrafterConfig()

        if args.api_key:
            config.set_api_key(args.api_key)
        else:
            config.load_from_env()
            if not config.global_api_key:
                print("❌ 请提供API Key！使用 --api-key 或 --config")
                sys.exit(1)

        if args.base_url:
            config.set_base_url(args.base_url)
        if args.max_rounds:
            config.set_max_rounds(args.max_rounds)
        if args.cache_dir:
            config.set_cache_dir(args.cache_dir)
        if args.base_image:
            config.set_docker_base_image(args.base_image)

    if args.repo:
        build_one_repo(args.repo, config)
    elif args.repo_list:
        run_with_file_list(args.repo_list, config)
    else:
        print("请提供 --repo 或 --repo-list")
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()