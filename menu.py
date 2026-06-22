"""
menu.py — PTA Receipt Parser TUI Menü
======================================
Tüm araçları tek bir interaktif Textual TUI'dan kullanmak için.

Kullanım:
    py menu.py

Alt menülerde 'q' yazarak veya Esc'e basarak ana menüye dönebilirsiniz.
"""

import json
import subprocess
import sys
from pathlib import Path

# ── Rich (alt-menüler suspend modunda kullanır) ───────────────────────────────
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

# ── Textual ───────────────────────────────────────────────────────────────────
try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, ScrollableContainer
    from textual.screen import Screen
    from textual.widgets import Footer, Header, Static
    from textual.timer import Timer
except ImportError:
    print("textual kütüphanesi gerekli:  pip install textual")
    sys.exit(1)

# ── Viewer ────────────────────────────────────────────────────────────────────
from viewer import ViewerScreen

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


# ── Yardımcı: görünüm (suspend mod içinde kullanılır) ────────────────────────

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
    choices verilmişse bunlarla kısıtla.
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

    engine     = ask_str("OCR motoru", choices=["paddleocr", "paddleocr_server", "easyocr", "trocr"],
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


# ── Durum metni (Textual Static için) ────────────────────────────────────────

def _build_status_markup() -> str:
    """Ana menü durum panelinin rich markup metnini döndürür."""
    rows: list[tuple[str, str]] = []

    # OCR cache
    if OCR_CACHE_DIR.exists():
        jsons = [f for f in OCR_CACHE_DIR.glob("*.json") if f.name != "processed.json"]
        rows.append(("OCR cache", f"{len(jsons)} fiş"))
    else:
        rows.append(("OCR cache", "—"))

    # İşlenen fişler
    if PROCESSED_FILE.exists():
        try:
            data = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
            hledger_n = len(data.get("hledger", {}))
            excel_n   = len(data.get("excel", {}))
            rows.append(("İşlendi", f"hledger:{hledger_n}  excel:{excel_n}"))
        except Exception:
            rows.append(("İşlendi", "—"))
    else:
        rows.append(("İşlendi", "—"))

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
            rows.append(("Snapshot", "—"))
    else:
        rows.append(("Snapshot", "—"))

    lines = ["[bold dim]Durum[/]\n"]
    for label, val in rows:
        lines.append(f"  [dim]{label:<12}[/] {val}")

    if not _CFG_OK:
        lines.append("\n  [red dim]config.toml yok[/]")

    return "\n".join(lines)


# ── Menü tablosu metni ────────────────────────────────────────────────────────

_MENU_ITEMS = [
    ("1", "Fişleri İşle",          "batch.py"),
    ("2", "Fiş Analiz / Parse",     "parser.py"),
    ("3", "Görüntü Ön İşle",       "preProcess.py"),
    ("4", "OCR Düzeltme Araçları", "corrections / import"),
    ("5", "Regresyon Testi",        "snapshots.py"),
    ("6", "TrOCR Fine-tuning",      "train_trocr.py"),
    ("7", "LLM Parser",             "llm_parser.py"),
    ("8", "Fiş Görüntüleyici",      "viewer.py ✨"),
]

_SUBMENU_FNS = {
    "1": menu_process,
    "2": menu_parse,
    "3": menu_preprocess,
    "4": menu_corrections,
    "5": menu_regression,
    "6": menu_trocr,
    "7": menu_llm,
}


def _build_menu_markup() -> str:
    lines = [f"[bold cyan]{TITLE}[/]  [dim]|[/]  {SUBTITLE}\n"]
    for key, label, hint in _MENU_ITEMS:
        lines.append(
            f"  [bold green]\\[{key}][/]  {label:<28} [dim]{hint}[/]"
        )
    lines.append("")
    lines.append("  [bold red]\\[q][/]  Çıkış")
    return "\n".join(lines)


# ── Textual: Ana Menü Screen ──────────────────────────────────────────────────

class MainMenuScreen(Screen):
    """Ana menü — Textual tabanlı."""

    BINDINGS = [
        Binding("1", "submenu('1')", "Fişleri İşle",     show=False),
        Binding("2", "submenu('2')", "Fiş Analiz",        show=False),
        Binding("3", "submenu('3')", "Görüntü Ön İşle",  show=False),
        Binding("4", "submenu('4')", "Düzeltme",          show=False),
        Binding("5", "submenu('5')", "Regresyon",         show=False),
        Binding("6", "submenu('6')", "TrOCR",             show=False),
        Binding("7", "submenu('7')", "LLM Parser",        show=False),
        Binding("8", "open_viewer", "Görüntüleyici",      show=True),
        Binding("q", "app.quit",    "Çıkış",              show=True),
    ]

    DEFAULT_CSS = """
    MainMenuScreen {
        layout: vertical;
    }
    #main_row {
        layout: horizontal;
        height: 1fr;
        padding: 1 2;
        align: center middle;
    }
    #menu_box {
        width: 58;
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin-right: 2;
    }
    #status_box {
        width: 32;
        height: auto;
        border: round $surface-lighten-2;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main_row"):
            yield Static(_build_menu_markup(), id="menu_box",   markup=True)
            yield Static(_build_status_markup(), id="status_box", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(30, self._refresh_status)

    def _refresh_status(self) -> None:
        try:
            self.query_one("#status_box", Static).update(_build_status_markup())
        except Exception:
            pass

    def action_submenu(self, key: str) -> None:
        fn = _SUBMENU_FNS.get(key)
        if fn is None:
            return
        with self.app.suspend():
            try:
                fn()
            except (GoBack, KeyboardInterrupt):
                pass

    def action_open_viewer(self) -> None:
        self.app.push_screen(ViewerScreen())


# ── Textual App ───────────────────────────────────────────────────────────────

class PTAApp(App):
    TITLE   = TITLE
    CSS_PATH = None   # DEFAULT_CSS kullan

    def on_mount(self) -> None:
        self.push_screen(MainMenuScreen())


# ── Giriş noktası ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        PTAApp().run()
    except KeyboardInterrupt:
        pass
