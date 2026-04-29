import os
import re
from cxxcrafter.llm.bot import GPTBot

def match_doc(project_path):
    readme_files = []
    # 遍历查找README文件（跨平台）
    for root, dirs, files in os.walk(project_path):
        for file in files:
            if re.search(r'README|readme', file):
                readme_files.append(os.path.join(root, file))
    
    if not readme_files:
        return ""
    
    # LLM选择最有用的文档
    return llm_help_choose_helpful_doc(readme_files, project_path)

def llm_help_choose_helpful_doc(files, directory):
    bot = GPTBot(system_prompt="你是一个C/C++项目构建助手，选择最有用的README文档")
    files_text = "\n".join([f"{i+1}. {os.path.basename(f)}" for i, f in enumerate(files)])
    prompt = f"请从以下文件中选择最适合构建的README文件，只返回文件名：\n{files_text}"
    
    choose = bot.inference(prompt)
    for f in files:
        if choose in os.path.basename(f):
            return get_helpful_content([f], bot)
    
    return get_helpful_content([files[0]], bot)

def get_helpful_content(files, bot):
    content = ""
    for f in files:
        try:
            # Windows UTF-8编码读取
            with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                content += fp.read() + "\n"
        except:
            continue
    
    prompt = "提取C/C++项目的构建依赖、编译命令、系统要求，返回纯文本总结："
    response = bot.inference(prompt + '\n' + content[:10000])
    return response