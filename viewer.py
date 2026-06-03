"""
viewer.py — OCR Cache Viewer (Textual TUI)
==========================================
.ocr_cache/ içindeki tüm OCR JSON dosyalarını split-panel TUI'da görüntüler.

Klavye:
  ↑↓       — dosya seçimi / sağ panel scroll
  Tab      — sol ↔ sağ panel geçişi
  P        — parse görünümü
  D        — debug görünümü (tüm detection koordinatları)
  J        — raw JSON görünümü
  S        — sıralama modu değiştir (isim / durum / tarih / toplam)
  R        — seçili dosyayı yeniden parse et
  H        — seçili fişi hledger'a yaz
  X        — seçili fişi Excel'e yaz
  [ / ]    — sol panel daralt / genişlet
  ?        — yardım
  q / Esc  — ana menüye dön
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Union

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.rule import Rule

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import Header, Footer, ListView, ListItem, Static
from textual.containers import Horizontal, ScrollableContainer
from textual._work_decorator import work

try:
    from config import (
        OCR_CACHE_DIR, OCR_CACHE_DIR_TROCR, OCR_CACHE_DIR_EASY,
        PROCESSED_FILE,
    )
except Exception:
    OCR_CACHE_DIR       = Path(".ocr_cache")
    OCR_CACHE_DIR_TROCR = Path(".ocr_cache_trocr")
    OCR_CACHE_DIR_EASY  = Path(".ocr_cache_easyocr")
    PROCESSED_FILE      = Path(".ocr_cache/processed.json")

# Engine listesi — sadece var olan cache klasörleri gösterilir
_ENGINES: list[tuple[str, Path]] = [
    ("paddle",  OCR_CACHE_DIR),
    ("trocr",   OCR_CACHE_DIR_TROCR),
    ("easyocr", OCR_CACHE_DIR_EASY),
]

try:
    from parser import parse_receipt, load_detections, Receipt
except Exception as _parser_err:
    parse_receipt   = None  # type: ignore[assignment]
    load_detections = None  # type: ignore[assignment]
    Receipt         = None  # type: ignore[assignment]
    _PARSER_ERR     = _parser_err
else:
    _PARSER_ERR = None

PY      = sys.executable
console = Console()

# ── Sıralama modları ──────────────────────────────────────────────────────────

_SORT_MODES = ["isim", "durum", "tarih", "toplam"]

# ── Yardım metni ─────────────────────────────────────────────────────────────

_HELP_TEXT = """\
[bold cyan]Fiş Görüntüleyici — Klavye Kısayolları[/]

[bold]Gezinme[/]
  [yellow]↑ / ↓[/]      Dosya listesinde yukarı / aşağı
  [yellow]Tab[/]        Sol ↔ sağ panel geçişi
  [yellow]q / Esc[/]    Ana menüye dön

[bold]Görünüm[/]
  [yellow]P[/]          Parse özeti
  [yellow]D[/]          Debug — tüm detection koordinatları
  [yellow]J[/]          Raw JSON

