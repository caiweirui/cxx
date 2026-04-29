import os
import shutil
from typing import Optional
from cxxcrafter.config import CXXCrafterConfig

def generate_dockerfile(
    project_path, 
    playground_path, 
    build_system, 
    deps, 
    docs, 
    project_name,
    config: Optional[CXXCrafterConfig] = None
):
    """生成Dockerfile的核心函数（Windows权限修复版）"""
    # 创建工作目录
    target_dir = playground_path
    os.makedirs(target_dir, exist_ok=True)
    
    # 复制项目文件（跳过 .git 目录，避免Windows权限问题）
    dest_project = os.path.join(target_dir, "project")
    
    # ===================== 修复点1：跨平台安全删除旧目录 =====================
    if os.path.exists(dest_project):
        try:
            # 跨平台：尝试正常删除
            shutil.rmtree(dest_project, ignore_errors=False)
        except PermissionError:
            # Ubuntu/Windows权限问题：强制删除
            try:
                import stat
                # 递归修改权限为可写
                for root, dirs, files in os.walk(dest_project):
                    for d in dirs:
                        os.chmod(os.path.join(root, d), stat.S_IRWXU)
                    for f in files:
                        os.chmod(os.path.join(root, f), stat.S_IRWXU)
                shutil.rmtree(dest_project, ignore_errors=True)
            except:
                # 最后兜底：重命名
                try:
                    old_dir = dest_project + ".old"
                    if os.path.exists(old_dir):
                        shutil.rmtree(old_dir, ignore_errors=True)
                    os.rename(dest_project, old_dir)
                except:
                    pass
    # ===================================================================
    # ===================================================================
    
    # ===================== 修复点2：复制时跳过 .git 目录 =====================
    def ignore_git(dir, files):
        return ['.git'] if '.git' in files else []
    
    shutil.copytree(
        project_path, 
        dest_project, 
        ignore=ignore_git,  # 关键：跳过 .git 目录
        symlinks=True,
        dirs_exist_ok=True
    )
    # ===================================================================
    
    # 构建提示词
    prompt = f"""
    你是C/C++项目构建专家，生成Ubuntu环境的Dockerfile。
    项目构建系统：{build_system}
    依赖：{deps}
    构建说明：{docs}
    只返回纯净的Dockerfile代码，无多余文字。
    """
    
    # 调用模型生成（使用传入的config）
    from cxxcrafter.llm.bot import GPTBot
    
    if config:
        bot = GPTBot(system_prompt="你是专业的Dockerfile生成助手", config=config)
    else:
        bot = GPTBot(system_prompt="你是专业的Dockerfile生成助手")
    
    dockerfile_content = bot.inference(prompt)
    
    # 写入Dockerfile
    dockerfile_path = os.path.join(target_dir, "Dockerfile")
    with open(dockerfile_path, "w", encoding="utf-8") as f:
        f.write(dockerfile_content)
    
    print(f"✅ {project_name} Dockerfile 生成完成：{dockerfile_path}")