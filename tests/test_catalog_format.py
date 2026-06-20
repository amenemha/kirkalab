"""Unit tests for the dependency-free catalog formatting helpers."""
from bot.catalog_format import format_device_card, format_firmware_list


def _model() -> dict:
    return {
        "id": 1,
        "brand": "Bitmain",
        "model_name": "Antminer S19 XP",
        "variant": "140 TH",
        "default_hashrate_ths": "140.00",
        "hashrate_unit": "TH/s",
        "algorithm": "SHA-256",
        "default_power_w": 3010,
        "efficiency_j_per_th": "21.5000",
        "cooling_type": "air",
        "release_year": 2022,
        "noise_db": "75.00",
        "source_url": "https://example.com/s19xp",
        "chip": None,
        "notes": "",
    }


def test_card_title_includes_variant():
    text = format_device_card(_model())
    assert "Antminer S19 XP 140 TH" in text


def test_card_trims_trailing_zeros():
    text = format_device_card(_model())
    assert "140 TH/s" in text
    assert "140.00" not in text
    assert "21.5 J/TH" in text


def test_card_skips_empty_fields():
    text = format_device_card(_model())
    # chip is None, notes is "" -> must not render "None" or empty labels.
    assert "None" not in text
    assert "Чип" not in text
    assert "ℹ️" not in text  # notes label
    # Present fields still render.
    assert "Алгоритм: SHA-256" in text
    assert "Источник:" in text


def test_card_without_variant():
    model = _model()
    model["variant"] = None
    text = format_device_card(model)
    assert "Antminer S19 XP" in text
    assert text.splitlines()[0].endswith("S19 XP</b>")


def test_firmware_list_shows_deltas():
    model = _model()
    presets = [
        {
            "firmware": "vnish",
            "preset_name": "Turbo",
            "mode": "overclock",
            "hashrate": "158.00",
            "hashrate_unit": "TH/s",
            "power_w": "3620.00",
            "efficiency_j_per_th": "22.9",
        },
        {
            "firmware": "vnish",
            "preset_name": "Eco",
            "mode": "undervolt",
            "hashrate": "134.00",
            "hashrate_unit": "TH/s",
            "power_w": "2520.00",
        },
    ]
    text = format_firmware_list(model, presets)
    assert "vnish" in text
    assert "Turbo" in text
    # +18 TH/s vs stock 140, +610 W vs stock 3010
    assert "+18" in text
    assert "+610" in text
    # Eco undervolt: -6 TH/s, -490 W
    assert "-6" in text
    assert "-490" in text
