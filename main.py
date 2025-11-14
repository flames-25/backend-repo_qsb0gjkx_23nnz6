import os
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional, Literal, Dict, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from bson import ObjectId

from database import db

app = FastAPI(title="SIAS - Sistem Informasi Absensi Siswa")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Helpers

def objid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")


def today_str() -> str:
    # Use Asia/Jakarta logical date by offsetting to UTC+7 if needed; here we rely on container localtime.
    return datetime.now().date().isoformat()


def now_time_str() -> str:
    return datetime.now().strftime("%H:%M")


def require_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1].strip()
    sess = db.admin_sessions.find_one({"token": token})
    if not sess:
        raise HTTPException(status_code=401, detail="Session invalid")
    # optional: expiry check
    if sess.get("expires_at") and datetime.utcnow() > sess["expires_at"]:
        db.admin_sessions.delete_one({"_id": sess["_id"]})
        raise HTTPException(status_code=401, detail="Session expired")
    admin = db.admin.find_one({"_id": objid(sess["admin_id"])})
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")
    return {"admin": admin, "token": token}


# Pydantic models for requests/responses
class KelasIn(BaseModel):
    nama_kelas: str


class KelasOut(BaseModel):
    id: str
    nama_kelas: str


class SiswaIn(BaseModel):
    nis: str
    nama_lengkap: str
    id_kelas: str


class SiswaOut(BaseModel):
    id: str
    nis: str
    nama_lengkap: str
    id_kelas: str
    nama_kelas: Optional[str] = None


StatusType = Literal['Hadir', 'Sakit', 'Izin', 'Alpha']


class AbsenCheckIn(BaseModel):
    nis: str


class AbsenSetStatus(BaseModel):
    nis: str
    tanggal: Optional[str] = Field(default_factory= today_str)
    status: StatusType
    jam_masuk: Optional[str] = None


class AdminLoginIn(BaseModel):
    username: str
    password: str


class AdminLoginOut(BaseModel):
    token: str
    username: str
    nama_lengkap: Optional[str] = None


