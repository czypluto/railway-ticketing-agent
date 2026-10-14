"""探测 WeMM-Embedding-2B 在本机能否用 int8/4bit 加载并产出 1024 维文本嵌入。"""
import traceback

MODEL = "/home/cpluto/models/WeMM-Embedding-2B"


def try_st(quant=None, name="plain"):
    from sentence_transformers import SentenceTransformer
    kw = {}
    if quant:
        from transformers import BitsAndBytesConfig
        cfg = {"bnb_4bit_compute_dtype": "float16"}
        cfg["load_in_8bit" if quant == 8 else "load_in_4bit"] = True
        kw["quantization_config"] = BitsAndBytesConfig(**cfg)
    m = SentenceTransformer(MODEL, device="cuda", trust_remote_code=True, model_kwargs=kw)
    v = m.encode(["学生票仅限动车组二等座且限寒暑假乘车"], normalize_embeddings=True, truncate_dim=1024)
    return name, v.shape, [round(float(x), 4) for x in v[0][:4]]


if __name__ == "__main__":
    import torch
    print("cuda:", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
    for quant in (8, 4):
        try:
            name, shape, head = try_st(quant, f"int{quant}")
            print("OK", name, "dim", shape, "head", head)
            print("gpu_mem_GB", round(torch.cuda.memory_allocated() / 2**30, 2))
            break
        except Exception as e:  # noqa: BLE001
            print("FAIL", f"int{quant}", type(e).__name__, str(e)[:200])
            traceback.print_exc(limit=1)
