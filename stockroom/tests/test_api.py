import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ.pop("APP_PASSWORD", None)
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)


def test_stock_flow_edit_and_import():
    r = c.post("/api/items", json={"sku": "LP-1", "name": "HP 250 G9", "category": "Laptops",
                                   "price_kes": 55000, "quantity": 2, "reorder_level": 1})
    assert r.status_code == 201
    i = r.json()["id"]
    # edit
    r = c.put(f"/api/items/{i}", json={"sku": "LP-1", "name": "HP 250 G9 i5", "category": "Laptops",
                                       "price_kes": 58000, "reorder_level": 1})
    assert r.status_code == 200 and r.json()["price_kes"] == 58000 and r.json()["quantity"] == 2
    # stock status + guard
    assert c.post(f"/api/items/{i}/move", json={"change": -1}).json()["status"] == "low"
    assert c.post(f"/api/items/{i}/move", json={"change": -1}).json()["status"] == "sold_out"
    assert c.post(f"/api/items/{i}/move", json={"change": -1}).status_code == 409
    assert len(c.get(f"/api/items/{i}/movements").json()) == 3
    # duplicate sku
    assert c.post("/api/items", json={"sku": "LP-1", "name": "x", "price_kes": 1}).status_code == 409
    # import: one update, one new, one bad row
    csv_text = "sku,name,category,price_kes,quantity\nLP-1,HP 250 G9 i5,Laptops,58000,5\nMS-1,Wireless Mouse,Accessories,"+'"1,200"'+",10\n,bad,,1,1\n"
    r = c.post("/api/import", content=csv_text, headers={"Content-Type": "text/csv"}).json()
    assert r["created"] == 1 and r["updated"] == 1 and len(r["errors"]) == 1
    items = {x["sku"]: x for x in c.get("/api/items").json()}
    assert items["LP-1"]["quantity"] == 5 and items["MS-1"]["price_kes"] == 1200
    assert c.get("/api/export.csv").status_code == 200
    # delete
    assert c.delete(f"/api/items/{i}").status_code == 204