[bold]Liste[/]
  [yellow]S[/]          Sıralama modu döngüsü  (isim → durum → tarih → toplam)
  [yellow]E[/]          OCR engine değiştir  (paddle → trocr → easyocr)
  [yellow][ / ][/]      Sol panel daralt / genişlet (4'er karakter)

[bold]İşlemler[/]
  [yellow]R[/]          Seçili dosyayı yeniden parse et
  [yellow]Space[/]      Dosyayı seçim listesine ekle / çıkar
  [yellow]A[/]          Tümünü seç / tümünün seçimini kaldır
  [yellow]T[/]          Yazma hedeflerini ayarla (hledger / excel / sheet)
  [yellow]H[/]          Seçili(ler)i hledger hedefine yaz  (seçim yoksa mevcut dosya)
  [yellow]X[/]          Seçili(ler)i Excel hedefine yaz    (seçim yoksa mevcut dosya)

[bold]İkonlar[/]
  [green]✓[/]  Parse başarılı, tutarlar eşleşiyor
  [yellow]⚠[/]  Parse başarılı ama sorun var
  [red]✗[/]  Parse başarısız
  📒  hledger'a yazılmış
  📊  Excel'e yazılmış

[dim]Kapatmak için Esc veya ?[/]
"""


# ── Mesajlar ─────────────────────────────────────────────────────────────────

class FileReady(Message):
    def __init__(self, orig_idx: int) -> None:
        super().__init__()
        self.orig_idx = orig_idx


class ProcessedReloaded(Message):
    pass


# ── Yardım modal ekranı ───────────────────────────────────────────────────────

class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Kapat"),
        Binding("q",      "dismiss", "Kapat", show=False),
        Binding("?",      "dismiss", "Kapat", show=False),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help_panel {
        width: 62;
        height: auto;
        max-height: 90vh;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(_HELP_TEXT, id="help_panel", markup=True)


# ── Viewer Screen ─────────────────────────────────────────────────────────────

class ViewerScreen(Screen):
    """Split-panel OCR cache browser."""

    TITLE = "Fiş Görüntüleyici"

    BINDINGS = [
        Binding("q",      "go_back",      "Ana Menü"),
        Binding("escape", "go_back",      "Geri",       show=False),
        Binding("p",      "show_parse",   "Parse"),
        Binding("d",      "show_debug",   "Debug"),
        Binding("j",      "show_json",    "JSON"),
        Binding("s",      "cycle_sort",   "Sırala"),
        Binding("e",      "cycle_engine", "Engine"),
        Binding("r",      "reparse",      "Re-parse"),
        Binding("space",  "toggle_select","Seç",        show=False),
        Binding("a",      "select_all",   "Tümü"),
        Binding("t",      "set_targets",  "Hedef"),
        Binding("h",      "write_hledger","→ hledger"),
        Binding("x",      "write_excel",  "→ Excel"),
        Binding("[",      "narrow_panel", "◀",          show=False),
        Binding("]",      "widen_panel",  "▶",          show=False),
        Binding("question_mark", "show_help", "?"),
    ]

    _LIST_WIDTH_MIN = 20
    _LIST_WIDTH_MAX = 80
    _LIST_WIDTH_DEF = 38

    DEFAULT_CSS = """
    ViewerScreen { layout: vertical; }

    #viewer_main {
        layout: horizontal;
        height: 1fr;
    }
    #file_list {
        width: 38;
        border-right: solid $primary-darken-2;
    }
    #file_list > ListItem { padding: 0 1; }
    #file_list > ListItem.--highlight { background: $accent 20%; }

    #right_panel { width: 1fr; padding: 0 1; }
    #receipt_view { padding: 1 1; }

    #sort_bar {
        height: 1;
        padding: 0 1;
        background: $surface-darken-1;
        color: $text-muted;
    }
    #engine_bar {
        height: 1;
        padding: 0 1;
        background: $surface-darken-3;
        color: $text-muted;
    }
    #target_bar {
        height: 1;
        padding: 0 1;
        background: $surface-darken-2;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        # Dosya listesi (orijinal sıra, hiç değişmez)
        self._files: list[Path] = []
        # Orijinal indeks → parse sonucu / raw JSON
        self._cache: dict[int, Union[Receipt, Exception]] = {}
        self._raw_jsons: dict[int, dict] = {}
        # Görüntüleme sırası: liste[orijinal_indeks]
        self._display_order: list[int] = []
        # Seçili dosyanın orijinal indeksi
        self._selected_orig_idx: int = 0
        # processed.json içeriği (ikonlar için)
        self._processed: dict = {"hledger": {}, "excel": {}}
        # ── Session hedefleri ────────────────────────────────────────────────
        self._target_hledger: str = ""   # hledger dosya yolu
        self._target_excel:   str = ""   # Excel dosya yolu
        self._target_sheet:   str = ""   # Excel sheet adı (boş = ilk)
        # ── Toplu seçim ──────────────────────────────────────────────────────
        self._selected_files: set[int] = set()  # seçili dosyaların orig_idx'leri
        # ── Engine seçimi ────────────────────────────────────────────────────
        self._engine_idx: int = 0   # _ENGINES listesindeki index
        # Görünüm modu ve sıralama
        self._mode: str = "p"
        self._sort_idx: int = 0
        self._list_width: int = self._LIST_WIDTH_DEF

    # ── Compose ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="engine_bar",  markup=True)
        yield Static("", id="sort_bar",    markup=True)
        yield Static("", id="target_bar",  markup=True)
        with Horizontal(id="viewer_main"):
            yield ListView(id="file_list")
            with ScrollableContainer(id="right_panel"):
                yield Static("", id="receipt_view", markup=True)
        yield Footer()

    # ── Mount ─────────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._update_engine_bar()
        self._reload()

    def _reload(self) -> None:
        """Aktif engine'in cache klasörünü yükle, listeyi sıfırla, parse başlat."""
        if _PARSER_ERR:
            self.query_one("#receipt_view", Static).update(
                f"[red]parser.py yüklenemedi:[/]\n{_PARSER_ERR}"
            )
            return

        _, cache_dir = _ENGINES[self._engine_idx]

        # State sıfırla
        self._cache.clear()
        self._raw_jsons.clear()
        self._selected_files.clear()
        self._selected_orig_idx = 0

        self._files = sorted(
            f for f in cache_dir.glob("*.json")
            if f.name != "processed.json"
        ) if cache_dir.exists() else []

        if not self._files:
            self.query_one("#file_list", ListView).clear()
            self.query_one("#receipt_view", Static).update(
                f"[yellow]Bu engine için cache boş:[/] {cache_dir}"
            )
            self._display_order = []
            self._update_sort_bar()
            self._update_target_bar()
            return

        self._load_processed()
        self._display_order = list(range(len(self._files)))
        self._rebuild_list()
        self._update_sort_bar()
        self._update_target_bar()
        self.query_one("#receipt_view", Static).update("⏳ [dim]Parse ediliyor...[/]")
        self._parse_all_worker()

    # ── processed.json yükleme ────────────────────────────────────────────────

    def _load_processed(self) -> None:
        try:
            if PROCESSED_FILE.exists():
                self._processed = json.loads(
                    PROCESSED_FILE.read_text(encoding="utf-8")
                )
            else:
                self._processed = {"hledger": {}, "excel": {}}
        except Exception:
            self._processed = {"hledger": {}, "excel": {}}

    # ── Liste oluşturma ───────────────────────────────────────────────────────

    def _build_item_text(self, orig_idx: int) -> str:
        """Bir liste satırının tam metnini üretir."""
        f      = self._files[orig_idx]
        result = self._cache.get(orig_idx)
        proc_h = f.name in self._processed.get("hledger", {})
        proc_x = f.name in self._processed.get("excel",   {})
        badges  = ("📒" if proc_h else "") + ("📊" if proc_x else "")
        sel_pfx = "[bold cyan]●[/] " if orig_idx in self._selected_files else "  "

        if result is None:
            return f"{sel_pfx}⏳ [dim]{f.name}[/]"

        icon, color, detail = self._receipt_icon(result)
        name_part = f"[{color}]{icon}[/] {f.name}"
        if badges:
            name_part += f"  {badges}"
        return f"{sel_pfx}{name_part}\n    [dim]{detail}[/]"

    def _rebuild_list(self) -> None:
        """Liste içeriğini sıfırdan oluşturur.
        clear() + append() aynı render döngüsünde çakışmasın diye
        clear önce, populate call_after_refresh ile sonra çalışır.
        """
        lv = self.query_one("#file_list", ListView)
        lv.clear()
        self.call_after_refresh(self._populate_list)

    def _populate_list(self) -> None:
        """_rebuild_list'in ikinci adımı: temizlenmiş listeyi doldur."""
        lv = self.query_one("#file_list", ListView)
        for display_pos, orig_idx in enumerate(self._display_order):
            lv.append(ListItem(
                Static(self._build_item_text(orig_idx), markup=True),
                id=f"fi_{display_pos}",
            ))

    def _resort_list(self) -> None:
        """Sıralama değişince tüm item metinlerini yerinde günceller.
        DOM add/remove yok → ID çakışması yok.
        """
        for display_pos, orig_idx in enumerate(self._display_order):
            try:
                item  = self.query_one(f"#fi_{display_pos}", ListItem)
                label = item.query_one(Static)
                label.update(self._build_item_text(orig_idx))
            except Exception:
                pass

    def _update_item(self, orig_idx: int) -> None:
        """Worker'dan gelen güncelleme: orig_idx'in display pozisyonunu bul, metni güncelle."""
        try:
            display_pos = self._display_order.index(orig_idx)
            item  = self.query_one(f"#fi_{display_pos}", ListItem)
            label = item.query_one(Static)
            label.update(self._build_item_text(orig_idx))
        except Exception:
            pass

    def _update_engine_bar(self) -> None:
        parts = []
        for i, (name, cache_dir) in enumerate(_ENGINES):
            exists = cache_dir.exists()
            if i == self._engine_idx:
                parts.append(f"[bold cyan]🔍 {name}[/]")
            elif exists:
                parts.append(f"[dim]{name}[/]")
            else:
                parts.append(f"[dim strike]{name}[/]")
        self.query_one("#engine_bar", Static).update(
            " Engine:  " + "  │  ".join(parts) + "   [dim]E=değiştir[/]"
        )

    def _update_sort_bar(self) -> None:
        mode = _SORT_MODES[self._sort_idx]
        modes_str = "  ".join(
            f"[bold cyan]{m}[/]" if m == mode else f"[dim]{m}[/]"
            for m in _SORT_MODES
        )
        self.query_one("#sort_bar", Static).update(
            f" Sıralama: {modes_str}   [dim]S=değiştir[/]"
        )

    def _update_target_bar(self) -> None:
        parts: list[str] = []
        if self._target_hledger:
            fname = Path(self._target_hledger).name
            parts.append(f"📒 [cyan]{fname}[/]")
        else:
            parts.append("📒 [dim]—[/]")

        if self._target_excel:
            fname = Path(self._target_excel).name
            sheet = f"[{self._target_sheet}]" if self._target_sheet else ""
            parts.append(f"📊 [cyan]{fname}{sheet}[/]")
        else:
            parts.append("📊 [dim]—[/]")

        sel_str = (
            f"   [bold cyan]{len(self._selected_files)} seçili[/]  [dim]A=tümü  Space=seç/kaldır[/]"
            if self._selected_files else
            "   [dim]Space=seç  A=tümü[/]"
        )
        self.query_one("#target_bar", Static).update(
            " Hedef:  " + "   ".join(parts) + "   [dim]T=değiştir[/]" + sel_str
        )

    # ── Arka plan worker ─────────────────────────────────────────────────────

    @work(thread=True, name="parse_all")
    def _parse_all_worker(self) -> None:
        for i, f in enumerate(self._files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self._raw_jsons[i] = data
                self._cache[i] = parse_receipt(data)
            except Exception as exc:
                self._cache[i] = exc
            self.post_message(FileReady(i))

    @work(thread=True, name="reparse_one")
    def _reparse_single_worker(self, orig_idx: int) -> None:
        f = self._files[orig_idx]
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            self._raw_jsons[orig_idx] = data
            self._cache[orig_idx] = parse_receipt(data)
        except Exception as exc:
            self._cache[orig_idx] = exc
        self.post_message(FileReady(orig_idx))

    # ── Mesaj işleyicileri ────────────────────────────────────────────────────

    def on_file_ready(self, message: FileReady) -> None:
        orig_idx = message.orig_idx
        if orig_idx >= len(self._files):
            return
        self._update_item(orig_idx)
        if orig_idx == self._selected_orig_idx:
            self._refresh_right()

    def _item_id_to_orig_idx(self, item_id: str) -> int | None:
        """fi_{display_pos} → orig_idx dönüşümü."""
        if item_id.startswith("fi_"):
            try:
                display_pos = int(item_id[3:])
                return self._display_order[display_pos]
            except (ValueError, IndexError):
                pass
        return None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Ok tuşlarıyla gezinince tetiklenir — sağ paneli güncelle."""
        if event.item is None:
            return
        orig_idx = self._item_id_to_orig_idx(event.item.id or "")
        if orig_idx is not None:
            self._selected_orig_idx = orig_idx
            self._refresh_right()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter/tıklama ile seçimde tetiklenir."""
        orig_idx = self._item_id_to_orig_idx(event.item.id or "")
        if orig_idx is not None:
            self._selected_orig_idx = orig_idx
            self._refresh_right()

    # ── Sağ panel ─────────────────────────────────────────────────────────────

    def _refresh_right(self) -> None:
        view = self.query_one("#receipt_view", Static)
        idx  = self._selected_orig_idx

        if idx not in self._cache:
            view.update("⏳ [dim]Parse ediliyor...[/]")
            return

        result = self._cache[idx]

        if self._mode == "j":
            raw = self._raw_jsons.get(idx, {})
            view.update(
                "[dim]--- raw JSON ---[/]\n" +
                json.dumps(raw, indent=2, ensure_ascii=False).replace("[", "\\[")
            )
        elif self._mode == "d":
            view.update(self._format_debug(idx))
        else:
            if isinstance(result, Exception):
                view.update(f"[bold red]Parse hatası:[/]\n\n{result}")
            else:
                view.update(self._format_receipt(result))

    def _format_receipt(self, r: Receipt) -> str:
        lines: list[str] = []
        lines.append(
            f"[bold cyan]{r.store}[/]"
            + ("  [dim]│[/]  " + r.date if r.date else "")
        )
        lines.append("[dim]" + "─" * 55 + "[/]")

        for item in r.items:
            name = item.name.replace("[", "\\[")
            lines.append(f"  {name:<38} [green]{item.amount:>8.2f} TL[/]")

        lines.append("[dim]" + "─" * 55 + "[/]")
        calc = sum(i.amount for i in r.items)
        lines.append(f"  {'Hesaplanan toplam':<38} [bold]{calc:>8.2f} TL[/]")

        warnings: list[str] = []
        if r.total:
            lines.append(f"  {'Fişteki toplam':<38} [bold]{r.total:>8.2f} TL[/]")
            diff = abs(calc - r.total)
            if diff > 0.02:
                warnings.append(f"⚠  Fark: {diff:.2f} TL (KDV/indirim olabilir)")
            else:
                warnings.append("✓ Tutarlar eşleşiyor")
        elif r.items:
            warnings.append("⚠  Toplam bulunamadı")

        if not r.date:
            warnings.append("⚠  Tarih bulunamadı")
        if not r.items:
            warnings.append("⚠  Hiç kalem çıkarılamadı")

        for w in warnings:
            color = "green" if w.startswith("✓") else "yellow"
            lines.append(f"\n[{color}]{w}[/]")

        # processed.json bilgisi
        fname = self._files[self._selected_orig_idx].name
        proc_lines: list[str] = []
        if fname in self._processed.get("hledger", {}):
            h = self._processed["hledger"][fname]
            proc_lines.append(f"📒 hledger satır {h.get('tx_line','?')}  ({h.get('updated_at','')})")
        if fname in self._processed.get("excel", {}):
            x = self._processed["excel"][fname]
            proc_lines.append(f"📊 Excel satır {x.get('row','?')}  sheet:{x.get('sheet','?')}  ({x.get('updated_at','')})")
        if proc_lines:
            lines.append("\n[dim]" + "─" * 55 + "[/]")
            for pl in proc_lines:
                lines.append(f"[dim]{pl}[/]")

        lines.append(
            f"\n[dim]{len(r.items)} kalem  │  {len(r.raw_detections)} detection[/]"
        )
        return "\n".join(lines)

    def _format_debug(self, idx: int) -> str:
        raw = self._raw_jsons.get(idx)
        if not raw:
            return "[dim]JSON henüz yüklenmedi[/]"

        lines: list[str] = []
        fname = self._files[idx].name if idx < len(self._files) else "?"
        lines.append(f"[bold]Detection Listesi — {fname}[/]\n")

        try:
            dets = load_detections(raw)
            lines.append(
                f"[dim]{'#':>4}  {'x_min':>6} {'x_max':>6}  "
                f"{'y_min':>6} {'y_max':>6}  {'conf':>5}  metin[/]"
            )
            lines.append("[dim]" + "─" * 70 + "[/]")
            for i, d in enumerate(dets):
                text = d.text.replace("[", "\\[")
                lines.append(
                    f"[dim]{i:>4}[/]  "
                    f"[cyan]{d.x_min:>6.0f} {d.x_max:>6.0f}[/]  "
                    f"[magenta]{d.y_min:>6.0f} {d.y_max:>6.0f}[/]  "
                    f"[yellow]{d.confidence:>5.2f}[/]  {text}"
                )
        except Exception as exc:
            lines.append(f"[red]Detection yüklenemedi: {exc}[/]")

        result = self._cache.get(idx)
        if isinstance(result, Receipt):
            lines.append(f"\n[bold]Parse Özeti[/]")
            lines.append(f"  Store : {result.store}")
            lines.append(f"  Date  : {result.date}")
            lines.append(f"  Total : {result.total}")
            lines.append(f"  Items : {len(result.items)}")

        return "\n".join(lines)

    # ── Kalite ikonu ──────────────────────────────────────────────────────────

    @staticmethod
    def _receipt_icon(result) -> tuple[str, str, str]:
        if isinstance(result, Exception):
            return "✗", "red", str(result)[:55]
        if not isinstance(result, Receipt):
            return "✗", "red", "Bilinmeyen hata"

        issues: list[str] = []
        if not result.items:
            issues.append("kalem yok")
        if not result.date:
            issues.append("tarih yok")
        if not result.store or result.store.upper() in ("UNKNOWN", "?", ""):
            issues.append("mağaza tanımsız")
        if result.items and not result.total:
            issues.append("toplam yok")
        elif result.total and result.items:
            calc = sum(i.amount for i in result.items)
            if abs(calc - result.total) > 0.02:
                issues.append(f"toplam uyuşmuyor Δ{abs(calc - result.total):.2f}")

        store   = result.store or "?"
        date    = result.date  or "?"
        total   = f"₺{result.total:.2f}" if result.total else "?"
        summary = f"{store}  {date}  {total}"

        if issues:
            return "⚠", "yellow", f"{summary}  [{', '.join(issues)}]"
        return "✓", "green", summary

    # ── Sıralama ──────────────────────────────────────────────────────────────

    def _sort_key(self, orig_idx: int) -> tuple:
        mode   = _SORT_MODES[self._sort_idx]
        result = self._cache.get(orig_idx)

        if mode == "durum":
            if isinstance(result, Exception):       status = 2
            elif not isinstance(result, Receipt):   status = 2
            else:
                icon, _, _ = self._receipt_icon(result)
                status = {"✗": 2, "⚠": 1, "✓": 0}[icon]
            return (status, self._files[orig_idx].name)

        if mode == "tarih":
            if isinstance(result, Receipt) and result.date:
                return (result.date,)
            return ("9999",)

        if mode == "toplam":
            if isinstance(result, Receipt) and result.total:
                return (-result.total,)   # büyükten küçüğe
            return (0.0,)

        # isim (varsayılan)
        return (self._files[orig_idx].name,)

    # ── Aksiyonlar ────────────────────────────────────────────────────────────

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_show_parse(self) -> None:
        self._mode = "p"
        self._refresh_right()

    def action_show_debug(self) -> None:
        self._mode = "d"
        self._refresh_right()

    def action_show_json(self) -> None:
        self._mode = "j"
        self._refresh_right()

    def action_show_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_widen_panel(self) -> None:
        self._list_width = min(self._list_width + 4, self._LIST_WIDTH_MAX)
        self.query_one("#file_list").styles.width = self._list_width

    def action_narrow_panel(self) -> None:
        self._list_width = max(self._list_width - 4, self._LIST_WIDTH_MIN)
        self.query_one("#file_list").styles.width = self._list_width

    def action_cycle_engine(self) -> None:
        # Sıradaki mevcut cache'e geç (olmayan klasörleri atla)
        start = self._engine_idx
        for _ in range(len(_ENGINES)):
            self._engine_idx = (self._engine_idx + 1) % len(_ENGINES)
            _, cache_dir = _ENGINES[self._engine_idx]
            if cache_dir.exists():
                break
        if self._engine_idx == start:
            return  # Tek engine var, değişme
        self._update_engine_bar()
        self._reload()

    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(_SORT_MODES)
        self._display_order.sort(key=self._sort_key)
        self._resort_list()       # DOM rebuild yok, sadece text güncelle
        self._update_sort_bar()

    def action_reparse(self) -> None:
        idx = self._selected_orig_idx
        if idx >= len(self._files):
            return
        # Cache'i temizle, ikonu sıfırla
        self._cache.pop(idx, None)
        self._raw_jsons.pop(idx, None)
        self._update_item(idx)
        self.query_one("#receipt_view", Static).update("⏳ [dim]Yeniden parse ediliyor...[/]")
        self._reparse_single_worker(idx)

    def action_set_targets(self) -> None:
        """T — session yazma hedeflerini ayarla / değiştir."""
        with self.app.suspend():
            console.print()
            console.print(Rule("[cyan]Yazma Hedefleri[/]", style="cyan"))
            console.print("  [dim]Boş bırakılan değer değişmez. q = iptal.[/]\n")
            try:
                # hledger
                h = Prompt.ask(
                    "  hledger dosyası",
                    default=self._target_hledger
                ).strip()
                if h.lower() == "q":
                    return
                if h:
                    if not Path(h).exists():
                        console.print(f"  [red]Dosya bulunamadı:[/] {h}")
                    else:
                        self._target_hledger = h

                # Excel
                x = Prompt.ask(
                    "  Excel dosyası (.xlsx/.xlsm)",
                    default=self._target_excel
                ).strip()
                if x.lower() == "q":
                    return
                if x:
                    if not Path(x).exists():
                        console.print(f"  [red]Dosya bulunamadı:[/] {x}")
                    else:
                        self._target_excel = x

                # Sheet (sadece excel ayarlıysa anlam ifade eder)
                s = Prompt.ask(
                    "  Sheet adı [Enter=ilk sheet]",
                    default=self._target_sheet
                ).strip()
                if s.lower() == "q":
                    return
                self._target_sheet = s

                console.print()
                if self._target_hledger:
                    console.print(f"  📒 hledger : [cyan]{self._target_hledger}[/]")
                if self._target_excel:
                    sheet_str = f"  [{self._target_sheet}]" if self._target_sheet else ""
                    console.print(f"  📊 Excel   : [cyan]{self._target_excel}{sheet_str}[/]")

            except KeyboardInterrupt:
                console.print("\n  [yellow]İptal.[/]")
            finally:
                Prompt.ask("\n[dim]Devam için Enter[/]", default="", show_default=False)

        self._update_target_bar()

    def _run_write(self, mode: str) -> None:
        """H ve X için yazma — seçili varsa toplu, yoksa mevcut dosya."""
        # Hedef ayarlı mı?
        target = self._target_hledger if mode == "hledger" else self._target_excel
        label  = "hledger" if mode == "hledger" else "Excel"
        if not target:
            with self.app.suspend():
                console.print(
                    f"\n  [yellow]⚠  {label} hedefi ayarlanmamış.[/]  "
                    f"[dim]T tuşuyla ayarla.[/]"
                )
                Prompt.ask("[dim]Enter[/]", default="", show_default=False)
            return

        # Yazılacak dosyaları belirle
        if self._selected_files:
            write_idxs = sorted(self._selected_files)
        else:
            write_idxs = [self._selected_orig_idx]

        # Sadece parse edilebilen dosyalar
        valid = [
            i for i in write_idxs
            if i < len(self._files)
            and i in self._cache
            and not isinstance(self._cache[i], Exception)
        ]

        if not valid:
            with self.app.suspend():
                console.print("\n  [yellow]⚠  Yazılabilecek seçili fiş yok.[/]")
                Prompt.ask("[dim]Enter[/]", default="", show_default=False)
            return

        with self.app.suspend():
            console.print()
            n = len(valid)
            title = f"{label}'a Yaz — {n} fiş" if n > 1 else f"{label}'a Yaz — {self._files[valid[0]].name}"
            console.print(Rule(f"[cyan]{title}[/]", style="cyan"))
            console.print(f"  Hedef: [cyan]{target}[/]")
            if mode == "excel" and self._target_sheet:
                console.print(f"  Sheet: [cyan]{self._target_sheet}[/]")
            if n > 1:
                console.print()
                for i, idx in enumerate(valid):
                    console.print(f"  [dim]{i+1:>2}.[/] {self._files[idx].name}")
            console.print()

            try:
                api_key = Prompt.ask(
                    "  Anthropic API key [Enter=env/atla]", default="", password=True
                ) or None

                flag = f"--{mode}"
                base_cmd = [flag, target, "--force"]
                if mode == "excel" and self._target_sheet:
                    base_cmd += ["--sheet", self._target_sheet]
                if api_key:
                    base_cmd += ["--api-key", api_key]

                console.print()
                for i, idx in enumerate(valid):
                    json_file = self._files[idx]
                    if n > 1:
                        console.print(Rule(
                            f"[dim]({i+1}/{n}) {json_file.name}[/]", style="dim"
                        ))
                    cmd = [PY, "parser.py", str(json_file)] + base_cmd
                    subprocess.run(cmd)
                    if n > 1:
                        console.print()

            except KeyboardInterrupt:
                console.print("\n  [yellow]İptal.[/]")
            finally:
                Prompt.ask("\n[dim]Devam için Enter[/]", default="", show_default=False)

        # Seçimi temizle, ikonları güncelle
        self._selected_files.clear()
        self._load_processed()
        for idx in valid:
            self._update_item(idx)
        self._refresh_right()
        self._update_target_bar()

    def action_toggle_select(self) -> None:
        idx = self._selected_orig_idx
        if idx in self._selected_files:
            self._selected_files.discard(idx)
        else:
            self._selected_files.add(idx)
        self._update_item(idx)
        self._update_target_bar()

    def action_select_all(self) -> None:
        if len(self._selected_files) == len(self._files):
            self._selected_files.clear()
        else:
            self._selected_files = set(range(len(self._files)))
        self._resort_list()
        self._update_target_bar()

    def action_write_hledger(self) -> None:
        self._run_write("hledger")

    def action_write_excel(self) -> None:
        self._run_write("excel")
