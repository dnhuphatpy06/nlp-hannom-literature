"""Đọc chữ trong các bounding box của MỘT ảnh trong thư mục vi/ bằng Vintern-1B-v3.5 (5CD-AI/Vintern-1B-v3_5), rồi vẽ kết quả để
kiểm tra chất lượng. Chỉ nhận dạng chữ trong box đã có: toạ độ lấy từ Label.txt (do row_projection ghi), mô hình không tìm box.

Dùng lại toàn bộ phần đọc Label.txt, cắt box, vẽ ảnh, đo CER/WER của scripts/vietocr_viz.py nên đầu ra và tham số --ref/--label/--pad/
--out-dir giống hệt, để so sánh công bằng giữa các mô hình trên cùng ảnh cắt.

Vintern-1B-v3.5 là mô hình ngôn ngữ-hình ảnh ~1B (fine-tune từ InternVL2.5-1B, giấy phép MIT) nên mỗi box là một lần hỏi "chép lại chữ trong
ảnh". Ảnh dòng rất dài được chia thành nhiều khung 448x448 (dynamic tiling của InternVL).

Cài đặt: DÙNG MÔI TRƯỜNG RIÊNG. Môi trường chính của dự án cần transformers>=5.10 cho PaddleOCR-VL (requirements-ocr.txt), còn mã của
Vintern (trust_remote_code) viết cho transformers 4.x nên có thể cần bản cũ hơn, hai yêu cầu có thể xung đột.

    python -m venv C:\\vintern                                # đường dẫn NGẮN, tránh lỗi đường dẫn dài trên Windows
    C:\\vintern\\Scripts\\activate
    pip install torch torchvision                             # đúng bản CUDA/CPU của máy: https://pytorch.org/get-started/locally/
    pip install "transformers>=4.44,<5" timm einops sentencepiece pillow     # BẮT BUỘC transformers 4.x (xem bên dưới)
    python scripts/vintern_viz.py "data/<Tên sách>/vi/<Tên sách>.008.jpg"

  * Lần chạy đầu tự tải mô hình từ Hugging Face (~2 GB, có cache ở ~/.cache/huggingface). Muốn tải trước: `huggingface-cli download
    5CD-AI/Vintern-1B-v3_5`, hoặc tải về thư mục rồi truyền --model đường/dẫn/thư/mục.
  * `trust_remote_code=True`: transformers chạy mã Python nằm trong repo mô hình trên Hugging Face. Xem mã (modeling_*.py,
    configuration_*.py) trước nếu bạn muốn kiểm tra.
  * timm, einops, sentencepiece là các thư viện mã của InternVL thường cần (chưa kiểm chứng đủ cho bản v3.5): thiếu cái nào thì script báo
    ImportError kèm tên; cài rồi chạy lại.
  * transformers 5.x KHÔNG chạy được: lỗi "AttributeError: 'InternVLChatModel' object has no attribute 'all_tied_weights_keys'" (đã gặp
    với 5.22.1). Dùng transformers 4.x trong môi trường riêng. Model card InternVL2.5 chỉ nói ">=4.37.2", không nêu giới hạn trên, nên
    bản 4.x chính xác tương thích chưa được kiểm chứng: thử `<5` mới nhất, nếu lỗi khác thì thử lùi, ví dụ `transformers==4.46.3`.
  * GPU: bf16 ~2 GB trọng số; ảnh dài nhiều khung (--max-num lớn) tốn thêm bộ nhớ. Hết VRAM thì giảm --max-num (6, như model card).

Đầu ra trong <out-dir> (mặc định output/vintern/<tên ảnh>/): <tên>_boxes.png, <tên>_lines.png, <tên>.json, <tên>.txt (xem vietocr_viz.py).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # import vietocr_viz cùng thư mục dù chạy bằng cách nào
import vietocr_viz as common  # noqa: E402

MODEL_ID = "5CD-AI/Vintern-1B-v3_5"
DEFAULT_PROMPT = ("<image>\nĐây là một dòng chữ viết tay tiếng Việt. Hãy chép lại chính xác từng chữ, giữ nguyên dấu thanh và dấu mũ, "
                  "không thêm, không bớt, không giải thích. Chỉ trả về đúng dòng chữ đó.")
IMAGENET_MEAN, IMAGENET_STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
TILE = 448   # kích thước một khung của InternViT


# ---------------------------------------------------------------- tiền xử lý (theo model card của Vintern/InternVL)
def find_closest_aspect_ratio(aspect_ratio, target_ratios, width, height, image_size):
    best_diff, best_ratio, area = float("inf"), (1, 1), width * height
    for ratio in target_ratios:
        diff = abs(aspect_ratio - ratio[0] / ratio[1])
        if diff < best_diff:
            best_diff, best_ratio = diff, ratio
        elif diff == best_diff and area > 0.5 * image_size * image_size * ratio[0] * ratio[1]:
            best_ratio = ratio
    return best_ratio


def dynamic_preprocess(image, min_num: int = 1, max_num: int = 12, image_size: int = TILE, use_thumbnail: bool = True):
    """Chia ảnh thành các khung image_size x image_size theo tỉ lệ khung hình gần nhất (tối đa max_num khung), thêm một ảnh thu nhỏ cả ảnh."""
    w, h = image.size
    target_ratios = sorted({(i, j) for n in range(min_num, max_num + 1) for i in range(1, n + 1) for j in range(1, n + 1)
                            if min_num <= i * j <= max_num}, key=lambda r: r[0] * r[1])
    ratio = find_closest_aspect_ratio(w / h, target_ratios, w, h, image_size)
    target_w, target_h = image_size * ratio[0], image_size * ratio[1]
    resized = image.resize((target_w, target_h))
    cols = target_w // image_size
    tiles = [resized.crop(((i % cols) * image_size, (i // cols) * image_size,
                           (i % cols + 1) * image_size, (i // cols + 1) * image_size)) for i in range(ratio[0] * ratio[1])]
    if use_thumbnail and len(tiles) != 1:
        tiles.append(image.resize((image_size, image_size)))
    return tiles


def to_pixel_values(image, max_num: int):
    """PIL.Image -> tensor (số khung, 3, 448, 448) đã chuẩn hoá."""
    import torch
    import torchvision.transforms as T
    from torchvision.transforms.functional import InterpolationMode

    transform = T.Compose([T.Resize((TILE, TILE), interpolation=InterpolationMode.BICUBIC), T.ToTensor(),
                           T.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD)])
    return torch.stack([transform(t.convert("RGB")) for t in dynamic_preprocess(image.convert("RGB"), max_num=max_num)])


def clean_answer(text: str) -> str:
    """Bỏ rào markdown/dấu nháy bao quanh và gom xuống dòng: mô hình hỏi đáp đôi khi bọc câu trả lời."""
    text = re.sub(r"```[a-zA-Z]*", "", text).strip()
    text = " ".join(text.split())
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”`":
        text = text[1:-1].strip()
    return text


# ---------------------------------------------------------------- mô hình
def transformers_major() -> int:
    try:
        import transformers
        return int(transformers.__version__.split(".")[0])
    except (ImportError, ValueError):
        return 0


def load_predictor(args):
    """Trả về (hàm predict(PIL.Image) -> str, thiết bị). torch/transformers nạp ở đây để --help chạy được khi chưa cài."""
    try:
        import torch
        import transformers
        from transformers import AutoModel, AutoTokenizer
    except ImportError as e:
        raise SystemExit(f"Chưa cài thư viện ({e}). Xem hướng dẫn ở đầu file: pip install torch torchvision transformers timm einops sentencepiece")
    if transformers_major() >= 5:
        print(f"Cảnh báo: đang dùng transformers {transformers.__version__}. Mã của Vintern viết cho transformers 4.x và thường lỗi với 5.x "
              "(AttributeError: ... all_tied_weights_keys). Nếu lỗi, dùng môi trường riêng với transformers<5.", file=sys.stderr)
    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = {"auto": torch.bfloat16 if device.startswith("cuda") else torch.float32,
             "bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[args.dtype]
    print(f"Nạp {args.model} (lần đầu tải ~2 GB, sau đó dùng cache)… thiết bị: {device} · dtype: {str(dtype).replace('torch.', '')}")
    try:
        model = AutoModel.from_pretrained(args.model, torch_dtype=dtype, low_cpu_mem_usage=True, trust_remote_code=True,
                                          use_flash_attn=False).eval().to(device)
        tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True, use_fast=False)
    except ImportError as e:
        raise SystemExit(f"Mã mô hình cần thêm thư viện: {e}. Cài rồi chạy lại (thường là: pip install timm einops sentencepiece).")
    except AttributeError as e:
        if "all_tied_weights_keys" in str(e) or transformers_major() >= 5:
            raise SystemExit(f"Mã của Vintern (trust_remote_code) không chạy được với transformers {transformers.__version__} (lỗi: {e}). "
                             "Cài môi trường riêng với transformers 4.x: pip install \"transformers>=4.44,<5\" (xem hướng dẫn đầu file); "
                             "đừng hạ bản trong .venv chính vì PaddleOCR-VL cần transformers>=5.10.")
        raise
    generation = dict(max_new_tokens=args.max_new_tokens, do_sample=False, num_beams=args.beams, repetition_penalty=args.repetition_penalty)
    print(f"Sinh chữ: {generation} · tối đa {args.max_num} khung/ảnh")

    def predict(image, repetition_penalty: float | None = None, beams: int | None = None) -> str:
        """Đọc chữ trong một ảnh. Hai tham số đổi cách sinh chữ cho riêng lần gọi này (vintern_worker.py thử lại bằng phạt lặp mạnh hơn)."""
        gen = dict(generation)
        if repetition_penalty is not None:
            gen["repetition_penalty"] = repetition_penalty
        if beams is not None:
            gen["num_beams"] = beams
        pixel_values = to_pixel_values(image, args.max_num).to(dtype).to(device)
        with torch.inference_mode():
            answer = model.chat(tokenizer, pixel_values, args.prompt, gen)
        return clean_answer(answer)

    return predict, device


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Đọc chữ trong các bounding box của một ảnh vi/ bằng Vintern-1B-v3.5, vẽ kết quả.")
    p.add_argument("image", type=Path, help="ảnh trong thư mục vi/, ví dụ data/<Tên sách>/vi/<Tên sách>.008.jpg (đặt trong dấu nháy)")
    p.add_argument("--label", type=Path, help="Label.txt chứa box (mặc định: Label.txt cùng thư mục với ảnh)")
    p.add_argument("--ref", type=Path, help="file chữ chuẩn (UTF-8), mỗi dòng ứng với một box theo thứ tự trong Label.txt; để tính CER/WER")
    p.add_argument("--out-dir", type=Path, help="thư mục đầu ra (mặc định output/vintern/<tên ảnh>/)")
    p.add_argument("--pad", type=int, default=common.CROP_PAD, help=f"điểm ảnh chừa quanh box khi cắt (mặc định {common.CROP_PAD})")
    p.add_argument("--model", default=MODEL_ID, help=f"id Hugging Face hoặc thư mục đã tải sẵn (mặc định {MODEL_ID})")
    p.add_argument("--device", default="auto", help="auto | cpu | cuda (mặc định auto)")
    p.add_argument("--dtype", default="auto", choices=["auto", "bf16", "fp16", "fp32"], help="auto: bf16 trên GPU, fp32 trên CPU")
    p.add_argument("--max-num", type=int, default=12,
                   help="số khung 448x448 tối đa cho một ảnh (mặc định 12; model card dùng 6). Dòng dài ~12:1 cần 12 khung để không bị méo; "
                        "giảm nếu hết VRAM hoặc chậm")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="câu hỏi gửi cùng ảnh; phải chứa <image> ở đầu")
    p.add_argument("--beams", type=int, default=3, help="num_beams (mặc định 3 như model card; 1 = tham lam, nhanh hơn)")
    p.add_argument("--repetition-penalty", type=float, default=1.1,
                   help="phạt lặp (mặc định 1.1; model card dùng 2.5, mạnh hơn, có thể làm đổi từ lặp hợp lệ). Thử cả hai để so")
    p.add_argument("--max-new-tokens", type=int, default=128, help="giới hạn độ dài câu trả lời cho MỘT dòng (mặc định 128)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if "<image>" not in args.prompt:
        raise SystemExit("--prompt phải chứa <image> (chỗ chèn ảnh), ví dụ: \"<image>\\nChép lại chữ trong ảnh.\"")
    if args.beams < 1 or args.max_num < 1:
        raise SystemExit("--beams và --max-num phải >= 1")
    predict, device = load_predictor(args)
    common.run(args, predict=predict, device=device, name="Vintern")


if __name__ == "__main__":
    main()
