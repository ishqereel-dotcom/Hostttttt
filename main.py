import asyncio, shutil, time
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from . import config, db, security, runner

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    yield
    runner.stop_all()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

def render(request, name, **ctx):
    ctx.update({"request": request, "user": getattr(request.state, "user", None)})
    return templates.TemplateResponse(name, ctx)

# ---------- middleware: attach user ----------
@app.middleware("http")
async def attach_user(request: Request, call_next):
    request.state.user = None
    token = request.cookies.get(security.SESSION_COOKIE)
    if token:
        data = security.read_session(token)
        if data:
            request.state.user = data
    return await call_next(request)

# ---------- auth ----------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return RedirectResponse("/dashboard" if request.state.user else "/login")

@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return render(request, "login.html", err=None)

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    if email == config.ADMIN_USER and password == config.ADMIN_PASS:
        resp = RedirectResponse("/admin", 303)
        resp.set_cookie(security.SESSION_COOKIE, security.make_session(0, True),
                        httponly=True, samesite="lax", max_age=security.MAX_AGE)
        return resp
    async with db.conn() as c:
        cur = await c.execute("SELECT id, pw_hash FROM users WHERE email=?", (email.lower(),))
        row = await cur.fetchone()
    if not row or not security.check_pw(password, row[1]):
        return render(request, "login.html", err="Galat email ya password")
    resp = RedirectResponse("/dashboard", 303)
    resp.set_cookie(security.SESSION_COOKIE, security.make_session(row[0], False),
                    httponly=True, samesite="lax", max_age=security.MAX_AGE)
    return resp

@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return render(request, "register.html", err=None)

@app.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    if len(password) < 6:
        return render(request, "register.html", err="Password kam se kam 6 characters")
    async with db.conn() as c:
        try:
            await c.execute("INSERT INTO users(email,pw_hash) VALUES(?,?)",
                            (email.lower(), security.hash_pw(password)))
            await c.commit()
        except Exception:
            return render(request, "register.html", err="Email pehle se registered hai")
        cur = await c.execute("SELECT id FROM users WHERE email=?", (email.lower(),))
        uid = (await cur.fetchone())[0]
    resp = RedirectResponse("/dashboard", 303)
    resp.set_cookie(security.SESSION_COOKIE, security.make_session(uid, False),
                    httponly=True, samesite="lax", max_age=security.MAX_AGE)
    return resp

@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", 303)
    resp.delete_cookie(security.SESSION_COOKIE)
    return resp

async def require_user(request: Request) -> dict:
    if not request.state.user:
        raise HTTPException(401)
    return request.state.user

async def require_admin(request: Request) -> dict:
    u = await require_user(request)
    if not u["adm"]:
        raise HTTPException(403, "admin only")
    return u

# ---------- user dashboard ----------
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        u = await require_user(request)
    except HTTPException:
        return RedirectResponse("/login", 303)
    async with db.conn() as c:
        cur = await c.execute(
            "SELECT id,name,status,created_at,entry_file FROM bots WHERE user_id=? ORDER BY id DESC",
            (u["uid"],))
        bots = await cur.fetchall()
    bots = [{**dict(zip(["id","name","status","created_at","entry_file"], b)),
             "alive": runner.alive(b["0"])} for b in bots]
    return render(request, "dashboard.html", bots=bots)

@app.get("/bots/new", response_class=HTMLResponse)
async def new_bot_form(request: Request):
    if not request.state.user:
        return RedirectResponse("/login", 303)
    return render(request, "new_bot.html", err=None)

@app.post("/bots/new")
async def new_bot(request: Request,
                  name: str = Form(...),
                  bot_token: str = Form(...),
                  api_id: str = Form(""),
                  api_hash: str = Form(""),
                  session_string: str = Form(""),
                  file: UploadFile = File(...)):
    if not request.state.user:
        return RedirectResponse("/login", 303)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".py", ".zip"):
        return render(request, "new_bot.html", err="Sirf .py ya .zip allowed hai")
    raw = await file.read()
    if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
        return render(request, "new_bot.html", err=f"File {config.MAX_UPLOAD_MB}MB se badi hai")
    async with db.conn() as c:
        cur = await c.execute(
            "INSERT INTO bots(user_id,name,bot_token,api_id,api_hash,session_string,filename,file_path,entry_file,status)"
            " VALUES(?,?,?,?,?,?,?, '', '', 'pending')",
            (request.state.user["uid"], name.strip(), bot_token.strip(),
             api_id.strip(), api_hash.strip(), session_string.strip(), file.filename))
        await c.commit()
        bid = cur.lastrowid
    saved = config.UPLOAD_DIR / f"{bid}_{file.filename}"
    saved.write_bytes(raw)
    workdir = runner.extract_upload(saved, bid)
    entry = runner.pick_entry(workdir)
    if not entry:
        entry = "main.py"
    async with db.conn() as c:
        await c.execute("UPDATE bots SET file_path=?, entry_file=? WHERE id=?",
                        (str(workdir / entry), entry, bid))
        await c.commit()
    return RedirectResponse(f"/bots/{bid}", 303)

