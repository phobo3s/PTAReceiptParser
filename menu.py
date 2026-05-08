"""
menu.py — PTA Receipt Parser TUI Menü
======================================
Tüm araçları tek bir interaktif menüden kullanmak için.

Kullanım:
    py menu.py

Herhangi bir parametre sorusunda 'q' yazarak ana menüye dönebilirsiniz.
"""

import sys
import json
import subprocess
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.columns import Columns
    from rich.rule import Rule
    from rich import box
except ImportError:
    print("rich kütüphanesi gerekli:  pip install rich")
    sys.exit(1)

# ── Sabitler ──────────────────────────────────────────────────────────────────

console  = Console()
PY       = sys.executable
ROOT     = Path(__file__).parent
TITLE    = "PTA Receipt Parser"
SUBTITLE = "Türkçe Fiş İşleme Aracı"

# Config'den path'leri yükle (hata olursa güvenli fallback)
try:
    from config import (
        RECEIPTS_DIR,
        OCR_CACHE_DIR, RULES_FILE, RULES_LEARNED,
        PARSE_SNAPSHOTS_DIR, PROCESSED_FILE,
        PPOCR_DATA_DIR, PROCESSED_RECEIPTS_DIR,
    )
    _CFG_OK = True
except Exception:
    RECEIPTS_DIR          = ROOT / "Receipts"
    OCR_CACHE_DIR         = Path(".ocr_cache")
    RULES_FILE            = Path("rules.toml")
    RULES_LEARNED         = Path("rules_learned.toml")
    PARSE_SNAPSHOTS_DIR   = Path(".parse_snapshots")
    PROCESSED_FILE        = Path(".ocr_cache/processed.json")
    PPOCR_DATA_DIR        = Path("PPOCRLabel_Data/Receipts")
    PROCESSED_RECEIPTS_DIR = Path(".processedReceipts")
    _CFG_OK = False

# ── Geri dönüş sinyali ────────────────────────────────────────────────────────

class GoBack(Exception):
    """Kullanıcı 'q' yazarak bu menüden çıkmak istedi."""

# ── Yardımcı: görünüm ─────────────────────────────────────────────────────────

def clear() -> None:
    console.clear()


def header(breadcrumb: str = "") -> None:
    crumb = f"  [dim]{breadcrumb}[/]" if breadcrumb else ""
    console.print(Panel(
        f"[bold cyan]{TITLE}[/]  [dim]|[/]  [white]{SUBTITLE}[/]{crumb}",
        box=box.DOUBLE_EDGE,
        style="cyan",
        padding=(0, 2),
    ))
    console.print()


def pause() -> None:
    console.print()
    Prompt.ask("[dim]Devam etmek için Enter'a bas[/]", default="", show_default=False)


def section(title: str) -> None:
    console.print(Rule(f"[bold]{title}[/]", style="dim cyan"))
    console.print()


def back_hint() -> None:
    """Her parametre ekranında gösterilen 'q=geri' ipucu."""
    console.print("  [dim](Herhangi bir soruya [bold]q[/] yazarak ana menüye dönebilirsin)[/]\n")


# ── Yardımcı: path girişi ─────────────────────────────────────────────────────

def ask_path(
    prompt: str,
    must_exist: bool = True,
    is_dir: bool = False,
    optional: bool = False,
    default: str = "",
) -> str | None:
    """
    Path sor, validasyon yap.
    'q'  → GoBack exception (ana menüye dön)
    ''   → optional=True ise None, değilse tekrar sor
    """
    while True:
        val = Prompt.ask(f"  {prompt}", default=default).strip().strip('"').strip("'")

        if val.lower() == "q":
            raise GoBack

        if not val:
            if optional:
                return None
            console.print("  [red]Bu alan zorunlu.[/]")
            continue

        p = Path(val)
        if must_exist:
            if is_dir and not p.is_dir():
                console.print(f"  [red]Klasör bulunamadı:[/] {val}")
                continue
            if not is_dir and not p.exists():
                console.print(f"  [red]Dosya bulunamadı:[/] {val}")
                continue
        return val


