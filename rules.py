"""
Rule Engine - Item seviyesi kategori tespiti
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_ACCOUNT = "Gider:Bilinmeyen"
LEARNED_RULES_FILE = Path("rules_learned.toml")

@dataclass
class Rule:
    account: str
    item: Optional[str]       = None  # regex, ürün adına uygulanır
    store: Optional[str]      = None  # regex, market adına uygulanır
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    comment: Optional[str]    = None  # ne için olduğu (opsiyonel açıklama)

    def matches(self, item_name: str, store: str, amount: float) -> bool:
        if self.store and not re.search(self.store, store, re.IGNORECASE):
            return False
        if self.item and not re.search(self.item, item_name, re.IGNORECASE):
            return False
        if self.amount_min is not None and amount < self.amount_min:
            return False
        if self.amount_max is not None and amount > self.amount_max:
            return False
        return True

def _parse_rule(d: dict) -> Rule:
    return Rule(
        account=d["account"],
        item=d.get("item"),
        store=d.get("store"),
        amount_min=d.get("amount_min"),
        amount_max=d.get("amount_max"),
        comment=d.get("comment"),
    )

def load_rules(path: Path) -> list[Rule]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [_parse_rule(r) for r in data.get("rule", [])]

def find_account(item_name: str, store: str, amount: float, rules: list[Rule]) -> Optional[str]:
    for rule in rules:
        if rule.matches(item_name, store, amount):
            return rule.account
    return None

def append_learned_rule(item_name: str, account: str, path: Path = LEARNED_RULES_FILE):
    """Kullanıcının/Claude'un verdiği cevabı learned rules dosyasına ekle."""
    base_name = re.sub(r'\s*\(.*\)\s*$', '', item_name)  # parantezi at
    words = base_name.upper().split()[:2]
    pattern = "^" + " ".join(re.escape(w) for w in words)

    # TOML basic string içinde \ geçersiz escape → \\ olarak yaz
    toml_pattern = pattern.replace('\\', '\\\\')
    entry = (
        f'\n[[rule]]\n'
        f'item    = "{toml_pattern}"\n'
        f'account = "{account}"\n'
        f'comment = "Otomatik öğrenildi: {item_name}"\n'
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
