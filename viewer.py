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
  q / Esc  — ana menüye dön
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Static
from textual.containers import Horizontal, ScrollableContainer
from textual._work_decorator import work

try:
    from config import OCR_CACHE_DIR
except Exception:
    OCR_CACHE_DIR = Path(".ocr_cache")

try:
    from parser import parse_receipt, load_detections, Receipt
except Exception as _parser_err:
    parse_receipt   = None  # type: ignore[assignment]
    load_detections = None  # type: ignore[assignment]
    Receipt         = None  # type: ignore[assignment]
    _PARSER_ERR     = _parser_err
else:
    _PARSER_ERR = None


# ── Mesaj: worker → UI ───────────────────────────────────────────────────────

class FileReady(Message):
    """Bir dosyanın parse işlemi tamamlandığında yayınlanır."""
    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index


# ── Viewer Screen ─────────────────────────────────────────────────────────────

class ViewerScreen(Screen):
    """Split-panel OCR cache browser."""

    TITLE = "Fiş Görüntüleyici"

    BINDINGS = [
        Binding("q",      "go_back",      "Ana Menü"),
        Binding("escape", "go_back",      "Geri",      show=False),
        Binding("p",      "show_parse",   "Parse"),
        Binding("d",      "show_debug",   "Debug"),
        Binding("j",      "show_json",    "JSON"),
        Binding("[",      "narrow_panel", "◀ Daralt",  show=False),
        Binding("]",      "widen_panel",  "▶ Genişlet", show=False),
    ]

    _LIST_WIDTH_MIN = 20
    _LIST_WIDTH_MAX = 80
    _LIST_WIDTH_DEF = 38

    DEFAULT_CSS = """
    ViewerScreen {
        layout: vertical;
    }
    #viewer_main {
        layout: horizontal;
        height: 1fr;
    }
    #file_list {
        width: 38;
        border-right: solid $primary-darken-2;
    }
    #file_list > ListItem {
        padding: 0 1;
    }
    #file_list > ListItem.--highlight {
        background: $accent 20%;
    }
    #right_panel {
        width: 1fr;
        padding: 0 1;
    }
    #receipt_view {
        padding: 1 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[int, Union[Receipt, Exception]] = {}
        self._raw_jsons: dict[int, dict] = {}
        self._files: list[Path] = []
        self._file_index: dict[str, int] = {}   # filename → index
        self._mode: str = "p"                   # p | d | j
        self._selected_idx: int = 0
        self._list_width: int = self._LIST_WIDTH_DEF

    # ── Compose ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="viewer_main"):
            yield ListView(id="file_list")
            with ScrollableContainer(id="right_panel"):
                yield Static("", id="receipt_view", markup=True)
        yield Footer()

    # ── Mount: dosya listesini doldur, worker'ı başlat ───────────────────────

    def on_mount(self) -> None:
        if _PARSER_ERR:
            self.query_one("#receipt_view", Static).update(
                f"[red]parser.py yüklenemedi:[/]\n{_PARSER_ERR}"
            )
            return

        self._files = sorted(
            f for f in OCR_CACHE_DIR.glob("*.json")
            if f.name != "processed.json"
        )

        if not self._files:
            self.query_one("#receipt_view", Static).update(
                f"[dim]OCR cache boş:[/] {OCR_CACHE_DIR}"
            )
            return

        lv = self.query_one("#file_list", ListView)
        for i, f in enumerate(self._files):
            self._file_index[f.name] = i
            lv.append(ListItem(Static(f"⏳ [dim]{f.name}[/]", markup=True), id=f"fi_{i}"))

        self._selected_idx = 0
        self.query_one("#receipt_view", Static).update("⏳ [dim]Parse ediliyor...[/]")
        self._parse_all_worker()

    # ── Arka plan worker ─────────────────────────────────────────────────────

    @work(thread=True, name="parse_all")
    def _parse_all_worker(self) -> None:
        """Tüm cache dosyalarını tek seferinde parse et."""
        for i, f in enumerate(self._files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self._raw_jsons[i] = data
                self._cache[i] = parse_receipt(data)
            except Exception as exc:
                self._cache[i] = exc
            self.post_message(FileReady(i))

    # ── Worker mesajı: tek dosya hazır ───────────────────────────────────────

    def on_file_ready(self, message: FileReady) -> None:
        idx = message.index
        if idx >= len(self._files):
            return

        f      = self._files[idx]
        result = self._cache.get(idx)

        # Sol panel: ikon + özet güncelle
        try:
            item  = self.query_one(f"#fi_{idx}", ListItem)
            label = item.query_one(Static)
            icon, color, detail = self._receipt_icon(result)
            label.update(f"[{color}]{icon}[/] {f.name}\n  [dim]{detail}[/]")
        except Exception:
            pass

        # Seçili dosyaysa sağ paneli güncelle
        if idx == self._selected_idx:
            self._refresh_right()

    # ── Dosya seçimi ─────────────────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id.startswith("fi_"):
            try:
                self._selected_idx = int(item_id[3:])
                self._refresh_right()
            except (ValueError, IndexError):
                pass

    # ── Sağ panel içeriği ────────────────────────────────────────────────────

    def _refresh_right(self) -> None:
        view = self.query_one("#receipt_view", Static)
        idx  = self._selected_idx

        if idx not in self._cache:
            view.update("⏳ [dim]Parse ediliyor...[/]")
            return

        result = self._cache[idx]

        if self._mode == "j":
            raw = self._raw_jsons.get(idx, {})
            # JSON metnini markup olarak işleme (köşeli parantez kaçış)
            view.update(
                "[dim]--- raw JSON ---[/]\n" +
                json.dumps(raw, indent=2, ensure_ascii=False)
                    .replace("[", "\\[")
            )

        elif self._mode == "d":
            view.update(self._format_debug(idx))

        else:  # p — parse görünümü
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
            # rich markup'ta köşeli parantezleri kaçır
            name = item.name.replace("[", "\\[")
            lines.append(f"  {name:<38} [green]{item.amount:>8.2f} TL[/]")

        lines.append("[dim]" + "─" * 55 + "[/]")

        calc = sum(i.amount for i in r.items)
        lines.append(f"  {'Hesaplanan toplam':<38} [bold]{calc:>8.2f} TL[/]")

        if r.total:
            lines.append(f"  {'Fişteki toplam':<38} [bold]{r.total:>8.2f} TL[/]")
            diff = abs(calc - r.total)
            if diff > 0.02:
                lines.append(f"\n[yellow]⚠  Fark: {diff:.2f} TL (KDV/indirim olabilir)[/]")
            else:
                lines.append(f"\n[green]✓ Tutarlar eşleşiyor[/]")

        lines.append(
            f"\n[dim]{len(r.items)} kalem  │  "
            f"{len(r.raw_detections)} detection[/]"
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

    # ── Fiş kalite ikonu ─────────────────────────────────────────────────────

    @staticmethod
    def _receipt_icon(result) -> tuple[str, str, str]:
        """(ikon, renk, detay_metni) döndürür.

        ✓ yeşil  — parse başarılı, kalemler var, toplam eşleşiyor
        ⚠ sarı   — parse başarılı ama sorun var (kalem yok, toplam uyuşmuyor,
                    tarih/mağaza yok)
        ✗ kırmızı — exception
        """
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

        if result.total and result.items:
            calc = sum(i.amount for i in result.items)
            if abs(calc - result.total) > 0.02:
                issues.append(f"toplam uyuşmuyor Δ{abs(calc - result.total):.2f}")

        store = result.store or "?"
        date  = result.date  or "?"
        total = f"₺{result.total:.2f}" if result.total else "?"
        summary = f"{store}  {date}  {total}"

        if issues:
            return "⚠", "yellow", f"{summary}  [{', '.join(issues)}]"

        return "✓", "green", summary

    # ── Panel genişlik aksiyonları ────────────────────────────────────────────

    def action_widen_panel(self) -> None:
        self._list_width = min(self._list_width + 4, self._LIST_WIDTH_MAX)
        self.query_one("#file_list").styles.width = self._list_width

    def action_narrow_panel(self) -> None:
        self._list_width = max(self._list_width - 4, self._LIST_WIDTH_MIN)
        self.query_one("#file_list").styles.width = self._list_width

    # ── Aksiyonlar ───────────────────────────────────────────────────────────

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
