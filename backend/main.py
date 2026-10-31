import os, hmac, hashlib, json, time, threading, re, secrets, calendar
from urllib.parse import parse_qsl
import requests
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Column, String, Integer, Float, BigInteger, Boolean, func
from backend.database import Base, engine, SessionLocal

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DEV = os.getenv("DEV_MODE") == "1"          # yalnızca yerelde 1 yapın
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
SECRET = os.getenv("ADSGRAM_SECRET", "")    # Adsgram Reward URL'deki gizli anahtar
DEPOT = int(os.getenv("DEPOT_SECONDS", 4 * 3600))
BOT = (os.getenv("BOT_USERNAME") or "ContinuumCyberWars_bot").strip().lstrip("@")   # boş değer de varsayılana döner
APP = (os.getenv("APP_NAME") or "play").strip().strip("/")
RUN_SEC, MAXS = 60, 120   # tur süresi, saniyede en fazla makul skor
EMAX, EREGEN = 5, 1200                      # enerji tavanı, 1 enerji = 20 dk
MIN_PAY = float(os.getenv("MIN_PAYOUT_USD", "100"))

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    name = Column(String(40), default="Commander")
    pts = Column(BigInteger, default=100)
    level = Column(Integer, default=1)
    depot_start = Column(Float, nullable=True)
    clan = Column(String(20), nullable=True)
    wallet = Column(String(42), nullable=True)
    day = Column(Integer, default=0)
    best = Column(Integer, default=0)
    runs = Column(Integer, default=0)
    energy = Column(Integer, default=5)
    energy_t = Column(Float, default=time.time)

class Clan(Base):
    __tablename__ = "clans"
    id = Column(String(20), primary_key=True)
    name = Column(String(40))
    partner = Column(BigInteger)
    pts = Column(BigInteger, default=0)
    ads = Column(Integer, default=0)

class Run(Base):
    __tablename__ = "runs"
    id = Column(String(20), primary_key=True)
    uid = Column(BigInteger, index=True)
    t0 = Column(Float)
    done = Column(Boolean, default=False)

class Ad(Base):
    __tablename__ = "ads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(BigInteger, index=True)
    kind = Column(String(12))
    t0 = Column(Float)
    earned = Column(Boolean, default=False)
    used = Column(Boolean, default=False)

class Payout(Base):
    __tablename__ = "payouts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    clan = Column(String(20), index=True)
    partner = Column(BigInteger)
    amount = Column(Float)
    wallet = Column(String(42))
    status = Column(String(10), default="pending")  # pending | paid | rejected
    tx = Column(String(80), nullable=True)
    t0 = Column(Float)

class AdLog(Base):  # Adsgram sunucu bildirimiyle onaylanan her ödüllü reklam
    __tablename__ = "adlog"
    id = Column(Integer, primary_key=True, autoincrement=True)
    day = Column(Integer, index=True)           # UTC gün numarası
    clan = Column(String(20), nullable=True, index=True)
    uid = Column(BigInteger)
    t0 = Column(Float)

class Day(Base):  # Adsgram panelinden elle girilen gerçek günlük rakamlar
    __tablename__ = "days"
    day = Column(Integer, primary_key=True)
    imps = Column(Integer)
    cpm = Column(Float)

def paid(db, cid):  # reddedilmeyen tüm talepler düşülür
    return float(db.query(func.coalesce(func.sum(Payout.amount), 0)).filter(Payout.clan == cid, Payout.status != "rejected").scalar())

def pending(db, cid):
    return float(db.query(func.coalesce(func.sum(Payout.amount), 0)).filter(Payout.clan == cid, Payout.status == "pending").scalar())

