"""知识库管理接口（仅 ADMIN 角色）：上传 / 列表 / 删除 / 重建。

- 上传：base64 JSON（文件名 + 内容）保存到 data/docs 后只灌该文件；store.ingest_file 先删同名旧向量，天然可替换。
- md 顶部可用 front matter / 连续行声明 dept_tags 做部门可见性，入库自动剥离。
- 返回统一 {code, msg, data}，与前端 jfetch 约定一致。
"""
import base64
from pathlib import PurePosixPath

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import config
from ..middleware import current_user
from ..rag.store import get_store

router = APIRouter(prefix="/api/admin/kb", tags=["知识库管理"])

_ALLOWED = {".md", ".docx", ".doc", ".pdf"}
_MAX_BYTES = 20 * 1024 * 1024  # 单文件上限 20MB


class UploadBody(BaseModel):
    name: str
    content: str  # 文件内容的 base64


def _admin() -> bool:
    u = current_user()
    return (u.role or "").upper() == "ADMIN"


def _ok(data):
    return {"code": 0, "msg": "ok", "data": data}


def _err(code: int, msg: str):
    return {"code": code, "msg": msg, "data": None}


@router.get("")
def list_kb():
    if not _admin():
        return _err(403, "需要管理员权限")
    st = get_store()
    out = []
    if config.DOCS_DIR.is_dir():
        for p in sorted(config.DOCS_DIR.iterdir()):
            if p.suffix.lower() in _ALLOWED:
                stat = p.stat()
                out.append({
                    "name": p.name,
                    "sizeBytes": stat.st_size,
                    "updatedAt": stat.st_mtime,
                    "paragraphs": st.count_by_source(p.name),
                })
    return _ok(out)


@router.post("/upload")
async def upload(body: UploadBody):
    if not _admin():
        return _err(403, "需要管理员权限")
    name = PurePosixPath((body.name or "").replace("\\", "/")).name.strip()
    if not name:
        return _err(400, "文件名为空")
    ext = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
    if ext not in _ALLOWED:
        return _err(400, f"仅支持 {'/'.join(sorted(_ALLOWED))}")
    try:
        data = base64.b64decode(body.content, validate=True)
    except Exception:  # noqa: BLE001
        return _err(400, "内容不是合法的 base64")
    if len(data) > _MAX_BYTES:
        return _err(400, "文件超过 20MB 上限")

    st = get_store()
    if not st.enabled:
        return _err(503, "RAG 不可用（缺少 pymilvus 或初始化失败）")
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.DOCS_DIR / name
    dest.write_bytes(data)
    try:
        added = await st.ingest_file(dest)
    except Exception as e:  # noqa: BLE001
        try:
            dest.unlink(missing_ok=True)  # 入库失败回滚，避免留半截文档
        except Exception:  # noqa: BLE001
            pass
        return _err(500, f"入库失败：{e}")
    return _ok({"name": dest.name, "added": added, "sizeBytes": len(data)})


@router.post("/reingest")
async def reingest():
    if not _admin():
        return _err(403, "需要管理员权限")
    st = get_store()
    if not st.enabled:
        return _err(503, "RAG 不可用")
    keep = set()
    if config.DOCS_DIR.is_dir():
        keep = {p.name for p in config.DOCS_DIR.iterdir() if p.suffix.lower() in _ALLOWED}
    purged = st.purge_missing(keep)  # 先清源文件已不在 docs 的残留向量，避免抢占召回
    total = await st.ingest_dir()
    return _ok({"purged": purged, "added": total})


@router.delete("")
def delete(file: str = Query(..., description="要删除的知识库文件名")):
    if not _admin():
        return _err(403, "需要管理员权限")
    name = PurePosixPath((file or "").replace("\\", "/")).name.strip()
    if not name:
        return _err(400, "缺少文件名")
    st = get_store()
    if st.enabled:
        st.delete_by_source(name)
    p = config.DOCS_DIR / name
    removed = False
    if p.exists():
        p.unlink()
        removed = True
    return _ok({"name": name, "removedFile": removed})
