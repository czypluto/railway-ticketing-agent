"""全局配置：环境、模型路由、外部服务地址。模型名一律出自此处，业务代码不写死。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 可选加载 .env（项目根或本目录），装了 python-dotenv 才生效
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT / ".env")
except Exception:
    pass

ENV = os.getenv("ENV", "dev")

# DeepSeek 官网 API
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Java 交易服务
JAVA_BASE = os.getenv("JAVA_BASE", "http://127.0.0.1:8080")

# JWT（与 Java 共用 HS256 密钥）
JWT_SECRET = os.getenv("JWT_SECRET", "resv-jwt-secret-change-me-0123456789abcdef")
JWT_ALG = "HS256"

# 任务 -> 模型别名。dev 档默认全 v4pro；上线后把 MODEL_AGENT/MODEL_MULTIMODAL
# 指到 deepseekv4flash 即可（prod 环境变量），代码零改动。
TASK_MODEL = {
    "agent": os.getenv("MODEL_AGENT", "deepseekv4pro"),
    "multimodal": os.getenv("MODEL_MULTIMODAL", "deepseekv4pro"),
    "gen": os.getenv("MODEL_GEN", "deepseekv4pro"),
}

# 别名 -> 官方 API model 标识（DeepSeek 官方 id 带连字符）。
# 个别账号/入口不同时，用 MODEL_ID_<大写别名> 覆盖。
_CANONICAL_IDS = {
    "deepseekv4pro": "deepseek-v4-pro",
    "deepseekv4flash": "deepseek-v4-flash",
    "deepseekv4flashvision": "deepseek-v4-flash-vision-exp",
}

MODEL_IDS: dict[str, str] = {}
for alias in set(TASK_MODEL.values()):
    MODEL_IDS[alias] = os.getenv(f"MODEL_ID_{alias.upper()}", _CANONICAL_IDS.get(alias, alias))

# 本地 embedding（独立进程 8001，OpenAI 兼容 /v1/embeddings）
EMBED_BASE = os.getenv("EMBED_BASE", "http://127.0.0.1:8001")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
EMBED_MODEL_DIR = os.getenv("EMBED_MODEL_DIR", "")
EMBED_MAX_LEN = int(os.getenv("EMBEDDING_MAX_LEN", "2048"))

RAG_DB = ROOT / "data" / "rag.db"
DOCS_DIR = ROOT / "data" / "docs"
AUDIT_FILE = ROOT / "data" / "audit" / "kb.jsonl"  # 知识库管理操作审计（含被拒的越权尝试）


def resolve_model(task: str) -> str:
    """任务 -> 官方 model 标识，带日志易排查。"""
    alias = TASK_MODEL.get(task, TASK_MODEL["gen"])
    return MODEL_IDS.get(alias, alias)