def split_revenue(counts, real):
    """counts: {gün: {klan|None: reklam_sayısı}}, real: {gün: (adsgram_gösterim, cpm)}
    Yalnızca gerçek rakamı girilmiş günler sayılır. Günün gerçek kazancı, bizim kayıtlı reklam sayımıza göre
    klanlara paylaştırılır; payda en az Adsgram gösterimi kadardır, bu yüzden toplam hiçbir zaman gerçek kazancı aşmaz.
    Dönüş: {klan|None: [onaylı_reklam, gelir_usd]}"""
    out = {}
    for d, per in counts.items():
        if d not in real: continue
        imps, cpm = real[d]
        den = max(imps, sum(per.values()))
        if den <= 0: continue
        earned = imps * cpm / 1000
        for c, n in per.items():
            a = out.setdefault(c, [0, 0.0]); a[0] += n; a[1] += earned * n / den
    return out

def stats(db):
    counts = {}
    for d, c, n in db.query(AdLog.day, AdLog.clan, func.count()).group_by(AdLog.day, AdLog.clan).all():
        counts.setdefault(d, {})[c] = n
    real = {x.day: (x.imps, x.cpm) for x in db.query(Day).all()}
    return counts, real, split_revenue(counts, real)

def clan_money(db, cid, counts, sp):
    total = sum(per.get(cid, 0) for per in counts.values())
    conf, rev = sp.get(cid, (0, 0.0))
    share, pd, pn = rev * 0.25, paid(db, cid), pending(db, cid)   # pd: reddedilmeyenler (bekleyen dahil)
    return {"ads": total, "confirmed": conf, "unconfirmed": total - conf, "revenue": round(rev, 4), "share": round(share, 4),
            "paid": round(pd - pn, 4), "pending": round(pn, 4), "available": round(share - pd, 4)}

Base.metadata.create_all(engine)
app = FastAPI(title="Continuum Clan Wars", version="3.0")
ROOT = os.path.join(os.path.dirname(__file__), "..")
if os.path.isdir(os.path.join(ROOT, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(ROOT, "assets")), name="assets")

def ping():  # Render ücretsiz katman uyumasın diye
    u = os.getenv("RENDER_EXTERNAL_URL")
    while u:
        time.sleep(600)
        try: requests.get(u + "/healthz", timeout=10)
        except Exception: pass
threading.Thread(target=ping, daemon=True).start()

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def auth(init: str):
    if DEV and init.startswith("dev:"):
        return {"id": int(init[4:]), "first_name": "Dev", "start_param": ""}
    try:
        d = dict(parse_qsl(init, strict_parsing=True)); h = d.pop("hash")
        chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        ok = hmac.compare_digest(hmac.new(key, chk.encode(), hashlib.sha256).hexdigest(), h)
        if not ok or time.time() - int(d["auth_date"]) > 86400: raise ValueError
        u = json.loads(d["user"]); u["start_param"] = d.get("start_param", "")
        return u
    except Exception:
        raise HTTPException(401, "Uygulamayı Telegram içinden aç")

def cur(db=Depends(get_db), x_init_data: str = Header("")):
    tu = auth(x_init_data); u = db.get(User, tu["id"])
    nm = (tu.get("username") or tu.get("first_name") or "Commander")[:30]
    if not u: u = User(id=tu["id"], name=nm); db.add(u)
    elif u.name != nm: u.name = nm
    sp = tu.get("start_param", "")
    if sp and not u.clan and db.get(Clan, sp): u.clan = sp
    db.commit(); return u

