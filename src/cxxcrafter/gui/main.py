import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import os
import sys
import threading
from io import StringIO

# 添加src到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from cxxcrafter.config import CXXCrafterConfig, SUPPORTED_MODELS
from cxxcrafter.cli import CXXCrafter

class StdoutRedirector(StringIO):
    """重定向stdout到GUI文本框"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def write(self, string):
        self.text_widget.configure(state="normal")
        self.text_widget.insert(tk.END, string)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")
        self.flush()

    def flush(self):
        pass

class CXXCrafterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CXXCrafter - 多智能体C/C++ Dockerfile生成系统")
        self.root.geometry("1000x700")
        self.root.minsize(900, 600)

        # 初始化配置
        self.config = CXXCrafterConfig()
        self.running = False

        # 创建Tab页
        self.tab_control = ttk.Notebook(root)
        
        # 1. 配置Tab
        self.tab_config = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_config, text="🔑 配置中心")
        self._init_config_tab()

        # 2. 运行Tab
        self.tab_run = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_run, text="🚀 运行控制")
        self._init_run_tab()

        # 3. 结果Tab
        self.tab_result = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_result, text="📊 结果查看")
        self._init_result_tab()

        # 4. 关于Tab
        self.tab_about = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_about, text="ℹ️ 关于")
        self._init_about_tab()

        self.tab_control.pack(expand=1, fill="both")

        # 重定向stdout
        self.redirector = StdoutRedirector(self.log_text)
        sys.stdout = self.redirector
        sys.stderr = self.redirector

    def _init_config_tab(self):
        """初始化配置Tab（支持每个智能体独立配置）"""
        # 创建滚动区域（内容较多）
        canvas = tk.Canvas(self.tab_config)
        scrollbar = ttk.Scrollbar(self.tab_config, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ===================== 全局配置区域 =====================
        global_frame = ttk.LabelFrame(scrollable_frame, text="🌐 全局配置（兜底）")
        global_frame.pack(fill="x", padx=20, pady=10)

        # 全局API Key
        ttk.Label(global_frame, text="全局API Key:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.global_api_key_var = tk.StringVar()
        self.global_api_key_entry = ttk.Entry(global_frame, textvariable=self.global_api_key_var, width=60, show="*")
        self.global_api_key_entry.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        # 全局Base URL
        ttk.Label(global_frame, text="全局Base URL:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.global_base_url_var = tk.StringVar(value="https://api.jiekou.ai/openai")
        self.global_base_url_entry = ttk.Entry(global_frame, textvariable=self.global_base_url_var, width=60)
        self.global_base_url_entry.grid(row=1, column=1, padx=10, pady=5, sticky="w")

        # 保存全局配置
        def save_global_config():
            try:
                self.config.set_global_api_key(self.global_api_key_var.get().strip())
                self.config.set_global_base_url(self.global_base_url_var.get().strip())
                messagebox.showinfo("成功", "全局配置已保存！")
            except ValueError as e:
                messagebox.showerror("错误", str(e))

        ttk.Button(global_frame, text="保存全局配置", command=save_global_config).grid(row=2, column=1, padx=10, pady=10, sticky="e")

        # ===================== 智能体独立配置区域 =====================
        # 所有支持的模型列表
        all_models = []
        for provider, models in SUPPORTED_MODELS.items():
            all_models.extend(models)
        all_models = sorted(list(set(all_models)))

        # 智能体配置
        agent_types = [
            ("dependency", "🔍 依赖解析智能体"),
            ("build", "🏗️  构建适配智能体"),
            ("error", "🔧 错误诊断智能体"),
            ("coordinator", "🎯 调度器")
        ]

        # 存储每个智能体的配置变量
        self.agent_vars = {}

        for i, (agent_key, agent_name) in enumerate(agent_types):
            agent_frame = ttk.LabelFrame(scrollable_frame, text=agent_name)
            agent_frame.pack(fill="x", padx=20, pady=10)

            # 模型选择
            ttk.Label(agent_frame, text="模型:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
            model_var = tk.StringVar(value=self.config.agent_configs[agent_key].model)
            model_combo = ttk.Combobox(agent_frame, textvariable=model_var, values=all_models, width=45, state="readonly")
            model_combo.grid(row=0, column=1, padx=10, pady=10, sticky="w")

            # 独立API Key（可选）
            ttk.Label(agent_frame, text="独立API Key (可选):").grid(row=1, column=0, padx=10, pady=5, sticky="w")
            api_key_var = tk.StringVar()
            api_key_entry = ttk.Entry(agent_frame, textvariable=api_key_var, width=45, show="*")
            api_key_entry.grid(row=1, column=1, padx=10, pady=5, sticky="w")
            ttk.Label(agent_frame, text="留空则使用全局配置", foreground="gray").grid(row=1, column=2, padx=5, pady=5, sticky="w")

            # 独立Base URL（可选）
            ttk.Label(agent_frame, text="独立Base URL (可选):").grid(row=2, column=0, padx=10, pady=5, sticky="w")
            base_url_var = tk.StringVar()
            base_url_entry = ttk.Entry(agent_frame, textvariable=base_url_var, width=45)
            base_url_entry.grid(row=2, column=1, padx=10, pady=5, sticky="w")
            ttk.Label(agent_frame, text="留空则使用全局配置", foreground="gray").grid(row=2, column=2, padx=5, pady=5, sticky="w")

            # 保存变量
            self.agent_vars[agent_key] = {
                "model": model_var,
                "api_key": api_key_var,
                "base_url": base_url_var
            }

        # ===================== 底部操作按钮 =====================
        button_frame = ttk.Frame(scrollable_frame)
        button_frame.pack(fill="x", padx=20, pady=20)

        def save_all_agent_configs():
            """保存所有智能体配置"""
            success = True
            for agent_key, vars in self.agent_vars.items():
                ok = self.config.set_agent_config(
                    agent_type=agent_key,
                    model=vars["model"].get().strip(),
                    api_key=vars["api_key"].get().strip(),
                    base_url=vars["base_url"].get().strip()
                )
                if not ok:
                    success = False
            
            if success:
                messagebox.showinfo("成功", "所有智能体配置已保存！")
            else:
                messagebox.showwarning("警告", "部分配置保存失败，请检查")

        def reset_to_recommended():
            """重置为推荐配置"""
            self.config.reset_to_recommended()
            # 更新GUI
            for agent_key, vars in self.agent_vars.items():
                vars["model"].set(self.config.agent_configs[agent_key].model)
                vars["api_key"].set("")
                vars["base_url"].set("")
            self.global_api_key_var.set("")
            self.global_base_url_var.set("https://api.jiekou.ai/openai")
            messagebox.showinfo("成功", "已重置为推荐配置！")

        ttk.Button(button_frame, text="💾 保存所有配置", command=save_all_agent_configs).pack(side="left", padx=10)
        ttk.Button(button_frame, text="🔄 一键重置推荐配置", command=reset_to_recommended).pack(side="left", padx=10)
        ttk.Button(button_frame, text="📋 查看配置摘要", command=lambda: messagebox.showinfo("配置摘要", self.config.get_config_summary())).pack(side="left", padx=10)
    def _init_run_tab(self):
        """初始化运行Tab"""
        # 项目选择区域
        project_frame = ttk.LabelFrame(self.tab_run, text="项目选择")
        project_frame.pack(fill="x", padx=20, pady=10)

        # 单个项目路径
        ttk.Label(project_frame, text="单个项目路径:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.single_repo_var = tk.StringVar()
        self.single_repo_entry = ttk.Entry(project_frame, textvariable=self.single_repo_var, width=50)
        self.single_repo_entry.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        
        def select_single_repo():
            path = filedialog.askdirectory(title="选择项目文件夹")
            if path:
                self.single_repo_var.set(path)
                self.repo_list_var.set("")

        ttk.Button(project_frame, text="浏览", command=select_single_repo).grid(row=0, column=2, padx=10, pady=10)

        # 项目列表文件
        ttk.Label(project_frame, text="项目列表文件:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.repo_list_var = tk.StringVar()
        self.repo_list_entry = ttk.Entry(project_frame, textvariable=self.repo_list_var, width=50)
        self.repo_list_entry.grid(row=1, column=1, padx=10, pady=5, sticky="w")
        
        def select_repo_list():
            path = filedialog.askopenfilename(title="选择项目列表文件", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if path:
                self.repo_list_var.set(path)
                self.single_repo_var.set("")

        ttk.Button(project_frame, text="浏览", command=select_repo_list).grid(row=1, column=2, padx=10, pady=5)

        # 运行控制区域
        control_frame = ttk.Frame(self.tab_run)
        control_frame.pack(fill="x", padx=20, pady=5)

        def run_task():
            if self.running:
                messagebox.showwarning("提示", "任务正在运行中！")
                return
            
            # 🔥 修复点：检查全局API Key
            if not self.config.global_api_key:
                try:
                    self.config.set_global_api_key(self.global_api_key_var.get().strip())
                except:
                    messagebox.showerror("错误", "请先设置有效的全局API Key！")
                    return
            
            # 检查项目路径
            single_repo = self.single_repo_var.get().strip()
            repo_list = self.repo_list_var.get().strip()
            
            if not single_repo and not repo_list:
                messagebox.showerror("错误", "请选择项目路径或项目列表文件！")
                return
            
            # 清空日志
            self.log_text.configure(state="normal")
            self.log_text.delete(1.0, tk.END)
            self.log_text.configure(state="disabled")

            # 子线程运行任务
            def task():
                self.running = True
                self.run_btn.configure(state="disabled")
                self.stop_btn.configure(state="normal")
                
                try:
                    if single_repo:
                        print(f"开始处理单个项目: {single_repo}")
                        cxxcrafter = CXXCrafter(single_repo, self.config)
                        cxxcrafter.run()
                    elif repo_list:
                        print(f"开始处理项目列表: {repo_list}")
                        with open(repo_list, 'r', encoding='utf-8') as f:
                            repos = [line.strip() for line in f if line.strip()]
                        
                        print(f"共 {len(repos)} 个项目")
                        for repo in repos:
                            if not self.running:
                                break
                            print(f"\n{'='*60}")
                            print(f"===== 正在处理: {repo} =====")
                            print('='*60)
                            
                            repo_path = os.path.abspath(repo)
                            if not os.path.exists(repo_path):
                                print(f"❌ 项目路径不存在: {repo_path}")
                                continue
                            
                            cxxcrafter = CXXCrafter(repo_path, self.config)
                            cxxcrafter.run()
                    
                    print("\n✅ 所有任务处理完成！")
                    messagebox.showinfo("完成", "所有任务处理完成！")
                
                except Exception as e:
                    print(f"\n❌ 任务运行失败: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    messagebox.showerror("错误", f"任务运行失败: {str(e)}")
                
                finally:
                    self.running = False
                    self.run_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")

            threading.Thread(target=task, daemon=True).start()

        def stop_task():
            self.running = False
            print("\n⏹️  任务已停止")
            self.stop_btn.configure(state="disabled")
            self.run_btn.configure(state="normal")

        self.run_btn = ttk.Button(control_frame, text="🚀 开始运行", command=run_task)
        self.run_btn.pack(side="left", padx=20, pady=10)

        self.stop_btn = ttk.Button(control_frame, text="⏹️  停止运行", command=stop_task, state="disabled")
        self.stop_btn.pack(side="left", padx=10, pady=10)

        # 日志区域
        log_frame = ttk.LabelFrame(self.tab_run, text="运行日志")
        log_frame.pack(fill="both", expand=1, padx=20, pady=10)

        self.log_text = scrolledtext.ScrolledText(log_frame, state="disabled", wrap=tk.WORD)
        self.log_text.pack(fill="both", expand=1, padx=10, pady=10)

    def _init_result_tab(self):
        """初始化结果Tab"""
        # 输出目录区域
        dir_frame = ttk.Frame(self.tab_result)
        dir_frame.pack(fill="x", padx=20, pady=10)

        self.output_dir_var = tk.StringVar(value=os.path.abspath("./dockerfile_playground"))
        ttk.Label(dir_frame, text="输出目录:").pack(side="left", padx=5)
        ttk.Entry(dir_frame, textvariable=self.output_dir_var, width=60, state="readonly").pack(side="left", padx=5)
        
        def open_output_dir():
            path = self.output_dir_var.get()
            if not os.path.exists(path):
                messagebox.showwarning("提示", "输出目录不存在，请先运行任务！")
                return
            
            # ===================== 跨平台打开目录 =====================
            import sys
            import subprocess
            import platform
            
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":  # macOS
                subprocess.call(["open", path])
            else:  # Linux (Ubuntu)
                subprocess.call(["xdg-open", path])
            # ===========================================================

        ttk.Button(dir_frame, text="📂 打开输出目录", command=open_output_dir).pack(side="left", padx=10)

        # Dockerfile查看区域
        df_frame = ttk.LabelFrame(self.tab_result, text="生成的Dockerfile")
        df_frame.pack(fill="both", expand=1, padx=20, pady=10)

        # 项目选择下拉框
        def refresh_project_list():
            output_dir = self.output_dir_var.get()
            if not os.path.exists(output_dir):
                return []
            projects = [d for d in os.listdir(output_dir) if os.path.isdir(os.path.join(output_dir, d))]
            self.project_combo['values'] = sorted(projects)
            return projects

        select_frame = ttk.Frame(df_frame)
        select_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(select_frame, text="选择项目:").pack(side="left", padx=5)
        self.selected_project = tk.StringVar()
        self.project_combo = ttk.Combobox(select_frame, textvariable=self.selected_project, width=40, state="readonly")
        self.project_combo.pack(side="left", padx=5)

        def load_dockerfile():
            project = self.selected_project.get()
            if not project:
                messagebox.showwarning("提示", "请先选择项目！")
                return
            
            df_path = os.path.join(self.output_dir_var.get(), project, "Dockerfile")
            if not os.path.exists(df_path):
                messagebox.showwarning("提示", "该项目未生成Dockerfile！")
                return
            
            with open(df_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.df_text.configure(state="normal")
            self.df_text.delete(1.0, tk.END)
            self.df_text.insert(tk.END, content)
            self.df_text.configure(state="disabled")

        ttk.Button(select_frame, text="🔄 刷新项目列表", command=refresh_project_list).pack(side="left", padx=5)
        ttk.Button(select_frame, text="📄 加载Dockerfile", command=load_dockerfile).pack(side="left", padx=5)

        # Dockerfile内容显示
        self.df_text = scrolledtext.ScrolledText(df_frame, state="disabled", wrap=tk.NONE)
        self.df_text.pack(fill="both", expand=1, padx=10, pady=10)

    def _init_about_tab(self):
        """初始化关于Tab"""
        about_text = """
CXXCrafter - 多智能体C/C++ Dockerfile生成系统

版本：v1.2.0
核心功能：
1. 多智能体协作架构，精准解析C/C++项目依赖
2. RAG知识库，复用历史构建经验
3. 多维度验证，确保构建结果可靠
4. 全Windows适配，可视化操作

支持的模型：OpenAI、Anthropic、Google全系列
        """
        ttk.Label(self.tab_about, text=about_text, font=("微软雅黑", 12), justify="left").pack(padx=50, pady=50)

# 启动GUI
def main():
    root = tk.Tk()
    app = CXXCrafterGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()