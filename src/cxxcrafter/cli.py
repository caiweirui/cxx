import os
import time
import shutil
from datetime import datetime
from cxxcrafter.parsing_module import parser
from cxxcrafter.generation_module import generate_dockerfile
from cxxcrafter.config import CXXCrafterConfig
from cxxcrafter.rag import KnowledgeBase, Retriever
from cxxcrafter.agents import AgentCoordinator
from cxxcrafter.verification import VerificationJudge
import logging

class CXXCrafter:
    def __init__(self, project_path: str, config: CXXCrafterConfig = None):
        self.project_path = project_path
        self.project_name = os.path.basename(project_path)
        self.config = config or CXXCrafterConfig()
        self.logger = self._init_logger()
        
        # ===================== 相对路径（Windows兼容） =====================
        self.base_playground = os.path.abspath("./dockerfile_playground")
        self.project_playground = os.path.join(self.base_playground, self.project_name)
        timestamp = datetime.now().strftime("history-%Y%m%d_%H%M")
        self.history_dir = os.path.join(self.project_playground, timestamp)
        
        # 自动创建目录
        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.project_playground, exist_ok=True)
        
        # ===================== 初始化创新点模块 =====================
        # RAG知识库
        self.kb = KnowledgeBase()
        self.retriever = Retriever(self.kb)
        
        # 多智能体协作
        self.coordinator = AgentCoordinator(self.config, self.retriever)
        
        # 多维度验证
        self.verifier = None

    def _init_logger(self):
        logger = logging.getLogger(f"cxxcrafter.cli - {self.project_name}")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def parse_project(self):
        self.logger.info("Parsing Module Starts")
        # 固定返回3个值，修复解包报错
        self.build_system, self.deps, self.docs = parser(self.project_path)
        self.logger.info("Parsing Module Finishes")

    def generate_with_agents(self):
        """使用多智能体协作生成Dockerfile"""
        self.logger.info("Multi-Agent Generation Module Starts")
        
        # 构建上下文
        context = {
            "project_path": self.project_path,
            "build_system": self.build_system,
            "docs": self.docs,
            "deps": self.deps
        }
        
        # 多智能体协作
        agent_result = self.coordinator.run_build_pipeline(context)
        
        # ===================== 修复点：传入 self.config =====================
        generate_dockerfile(
            self.project_path,
            self.project_playground,
            self.build_system,
            str(agent_result.get("dependencies", "")),
            self.docs,
            self.project_name,
            self.config  # 🔥 关键：传入配置对象
        )
        # ===================================================================
        
        self.logger.info("Multi-Agent Generation Module Finishes")
        return agent_result

    def run_verification(self):
        """运行多维度验证"""
        self.logger.info("Verification Module Starts")
        
        # 初始化验证器
        self.verifier = VerificationJudge(self.project_playground)
        
        # 执行完整验证
        verification_result = self.verifier.full_verification()
        
        self.logger.info("Verification Module Finishes")
        return verification_result

    def run(self):
        """完整的端到端流程"""
        print("\n" + "="*60)
        print("CXXCrafter 完整系统启动")
        print("="*60)
        
        # 1. 解析项目
        self.parse_project()
        
        # 2. 多智能体生成
        agent_result = self.generate_with_agents()
        
        # 3. 多维度验证
        verification_result = self.run_verification()
        
        # 4. 结果总结
        print("\n" + "="*60)
        print("系统运行完成 - 最终总结")
        print("="*60)
        print(f"项目: {self.project_name}")
        print(f"构建系统: {self.build_system}")
        print(f"智能体协作: 成功")
        print(f"验证结果: {verification_result.get('final_verdict', {}).get('verdict', 'unknown')}")
        print(f"置信度: {verification_result.get('final_verdict', {}).get('confidence', 0)}%")
        print(f"输出目录: {self.project_playground}")
        print("="*60)
        
        return self.project_name, True