def ask_str(prompt: str, default: str = "", choices: list[str] | None = None) -> str:
    """
    Serbest metin sor.
    'q' → GoBack exception
    choices verilmişse bunlarla kısıtla (q zaten ekleniyor).
    """
    if choices:
        choices_with_q = choices + ["q"]
        val = Prompt.ask(f"  {prompt}", choices=choices_with_q, default=default)
    else:
        val = Prompt.ask(f"  {prompt}", default=default)

    if val.lower() == "q":
        raise GoBack
    return val


# ── Yardımcı: komut çalıştır ─────────────────────────────────────────────────

def run_cmd(cmd: list[str], *, confirm: bool = True) -> None:
    """Komutu canlı çıktıyla çalıştır."""
    console.print()
    console.print(Rule("[dim]Komut başlatılıyor[/]", style="dim"))
    console.print(f"  [dim]$ {' '.join(str(c) for c in cmd)}[/]")
    console.print()

    if confirm:
        if not Confirm.ask("  Çalıştır?", default=True):
            console.print("  [yellow]İptal edildi.[/]")
            pause()
            return

    try:
        subprocess.run([str(c) for c in cmd], cwd=str(ROOT))
    except KeyboardInterrupt:
        console.print("\n  [yellow]Kullanıcı durdurdu.[/]")

    console.print()
    console.print(Rule("[dim]Tamamlandı[/]", style="dim"))
    pause()


# ── Durum paneli (ana menü için) ──────────────────────────────────────────────

def _status_panel() -> Panel:
    """OCR cache, rules, snapshot istatistikleri."""
    rows: list[tuple[str, str]] = []

    # OCR cache
    if OCR_CACHE_DIR.exists():
        jsons = [f for f in OCR_CACHE_DIR.glob("*.json") if f.name != "processed.json"]
        rows.append(("OCR cache", f"{len(jsons)} fiş"))
    else:
        rows.append(("OCR cache", "[dim]—[/]"))

    # İşlenen fişler
    if PROCESSED_FILE.exists():
        try:
            data = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
            hledger_n = len(data.get("hledger", {}))
            excel_n   = len(data.get("excel", {}))
            rows.append(("İşlendi", f"hledger:{hledger_n}  excel:{excel_n}"))
        except Exception:
            rows.append(("İşlendi", "[dim]—[/]"))
    else:
        rows.append(("İşlendi", "[dim]—[/]"))

    # Rules
    n_rules = 0
    for rf in [RULES_FILE, RULES_LEARNED]:
        if rf.exists():
            try:
                import tomllib
                with open(rf, "rb") as f:
                    n_rules += len(tomllib.load(f).get("rule", []))
            except Exception:
                pass
    rows.append(("Kurallar", f"{n_rules} kural"))

    # Snapshots
    snap_file = PARSE_SNAPSHOTS_DIR / "snapshots.json"
    if snap_file.exists():
        try:
            snaps = json.loads(snap_file.read_text(encoding="utf-8"))
            rows.append(("Snapshot", f"{len(snaps)} fiş"))
        except Exception:
            rows.append(("Snapshot", "[dim]—[/]"))
    else:
        rows.append(("Snapshot", "[dim]—[/]"))

    if not _CFG_OK:
        rows.append(("", "[red dim]config.toml yok[/]"))

    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="dim", no_wrap=True)
    t.add_column(style="white")
    for label, val in rows:
        t.add_row(label, val)

    return Panel(t, title="[dim]Durum[/]", border_style="dim", padding=(0, 1))


# ── Alt menü: Fişleri İşle ───────────────────────────────────────────────────