today = lambda: int(time.time() // 86400)
reward = lambda u: 1200 + 300 * (u.level - 1)
cost = lambda u: 1500 * u.level
invite = lambda c: f"https://t.me/{BOT}/{APP}?startapp={c}" if APP else f"https://t.me/{BOT}?startapp={c}"

def regen(u):
    now = time.time()
    if u.energy >= EMAX: u.energy, u.energy_t = EMAX, now; return
    n = int((now - u.energy_t) // EREGEN)
    if n > 0:
        u.energy = min(EMAX, u.energy + n)
        u.energy_t = now if u.energy >= EMAX else u.energy_t + n * EREGEN

def view(u, db):
    regen(u)
    c = db.get(Clan, u.clan) if u.clan else None
    return {"id": u.id, "name": u.name, "pts": u.pts, "level": u.level, "wallet": u.wallet,
            "depot_start": u.depot_start, "depot_sec": DEPOT, "reward": reward(u), "cost": cost(u),
            "best": u.best if u.day == today() else 0, "now": time.time(),
            "energy": u.energy, "emax": EMAX, "eregen": EREGEN, "enext": int(max(0, u.energy_t + EREGEN - time.time())) if u.energy < EMAX else 0,
            "clan": c and {"id": c.id, "name": c.name, "link": invite(c.id)},
            "block_id": os.getenv("ADSGRAM_BLOCK_ID", "54936"), "is_admin": u.id in ADMINS}

def bump(db, u, n):
    if u.clan: db.query(Clan).filter(Clan.id == u.clan).update({Clan.pts: Clan.pts + n})

def use_ad(db, uid, kind):  # Adsgram sunucu geri çağrısı onayladıysa 1 kez harcar
    a = db.query(Ad).filter(Ad.uid == uid, Ad.kind == kind, Ad.earned == True, Ad.used == False,
                            Ad.t0 > time.time() - 900).order_by(Ad.id).first()
    if not a: return False
    n = db.query(Ad).filter(Ad.id == a.id, Ad.used == False).update({Ad.used: True})
    db.commit(); return n == 1

@app.get("/healthz")
def healthz(): return {"ok": 1}

@app.get("/")
def index(): return FileResponse(os.path.join(ROOT, "frontend", "index.html"))

@app.get("/api/me")
def me(db=Depends(get_db), u=Depends(cur)):
    r = view(u, db); db.commit(); return r

@app.post("/api/depot/start")
def depot_start(db=Depends(get_db), u=Depends(cur)):
    if u.depot_start is None: u.depot_start = time.time(); db.commit()
    return view(u, db)

class Claim(BaseModel): double: bool = False

@app.post("/api/depot/claim")
def depot_claim(b: Claim, db=Depends(get_db), u=Depends(cur)):
    s = u.depot_start
    if s is None or time.time() < s + DEPOT: raise HTTPException(400, "Depo henüz dolmadı")
    if b.double and not use_ad(db, u.id, "depot2x"): raise HTTPException(402, "Reklam onayı bekleniyor")
    r = reward(u) * (2 if b.double else 1)
    n = db.query(User).filter(User.id == u.id, User.depot_start == s).update(
        {User.pts: User.pts + r, User.depot_start: None})
    if n != 1: db.rollback(); raise HTTPException(409, "Zaten toplandı")
    bump(db, u, r); db.commit(); db.refresh(u)
    return {"gain": r, **view(u, db)}

@app.post("/api/upgrade")
def upgrade(db=Depends(get_db), u=Depends(cur)):
    n = db.query(User).filter(User.id == u.id, User.pts >= cost(u), User.level == u.level).update(
        {User.pts: User.pts - cost(u), User.level: User.level + 1})
    if n != 1: db.rollback(); raise HTTPException(400, f"Yetersiz PTS (gerekli: {cost(u)})")
    db.commit(); db.refresh(u); return view(u, db)

class AdI(BaseModel): kind: str

@app.post("/api/ad/intent")
def ad_intent(b: AdI, db=Depends(get_db), u=Depends(cur)):
    if b.kind not in ("depot2x", "score2x", "energy"): raise HTTPException(400, "Geçersiz")
    db.add(Ad(uid=u.id, kind=b.kind, t0=time.time(), earned=DEV)); db.commit(); return {"ok": 1}

@app.get("/api/adsgram/reward")  # Adsgram Reward URL: .../api/adsgram/reward?userid=[userId]&secret=GIZLI
def ad_reward(userid: int, secret: str = ""):
    if not SECRET or not hmac.compare_digest(secret, SECRET): raise HTTPException(403, "Yetkisiz")
    db = SessionLocal()
    try:
        a = db.query(Ad).filter(Ad.uid == userid, Ad.earned == False,
                                Ad.t0 > time.time() - 900).order_by(Ad.id.desc()).first()
        if a:
            a.earned = True; u = db.get(User, userid)
            if u and u.clan: db.query(Clan).filter(Clan.id == u.clan).update({Clan.ads: Clan.ads + 1})
            db.add(AdLog(day=today(), clan=u.clan if u else None, uid=userid, t0=time.time()))
            db.commit()
        return {"ok": 1}
    finally: db.close()

@app.post("/api/run/start")
def run_start(db=Depends(get_db), u=Depends(cur)):
    d = today()
    if u.day != d: u.day, u.best, u.runs = d, 0, 0
    if u.runs >= 40: raise HTTPException(429, "Günlük tur limiti doldu")
    regen(u); db.flush()  # bekleyen değişiklikleri yaz, sonra atomik düş
    if db.query(User).filter(User.id == u.id, User.energy > 0).update({User.energy: User.energy - 1}) != 1:
        db.commit(); raise HTTPException(402, "Enerji bitti")
    u.runs += 1; r = Run(id=secrets.token_hex(8), uid=u.id, t0=time.time()); db.add(r); db.commit()
    return {"run": r.id, "seed": d, "dur": RUN_SEC, "energy": u.energy}

class Fin(BaseModel):
    run: str
    score: int
    double: bool = False

@app.post("/api/run/finish")
def run_finish(b: Fin, db=Depends(get_db), u=Depends(cur)):
    r = db.get(Run, b.run)
    if not r or r.uid != u.id or r.done: raise HTTPException(400, "Geçersiz tur")
    el = min(time.time() - r.t0, RUN_SEC + 5); r.done = True
    if el < 5 or b.score < 0 or b.score > MAXS * el + 100:
        db.commit(); raise HTTPException(400, "Skor doğrulanamadı")
    dbl = False
    if b.double:
        if not use_ad(db, u.id, "score2x"): raise HTTPException(402, "Reklam onayı bekleniyor")  # commit edilmedi, tur açık kalır
        dbl = True
    gain = b.score // 30 * (2 if dbl else 1)
    if u.day != today(): u.day, u.best, u.runs = today(), 0, 1
    u.pts += gain; u.best = max(u.best, b.score); bump(db, u, gain); db.commit()
    return {"gain": gain, "doubled": dbl, **view(u, db)}

@app.get("/api/leaderboard")
def leaderboard(db=Depends(get_db)):
    us = db.query(User).filter(User.day == today(), User.best > 0).order_by(User.best.desc()).limit(20).all()
    cs = db.query(Clan).order_by(Clan.pts.desc()).limit(20).all()
    mc = dict(db.query(User.clan, func.count()).group_by(User.clan).all())
    return {"users": [{"n": x.name, "s": x.best} for x in us],
            "clans": [{"n": c.name, "p": c.pts, "m": mc.get(c.id, 0)} for c in cs]}

@app.get("/api/partner")
def partner(db=Depends(get_db), u=Depends(cur)):
    counts, _, sp = stats(db); out = []
    for c in db.query(Clan).filter(Clan.partner == u.id).all():
        m = clan_money(db, c.id, counts, sp)
        out.append({"id": c.id, "name": c.name, **m, "available": max(0.0, m["available"]), "link": invite(c.id),
                    "min": MIN_PAY, "members": db.query(User).filter(User.clan == c.id).count()})
    return out

class NewClan(BaseModel):
    name: str
    partner: int

@app.post("/api/admin/clan")
def make_clan(b: NewClan, db=Depends(get_db), u=Depends(cur)):
    if u.id not in ADMINS: raise HTTPException(403, "Yetkisiz")
    c = Clan(id="c" + secrets.token_hex(4), name=b.name[:40], partner=b.partner)
    db.add(c); db.commit(); return {"id": c.id, "link": invite(c.id)}

class Wal(BaseModel): address: str

@app.post("/api/wallet")
def wallet(b: Wal, db=Depends(get_db), u=Depends(cur)):
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", b.address): raise HTTPException(400, "Geçersiz cüzdan adresi")
    u.wallet = b.address; db.commit(); return {"ok": 1}

@app.post("/api/energy/refill")
def energy_refill(db=Depends(get_db), u=Depends(cur)):
    regen(u)
    if u.energy >= EMAX: raise HTTPException(400, "Enerjin zaten dolu")
    if not use_ad(db, u.id, "energy"): raise HTTPException(402, "Reklam onayı bekleniyor")
    u.energy, u.energy_t = EMAX, time.time(); db.commit()
    return view(u, db)

class PayReq(BaseModel):
    clan: str
    wallet: str

@app.post("/api/payout/request")
def payout_request(b: PayReq, db=Depends(get_db), u=Depends(cur)):
    c = db.get(Clan, b.clan)
    if not c or c.partner != u.id: raise HTTPException(403, "Yetkisiz")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", b.wallet): raise HTTPException(400, "Geçersiz cüzdan adresi")
    if pending(db, c.id) > 0: raise HTTPException(409, "Bekleyen bir talebin var")
    counts, _, sp = stats(db)
    av = round(max(0.0, clan_money(db, c.id, counts, sp)["available"]), 4)
    if av < MIN_PAY: raise HTTPException(400, f"En az {MIN_PAY} USD birikmeli")
    p = Payout(clan=c.id, partner=u.id, amount=av, wallet=b.wallet, t0=time.time()); db.add(p); db.commit()
    return {"ok": 1, "amount": av}

@app.get("/api/admin/payouts")
def admin_payouts(db=Depends(get_db), u=Depends(cur)):
    if u.id not in ADMINS: raise HTTPException(403, "Yetkisiz")
    counts, _, sp = stats(db); out = []
    for x in db.query(Payout).filter(Payout.status == "pending").order_by(Payout.id).all():
        c = db.get(Clan, x.clan); m = clan_money(db, x.clan, counts, sp)
        limit = round(m["share"] - m["paid"], 4)   # bu talebin en fazla olabileceği tutar (önceden ödenenler düşülmüş)
        out.append({"id": x.id, "clan": x.clan, "name": c.name if c else x.clan, "partner": x.partner, "amount": x.amount,
                    "wallet": x.wallet, "share": m["share"], "paid": m["paid"], "limit": limit,
                    "ok": x.amount <= limit + 0.0001, "unconfirmed": m["unconfirmed"]})
    return out

@app.get("/api/admin/dashboard")
def admin_dashboard(db=Depends(get_db), u=Depends(cur)):
    if u.id not in ADMINS: raise HTTPException(403, "Yetkisiz")
    counts, real, sp = stats(db)
    clans = [{"id": c.id, "name": c.name, "partner": c.partner, **clan_money(db, c.id, counts, sp)}
             for c in db.query(Clan).order_by(Clan.name).all()]
    days = []
    for d in sorted(set(counts) | set(real), reverse=True)[:21]:
        r = real.get(d)
        days.append({"date": time.strftime("%Y-%m-%d", time.gmtime(d * 86400)), "ours": sum(counts.get(d, {}).values()),
                     "imps": r[0] if r else None, "cpm": r[1] if r else None, "earned": round(r[0] * r[1] / 1000, 4) if r else None})
    return {"clans": clans, "days": days, "total_ads": sum(sum(p.values()) for p in counts.values()),
            "no_clan_ads": sum(p.get(None, 0) for p in counts.values()),
            "earned": round(sum(i * m / 1000 for i, m in real.values()), 4),
            "owed": round(sum(c["share"] for c in clans), 4), "paid": round(sum(c["paid"] for c in clans), 4),
            "pending": round(sum(c["pending"] for c in clans), 4), "today": time.strftime("%Y-%m-%d", time.gmtime())}

class DayIn(BaseModel):
    date: str
    imps: int
    cpm: float

@app.post("/api/admin/day")
def admin_day(b: DayIn, db=Depends(get_db), u=Depends(cur)):
    if u.id not in ADMINS: raise HTTPException(403, "Yetkisiz")
    try: d = calendar.timegm(time.strptime(b.date, "%Y-%m-%d")) // 86400
    except ValueError: raise HTTPException(400, "Tarih YYYY-AA-GG biçiminde olmalı")
    if d >= today(): raise HTTPException(400, "Gün bitmeden onaylanamaz, dünü ve öncesini gir")
    if not (0 <= b.imps <= 10_000_000) or not (0 <= b.cpm <= 100): raise HTTPException(400, "Değerler geçersiz")
    r = db.get(Day, d)
    if r: r.imps, r.cpm = b.imps, b.cpm
    else: db.add(Day(day=d, imps=b.imps, cpm=b.cpm))
    db.commit(); return {"ok": 1}

class PayDone(BaseModel):
    id: int
    ok: bool
    tx: str = ""

@app.post("/api/admin/payout")
def admin_payout(b: PayDone, db=Depends(get_db), u=Depends(cur)):
    if u.id not in ADMINS: raise HTTPException(403, "Yetkisiz")
    p = db.get(Payout, b.id)
    if not p or p.status != "pending": raise HTTPException(400, "Talep bulunamadı")
    p.status = "paid" if b.ok else "rejected"; p.tx = b.tx[:80]; db.commit()
    return {"ok": 1}

# ——— Telegram bot (webhook): /start'a yanıt verir, Adsgram moderasyonu "bot çalışıyor mu" diye bakar ———
PUBLIC = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WH_SECRET = hashlib.sha256(("wh:" + BOT_TOKEN).encode()).hexdigest()   # ayrı ortam değişkeni gerekmez

def tg(method, **data):
    if not BOT_TOKEN: return {}
    try: return requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data, timeout=10).json()
    except Exception: return {}

def open_link(param=""):
    base = f"https://t.me/{BOT}/{APP}" if APP else f"https://t.me/{BOT}"
    return base + (f"?startapp={param}" if param else "")

def setup_bot():
    time.sleep(3)
    if not (BOT_TOKEN and PUBLIC): return
    tg("setWebhook", url=f"{PUBLIC}/tg/webhook", secret_token=WH_SECRET, allowed_updates=["message"])
    tg("setMyCommands", commands=[{"command": "start", "description": "Oyunu aç"}])
    tg("setChatMenuButton", menu_button={"type": "web_app", "text": "Oyna", "web_app": {"url": PUBLIC}})
threading.Thread(target=setup_bot, daemon=True).start()

@app.post("/tg/webhook")
async def tg_webhook(req: Request, x_telegram_bot_api_secret_token: str = Header("")):
    if not BOT_TOKEN or not hmac.compare_digest(x_telegram_bot_api_secret_token, WH_SECRET):
        raise HTTPException(403, "Yetkisiz")
    try: m = (await req.json()).get("message") or {}
    except Exception: return {"ok": 1}
    chat, text = m.get("chat") or {}, (m.get("text") or "").strip()
    if chat.get("type") == "private" and text.startswith("/start"):
        param = text.split(maxsplit=1)[1][:20] if len(text.split()) > 1 else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]*", param): param = ""
        tg("sendMessage", chat_id=chat["id"], text="⚔️ Continuum Clan Wars\n\nDeponu doldur, 60 saniyelik turlarda çekirdeği koru, klanınla sıralamaya çık.",
           reply_markup={"inline_keyboard": [[{"text": "⚔️ Oyunu aç", "web_app": {"url": PUBLIC}} if not param
                                              else {"text": "⚔️ Klana katıl ve oyna", "url": open_link(param)}]]})
    return {"ok": 1}