# Root & health
@app.get("/")
def read_root():
    return {"message": "SIAS Backend running"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# ========== ADMIN AUTH ==========
from passlib.hash import bcrypt

@app.post("/api/admin/seed-default")
def seed_default_admin():
    """Create default admin if not exists: username=admin, password=admin123"""
    existing = db.admin.find_one({"username": "admin"})
    if existing:
        return {"status": "ok", "message": "Admin already exists"}
    pw_hash = bcrypt.hash("admin123")
    db.admin.insert_one({
        "username": "admin",
        "password_hash": pw_hash,
        "nama_lengkap": "Administrator",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })
    return {"status": "ok", "message": "Default admin created", "username": "admin", "password": "admin123"}


@app.post("/api/admin/login", response_model=AdminLoginOut)
def admin_login(payload: AdminLoginIn):
    admin = db.admin.find_one({"username": payload.username})
    if not admin:
        raise HTTPException(status_code=401, detail="Username atau password salah")
    if not bcrypt.verify(payload.password, admin.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Username atau password salah")
    token = uuid4().hex
    db.admin_sessions.insert_one({
        "admin_id": str(admin["_id"]),
        "token": token,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(hours=12),
    })
    return {"token": token, "username": admin.get("username"), "nama_lengkap": admin.get("nama_lengkap")}


@app.post("/api/admin/logout")
def admin_logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        db.admin_sessions.delete_many({"token": token})
    return {"status": "ok"}


# ========== KELAS CRUD ==========
@app.post("/api/kelas", response_model=KelasOut)
def create_kelas(payload: KelasIn, admin=Header(None, alias="Authorization")):
    require_admin(admin)
    existing = db.kelas.find_one({"nama_kelas": payload.nama_kelas})
    if existing:
        raise HTTPException(status_code=400, detail="Nama kelas sudah ada")
    res = db.kelas.insert_one({"nama_kelas": payload.nama_kelas, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()})
    return {"id": str(res.inserted_id), "nama_kelas": payload.nama_kelas}


@app.get("/api/kelas", response_model=List[KelasOut])
def list_kelas():
    items = []
    for k in db.kelas.find({}).sort("nama_kelas", 1):
        items.append({"id": str(k["_id"]), "nama_kelas": k.get("nama_kelas", "")})
    return items


@app.put("/api/kelas/{kelas_id}", response_model=KelasOut)
def update_kelas(kelas_id: str, payload: KelasIn, admin=Header(None, alias="Authorization")):
    require_admin(admin)
    result = db.kelas.update_one({"_id": objid(kelas_id)}, {"$set": {"nama_kelas": payload.nama_kelas, "updated_at": datetime.utcnow()}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")
    doc = db.kelas.find_one({"_id": objid(kelas_id)})
    return {"id": kelas_id, "nama_kelas": doc.get("nama_kelas", "")}


@app.delete("/api/kelas/{kelas_id}")
def delete_kelas(kelas_id: str, admin=Header(None, alias="Authorization")):
    require_admin(admin)
    # Prevent delete if referenced by siswa
    ref = db.siswa.find_one({"id_kelas": str(objid(kelas_id))})
    if ref:
        raise HTTPException(status_code=400, detail="Kelas digunakan oleh data siswa")
    result = db.kelas.delete_one({"_id": objid(kelas_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")
    return {"status": "ok"}


# ========== SISWA CRUD ==========
@app.post("/api/siswa", response_model=SiswaOut)
def create_siswa(payload: SiswaIn, admin=Header(None, alias="Authorization")):
    require_admin(admin)
    # Validate kelas exists
    kelas = db.kelas.find_one({"_id": objid(payload.id_kelas)})
    if not kelas:
        raise HTTPException(status_code=400, detail="Kelas tidak valid")
    # Unique NIS
    if db.siswa.find_one({"nis": payload.nis}):
        raise HTTPException(status_code=400, detail="NIS sudah terdaftar")
    doc = {
        "nis": payload.nis,
        "nama_lengkap": payload.nama_lengkap,
        "id_kelas": str(kelas["_id"]),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    res = db.siswa.insert_one(doc)
    return {
        "id": str(res.inserted_id),
        "nis": payload.nis,
        "nama_lengkap": payload.nama_lengkap,
        "id_kelas": str(kelas["_id"]),
        "nama_kelas": kelas.get("nama_kelas"),
    }


@app.get("/api/siswa", response_model=List[SiswaOut])
def list_siswa(id_kelas: Optional[str] = None, q: Optional[str] = None):
    filter_: Dict[str, Any] = {}
    if id_kelas:
        filter_["id_kelas"] = id_kelas
    if q:
        filter_["$or"] = [
            {"nama_lengkap": {"$regex": q, "$options": "i"}},
            {"nis": {"$regex": q, "$options": "i"}},
        ]
    items: List[SiswaOut] = []
    kelas_map: Dict[str, str] = {str(k["_id"]): k.get("nama_kelas", "") for k in db.kelas.find({})}
    for s in db.siswa.find(filter_).sort("nama_lengkap", 1):
        items.append({
            "id": str(s["_id"]),
            "nis": s.get("nis", ""),
            "nama_lengkap": s.get("nama_lengkap", ""),
            "id_kelas": s.get("id_kelas", ""),
            "nama_kelas": kelas_map.get(s.get("id_kelas", "")),
        })
    return items


@app.put("/api/siswa/{siswa_id}", response_model=SiswaOut)
def update_siswa(siswa_id: str, payload: SiswaIn, admin=Header(None, alias="Authorization")):
    require_admin(admin)
    # validate kelas
    if not db.kelas.find_one({"_id": objid(payload.id_kelas)}):
        raise HTTPException(status_code=400, detail="Kelas tidak valid")
    # unique NIS (exclude current)
    if db.siswa.find_one({"nis": payload.nis, "_id": {"$ne": objid(siswa_id)}}):
        raise HTTPException(status_code=400, detail="NIS sudah terpakai oleh siswa lain")
    result = db.siswa.update_one(
        {"_id": objid(siswa_id)},
        {"$set": {"nis": payload.nis, "nama_lengkap": payload.nama_lengkap, "id_kelas": payload.id_kelas, "updated_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    s = db.siswa.find_one({"_id": objid(siswa_id)})
    kelas = db.kelas.find_one({"_id": objid(s.get("id_kelas"))}) if s else None
    return {
        "id": siswa_id,
        "nis": s.get("nis", ""),
        "nama_lengkap": s.get("nama_lengkap", ""),
        "id_kelas": s.get("id_kelas", ""),
        "nama_kelas": (kelas or {}).get("nama_kelas"),
    }


@app.delete("/api/siswa/{siswa_id}")
def delete_siswa(siswa_id: str, admin=Header(None, alias="Authorization")):
    require_admin(admin)
    # Also delete absensi records for this siswa
    db.absensi.delete_many({"id_siswa": siswa_id})
    result = db.siswa.delete_one({"_id": objid(siswa_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    return {"status": "ok"}


# ========== ABSENSI ==========
@app.post("/api/absen/checkin")
def absen_checkin(payload: AbsenCheckIn):
    # find siswa by NIS
    siswa = db.siswa.find_one({"nis": payload.nis})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    id_siswa = str(siswa["_id"])
    tanggal = today_str()

    # prevent duplicate Hadir in same day
    existing = db.absensi.find_one({"id_siswa": id_siswa, "tanggal": tanggal, "status": "Hadir"})
    if existing:
        return {
            "message": f"Sudah absen hari ini pada {existing.get('jam_masuk', '-')}",
            "nama": siswa.get("nama_lengkap"),
            "kelas": siswa.get("id_kelas"),
            "status": "Hadir",
            "jam_masuk": existing.get("jam_masuk"),
        }

    now_time = now_time_str()
    doc = {
        "id_siswa": id_siswa,
        "tanggal": tanggal,
        "jam_masuk": now_time,
        "status": "Hadir",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    db.absensi.update_one({"id_siswa": id_siswa, "tanggal": tanggal}, {"$set": doc}, upsert=True)

    return {
        "message": "Absensi berhasil",
        "nama": siswa.get("nama_lengkap"),
        "kelas": siswa.get("id_kelas"),
        "status": "Hadir",
        "jam_masuk": now_time,
    }


@app.get("/api/status/today")
def status_today(id_kelas: Optional[str] = None):
    # Build siswa list (optionally by kelas)
    filter_: Dict[str, Any] = {}
    if id_kelas:
        filter_["id_kelas"] = id_kelas
    siswa_list = list(db.siswa.find(filter_))

    tanggal = today_str()
    # map absensi today by id_siswa
    abs_map: Dict[str, Dict[str, Any]] = {a["id_siswa"]: a for a in db.absensi.find({"tanggal": tanggal})}

    kelas_map: Dict[str, str] = {str(k["_id"]): k.get("nama_kelas", "") for k in db.kelas.find({})}

    result = []
    for s in siswa_list:
        sid = str(s["_id"])
        kelas_name = kelas_map.get(s.get("id_kelas", ""), "")
        a = abs_map.get(sid)
        if a and a.get("status") == "Hadir":
            status_text = f"Sudah Absen pukul {a.get('jam_masuk', '-') }"
        elif a and a.get("status") in ("Sakit", "Izin"):
            status_text = a.get("status")
        else:
            status_text = "Belum Absen"
        result.append({
            "nama": s.get("nama_lengkap"),
            "nis": s.get("nis"),
            "kelas": kelas_name,
            "status_hari_ini": status_text,
        })
    return {"tanggal": tanggal, "data": result}


@app.get("/api/stats/today")
def stats_today():
    tanggal = today_str()
    total_siswa = db.siswa.count_documents({})
    pipeline = [
        {"$match": {"tanggal": tanggal}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    agg = list(db.absensi.aggregate(pipeline))
    counts = { (x["_id"] or "None"): x["count"] for x in agg }
    hadir = counts.get("Hadir", 0)
    sakit = counts.get("Sakit", 0)
    izin = counts.get("Izin", 0)
    # Alpha = yang tidak ada catatan hadir/sakit/izin
    alpha = max(0, total_siswa - (hadir + sakit + izin))
    return {"tanggal": tanggal, "hadir": hadir, "sakit": sakit, "izin": izin, "alpha": alpha, "total": total_siswa}


@app.put("/api/absen/status")
def set_status(payload: AbsenSetStatus, admin=Header(None, alias="Authorization")):
    require_admin(admin)
    # find siswa by NIS
    siswa = db.siswa.find_one({"nis": payload.nis})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    id_siswa = str(siswa["_id"])
    tanggal = payload.tanggal or today_str()

    update_doc: Dict[str, Any] = {
        "id_siswa": id_siswa,
        "tanggal": tanggal,
        "updated_at": datetime.utcnow(),
        "status": payload.status,
    }
    if payload.status == "Hadir":
        update_doc["jam_masuk"] = payload.jam_masuk or now_time_str()
    elif payload.jam_masuk is not None:
        update_doc["jam_masuk"] = payload.jam_masuk

    db.absensi.update_one({"id_siswa": id_siswa, "tanggal": tanggal}, {"$set": update_doc}, upsert=True)
    return {"message": "Status tersimpan", "nis": payload.nis, "tanggal": tanggal, "status": payload.status}


# ========== LAPORAN ==========
@app.get("/api/laporan/rekap")
def laporan_rekap(
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    id_kelas: Optional[str] = None,
    nis: Optional[str] = None,
    admin=Header(None, alias="Authorization")
):
    require_admin(admin)
    try:
        start_d = datetime.fromisoformat(start).date()
        end_d = datetime.fromisoformat(end).date()
    except Exception:
        raise HTTPException(status_code=400, detail="Format tanggal tidak valid (YYYY-MM-DD)")
    if end_d < start_d:
        raise HTTPException(status_code=400, detail="Rentang tanggal tidak valid")

    siswa_filter: Dict[str, Any] = {}
    if id_kelas:
        siswa_filter["id_kelas"] = id_kelas
    if nis:
        siswa_filter["nis"] = nis
    siswa_list = list(db.siswa.find(siswa_filter))
    if nis and not siswa_list:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    siswa_ids = [str(s["_id"]) for s in siswa_list]

    abs_filter: Dict[str, Any] = {"tanggal": {"$gte": start, "$lte": end}}
    if siswa_ids:
        abs_filter["id_siswa"] = {"$in": siswa_ids}

    data = list(db.absensi.find(abs_filter))

    # build maps
    kelas_map: Dict[str, str] = {str(k["_id"]): k.get("nama_kelas", "") for k in db.kelas.find({})}

    # init summary per siswa
    summary: Dict[str, Dict[str, Any]] = {}
    for s in siswa_list:
        summary[str(s["_id"])] = {
            "nis": s.get("nis"),
            "nama": s.get("nama_lengkap"),
            "kelas": kelas_map.get(s.get("id_kelas", ""), ""),
            "Hadir": 0,
            "Sakit": 0,
            "Izin": 0,
            "Alpha": 0,
        }

    # Count statuses; Alpha will be derived by days without any record
    # Generate all dates in range
    delta_days = (end_d - start_d).days + 1
    all_dates = [(start_d + timedelta(days=i)).isoformat() for i in range(delta_days)]

    # mark present statuses
    present_map: Dict[str, set] = {sid: set() for sid in summary.keys()}

    for a in data:
        sid = a.get("id_siswa")
        st = a.get("status") or "Alpha"
        if sid in summary:
            if st in ("Hadir", "Sakit", "Izin"):
                summary[sid][st] += 1
                present_map[sid].add(a.get("tanggal"))

    # Compute Alpha as days in range minus days with any record for that siswa
    for sid in summary.keys():
        days_recorded = len(present_map[sid])
        summary[sid]["Alpha"] = max(0, len(all_dates) - days_recorded)

    return {"range": {"start": start, "end": end}, "data": list(summary.values())}


@app.get("/api/laporan/rekap/csv")
def laporan_rekap_csv(
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    id_kelas: Optional[str] = None,
    nis: Optional[str] = None,
    admin=Header(None, alias="Authorization")
):
    # Reuse the JSON computation then format CSV
    json_data = laporan_rekap(start=start, end=end, id_kelas=id_kelas, nis=nis, admin=admin)
    rows = json_data["data"]
    lines = ["NIS,Nama,Kelas,Hadir,Sakit,Izin,Alpha"]
    for r in rows:
        line = f"{r['nis']},{r['nama']},{r['kelas']},{r['Hadir']},{r['Sakit']},{r['Izin']},{r['Alpha']}"
        lines.append(line)
    csv_text = "\n".join(lines)
    return Response(content=csv_text, media_type="text/csv")


# ========== DEMO SEED ==========
@app.post("/api/seed-demo")
def seed_demo():
    """Idempotent: create sample classes and students if not present"""
    # Classes
    kelas_names = ["X-A", "X-B"]
    kelas_map: Dict[str, str] = {}
    for name in kelas_names:
        k = db.kelas.find_one({"nama_kelas": name})
        if not k:
            res = db.kelas.insert_one({"nama_kelas": name, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()})
            kelas_map[name] = str(res.inserted_id)
        else:
            kelas_map[name] = str(k["_id"])

    # Students
    samples = [
        ("1001", "Budi Santoso", kelas_map["X-A"]),
        ("1002", "Siti Aminah", kelas_map["X-A"]),
        ("2001", "Andi Wijaya", kelas_map["X-B"]),
        ("2002", "Rina Marlina", kelas_map["X-B"]),
    ]
    created = 0
    for nis, nama, kid in samples:
        if not db.siswa.find_one({"nis": nis}):
            db.siswa.insert_one({
                "nis": nis,
                "nama_lengkap": nama,
                "id_kelas": kid,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            })
            created += 1
    return {"status": "ok", "kelas": list(kelas_map.items()), "siswa_baru": created}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
