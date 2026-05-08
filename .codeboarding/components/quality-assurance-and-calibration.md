---
component_id: 5
component_name: Quality Assurance & Calibration
---

# Quality Assurance & Calibration

## Component Description

Manages the system's reliability and evolution. It tracks processed state to prevent duplicates, runs regression snapshots to ensure parser changes don't break existing logic, and provides tools for fine-tuning OCR models (TrOCR) and correction tables.

---

## Key References:

### c:\PTAReceiptParser\processed.py (lines 49-51)
```
def is_processed(ocr_name: str, system: str) -> Optional[dict]:
    """Daha önce işlendiyse bilgi dict'ini döner, değilse None."""
    return _load().get(system, {}).get(ocr_name)
```

### c:\PTAReceiptParser\snapshots.py (lines 141-188)
```
def run_regression(cache_dir: Path = OCR_CACHE_DIR) -> int:
    """
    Tüm snapshot'ları yeniden parse ederek regresyon testi yapar.
    Döndürür: bulunan regresyon sayısı.
    """
    data = _load_snapshots()
    if not data:
        print("Henüz hiç snapshot kaydedilmemiş.")
        return 0

    print("=" * 60)
    print(f"  Regresyon Testi  ({len(data)} snapshot)")
    print("=" * 60)

    regressions = 0
    ok_count     = 0
    skip_count   = 0

    for key, snap in data.items():
        ocr_file = cache_dir / key
        if not ocr_file.exists():
            print(f"  [ATLANDI]  {key}  (OCR cache bulunamadi)")
            skip_count += 1
            continue

        try:
            ocr_json = json.loads(ocr_file.read_text(encoding="utf-8"))
            receipt  = parse_receipt(ocr_json)
        except Exception as e:
            print(f"  [HATA]     {key}  ({e})")
            regressions += 1
            continue

        diffs = check_snapshot(ocr_file, receipt)
        if diffs:
            print(f"  [REGRESYON]  {key}")
            for d in diffs:
                print(f"      - {d}")
            regressions += 1
        else:
            print(f"  [OK]         {key}")
            ok_count += 1

    print("-" * 60)
    print(f"  Sonuc: {ok_count} OK, {regressions} regresyon, {skip_count} atlandi")
    print("=" * 60)
    print()
    return regressions
```

### c:\PTAReceiptParser\train_trocr.py (lines 161-199)
```
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
```


## Source Files:

- `build_corrections.py`
- `correct_labels.py`
- `parser.py`
- `processed.py`
- `snapshots.py`
- `train_trocr.py`

