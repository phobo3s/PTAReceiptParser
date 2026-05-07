"""
train_trocr.py — TrOCR LoRA fine-tuner for Turkish receipt OCR
==============================================================
PPOCRLabel'den üretilen crop+label verileriyle TrOCR modelini LoRA ile
artımlı eğitir. Her çalıştırmada trocr_adapter/ klasörüne kaydeder.

Kurulum (bir kez):
    pip install peft>=0.10.0 accelerate>=0.27.0

Kullanım:
    python train_trocr.py                           # tüm veri, 3 epoch
    python train_trocr.py --labels path/to/rec_gt.txt
    python train_trocr.py --epochs 1 --batch-size 4  # hızlı günlük eğitim
    python train_trocr.py --val-split 0.1            # %10 validation
    python train_trocr.py --no-continue              # sıfırdan başla

Günlük iş akışı:
    1. PPOCRLabel'de yeni fişleri etiketle → rec_gt.txt'e eklenir
    2. python train_trocr.py --epochs 1
    3. python batch.py <fis_klasoru> --engine trocr
"""

import argparse
import random
import sys
from pathlib import Path
from typing import Optional

from config import PPOCR_DATA_DIR

BASE_MODEL_ID     = "microsoft/trocr-base-printed"
ADAPTER_DIR       = Path("trocr_adapter")
DEFAULT_LABELS    = PPOCR_DATA_DIR / "rec_gt.txt"
DEFAULT_EPOCHS    = 3
DEFAULT_BATCH     = 4
DEFAULT_LR        = 5e-4
DEFAULT_VAL_SPLIT = 0.0


# ── Dataset ────────────────────────────────────────────────────────────────────

def parse_rec_gt(label_file: Path, base_dir: Optional[Path] = None) -> list:
    """rec_gt.txt'i parse et. Her satır: <relatif_yol>\t<etiket>

    Döndürür: [(abs_image_path, label_text), ...]
    """
    if base_dir is None:
        base_dir = label_file.parent

    samples = []
    missing = 0
    with open(label_file, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            if "\t" not in line:
                print(f"  [W] Satır {lineno}: tab yok, atlandı")
                continue
            rel_path, label = line.split("\t", 1)
            abs_path = base_dir / rel_path
            if not abs_path.exists():
                missing += 1
                continue
            samples.append((abs_path, label))

    if missing:
        print(f"  [W] {missing} resim dosyada eksik, atlandı")
    print(f"  {len(samples)} örnek yüklendi: {label_file}")
    return samples


class ReceiptDataset:
    """TrOCR eğitimi için PyTorch Dataset.

    __getitem__ döndürür:
      pixel_values: Tensor [3, H, W]
      labels:       Tensor [seq_len]  (padding -100 ile maskelenir)
    """

    def __init__(self, samples: list, processor, max_label_len: int = 128):
        self.samples = samples
        self.processor = processor
        self.max_label_len = max_label_len

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> dict:
        from PIL import Image

        img_path, label = self.samples[idx]
        image = Image.open(img_path).convert("RGB")

        pixel_values = self.processor(images=image, return_tensors="pt").pixel_values.squeeze(0)

        labels = self.processor.tokenizer(
            label,
            max_length=self.max_label_len,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        ).input_ids.squeeze(0)

        # Padding token'ları loss hesabından çıkar
        pad_id = self.processor.tokenizer.pad_token_id
        labels[labels == pad_id] = -100

        return {"pixel_values": pixel_values, "labels": labels}


# ── Model ──────────────────────────────────────────────────────────────────────

def build_lora_model(base_model_id: str, adapter_dir: Optional[Path] = None):
    """TrOCR'ı yükle ve LoRA ile sarmala.

    adapter_dir/adapter_config.json varsa mevcut adapter'ı yükleyip
    eğitime devam eder (artımlı mod).

    Döndürür: (processor, peft_model)
    """
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        from peft import LoraConfig, TaskType, get_peft_model, PeftModel
    except ImportError as e:
        print(f"❌ Eksik paket: {e}")
        print("   pip install peft>=0.10.0 accelerate>=0.27.0")
        sys.exit(1)

    print(f"  Temel model yükleniyor: {base_model_id}")
    processor = TrOCRProcessor.from_pretrained(base_model_id)
    base_model = VisionEncoderDecoderModel.from_pretrained(base_model_id)

    # Teacher-forcing için gerekli token ID'leri
    base_model.config.decoder_start_token_id = processor.tokenizer.cls_token_id
    base_model.config.pad_token_id = processor.tokenizer.pad_token_id
    base_model.config.eos_token_id = processor.tokenizer.sep_token_id

    if adapter_dir is not None and (adapter_dir / "adapter_config.json").exists():
        print(f"  Mevcut adapter yükleniyor: {adapter_dir}/")
        model = PeftModel.from_pretrained(base_model, str(adapter_dir), is_trainable=True)
    else:
        print(f"  Yeni LoRA konfigürasyonu oluşturuluyor")
        lora_config = LoraConfig(
            task_type=TaskType.SEQ_2_SEQ_LM,
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            bias="none",
            # TrOCR decoder (TrOCRAttention) linear projection isimleri
            target_modules=["q_proj", "k_proj", "v_proj", "out_proj"],
        )
        model = get_peft_model(base_model, lora_config)

    model.print_trainable_parameters()
    return processor, model


# ── Eğitim ─────────────────────────────────────────────────────────────────────

def train(model, train_loader, val_loader, optimizer, scheduler, epochs, device, adapter_dir):
    import torch

    model.train()
    model.to(device)

    for epoch in range(1, epochs + 1):
        total_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            pixel_values = batch["pixel_values"].to(device)
            labels       = batch["labels"].to(device)

            optimizer.zero_grad()
            loss = model(pixel_values=pixel_values, labels=labels).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            if scheduler is not None:
                scheduler.step()

            total_loss += loss.item()
            n_batches  += 1

        avg_train = total_loss / n_batches if n_batches else 0.0

        val_msg = ""
        if val_loader is not None:
            avg_val = _evaluate(model, val_loader, device)
            val_msg = f"  val_loss={avg_val:.4f}"
            model.train()

        print(f"  Epoch {epoch}/{epochs}  train_loss={avg_train:.4f}{val_msg}")

        # Her epoch sonunda kaydet
        adapter_dir.mkdir(exist_ok=True)
        model.save_pretrained(str(adapter_dir))
        print(f"  Adapter kaydedildi → {adapter_dir}/")


def _evaluate(model, val_loader, device) -> float:
    import torch

    model.eval()
    total, n = 0.0, 0
    with torch.no_grad():
        for batch in val_loader:
            pv = batch["pixel_values"].to(device)
            lb = batch["labels"].to(device)
            total += model(pixel_values=pv, labels=lb).loss.item()
            n += 1
    return total / n if n else 0.0


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="TrOCR LoRA fine-tuner — Türkçe fiş OCR")
    p.add_argument("--labels",       default=str(DEFAULT_LABELS), help="rec_gt.txt yolu")
    p.add_argument("--epochs",       type=int,   default=DEFAULT_EPOCHS)
    p.add_argument("--batch-size",   type=int,   default=DEFAULT_BATCH)
    p.add_argument("--lr",           type=float, default=DEFAULT_LR)
    p.add_argument("--val-split",    type=float, default=DEFAULT_VAL_SPLIT,
                   help="Validation için ayrılacak oran (0 = yok)")
    p.add_argument("--adapter-dir",  default=str(ADAPTER_DIR))
    p.add_argument("--seed",         type=int,   default=42)
    p.add_argument("--no-continue",  action="store_true",
                   help="Mevcut adapter'ı yoksay, sıfırdan başla")
    p.add_argument("--max-label-len", type=int,  default=128)
    return p.parse_args()


