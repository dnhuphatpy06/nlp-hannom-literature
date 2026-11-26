"""Hai mô hình ngôn ngữ dùng để chấm điểm ngữ cảnh.

- NomLM: masked LM chữ Nôm (mặc định btqkhai/SinoNomBERT). Che chữ cần xét, softmax CHỈ trên các chữ ứng viên.
- QnLM : LM nhân quả tiếng Việt (mặc định Qwen/Qwen2.5-0.5B). log P(cả dòng); dùng hiệu giữa dòng gốc và dòng đã thay một âm.

Tên mô hình có thể là tên trên HuggingFace hoặc đường dẫn thư mục đã tải sẵn (dùng khi máy không có Internet).
"""
from __future__ import annotations

NOM_MODEL = "btqkhai/SinoNomBERT"
QN_MODEL = "Qwen/Qwen2.5-0.5B"


def pick_device(device: str | None = None) -> str:
    import torch
    if device and device != "auto":
        return device
    return "cuda" if torch.cuda.is_available() else "cpu"


def _progress(it, total, desc, show):
    if not show:
        return it
    try:
        from tqdm.auto import tqdm
        return tqdm(it, total=total, desc=desc)
    except ImportError:
        return it


class NomLM:
    """SinoNomBERT là mô hình mức chữ: mỗi chữ Nôm là một token trong vocab. Vì vậy ta tự mã hoá bằng tra vocab
    ([CLS] chữ… [MASK] chữ… [SEP]) thay vì gọi tokenizer — tránh lỗi panic "AddedVocabulary bad split" của tokenizer
    Rust và lỗi tokenizer chậm bị bỏ ở transformers 5.x, nên chạy ổn định với mọi phiên bản transformers."""

    def __init__(self, model_name: str = NOM_MODEL, device: str | None = None, batch_size: int = 32,
                 max_length: int = 160):
        import torch
        from transformers import AutoModelForMaskedLM, AutoTokenizer

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_name)      # chỉ dùng để lấy vocab và id các token đặc biệt
        self.model = AutoModelForMaskedLM.from_pretrained(model_name).eval()
        self.device = pick_device(device)
        self.model.to(self.device)
        self.batch_size = batch_size
        self.max_length = max_length
        full_vocab = self.tok.get_vocab()
        # vocab của tokenizer có thể dài hơn bảng embedding của mô hình (vd 50422 so với 50420): id vượt giới hạn
        # gây "device-side assert" trên GPU / IndexError trên CPU, nên coi các chữ đó là ngoài vocab (OOV).
        n_embed = self.model.get_input_embeddings().num_embeddings
        self.vocab = {c: i for c, i in full_vocab.items() if i < n_embed}
        self.n_oov_dropped = len(full_vocab) - len(self.vocab)
        self.unk_id = self._special("unk_token_id", "[UNK]")
        self.cls_id = self._special("cls_token_id", "[CLS]")
        self.sep_id = self._special("sep_token_id", "[SEP]")
        self.pad_id = self._special("pad_token_id", "[PAD]", default=0)
        self.mask_id = self._special("mask_token_id", "[MASK]")
        self._cache: dict[tuple, dict] = {}

    def _special(self, attr: str, name: str, default: int | None = None) -> int:
        v = getattr(self.tok, attr, None)
        if v is None:
            v = self.vocab.get(name, default)
        if v is None:
            raise ValueError(f"Vocab của mô hình thiếu token đặc biệt {name}")
        return v

    def in_vocab(self, c: str) -> bool:
        return c in self.vocab

    def _char_id(self, c: str) -> int:
        i = self.vocab.get(c)
        if i is None:
            i = self.vocab.get(c.lower())
        return self.unk_id if i is None else i

    def encode(self, ctx: list[str], idx: int) -> tuple[list[int], int]:
        """Trả về (input_ids, vị trí [MASK]). Chữ hỏng ([MASK]) trong ngữ cảnh -> [UNK], để chỉ có một vị trí bị che.
        Nếu dài quá max_length thì cắt một cửa sổ quanh vị trí che."""
        ids = [self.unk_id if t == self.tok.mask_token or t == "[MASK]" else self._char_id(t) for t in ctx]
        room = self.max_length - 2
        if len(ids) > room:
            start = max(0, min(idx - room // 2, len(ids) - room))
            ids, idx = ids[start: start + room], idx - start
        ids[idx] = self.mask_id
        return [self.cls_id] + ids + [self.sep_id], idx + 1

    def restricted_scores(self, requests: list[tuple[list[str], int, list[str]]], show_progress: bool = False
                          ) -> list[dict[str, float | None]]:
        """requests: (các chữ ngữ cảnh, chỉ số vị trí che, các ứng viên).
        Trả về {ứng viên: log P} — softmax chỉ trên ứng viên có trong vocab; ứng viên ngoài vocab -> None."""
        torch = self.torch
        out: list[dict | None] = [None] * len(requests)
        todo = []
        for i, (ctx, idx, cands) in enumerate(requests):
            key = (tuple(ctx), idx, tuple(cands))
            if key in self._cache:
                out[i] = self._cache[key]
            else:
                todo.append((i, key))
        batches = range(0, len(todo), self.batch_size)
        for b in _progress(batches, len(batches), "SinoNomBERT", show_progress):
            chunk = todo[b: b + self.batch_size]
            enc = [self.encode(list(ctx), idx) for _i, (ctx, idx, _c) in chunk]
            width = max(len(e[0]) for e in enc)
            input_ids = torch.full((len(enc), width), self.pad_id, dtype=torch.long)
            attention = torch.zeros((len(enc), width), dtype=torch.long)
            for j, (ids, _m) in enumerate(enc):
                input_ids[j, : len(ids)] = torch.tensor(ids)
                attention[j, : len(ids)] = 1
            with torch.no_grad():
                logits = self.model(input_ids=input_ids.to(self.device), attention_mask=attention.to(self.device),
                                    token_type_ids=torch.zeros_like(input_ids).to(self.device)).logits
            for j, (i, key) in enumerate(chunk):
                cands = key[2]
                ids = [self.vocab[c] for c in cands if c in self.vocab]
                res: dict[str, float | None] = {c: None for c in cands}
                if ids:
                    sel = logits[j, enc[j][1]][torch.tensor(ids, device=logits.device)].float()
                    it = iter(torch.log_softmax(sel, -1).tolist())
                    for c in cands:
                        if c in self.vocab:
                            res[c] = next(it)
                self._cache[key] = res
                out[i] = res
        return out


class QnLM:
    def __init__(self, model_name: str = QN_MODEL, device: str | None = None, batch_size: int = 32):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.device = pick_device(device)
        self.tok = AutoTokenizer.from_pretrained(model_name)
        # ép float32: chạy được trên CPU và GPU cũ (P100/T4) bất kể transformers mặc định nạp dtype nào
        self.model = AutoModelForCausalLM.from_pretrained(model_name).float().eval()
        self.model.to(self.device)
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.tok.padding_side = "right"
        self.bos = self.tok.bos_token_id if self.tok.bos_token_id is not None else self.tok.eos_token_id
        self.batch_size = batch_size
        self._cache: dict[str, float] = {}

    def logprobs(self, texts: list[str], show_progress: bool = False) -> dict[str, float]:
        torch = self.torch
        todo = sorted({t for t in texts if t not in self._cache}, key=len)
        batches = range(0, len(todo), self.batch_size)
        for b in _progress(batches, len(batches), "Qwen LM", show_progress):
            chunk = todo[b: b + self.batch_size]
            enc = self.tok(chunk, return_tensors="pt", padding=True, add_special_tokens=False)
            ids = torch.cat([torch.full((enc.input_ids.size(0), 1), self.bos), enc.input_ids], 1).to(self.device)
            att = torch.cat([torch.ones(ids.size(0), 1, dtype=torch.long), enc.attention_mask], 1).to(self.device)
            with torch.no_grad():
                lg = self.model(input_ids=ids, attention_mask=att).logits.float()
            lp = torch.log_softmax(lg[:, :-1], -1).gather(2, ids[:, 1:, None])[:, :, 0] * att[:, 1:]
            for t, v in zip(chunk, lp.sum(1).tolist()):
                self._cache[t] = v
        return {t: self._cache[t] for t in texts}


def load_models(nom_model: str | None = NOM_MODEL, qn_model: str | None = QN_MODEL, device: str | None = "auto",
                nom_batch_size: int = 32, qn_batch_size: int = 32, verbose: bool = True):
    """Nạp hai mô hình. Truyền None để bỏ qua mô hình tương ứng. Trả về (nom_lm, qn_lm, device)."""
    dev = pick_device(device)
    if verbose:
        import torch
        gpu = f" ({torch.cuda.get_device_name(0)})" if dev.startswith("cuda") and torch.cuda.is_available() else ""
        print(f"Thiết bị: {dev}{gpu}")
    nom_lm = qn_lm = None
    if nom_model:
        if verbose:
            print(f"Nạp mô hình Nôm: {nom_model} ...")
        nom_lm = NomLM(nom_model, dev, nom_batch_size)
    if qn_model:
        if verbose:
            print(f"Nạp mô hình Quốc ngữ: {qn_model} ...")
        qn_lm = QnLM(qn_model, dev, qn_batch_size)
    return nom_lm, qn_lm, dev