def menu_process() -> None:
    clear()
    header("Ana Menü › Fişleri İşle")
    section("Parametreler")
    back_hint()

    receipts_default = str(RECEIPTS_DIR) if RECEIPTS_DIR.is_dir() else ""

    receipt_dir = ask_path("Fiş klasörü", must_exist=True, is_dir=True,
                            default=receipts_default)
    hledger     = ask_path("hledger dosyası [Enter=atla]", must_exist=True, optional=True)
    excel       = ask_path("Excel dosyası  [Enter=atla]",  must_exist=True, optional=True)
    sheet: str | None = None
    if excel:
        sheet_raw = ask_str("Excel sheet adı [Enter=ilk sheet]", default="")
        sheet = sheet_raw or None

    engine     = ask_str("OCR motoru", choices=["paddleocr", "easyocr", "trocr"],
                          default="paddleocr")
    api_key    = Prompt.ask("  Anthropic API key [Enter=atla]", default="", password=True) or None
    preprocess = Confirm.ask("  Görüntü ön işleme uygula?", default=False)
    force      = Confirm.ask("  Daha önce işlenenleri tekrar işle?", default=False)

    if not hledger and not excel:
        console.print("\n  [yellow]⚠  Güncelleme kanalı yok — sadece OCR + parse yapılacak.[/]")

    cmd = [PY, "batch.py", receipt_dir, "--engine", engine]
    if hledger:    cmd += ["--hledger", hledger]
    if excel:      cmd += ["--excel", excel]
    if sheet:      cmd += ["--sheet", sheet]
    if api_key:    cmd += ["--api-key", api_key]
    if preprocess: cmd += ["--preprocess"]
    if force:      cmd += ["--force"]

    run_cmd(cmd)


# ── Alt menü: Fiş Analiz ─────────────────────────────────────────────────────

def menu_parse() -> None:
    clear()
    header("Ana Menü › Fiş Analiz")
    section("Parametreler")
    back_hint()

    # Default: config'deki OCR cache klasörü
    cache_default = str(OCR_CACHE_DIR)

    input_path    = ask_path("OCR JSON dosyası veya klasörü", must_exist=True,
                              default=cache_default)
    debug         = Confirm.ask("  Debug modu (satır satır iz)?", default=False)
    mismatch_only = Confirm.ask("  Sadece hatalı fişleri göster?",  default=False)
    hledger       = ask_path("hledger dosyası [Enter=atla]", must_exist=True, optional=True)
    excel         = ask_path("Excel dosyası  [Enter=atla]",  must_exist=True, optional=True)
    sheet: str | None = None
    if excel:
        sheet_raw = ask_str("Excel sheet adı [Enter=ilk sheet]", default="")
        sheet = sheet_raw or None
    force = Confirm.ask("  Zaten işlenenleri tekrar işle?", default=False)

    cmd = [PY, "parser.py", input_path]
    if debug:          cmd += ["--debug"]
    if mismatch_only:  cmd += ["--mismatch-only"]
    if hledger:        cmd += ["--hledger", hledger]
    if excel:          cmd += ["--excel", excel]
    if sheet:          cmd += ["--sheet", sheet]
    if force:          cmd += ["--force"]

    run_cmd(cmd)


# ── Alt menü: Görüntü Ön İşle ────────────────────────────────────────────────

def menu_preprocess() -> None:
    clear()
    header("Ana Menü › Görüntü Ön İşle")

    console.print("  [dim]Pipeline:[/] upscale → deskew → perspektif → BG normalize →")
    console.print("  [dim]          gamma → CLAHE → denoise → sharpen → crop[/]")
    console.print()
    section("Parametreler")
    back_hint()

    # Receipts/ yoksa PROCESSED_RECEIPTS_DIR dene, son çare boş
    if RECEIPTS_DIR.is_dir():
        target_default = str(RECEIPTS_DIR)
    elif PROCESSED_RECEIPTS_DIR.is_dir():
        target_default = str(PROCESSED_RECEIPTS_DIR)
    else:
        target_default = ""

    target    = ask_path("Klasör veya tek görüntü", must_exist=True, default=target_default)
    engine    = ask_str("Hedef motor", choices=["paddle", "tesseract"], default="paddle")

    gamma_str = ask_str("Gamma değeri [0=kapalı, önerilen: 0.7]", default="0")
    try:
        gamma = float(gamma_str)
    except ValueError:
        gamma = 0.0

    sharpen  = Confirm.ask("  Unsharp masking (kenar keskinleştirme)?", default=False)
    no_debug = not Confirm.ask("  Debug görüntüleri kaydet?", default=True)

    cmd = [PY, "preProcess.py", target, "--engine", engine]
    if gamma > 0:  cmd += ["--gamma", str(gamma)]
    if sharpen:    cmd += ["--sharpen"]
    if no_debug:   cmd += ["--no-debug"]

    run_cmd(cmd)