def main():
    args = parse_args()

    import torch
    from torch.utils.data import DataLoader
    from torch.optim import AdamW
    from torch.optim.lr_scheduler import CosineAnnealingLR

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    label_file  = Path(args.labels)
    adapter_dir = Path(args.adapter_dir)

    if not label_file.exists():
        print(f"❌ Label dosyası bulunamadı: {label_file}")
        sys.exit(1)

    # ── Veri ──────────────────────────────────────────────────────────────────
    samples = parse_rec_gt(label_file)
    if not samples:
        print("Örnek bulunamadı. Çıkılıyor.")
        sys.exit(1)

    random.shuffle(samples)

    val_samples, train_samples = [], samples
    if args.val_split > 0.0:
        n_val = max(1, int(len(samples) * args.val_split))
        val_samples, train_samples = samples[:n_val], samples[n_val:]
        print(f"  Eğitim: {len(train_samples)}  Doğrulama: {len(val_samples)}")

    # ── Model ──────────────────────────────────────────────────────────────────
    existing = None if args.no_continue else adapter_dir
    processor, model = build_lora_model(BASE_MODEL_ID, existing)

    # ── DataLoader ─────────────────────────────────────────────────────────────
    train_ds = ReceiptDataset(train_samples, processor, args.max_label_len)
    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)

    val_dl = None
    if val_samples:
        val_ds = ReceiptDataset(val_samples, processor, args.max_label_len)
        val_dl = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)

    # ── Optimizer ──────────────────────────────────────────────────────────────
    optimizer = AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=args.lr,
        weight_decay=0.01,
    )
    total_steps = len(train_dl) * args.epochs
    scheduler = CosineAnnealingLR(optimizer, T_max=max(total_steps, 1), eta_min=1e-6)

    # ── Cihaz ──────────────────────────────────────────────────────────────────
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  Cihaz: {device}")
    if device.type == "cpu":
        print("  [W] CPU'da eğitim yavaş olacak. Günlük çalıştırma için --epochs 1 önerilir.")

    # ── Eğitim ─────────────────────────────────────────────────────────────────
    print(f"\nEğitim başlıyor: {args.epochs} epoch, batch={args.batch_size}, lr={args.lr}")
    train(model, train_dl, val_dl, optimizer, scheduler, args.epochs, device, adapter_dir)

    print(f"\nTamamlandı. Adapter: {adapter_dir}/")
    print(f"Kullanmak için: python batch.py <fis_klasoru> --engine trocr")


if __name__ == "__main__":
    main()