@app.get("/bots/{bot_id}", response_class=HTMLResponse)
async def bot_detail(request: Request, bot_id: int):
    if not request.state.user:
        return RedirectResponse("/login", 303)
    u = request.state.user
    async with db.conn() as c:
        cur = await c.execute("SELECT * FROM bots WHERE id=?", (bot_id,))
        row = await cur.fetchone()
        cols = [d[0] for d in cur.description]
    if not row:
        raise HTTPException(404)
    bot = dict(zip(cols, row))
    if not u["adm"] and bot["user_id"] != u["uid"]:
        raise HTTPException(403)
    src = Path(bot["file_path"]).parent
    files = sorted(p.name for p in src.glob("*.py"))
    return render(request, "bot_detail.html", bot=bot, files=files,
                  alive=runner.alive(bot_id), logs=runner.log_lines(bot_id)[-120:])

@app.post("/bots/{bot_id}/stop")
async def user_stop(request: Request, bot_id: int):
    if not request.state.user:
        return RedirectResponse("/login", 303)
    runner.stop(bot_id)
    async with db.conn() as c:
        await c.execute("UPDATE bots SET status='stopped', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'", (bot_id,))
        await c.commit()
    return RedirectResponse(f"/bots/{bot_id}", 303)

# ---------- admin ----------
@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    try:
        await require_admin(request)
    except HTTPException:
        return RedirectResponse("/login", 303)
    async with db.conn() as c:
        cur = await c.execute("""
            SELECT b.id, b.name, b.status, u.email, b.created_at
            FROM bots b JOIN users u ON u.id=b.user_id ORDER BY
            CASE b.status WHEN 'pending' THEN 0 WHEN 'running' THEN 1 ELSE 2 END, b.id DESC""")
        bots = await cur.fetchall()
    bots = [{"id": b[0], "name": b[1], "status": b[2], "email": b[3],
             "created_at": b[4], "alive": runner.alive(b[0])} for b in bots]
    return render(request, "admin.html", bots=bots)

@app.get("/admin/bots/{bot_id}", response_class=HTMLResponse)
async def admin_bot(request: Request, bot_id: int, f: str = ""):
    try:
        await require_admin(request)
    except HTTPException:
        return RedirectResponse("/login", 303)
    async with db.conn() as c:
        cur = await c.execute("SELECT b.*, u.email FROM bots b JOIN users u ON u.id=b.user_id WHERE b.id=?", (bot_id,))
        row = await cur.fetchone()
        cols = [d[0] for d in cur.description]
    if not row:
        raise HTTPException(404)
    bot = dict(zip(cols, row))
    src = Path(bot["file_path"]).parent
    files = sorted(p.name for p in src.rglob("*") if p.is_file())
    opened = f if f in files else (bot["entry_file"] if bot["entry_file"] in files else (files[0] if files else ""))
    code = ""
    if opened:
        code = (src / opened).read_text("utf-8", "replace")[:200_000]
    return render(request, "admin_bot.html", bot=bot, files=files,
                  opened=opened, code=code, alive=runner.alive(bot_id),
                  logs=runner.log_lines(bot_id)[-200:])

@app.post("/admin/bots/{bot_id}/approve")
async def approve(request: Request, bot_id: int):
    await require_admin(request)
    async with db.conn() as c:
        cur = await c.execute("SELECT * FROM bots WHERE id=?", (bot_id,))
        row = await cur.fetchone()
        cols = [d[0] for d in cur.description]
    bot = dict(zip(cols, row))
    ok, msg = runner.start(bot)
    status = "running" if ok else "error"
    async with db.conn() as c:
        await c.execute("UPDATE bots SET status=?, reject_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (status, "" if ok else msg, bot_id))
        await c.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", 303)

@app.post("/admin/bots/{bot_id}/reject")
async def reject(request: Request, bot_id: int, reason: str = Form("")):
    await require_admin(request)
    runner.stop(bot_id)
    async with db.conn() as c:
        await c.execute("UPDATE bots SET status='rejected', reject_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (reason.strip(), bot_id))
        await c.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", 303)

@app.post("/admin/bots/{bot_id}/restart")
async def restart(request: Request, bot_id: int):
    await require_admin(request)
    runner.stop(bot_id)
    await asyncio.sleep(0.6)
    async with db.conn() as c:
        cur = await c.execute("SELECT * FROM bots WHERE id=?", (bot_id,))
        row = await cur.fetchone()
        cols = [d[0] for d in cur.description]
    bot = dict(zip(cols, row))
    ok, msg = runner.start(bot)
    async with db.conn() as c:
        await c.execute("UPDATE bots SET status=?, reject_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        ("running" if ok else "error", "" if ok else msg, bot_id))
        await c.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", 303)

@app.get("/admin/running", response_class=HTMLResponse)
async def admin_running(request: Request):
    try:
        await require_admin(request)
    except HTTPException:
        return RedirectResponse("/login", 303)
    async with db.conn() as c:
        cur = await c.execute("SELECT id,name,status,file_path FROM bots WHERE status='running'")
        bots = await cur.fetchall()
    return render(request, "admin.html",
                  bots=[{"id": b[0], "name": b[1], "status": b[2], "email": "-",
                         "created_at": "", "alive": runner.alive(b[0])} for b in bots])