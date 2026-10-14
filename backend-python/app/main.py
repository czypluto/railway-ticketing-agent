"""FastAPI 入口。三合一：JWT 校验 + Agent + RAG；模型路由在 config.py。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .middleware import auth_middleware
from .routers import chat, history, kb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动时打印模型路由，便于确认 dev=pro、上线后 agent/multimodal=flash
    logger.info("环境=%s 模型路由=%s", config.ENV, config.TASK_MODEL)
    logger.info("RAG 状态: %s", config.RAG_DB)
    yield


app = FastAPI(title="resv-agent", version="0.1.0", lifespan=lifespan)

# 认证走 Authorization 头（Bearer），不依赖 Cookie，因此 allow_credentials 必须为 False。
# 注意：allow_origins=["*"] 与 allow_credentials=True 是浏览器拒绝的非法组合，不能同时开。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(auth_middleware)

app.include_router(chat.router)
app.include_router(history.router)
app.include_router(kb.router)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "env": config.ENV,
        "models": config.TASK_MODEL,
        "resolved": config.MODEL_IDS,
    }
