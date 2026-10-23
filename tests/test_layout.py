from app.services.farm import layout as L


def _devs(net_places, model="Antminer T21"):
    out, i = [], 1
    for net, places in net_places.items():
        for p in places:
            out.append({"id": i, "ip": f"10.116.{net}.{p}", "worker": None, "model": model})
            i += 1
    return out


def test_name_subnets():
    assert L.subnets_from_name("4к 6-7 ↓ Ф1 (КТП 2) P33 IBMM") == [6, 7]
    assert L.subnets_from_name("11к 21 Ф24 (КТП 7) hydro Bitmain") == [21]
    assert L.subnets_from_name("11К HD5 40-41  Ф1 (КТП 2,3,5) IBMM") == [40, 41]
    assert L.subnets_from_name("Б 102-103 ↓ Ф1 (КТП 1 КТП 4) С19") == [102, 103]


def test_air_t21_two_subnets():
    devs = _devs({8: range(1, 101), 9: range(1, 95)})
    spec = L.infer("5а 8-9 ↑ Ф1 (КТП 3) P33 IBMM", devs)
    assert spec["kind"] == "air"
    assert [(s["racks"], s["shelves"], s["places"]) for s in spec["sections"]] == [(5, 5, 4), (5, 5, 4)]
    pl = L.place(spec, devs)
    assert not pl["unplaced"] and not pl["doubles"]
    assert pl["placed"][1] == [0, 0, 0, 0, 1]
    assert pl["placed"][100][:4] == [0, 4, 4, 3]


def test_air_c19_150():
    devs = _devs({16: range(1, 150), 17: range(2, 151)}, "Antminer S19 XP")
    spec = L.infer("9К 16-17 ↑ Ф1  (КТП 5) C19 IBMM", devs)
    assert [(s["racks"], s["shelves"], s["places"]) for s in spec["sections"]] == [(5, 6, 5), (5, 6, 5)]


def test_hydro_bitmain_two_cabinets():
    devs = _devs({22: range(1, 192)}, "Antminer S19 XP Hyd.")
    spec = L.infer("12к 22 Ф24 (КТП 7) hydro Bitmain", devs)
    assert spec["kind"] == "hydro" and len(spec["sections"]) == 2
    pl = L.place(spec, devs)
    assert not pl["unplaced"]
    assert pl["placed"][94][0] == 1 and pl["placed"][94][4] == 94


def test_worker_fallback_and_bath():
    devs = [{"id": 1, "ip": "", "worker": "x.116x18x66", "model": "T21"}]
    spec = L.infer("10к 18-19 ↓ Ф1 (КТП 5) P33 IBMM", devs)
    assert L.place(spec, devs)["placed"][1][4] == 66
    bath = L.infer("Модуль Ф24 (КТП 10) (Распределительный пункт)", [{"id": i, "ip": "", "worker": None, "model": "S19"} for i in range(1, 40)])
    assert bath["kind"] == "bath"
    assert len(L.place(bath, [{"id": i, "ip": "", "worker": None} for i in range(1, 40)])["placed"]) == 39


def test_clean_limits():
    s = L.clean({"kind": "x", "sections": [{"title": "A", "subnet": 6, "racks": 99, "shelves": 0, "places": "5"}]})
    assert s["kind"] == "air" and s["sections"][0]["racks"] == 30 and s["sections"][0]["shelves"] == 1
