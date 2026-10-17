#!/bin/sh
# Fixture: "shop", where calc_total is used across ~12 files, next to look-alikes that must survive
# (calc_total_weight, recalc_totals).
# Usage: setup.sh [DIR]  (default: a new mktemp dir). Prints the fixture path.
set -eu
. "$(dirname "$0")/../_lib.sh"
fixture_dir "${1:-}"

python3 - <<'PYEOF'
import os


def w(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text.lstrip("\n"))


w(".gitignore", "__pycache__/\n")
w("README.md", """
# shop

Order pricing toolkit. The entry point for prices is `shop.pricing.calc_total(cart, tax_rate)`.

    python3 -m unittest
""")
w("docs/pricing.md", """
# Pricing

`calc_total` sums line prices, applies promotions, then tax (rounded to cents).
Shipping weight is separate: see `calc_total_weight` in shop/shipping.py.
""")
w("shop/__init__.py", '"""shop: order pricing toolkit."""\n')
w("shop/pricing.py", '''
"""Order pricing."""
from shop.promotions import apply_promotions


def line_price(line):
    return line["qty"] * line["unit_price"]


def calc_total(cart, tax_rate=0.0):
    """Total of a cart in cents: lines, then promotions, then tax (rounded)."""
    subtotal = apply_promotions(cart, sum(line_price(l) for l in cart["lines"]))
    return round(subtotal * (1 + tax_rate))
''')
w("shop/promotions.py", '''
"""Promotions are applied to the subtotal before tax (see calc_total)."""


def apply_promotions(cart, subtotal):
    code = cart.get("promo")
    if code == "TENOFF":
        return max(0, subtotal - 1000)
    if code == "HALF":
        return subtotal // 2
    return subtotal
''')
w("shop/shipping.py", '''
"""Shipping weight and cost."""


def calc_total_weight(cart):
    return sum(l["qty"] * l.get("weight_g", 0) for l in cart["lines"])


def shipping_cost(cart):
    grams = calc_total_weight(cart)
    return 0 if grams == 0 else 499 + grams // 1000 * 100
''')
w("shop/cart.py", '''
"""Cart helpers."""
from shop.pricing import calc_total


def new_cart(promo=None):
    return {"lines": [], "promo": promo}


def add_line(cart, sku, qty, unit_price, weight_g=0):
    cart["lines"].append({"sku": sku, "qty": qty, "unit_price": unit_price, "weight_g": weight_g})
    return cart


def preview(cart):
    return f"{len(cart['lines'])} lines, {calc_total(cart)} cents before tax"
''')
w("shop/checkout.py", '''
"""Checkout: price + shipping."""
from shop.pricing import calc_total
from shop.shipping import shipping_cost


def checkout(cart, tax_rate):
    # calc_total already includes promotions and tax
    goods = calc_total(cart, tax_rate)
    return {"goods": goods, "shipping": shipping_cost(cart), "due": goods + shipping_cost(cart)}
''')
w("shop/api.py", '''
"""Thin JSON API over the pricing functions."""
from shop import pricing


def quote(payload):
    cart = {"lines": payload["lines"], "promo": payload.get("promo")}
    return {"total": pricing.calc_total(cart, payload.get("tax_rate", 0.0))}
''')
w("shop/reports.py", '''
"""Daily sales report."""
from shop.pricing import calc_total


def recalc_totals(orders, tax_rate):
    """Recompute every order's total (used after a price change)."""
    for order in orders:
        order["total"] = calc_total(order["cart"], tax_rate)
    return orders


def revenue(orders, tax_rate):
    return sum(o["total"] for o in recalc_totals(orders, tax_rate))
''')
w("shop/cli.py", '''
"""Command line: python3 -m shop.cli SKU:QTY:PRICE ..."""
import sys

from shop.cart import add_line, new_cart
from shop.pricing import calc_total


def main(argv=None):
    cart = new_cart()
    for spec in (argv if argv is not None else sys.argv[1:]):
        sku, qty, price = spec.split(":")
        add_line(cart, sku, int(qty), int(price))
    print(calc_total(cart))


if __name__ == "__main__":
    main()
''')
w("tests/__init__.py", "")
w("tests/test_pricing.py", '''
import unittest

from shop.pricing import calc_total


def cart(promo=None):
    return {"lines": [{"qty": 2, "unit_price": 1500}, {"qty": 1, "unit_price": 500}], "promo": promo}


class PricingTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(calc_total(cart()), 3500)

    def test_tax(self):
        self.assertEqual(calc_total(cart(), 0.2), 4200)

    def test_promos(self):
        self.assertEqual(calc_total(cart("TENOFF")), 2500)
        self.assertEqual(calc_total(cart("HALF"), 0.1), 1925)
''')
w("tests/test_checkout.py", '''
import unittest

from shop.api import quote
from shop.cart import add_line, new_cart
from shop.checkout import checkout
from shop.reports import recalc_totals, revenue
from shop.shipping import calc_total_weight


class CheckoutTest(unittest.TestCase):
    def test_checkout(self):
        c = add_line(new_cart(), "a", 2, 1000, weight_g=600)
        self.assertEqual(calc_total_weight(c), 1200)
        self.assertEqual(checkout(c, 0.0), {"goods": 2000, "shipping": 599, "due": 2599})

    def test_api_and_reports(self):
        self.assertEqual(quote({"lines": [{"qty": 1, "unit_price": 999}]}), {"total": 999})
        orders = [{"cart": add_line(new_cart(), "a", 1, 100)}]
        self.assertEqual(recalc_totals(orders, 0.0)[0]["total"], 100)
        self.assertEqual(revenue(orders, 0.5), 150)
''')
PYEOF

fixture_commit "shop fixture"
echo "$DIR"
