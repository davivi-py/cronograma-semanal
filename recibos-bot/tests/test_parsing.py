from datetime import date

from app.config import DEFAULT_CATEGORIES
from app.parsing import format_brl, match_category, parse_correction, parse_date, parse_money, parse_month

CATS = tuple(DEFAULT_CATEGORIES.split(","))
TODAY = date(2026, 9, 29)


def test_parse_money():
    assert parse_money("87,40") == 8740
    assert parse_money("R$ 87,40") == 8740
    assert parse_money("r$1.234,56") == 123456
    assert parse_money("87.40") == 8740
    assert parse_money("87") == 8700
    assert parse_money("12,5") == 1250
    assert parse_money("Extra") is None
    assert parse_money("29/09") is None


def test_format_brl():
    assert format_brl(8740) == "R$ 87,40"
    assert format_brl(123456) == "R$ 1.234,56"
    assert format_brl(5) == "R$ 0,05"
    assert format_brl(None) == "R$ ?"


def test_parse_date():
    assert parse_date("28/09", TODAY) == date(2026, 9, 28)
    assert parse_date("28/09/2025", TODAY) == date(2025, 9, 28)
    assert parse_date("28/09/25", TODAY) == date(2025, 9, 28)
    assert parse_date("2026-09-01", TODAY) == date(2026, 9, 1)
    assert parse_date("ontem", TODAY) == date(2026, 9, 28)
    # dezembro ainda não chegou -> ano anterior
    assert parse_date("15/12", TODAY) == date(2025, 12, 15)
    assert parse_date("31/02", TODAY) is None


def test_parse_month():
    assert parse_month("", TODAY) == (2026, 9)
    assert parse_month("anterior", TODAY) == (2026, 8)
    assert parse_month("09/2026", TODAY) == (2026, 9)
    assert parse_month("2026-08", TODAY) == (2026, 8)
    assert parse_month("março", TODAY) == (2026, 3)
    assert parse_month("dezembro", TODAY) == (2025, 12)
    assert parse_month("set 2025", TODAY) == (2025, 9)
    assert parse_month("13", TODAY) is None
    assert parse_month(
        "anterior", date(2026, 1, 1)) == (2025, 12)


def test_match_category():
    assert match_category("farmacia", CATS) == "Farmácia"
    assert match_category("MERCADO", CATS) == "Mercado"
    assert match_category("lanche", CATS) == "Lanche/Café"
    assert match_category("xyz", CATS) is None


def test_parse_correction_keyed():
    c = parse_correction("valor 90,10; data 27/09\nloja Pão de Açúcar\ncategoria farmácia", TODAY, CATS)
    assert c.fields == {
        "total_cents": 9010, "purchase_date": "2026-09-27", "merchant": "Pão de Açúcar", "category": "Farmácia",
    }
    assert not c.unparsed and not c.delete


def test_parse_correction_bare_values_and_delete():
    assert parse_correction("87,40", TODAY, CATS).fields == {"total_cents": 8740}
    assert parse_correction("28/09", TODAY, CATS).fields == {"purchase_date": "2026-09-28"}
    assert parse_correction("Apagar", TODAY, CATS).delete


def test_parse_correction_free_text_is_unparsed():
    c = parse_correction("na verdade foi no Carrefour", TODAY, CATS)
    assert c.unparsed and c.empty
