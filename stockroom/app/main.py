import asyncio, csv, hashlib, hmac, io, os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .database import IS_SQLITE, Base, engine, get_db
from .models import Item, StockMovement

# ---------- config ----------
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
AUTH_ON = bool(APP_PASSWORD)
if not AUTH_ON and not IS_SQLITE:
    raise RuntimeError("APP_PASSWORD must be set when using a cloud database.")
SECRET_KEY = os.getenv("SECRET_KEY") or hashlib.sha256(("stockroom:" + APP_PASSWORD).encode()).hexdigest()
ON_CLOUD = bool(os.getenv("RENDER"))  # Render sets this; enables secure cookies

Base.metadata.create_all(engine)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Stockroom")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 24 * 30,
                   same_site="lax", https_only=ON_CLOUD)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------- auth ----------
def require_auth(request: Request):
    if AUTH_ON and not request.session.get("ok"):
        raise HTTPException(401, "Login required")


class LoginIn(BaseModel):
    password: str


@app.get("/auth/me")
def me(request: Request):
    return {"auth_required": AUTH_ON, "logged_in": (not AUTH_ON) or bool(request.session.get("ok"))}


@app.post("/auth/login")
async def login(data: LoginIn, request: Request):
    if AUTH_ON and not hmac.compare_digest(data.password.encode(), APP_PASSWORD.encode()):
        await asyncio.sleep(1)  # slows down password guessing
        raise HTTPException(401, "Wrong password")
    request.session["ok"] = True
    return {"ok": True}


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


# ---------- schemas ----------
class ItemUpdate(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="General", max_length=100)
    price_kes: int = Field(ge=0)
    reorder_level: int = Field(ge=0, default=3)


class ItemCreate(ItemUpdate):
    quantity: int = Field(ge=0, default=0)


class ItemOut(ItemCreate):
    id: int
    status: str
    model_config = {"from_attributes": True}


class MoveIn(BaseModel):
    change: int = Field(description="positive = restock, negative = sale/loss")
    reason: str = Field(default="sale", max_length=50)


class MoveOut(BaseModel):
    change: int
    reason: str
    created_at: datetime
    model_config = {"from_attributes": True}


# ---------- pages ----------
@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/healthz")
def health():
    return {"ok": True}


# ---------- API ----------
api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


def get_item(db: Session, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    return item


def sku_taken(db: Session, sku: str, except_id: int | None = None) -> bool:
    found = db.scalar(select(Item).where(Item.sku == sku))
    return bool(found and found.id != except_id)


@api.get("/items", response_model=list[ItemOut])
def list_items(db: Session = Depends(get_db)):
    return db.scalars(select(Item).order_by(Item.name)).all()


@api.post("/items", response_model=ItemOut, status_code=201)
def create_item(data: ItemCreate, db: Session = Depends(get_db)):
    data.sku, data.name = data.sku.strip(), data.name.strip()
    if sku_taken(db, data.sku):
        raise HTTPException(409, f"SKU '{data.sku}' already exists")
    item = Item(**data.model_dump())
    db.add(item)
    db.flush()
    if item.quantity:
        db.add(StockMovement(item_id=item.id, change=item.quantity, reason="initial"))
    db.commit()
    db.refresh(item)
    return item


@api.put("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: int, data: ItemUpdate, db: Session = Depends(get_db)):
    item = get_item(db, item_id)
    data.sku, data.name = data.sku.strip(), data.name.strip()
    if sku_taken(db, data.sku, item.id):
        raise HTTPException(409, f"SKU '{data.sku}' already exists")
    for k, v in data.model_dump().items():  # quantity is changed only via /move so it's always logged
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@api.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    db.delete(get_item(db, item_id))
    db.commit()


@api.post("/items/{item_id}/move", response_model=ItemOut)
def move_stock(item_id: int, data: MoveIn, db: Session = Depends(get_db)):
    item = get_item(db, item_id)
    if data.change == 0:
        raise HTTPException(422, "Change cannot be 0")
    if item.quantity + data.change < 0:
        raise HTTPException(409, f"Only {item.quantity} left in stock")
    item.quantity += data.change
    db.add(StockMovement(item_id=item.id, change=data.change, reason=data.reason))
    db.commit()
    db.refresh(item)
    return item


@api.get("/items/{item_id}/movements", response_model=list[MoveOut])
def history(item_id: int, db: Session = Depends(get_db)):
    get_item(db, item_id)
    return db.scalars(select(StockMovement).where(StockMovement.item_id == item_id)
                      .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(200)).all()


@api.get("/export.csv")
def export_csv(db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sku", "name", "category", "price_kes", "quantity", "reorder_level"])
    for i in db.scalars(select(Item).order_by(Item.name)):
        w.writerow([i.sku, i.name, i.category, i.price_kes, i.quantity, i.reorder_level])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=stock.csv"})


@api.post("/import")
async def import_csv(request: Request, db: Session = Depends(get_db)):
    """CSV columns: sku, name, price_kes (required); category, quantity, reorder_level (optional).
    Existing SKUs are updated, new SKUs are created."""
    text = (await request.body()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(422, "The file is empty")
    missing = {"sku", "name", "price_kes"} - {c.strip().lower() for c in reader.fieldnames}
    if missing:
        raise HTTPException(422, f"Missing columns: {', '.join(sorted(missing))}")
    created = updated = 0
    errors: list[str] = []
    for n, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v if isinstance(v, str) else "").strip() for k, v in raw.items()}
        try:
            sku, name = row["sku"], row["name"]
            if not sku or not name:
                raise ValueError("sku and name are required")
            price = int(float(row["price_kes"].replace(",", "")))
            qty = int(float(row.get("quantity") or 0))
            rl = int(float(row.get("reorder_level") or 3))
            if min(price, qty, rl) < 0:
                raise ValueError("numbers cannot be negative")
        except Exception as e:
            errors.append(f"Row {n}: {e}")
            continue
        item = db.scalar(select(Item).where(Item.sku == sku))
        if item:
            item.name, item.price_kes, item.reorder_level = name, price, rl
            item.category = row.get("category") or item.category
            if row.get("quantity") and qty != item.quantity:
                db.add(StockMovement(item_id=item.id, change=qty - item.quantity, reason="correction"))
                item.quantity = qty
            updated += 1
        else:
            item = Item(sku=sku, name=name, category=row.get("category") or "General",
                        price_kes=price, quantity=qty, reorder_level=rl)
            db.add(item)
            db.flush()
            if qty:
                db.add(StockMovement(item_id=item.id, change=qty, reason="initial"))
            created += 1
    db.commit()
    return {"created": created, "updated": updated, "errors": errors[:10]}


app.include_router(api)
