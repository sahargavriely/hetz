#!/bin/sh
# Fixture: "ledger", a tiny invoicing library whose half-cent rounding is wrong for positive halves.
# Usage: setup.sh [DIR]  (default: a new mktemp dir). Prints the fixture path.
set -eu
. "$(dirname "$0")/../_lib.sh"
fixture_dir "${1:-}"
mkdir -p ledger tests

cat > README.md <<'EOF'
# ledger

Tiny invoicing library: integer cents everywhere, tax rates in basis points (1% = 100 bp).

    python3 -m unittest
EOF

cat > .gitignore <<'EOF'
__pycache__/
EOF

cat > ledger/__init__.py <<'EOF'
"""ledger: integer-cent invoicing helpers."""
from .invoice import Invoice, Line
from .money import format_cents, parse_amount, round_half_up

__all__ = ["Invoice", "Line", "format_cents", "parse_amount", "round_half_up"]
EOF

cat > ledger/money.py <<'EOF'
"""Money helpers. Amounts are integer cents; never floats."""


def round_half_up(numerator, denominator):
    """Divide two ints and round to the nearest integer; exact halves round away from zero.

    denominator must be > 0. round_half_up(15, 10) == 2, round_half_up(-15, 10) == -2.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    q, r = divmod(numerator, denominator)
    if r * 2 > denominator:
        q += 1
    return q


def format_cents(cents, currency="USD"):
    """1234 -> 'USD 12.34'; negative amounts keep the sign in front."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{currency} {sign}{whole:,}.{frac:02d}"


def parse_amount(text):
    """'12.34' -> 1234, '-0.5' -> -50, '7' -> 700. At most two decimals."""
    text = text.strip().replace(",", "")
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    whole, _, frac = text.partition(".")
    if len(frac) > 2:
        raise ValueError(f"too many decimals: {text!r}")
    return sign * (int(whole or "0") * 100 + int((frac + "00")[:2]))
EOF

cat > ledger/tax.py <<'EOF'
"""Tax computation. Rates are basis points (bp): 2500 bp == 25%."""
from .money import round_half_up

BP = 10_000

REDUCED_RATES = {
    "books": 500,
    "food": 700,
}


def tax_cents(subtotal_cents, rate_bp):
    """Tax on a subtotal, rounded half away from zero to the cent."""
    return round_half_up(subtotal_cents * rate_bp, BP)


def rate_for(category, standard_bp):
    """Reduced rate for known categories, else the standard rate."""
    return REDUCED_RATES.get(category, standard_bp)
EOF

cat > ledger/invoice.py <<'EOF'
"""Invoice model: lines -> subtotal -> tax -> total."""
from dataclasses import dataclass, field

from .money import format_cents
from .tax import rate_for, tax_cents


@dataclass
class Line:
    description: str
    qty: int
    unit_cents: int
    category: str = "general"

    @property
    def amount(self):
        return self.qty * self.unit_cents


@dataclass
class Invoice:
    standard_rate_bp: int
    lines: list = field(default_factory=list)

    def add(self, description, qty, unit_cents, category="general"):
        self.lines.append(Line(description, qty, unit_cents, category))
        return self

    @property
    def subtotal(self):
        return sum(line.amount for line in self.lines)

    @property
    def tax(self):
        by_rate = {}
        for line in self.lines:
            rate = rate_for(line.category, self.standard_rate_bp)
            by_rate[rate] = by_rate.get(rate, 0) + line.amount
        return sum(tax_cents(amount, rate) for rate, amount in sorted(by_rate.items()))

    @property
    def total(self):
        return self.subtotal + self.tax

    def render(self):
        rows = [f"{l.qty} x {l.description}: {format_cents(l.amount)}" for l in self.lines]
        rows += [f"subtotal: {format_cents(self.subtotal)}", f"tax: {format_cents(self.tax)}",
                 f"total: {format_cents(self.total)}"]
        return "\n".join(rows)
EOF

cat > ledger/report.py <<'EOF'
"""Summaries over many invoices."""
from .money import format_cents


def totals(invoices):
    return {
        "count": len(invoices),
        "subtotal": sum(i.subtotal for i in invoices),
        "tax": sum(i.tax for i in invoices),
        "total": sum(i.total for i in invoices),
    }


def summary_line(invoices):
    t = totals(invoices)
    return f"{t['count']} invoices, total {format_cents(t['total'])} (tax {format_cents(t['tax'])})"
EOF

touch tests/__init__.py

cat > tests/test_money.py <<'EOF'
import unittest

from ledger.money import format_cents, parse_amount


class MoneyTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_cents(123456), "USD 1,234.56")
        self.assertEqual(format_cents(-5), "USD -0.05")

    def test_parse(self):
        self.assertEqual(parse_amount("12.34"), 1234)
        self.assertEqual(parse_amount("-0.5"), -50)
        self.assertEqual(parse_amount("7"), 700)
        with self.assertRaises(ValueError):
            parse_amount("1.234")


if __name__ == "__main__":
    unittest.main()
EOF

cat > tests/test_invoice.py <<'EOF'
import unittest

from ledger import Invoice
from ledger.report import summary_line


class InvoiceTest(unittest.TestCase):
    def test_simple_total(self):
        inv = Invoice(standard_rate_bp=2000).add("widget", 2, 500)
        self.assertEqual(inv.subtotal, 1000)
        self.assertEqual(inv.tax, 200)
        self.assertEqual(inv.total, 1200)

    def test_half_cent_tax_rounds_up(self):
        # 1990 * 25% = 497.5 cents -> 498
        inv = Invoice(standard_rate_bp=2500).add("gadget", 2, 995)
        self.assertEqual(inv.tax, 498)
        self.assertEqual(inv.total, 2488)

    def test_credit_note_rounds_away_from_zero(self):
        # -1990 * 25% = -497.5 cents -> -498
        inv = Invoice(standard_rate_bp=2500).add("refund", 2, -995)
        self.assertEqual(inv.tax, -498)
        self.assertEqual(inv.total, -2488)

    def test_reduced_rate(self):
        # books at 5%: 1010 * 5% = 50.5 -> 51; general at 20%: 1000 -> 200
        inv = Invoice(standard_rate_bp=2000).add("novel", 1, 1010, "books").add("pen", 1, 1000)
        self.assertEqual(inv.tax, 251)

    def test_summary(self):
        inv = Invoice(standard_rate_bp=2000).add("widget", 2, 500)
        self.assertEqual(summary_line([inv, inv]), "2 invoices, total USD 24.00 (tax USD 4.00)")


if __name__ == "__main__":
    unittest.main()
EOF

fixture_commit "ledger fixture"
echo "$DIR"