# ── Alt menü: OCR Düzeltme Araçları ─────────────────────────────────────────

def menu_corrections() -> None:
    while True:
        clear()
        header("Ana Menü › OCR Düzeltme Araçları")

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        t.add_column(style="bold yellow", no_wrap=True, width=5)
        t.add_column(no_wrap=True)
        t.add_column(style="dim")
        t.add_row("[a]", "Düzeltme sözlüğü oluştur",   "Cache.cach + Label.txt → corrections.toml")
        t.add_row("[b]", "Etiket import",               "Label.txt → .ocr_cache/")
        t.add_row("[c]", "Label.txt otomatik düzelt",   "Claude Haiku Vision ile crop'ları düzelt")
        t.add_row("[d]", "Tesseract cache oluştur",     "Paddle cache'ten Tesseract cache üret")
        t.add_row("",    "", "")
        t.add_row("[q]", "[dim]← Geri[/]", "")
        console.print(t)
        console.print()

        choice = Prompt.ask("  Seçim", choices=["a", "b", "c", "d", "q"], default="q")

        if choice == "q":
            break

        if choice == "a":
            run_cmd([PY, "build_corrections.py"])

        elif choice == "b":
            try:
                all_caches = Confirm.ask("  Hem paddle hem trocr cache'e yaz?", default=False)
                cmd = [PY, "import_labels.py"]
                if all_caches:
                    cmd += ["--all-caches"]
                run_cmd(cmd)
            except GoBack:
                pass

        elif choice == "c":
            try:
                console.print("  [dim]API key ANTHROPIC_API_KEY env değişkeninden okunur.[/]")
                label_default = str(PPOCR_DATA_DIR / "Label.txt") if (PPOCR_DATA_DIR / "Label.txt").exists() else ""
                label_path = ask_path("Label.txt yolu [Enter=varsayılan]",
                                       must_exist=True, optional=True, default=label_default)
                cmd = [PY, "correct_labels.py"]
                if label_path:
                    cmd += [label_path]
                run_cmd(cmd)
            except GoBack:
                pass

        elif choice == "d":
            run_cmd([PY, "generate_tesseract_cache.py"])


# ── Alt menü: Regresyon Testi ────────────────────────────────────────────────

def menu_regression() -> None:
    clear()
    header("Ana Menü › Regresyon Testi")

    console.print("  [dim]Kaydedilmiş tüm snapshot'ları yeniden parse eder.[/]")
    console.print("  [dim]Kod veya regex değişikliklerinden sonra çalıştır.[/]")
    console.print()
    section("Parametreler")
    back_hint()

    # Default: config'deki OCR cache klasörü
    cache_dir = ask_str("OCR cache klasörü", default=str(OCR_CACHE_DIR))

    run_cmd([PY, "snapshots.py", "--regression", "--cache-dir", cache_dir])


# ── Alt menü: TrOCR Fine-tune ────────────────────────────────────────────────

def menu_trocr() -> None:
    clear()
    header("Ana Menü › TrOCR Fine-tuning")

    console.print("  [dim]microsoft/trocr-base-printed'ı LoRA ile artımlı fine-tune eder.[/]")
    console.print("  [dim]Adapter her epoch sonunda .trocr_adapter/ klasörüne kaydedilir.[/]")
    console.print()
    section("Parametreler")
    back_hint()

    epochs      = ask_str("Epoch sayısı",            default="1")
    batch_size  = ask_str("Batch size",               default="4")
    lr          = ask_str("Learning rate",            default="5e-4")
    val_split   = ask_str("Validation oranı (0=yok)", default="0")
    no_continue = Confirm.ask("  Sıfırdan başla (mevcut adapter'ı yoksay)?", default=False)

    cmd = [PY, "train_trocr.py",
           "--epochs",     epochs,
           "--batch-size", batch_size,
           "--lr",         lr,
           "--val-split",  val_split]
    if no_continue:
        cmd += ["--no-continue"]

    run_cmd(cmd)


# ── Alt menü: LLM Parser ─────────────────────────────────────────────────────

