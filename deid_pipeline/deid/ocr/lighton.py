"""LightOnOCR-2-1B OCR step.

Rasterizes a scanned/image-only PDF at 200 DPI (PyMuPDF) and runs LightOnOCR-2-1B
via vLLM to produce markdown per page.

Output: pages = [{"page","width","height","text","finish_reason"}, ...].

vLLM/torch are imported lazily and only required for this OCR path — the
de-identification paths run without them (and without a GPU). vLLM is declared
in pyproject.toml; install with `uv sync`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF
from PIL import Image

log = logging.getLogger(__name__)

MODEL_ID = "lightonai/LightOnOCR-2-1B"
RENDER_DPI = 200
MAX_NEW_TOKENS = 2048

# LightOn's processor expands <|image_pad|> into the image-feature placeholders.
_PROMPT = (
    "<|im_start|>system<|im_end|>\n"
    "<|im_start|>user\n<|image_pad|><|im_end|>\n"
    "<|im_start|>assistant\n"
)

_LLM = None  # cached engine across calls in one process


def render_pdf_to_images(pdf_path: str, dpi: int = RENDER_DPI) -> List[Image.Image]:
    """Rasterize every page to an in-memory RGB PIL image at `dpi`."""
    imgs: List[Image.Image] = []
    with fitz.open(pdf_path) as doc:
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat, alpha=False)
            imgs.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    return imgs


def _get_llm():
    global _LLM
    if _LLM is None:
        try:
            from vllm import LLM
        except Exception as e:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "vLLM is required for the LightOnOCR OCR path. Install it with "
                "`uv sync` (declared in pyproject.toml) on a CUDA GPU machine."
            ) from e
        _LLM = LLM(
            model=MODEL_ID,
            dtype="bfloat16",
            gpu_memory_utilization=0.90,
            max_model_len=4096,
            limit_mm_per_prompt={"image": 1},
            enforce_eager=False,
            disable_log_stats=True,
        )
    return _LLM


def ocr_pdf(pdf_path: str, out_dir: Optional[str] = None,
            dpi: int = RENDER_DPI) -> dict:
    """OCR a scanned PDF -> {"model","pdf","dpi","num_pages","pages":[...]}.

    If out_dir is given, also writes pages.json and a combined <stem>.md there.
    """
    from vllm import SamplingParams  # local import keeps module CPU-importable

    images = render_pdf_to_images(pdf_path, dpi=dpi)
    llm = _get_llm()
    sp = SamplingParams(temperature=0.0, max_tokens=MAX_NEW_TOKENS)

    requests = [{"prompt": _PROMPT, "multi_modal_data": {"image": img}}
                for img in images]
    outs = llm.generate(requests, sp, use_tqdm=False)

    pages = []
    for i, (img, out) in enumerate(zip(images, outs)):
        finish_reason = out.outputs[0].finish_reason
        if finish_reason != "stop":
            log.warning("page %d: OCR output truncated (finish_reason=%r, "
                        "max_tokens=%d) — text may be incomplete",
                        i + 1, finish_reason, MAX_NEW_TOKENS)
        pages.append({
            "page": i + 1,
            "width": img.width,
            "height": img.height,
            "text": out.outputs[0].text,  # RAW markdown (line breaks preserved)
            "finish_reason": finish_reason,
        })

    result = {
        "engine": "lighton_vllm",
        "model": MODEL_ID,
        "pdf": str(pdf_path),
        "dpi": dpi,
        "num_pages": len(pages),
        "pages": pages,
    }

    if out_dir:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        (od / "pages.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        stem = Path(pdf_path).stem
        md = "\n\n---\n\n".join(
            f"<!-- page {p['page']} -->\n\n{p['text'].strip()}\n" for p in pages
        )
        (od / f"{stem}.md").write_text(md, encoding="utf-8")

    return result


def pages_as_pairs(ocr_result: dict):
    """Convert an ocr_pdf() result to (page_number, text) pairs."""
    return [(p["page"], p["text"]) for p in ocr_result["pages"]]
