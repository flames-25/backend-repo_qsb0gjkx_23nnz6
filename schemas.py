"""
Database Schemas for SIAS (Sistem Informasi Absensi Siswa)

Each Pydantic model generally maps to a MongoDB collection using the
lowercase class name as collection name.

Collections:
- Admin -> "admin"
- Kelas -> "kelas"
- Siswa -> "siswa"
- Absensi -> "absensi"
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import date, time


class Admin(BaseModel):
    username: str = Field(..., description="Unique username")
    password_hash: str = Field(..., description="BCrypt hash of password")
    nama_lengkap: str = Field(..., description="Full name of admin")


class Kelas(BaseModel):
    nama_kelas: str = Field(..., description='e.g., "X-A", "XII-IPA-1"')


class Siswa(BaseModel):
    nis: str = Field(..., description="Unique student ID")
    nama_lengkap: str = Field(..., description="Student full name")
    id_kelas: str = Field(..., description="Reference to kelas _id as string")


StatusType = Literal['Hadir', 'Sakit', 'Izin', 'Alpha']


class Absensi(BaseModel):
    id_siswa: str = Field(..., description="Reference to siswa _id as string")
    tanggal: date = Field(..., description="Attendance calendar date (YYYY-MM-DD)")
    jam_masuk: Optional[str] = Field(None, description="HH:MM in local time")
    status: Optional[StatusType] = Field(None, description="Attendance status for the day")