def menu_llm() -> None:
    while True:
        clear()
        header("Ana Menü › LLM Parser")

        console.print("  [dim]Regex parser yerine Claude API ile fiş parse eder.[/]")
        console.print()

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        t.add_column(style="bold yellow", no_wrap=True, width=5)
        t.add_column(no_wrap=True)
        t.add_column(style="dim")
        t.add_row("[a]", "Regex vs LLM karşılaştırma",  "Tüm cache — sonuçları yan yana gösterir")
        t.add_row("[b]", "Tek dosya LLM parse",          "Tek OCR JSON → LLM ile Receipt")
        t.add_row("[c]", "Dry-run",                      "LLM'e gidecek metni göster, API çağırma")
        t.add_row("",    "", "")
        t.add_row("[q]", "[dim]← Geri[/]", "")
        console.print(t)
        console.print()

        choice = Prompt.ask("  Seçim", choices=["a", "b", "c", "q"], default="q")

        if choice == "q":
            break

        if choice == "a":
            try:
                api_key = Prompt.ask("  Anthropic API key [Enter=env]", default="", password=True) or None
                cmd = [PY, "llm_parser.py", "--compare"]
                if api_key:
                    cmd += ["--api-key", api_key]
                run_cmd(cmd)
            except GoBack:
                pass

        elif choice == "b":
            try:
                json_default = str(OCR_CACHE_DIR) if OCR_CACHE_DIR.exists() else ""
                json_path = ask_path("OCR JSON dosyası veya klasörü", must_exist=True,
                                      default=json_default)
                api_key = Prompt.ask("  Anthropic API key [Enter=env]", default="", password=True) or None
                cmd = [PY, "llm_parser.py", json_path]
                if api_key:
                    cmd += ["--api-key", api_key]
                run_cmd(cmd)
            except GoBack:
                pass

        elif choice == "c":
            run_cmd([PY, "llm_parser.py", "--dry-run"], confirm=False)


# ── Ana menü ─────────────────────────────────────────────────────────────────

MENU_ITEMS = [
    ("1", "Fişleri İşle",           "batch.py",              menu_process),
    ("2", "Fiş Analiz / Parse",      "parser.py",             menu_parse),
    ("3", "Görüntü Ön İşle",        "preProcess.py",         menu_preprocess),
    ("4", "OCR Düzeltme Araçları",  "corrections / import",  menu_corrections),
    ("5", "Regresyon Testi",         "snapshots.py",          menu_regression),
    ("6", "TrOCR Fine-tuning",       "train_trocr.py",        menu_trocr),
    ("7", "LLM Parser",              "llm_parser.py",         menu_llm),
]


def main_menu() -> None:
    while True:
        clear()
        header()

        # ── Sol: Menü tablosu ──────────────────────────────────────────────
        menu_table = Table(
            box=box.SIMPLE,
            show_header=False,
            padding=(0, 1),
            min_width=46,
        )
        menu_table.add_column(style="bold green", no_wrap=True, width=4)
        menu_table.add_column(width=30)
        menu_table.add_column(style="dim", width=22)

        for key, label, hint, _ in MENU_ITEMS:
            menu_table.add_row(f"[{key}]", label, hint)

        menu_table.add_row("", "", "")
        menu_table.add_row("[q]", "[red]Çıkış[/]", "")

        # ── Sağ: Durum paneli ─────────────────────────────────────────────
        try:
            status = _status_panel()
        except Exception:
            status = Panel("[dim]config.toml yüklenemedi[/]", title="[dim]Durum[/]",
                           border_style="dim", padding=(0, 1))

        # ── Yan yana göster ───────────────────────────────────────────────
        menu_panel = Panel(
            menu_table,
            title="[bold]ANA MENÜ[/]",
            border_style="cyan",
            padding=(1, 2),
        )
        console.print(Columns([menu_panel, status], padding=(0, 2), expand=False))
        console.print()

        valid = [item[0] for item in MENU_ITEMS] + ["q"]
        choice = Prompt.ask("  Seçim", choices=valid)

        if choice == "q":
            clear()
            console.print("[dim]Görüşürüz![/]\n")
            break

        for key, _, _, fn in MENU_ITEMS:
            if choice == key:
                try:
                    fn()
                except GoBack:
                    pass  # q'ya basıldı, ana menüye dön
                break


# ── Giriş noktası ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\n\n[dim]Çıkış.[/]\n")
