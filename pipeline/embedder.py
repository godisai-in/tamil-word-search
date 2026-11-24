"""Word -> vector. The ONE place that defines how vectors are made, imported by the
bulk pipeline and (later) by the search service so queries land in the same space.

Recipe (stored in the pkl so it can never drift):
  1. tokenise the *normalised* word (textnorm.norm), max 32 tokens
  2. mean-pool the last hidden state over real tokens (CLS/SEP/PAD excluded)
  3. OPTIONAL per-language mean subtraction (`means` given to finalize()).
     Measured on a 3k-word sample: generic means did NOT help en<->ta retrieval
     (17-20% top-1 vs 20% raw), so the default is off; pack_pkl.py --center lang
     enables it and records the means in the pkl so queries can mirror it.
  4. L2-normalise  -> cosine similarity == dot product
NOTE: IndicBERT is an MLM, not a sentence encoder. Expect weak cross-lingual
word matching (~20% top-1 en->ta on 30 common-word pairs; chance is 3%).
"""
import numpy as np

MODEL_NAME = "ai4bharat/IndicBERTv2-MLM-Sam-TLM"
MAX_LEN = 32


class Embedder:
    def __init__(self, model_name=MODEL_NAME, threads=None, max_len=MAX_LEN):
        import torch
        from transformers import AutoModel, AutoTokenizer

        if threads:
            torch.set_num_threads(threads)
        self.torch = torch
        self.max_len = max_len
        self.model_name = model_name
        self.tok = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).eval()
        self.dim = self.model.config.hidden_size

    def embed_raw(self, texts):
        """Mean-pooled, un-centred, un-normalised float32 vectors, shape (n, dim)."""
        torch = self.torch
        enc = self.tok(
            list(texts),
            padding=True,
            truncation=True,
            max_length=self.max_len,
            return_tensors="pt",
            return_special_tokens_mask=True,
        )
        special = enc.pop("special_tokens_mask")
        # token_type_ids is not used by this model family
        enc.pop("token_type_ids", None)
        with torch.inference_mode():
            h = self.model(**enc).last_hidden_state
        m = (enc["attention_mask"] * (1 - special)).float()
        empty = m.sum(1) == 0                      # safety: nothing but special tokens
        m[empty] = enc["attention_mask"][empty].float()
        pooled = (h * m.unsqueeze(-1)).sum(1) / m.sum(1, keepdim=True)
        return pooled.numpy().astype(np.float32)


def finalize(raw, langs, means):
    """Optionally centre per language, then L2-normalise.
    `means` = {'en': vec, 'ta': vec}, or None to only normalise."""
    out = np.array(raw, dtype=np.float32, copy=True)
    langs = np.asarray(langs)
    for lang, mu in (means or {}).items():
        idx = langs == lang
        out[idx] = raw[idx] - mu
    n = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(n, 1e-12)
