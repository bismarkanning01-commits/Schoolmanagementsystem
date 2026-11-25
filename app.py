from flask import Flask, current_app, render_template, request, redirect, url_for, session, flash
from flask_login import (LoginManager,login_user,login_required,logout_user,current_user,UserMixin)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from config import Config
import hashlib
import joblib
import numpy as np
import os
import secrets
import string
import smtplib
import threading
import webbrowser
from datetime import datetime, timedelta
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import SQLAlchemyError
from datetime import date
from reportlab.pdfgen import canvas
from download_report import (
    report_bp,
    _grade_and_remark,
    _invoice_arrears,
    _next_academic_period,
    _promotion_display,
    _promotion_target,
    _term_fee_amounts,
)
from reportlab.lib.colors import *
from reportlab.lib.units import inch
from reportlab.platypus import Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from flask import make_response
from reportlab.lib import colors
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager, create_access_token
from flask_jwt_extended import jwt_required
from collections import defaultdict
from weasyprint import HTML
from flask import send_file
from flask_mail import Mail, Message
from PIL import Image, ImageOps, UnidentifiedImageError
import io
import re
import math
#import requests
import uuid
import base64
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

#from sms_service import send_sms

# =========================
# APP SETUP
# =========================
app = Flask(__name__)
app.config.from_object("config.Config")
app.config["TEMPLATES_AUTO_RELOAD"] = (
    app.config["APP_ENV"] == "development"
)
app.register_blueprint(report_bp)
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = (
    os.environ.get("MAIL_DEFAULT_SENDER") or app.config["MAIL_USERNAME"]
)
app.config["REPORT_HEADTEACHER_NAME"] = "Mr. John Mensah"
app.config["REPORT_SCHOOL_NAME"] = "SENIOR VICTORY COLLEGE"
app.config["REPORT_SCHOOL_ADDRESS"] = "SCHOOL ADDRESS"
app.config["REPORT_SCHOOL_PHONE"] = "SCHOOL PHONE"
app.config["REPORT_SCHOOL_EMAIL"] = "SCHOOL EMAIL"
app.config["REPORT_SCHOOL_MOTTO"] = "Raising the Standard"
app.config["REPORT_LOGO_PATH"] = "uploads/school_logo.png"
mail = Mail(app)

db = SQLAlchemy(app)

jwt = JWTManager(app)

migrate = Migrate(app, db)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# =========================
# PHONE FORMAT
# =========================
def format_ghana_phone(phone):
    if not phone:
        return None

    phone = re.sub(r"[\s()-]", "", phone.strip())

    if re.match(r"^0\d{9}$", phone):
        return "233" + phone[1:]

    if phone.startswith("+233"):
        return phone[1:]

    if re.match(r"^233\d{9}$", phone):
        return phone

    return None   


def _normalize_email(value):
    email = (value or '').strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None
    return email


def _email_in_use(email, exclude_user_id=None):
    query = User.query.filter(func.lower(User.email) == email)
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.first() is not None


MAX_PASSPORT_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PASSPORT_PHOTO_PIXELS = 20_000_000


def _save_student_passport_photo(uploaded_file):
    image_bytes = uploaded_file.read(MAX_PASSPORT_PHOTO_BYTES + 1)
    if not image_bytes:
        raise ValueError("Choose a passport photo to upload.")
    if len(image_bytes) > MAX_PASSPORT_PHOTO_BYTES:
        raise ValueError("The passport photo must be 5 MB or smaller.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image_format = source.format
            width, height = source.size
            if image_format not in {"JPEG", "PNG"}:
                raise ValueError("Upload a JPG or PNG passport photo.")
            if width * height > MAX_PASSPORT_PHOTO_PIXELS:
                raise ValueError("The passport photo dimensions are too large.")
            source.verify()

        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            if image_format == "JPEG":
                image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The uploaded file is not a valid JPG or PNG image.") from error

    extension = "jpg" if image_format == "JPEG" else "png"
    filename = f"{uuid.uuid4().hex}.{extension}"
    relative_path = os.path.join(
        "uploads",
        "student_photos",
        filename
    ).replace(os.sep, "/")
    photo_directory = os.path.join(
        app.static_folder,
        "uploads",
        "student_photos"
    )
    os.makedirs(photo_directory, exist_ok=True)
    image.save(
        os.path.join(photo_directory, filename),
        format=image_format
    )
    return relative_path


MAX_SCHOOL_LOGO_BYTES = 5 * 1024 * 1024


def _save_school_logo(uploaded_file):
    if uploaded_file is None:
        raise ValueError("Choose a school logo to upload.")

    image_bytes = uploaded_file.read(MAX_SCHOOL_LOGO_BYTES + 1)
    if not image_bytes:
        raise ValueError("Choose a school logo to upload.")
    if len(image_bytes) > MAX_SCHOOL_LOGO_BYTES:
        raise ValueError("The school logo must be 5 MB or smaller.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image_format = source.format
            width, height = source.size
            if image_format not in {"JPEG", "PNG"}:
                raise ValueError("Upload a JPG or PNG school logo.")
            if width * height > MAX_PASSPORT_PHOTO_PIXELS:
                raise ValueError("The school logo dimensions are too large.")
            source.verify()

        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            image = image.convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The uploaded file is not a valid JPG or PNG image.") from error

    logo_directory = os.path.join(app.static_folder, "uploads")
    os.makedirs(logo_directory, exist_ok=True)
    logo_path = os.path.join(logo_directory, "school_logo.png")
    temporary_path = f"{logo_path}.tmp"
    try:
        image.save(temporary_path, format="PNG")
        os.replace(temporary_path, logo_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)

    return app.config["REPORT_LOGO_PATH"]


def send_sms(phone: str, message: str) -> bool:
    """Send an SMS through Twilio using credentials supplied via environment."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not account_sid or not auth_token or not from_number:
        current_app.logger.error(
            "SMS not sent: Twilio environment configuration is incomplete."
        )
        return False

    normalized_phone = format_ghana_phone(phone)
    if not normalized_phone:
        current_app.logger.error("SMS not sent: destination phone number is invalid.")
        return False

    endpoint = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{account_sid}/Messages.json"
    )
    payload = urlencode({
        "To": f"+{normalized_phone}",
        "From": from_number,
        "Body": message
    }).encode("utf-8")
    credentials = base64.b64encode(
        f"{account_sid}:{auth_token}".encode("utf-8")
    ).decode("ascii")
    sms_request = Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    try:
        with urlopen(sms_request, timeout=10) as response:
            if response.status not in {200, 201}:
                current_app.logger.error(
                    "Twilio returned an unexpected status when sending SMS: %s",
                    response.status
                )
                return False
    except HTTPError as error:
        current_app.logger.error(
            "Twilio rejected an SMS request with HTTP status %s.",
            error.code
        )
        return False
    except (HTTPException, URLError, TimeoutError, OSError) as error:
        current_app.logger.error("Twilio SMS delivery request failed: %s", error)
        return False

    return True


def _notify_parent(student, message: str) -> tuple[bool, str]:
    if not student.parent_phone:
        return False, "no_parent_phone"
    if not format_ghana_phone(student.parent_phone):
        return False, "invalid_parent_phone"
    if not send_sms(student.parent_phone, message):
        return False, "delivery_failed"
    return True, "sent"


def get_gpa(scores):
    if not scores:
        return 0

    total = 0
    for s in scores:
        if s >= 80:
            total += 4
        elif s >= 70:
            total += 3
        elif s >= 60:
            total += 2
        elif s >= 50:
            total += 1

    return round(total / len(scores), 2)


# =========================
# LOGIN REQUIRED 🔐
# =========================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):

        # ✅ Check full session integrity
        if not session.get("user_id") or not session.get("role"):
            flash("Login required ❌")
            return redirect(url_for("login"))

        # ✅ Optional: verify user still exists in DB
        user = User.query.get(session.get("user_id"))
        if not user:
            session.clear()
            flash("Session expired. Please login again.")
            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return wrapper

# =========================
# ROLE REQUIRED 🔐
# =========================
def role_required(*roles):
    def wrapper(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'role' not in session:
                flash("Access denied. Please login.")
                return redirect(url_for('login'))

            user_role = session.get('role', '').strip().lower()

            if user_role not in [r.lower() for r in roles]:
                flash("Access denied: Unauthorized role")
                return redirect(url_for('login'))

            return f(*args, **kwargs)
        return decorated_function
    return wrapper

def set_tenant():
    """
    Automatically attach school_id to every request
    """
    g.school_id = session.get('school_id')


def tenant_query(model):
    """
    Auto-filter all queries by school_id
    """
    return model.query.filter_by(school_id=g.school_id)


def tenant_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'school_id' not in session:
            return "No school assigned", 403
        return f(*args, **kwargs)
    return wrapper

# =========================
# DATABASE MODELS (FIXED)
# =========================

# ==================================================
# MANY-TO-MANY: TEACHER ↔ CLASS
# ==================================================
teacher_class = db.Table(
    'teacher_class',
    db.Column('teacher_id', db.Integer, db.ForeignKey('users.id')),
    db.Column('class_id', db.Integer, db.ForeignKey('classes.id'))
)

# ==================================================
# 🏫 SCHOOL
# ==================================================
class School(db.Model):
    __tablename__ = 'schools'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150))
    address = db.Column(db.String(200))
    
    subscription_status = db.Column(db.String(50), default="active")
    plan = db.Column(db.String(50), default="basic")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship('User', back_populates='school', cascade="all, delete")
    students = db.relationship('Student', back_populates='school', cascade="all, delete")
    classes = db.relationship('SchoolClass', back_populates='school', cascade="all, delete")


class AcademicPeriodSetting(db.Model):
    __tablename__ = 'academic_period_settings'
    __table_args__ = (
        db.UniqueConstraint(
            'school_id',
            name='uq_academic_period_settings_school'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey('schools.id'))
    term = db.Column(db.String(50), nullable=False)
    academic_year = db.Column(db.String(20), nullable=False)


# ==================================================
# 👤 USER (ADMIN / TEACHER / ACCOUNTANT)
# ==================================================
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(
        db.String(100),
        unique=True,
        nullable=False
    )

    password = db.Column(
        db.String(255),
        nullable=False
    )

    email = db.Column(
        db.String(254),
        nullable=True
    )

    role = db.Column(
        db.String(50),
        nullable=False
    )

    school_id = db.Column(
        db.Integer,
        db.ForeignKey('schools.id')
    )

    school = db.relationship(
        'School',
        back_populates='users'
    )

    reset_token = db.Column(
        db.String(200),
        nullable=True
    )

    reset_token_expires_at = db.Column(
        db.DateTime,
        nullable=True
    )

    must_change_password = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    classes = db.relationship(
        'SchoolClass',
        secondary=teacher_class,
        back_populates='teachers'
    )


db.Index("uq_users_email", User.email, unique=True)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.before_request
def enforce_temporary_password_change():
    if (
        current_user.is_authenticated
        and current_user.must_change_password
        and request.endpoint not in {'change_password', 'logout', 'static'}
    ):
        flash("Change your temporary password before continuing.", "warning")
        return redirect(url_for('change_password'))


# ==================================================
# 🏫 CLASS
# ==================================================
class SchoolClass(db.Model):
    __tablename__ = 'classes'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    school_id = db.Column(db.Integer, db.ForeignKey('schools.id'))

    school = db.relationship('School', back_populates='classes')

    students = db.relationship(
        'Student',
        back_populates='classroom',
        cascade="all, delete-orphan"
    )

    teachers = db.relationship(
        'User',
        secondary=teacher_class,
        back_populates='classes'
    )


# ==================================================
# 👨‍🎓 STUDENT
# ==================================================
class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)

    admission_no = db.Column(db.String(30), unique=True)

    name = db.Column(db.String(100), nullable=False)

    gender = db.Column(db.String(10))

    dob = db.Column(db.Date)

    photo = db.Column(db.String(255))

    phone = db.Column(db.String(20))

    parent_phone = db.Column(db.String(20))

    class_id = db.Column(
        db.Integer,
        db.ForeignKey('classes.id'),
        nullable=False
    )

    school_id = db.Column(
        db.Integer,
        db.ForeignKey('schools.id')
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    fee_amount = db.Column(
        db.Float,
        default=0
    )

    classroom = db.relationship(
        'SchoolClass',
        back_populates='students'
    )

    school = db.relationship(
        'School',
        back_populates='students'
    )

    invoices = db.relationship(
        'Invoice',
        back_populates='student',
        cascade='all, delete-orphan'
    )

    payments = db.relationship(
        'Payment',
        back_populates='student',
        cascade='all, delete-orphan'
    )

    performances = db.relationship(
        'Performance',
        back_populates='student',
        cascade='all, delete-orphan'
    )

    attendances = db.relationship(
        'Attendance',
        back_populates='student',
        cascade='all, delete-orphan'
    )

    report_cards = db.relationship(
        'ReportCard',
        back_populates='student',
        cascade='all, delete-orphan'
    )


class AdmissionApplication(db.Model):
    __tablename__ = 'admission_applications'

    id = db.Column(db.Integer, primary_key=True)
    admission_no = db.Column(db.String(30), unique=True, nullable=False)
    school_id = db.Column(
        db.Integer,
        db.ForeignKey('schools.id'),
        nullable=False
    )
    class_id = db.Column(
        db.Integer,
        db.ForeignKey('classes.id'),
        nullable=False
    )
    name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    dob = db.Column(db.Date)
    passport_photo = db.Column(db.String(255))
    phone = db.Column(db.String(20))
    parent_phone = db.Column(db.String(20))
    status = db.Column(db.String(20), nullable=False, default='Pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'))

    school = db.relationship('School')
    classroom = db.relationship('SchoolClass')
    student = db.relationship('Student')


# ==================================================
# 📅 ATTENDANCE
# ==================================================
class Attendance(db.Model):
    __tablename__ = 'attendance'

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(
        db.Integer,
        db.ForeignKey('students.id'),
        nullable=False
    )

    status = db.Column(db.String(10), nullable=False)

    date = db.Column(db.Date)

    term = db.Column(db.String(30))

    academic_year = db.Column(db.String(20))

    school_id = db.Column(
        db.Integer,
        db.ForeignKey('schools.id')
    )

    student = db.relationship(
        'Student',
        back_populates='attendances'
    )

# ==================================================
# 📊 PERFORMANCE
# ==================================================

class Performance(db.Model):
    __tablename__ = 'performances'

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(
        db.Integer,
        db.ForeignKey('students.id'),
        nullable=False
    )

    subject = db.Column(
        db.String(100),
        nullable=False
    )

    # 30%
    class_score = db.Column(
        db.Float,
        default=0
    )

    # 70%
    exam_score = db.Column(
        db.Float,
        default=0
    )

    # 100%
    total_score = db.Column(
        db.Float,
        default=0
    )

    term = db.Column(
        db.String(20)
    )

    academic_year = db.Column(
        db.String(20)
    )

    date = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    school_id = db.Column(
        db.Integer,
        db.ForeignKey('schools.id')
    )

    # Relationship
    student = db.relationship(
        'Student',
        back_populates='performances'
    )

    # ==========================
    # GRADE
    # ==========================
    @property
    def grade(self):
        return _grade_and_remark(
            float(self.total_score or 0)
        )[0]

    # ==========================
    # REMARK
    # ==========================
    @property
    def remark(self):
        return _grade_and_remark(
            float(self.total_score or 0)
        )[1]

    # ==========================
    # SUBJECT POSITION
    # ==========================
    @staticmethod
    def subject_position(subject, student_id):

        records = Performance.query.filter_by(
            subject=subject
        ).order_by(
            Performance.total_score.desc()
        ).all()

        for pos, record in enumerate(records, start=1):

            if record.student_id == student_id:
                return pos

        return "-"

    # ==========================
    # CLASS POSITION
    # ==========================
    @staticmethod
    def class_position(student):

        classmates = Student.query.filter_by(
            class_id=student.class_id
        ).all()

        ranking = []

        for s in classmates:

            results = Performance.query.filter_by(
                student_id=s.id
            ).all()

            avg = 0

            if results:
                avg = sum(
                    r.total_score for r in results
                ) / len(results)

            ranking.append({
                "student_id": s.id,
                "avg": avg
            })

        ranking.sort(
            key=lambda x: x["avg"],
            reverse=True
        )

        for pos, item in enumerate(
            ranking,
            start=1
        ):

            if item["student_id"] == student.id:
                return pos

        return "-"



# ==================================================
# 🧾 INVOICE (FINANCE CORE)
# ==================================================
class Invoice(db.Model):
    __tablename__ = 'invoices'

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)

    payments = db.relationship(
        'Payment',
        back_populates='invoice',
        cascade='all, delete-orphan'
    )

    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='Pending')
    partial_paid = db.Column(db.Float, default=0)
    term = db.Column(db.String(50))
    academic_year = db.Column(db.String(20))
    tuition_fee = db.Column(db.Float, nullable=False, default=0)
    sports_fee = db.Column(db.Float, nullable=False, default=0)
    library_fee = db.Column(db.Float, nullable=False, default=0)
    ict_fee = db.Column(db.Float, nullable=False, default=0)

    date_created = db.Column(db.DateTime, default=datetime.utcnow)

    school_id = db.Column(db.Integer, db.ForeignKey('schools.id'))

    student = db.relationship('Student', back_populates='invoices')


# ==================================================
# 💸 EXPENSE
# ==================================================
class Expense(db.Model):
    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(100))
    amount = db.Column(db.Float)
    category = db.Column(db.String(50))

    date_created = db.Column(db.DateTime, default=datetime.utcnow)

    school_id = db.Column(db.Integer, db.ForeignKey('schools.id'))


class OtherIncome(db.Model):
    __tablename__ = 'other_income'

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    received_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    payment_method = db.Column(db.String(50), nullable=False, default='Cash')
    source = db.Column(db.String(150))
    reference = db.Column(db.String(100))
    remarks = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    school_id = db.Column(db.Integer, db.ForeignKey('schools.id'), nullable=False)


# ==================================================
# 💳 PAYMENT
# ==================================================
class Payment(db.Model):
    __tablename__ = 'payments'

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(
        db.Integer,
        db.ForeignKey('students.id', ondelete='CASCADE'),
        nullable=False
    )

    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey('invoices.id', ondelete='CASCADE'),
        nullable=False
    )

    amount_paid = db.Column(
        db.Numeric(10, 2),
        nullable=False
    )

    method = db.Column(
        db.String(50),
        default='Cash'
    )

    reference = db.Column(
        db.String(100),
        nullable=True
    )

    payment_date = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    remarks = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    student = db.relationship(
        'Student',
        back_populates='payments'
    )

    invoice = db.relationship(
        'Invoice',
        back_populates='payments'
    )
class FeeStructure(db.Model):
    __tablename__ = 'fee_structure'

    id = db.Column(db.Integer, primary_key=True)

    class_id = db.Column(
        db.Integer,
        db.ForeignKey('classes.id'),
        nullable=False
    )

    amount = db.Column(
        db.Float,
        default=0
    )

    classroom = db.relationship(
        'SchoolClass',
        backref='fee_structure'
    )


class TermFeeSchedule(db.Model):
    __tablename__ = 'term_fee_schedules'
    __table_args__ = (
        db.UniqueConstraint(
            'class_id',
            'term',
            'academic_year',
            name='uq_term_fee_schedule_class_term_year'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(
        db.Integer,
        db.ForeignKey('classes.id'),
        nullable=False
    )
    term = db.Column(db.String(50), nullable=False)
    academic_year = db.Column(db.String(20), nullable=False)
    tuition_fee = db.Column(db.Float, nullable=False, default=0)
    sports_fee = db.Column(db.Float, nullable=False, default=0)
    library_fee = db.Column(db.Float, nullable=False, default=0)
    ict_fee = db.Column(db.Float, nullable=False, default=0)

    classroom = db.relationship('SchoolClass')


class ReportCard(db.Model):
    __tablename__ = "report_cards"

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id"),
        nullable=False
    )

    term = db.Column(db.String(50), nullable=False)
    academic_year = db.Column(db.String(20), nullable=False)

    conduct = db.Column(db.String(100))
    attitude = db.Column(db.String(100))
    interest = db.Column(db.String(100))

    attendance = db.Column(db.Integer)

    class_teacher_remark = db.Column(db.Text)

    headteacher_remark = db.Column(db.Text)

    promoted_to = db.Column(db.String(100))

    next_term_begins = db.Column(db.String(100))

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    student = db.relationship(
        "Student",
        back_populates="report_cards"
    )

# =========================
# HOME
# =========================
@app.route('/')
def index():
    return render_template('index.html')


def _school_admission_prefix(school_name: str) -> str:
    initials = ''.join(
        part[0]
        for part in re.findall(r'[A-Za-z0-9]+', school_name or '')
    ).upper()[:10]
    return initials or 'SCH'


@app.route('/admission-portal', methods=['GET', 'POST'])
def admission_portal():
    classes = SchoolClass.query.join(School).order_by(
        School.name.asc(),
        SchoolClass.name.asc()
    ).all()

    if request.method == 'POST':
        class_id = request.form.get('class_id', type=int)
        classroom = SchoolClass.query.get(class_id) if class_id else None
        name = (request.form.get('name') or '').strip()
        gender = (request.form.get('gender') or '').strip().title()
        dob_value = (request.form.get('dob') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        parent_phone = (request.form.get('parent_phone') or '').strip()

        if (
            not name
            or len(name) > 100
            or gender not in {'Male', 'Female'}
            or classroom is None
        ):
            flash("Enter the student's name, gender, and a valid class.", "warning")
            return render_template(
                'admission_portal.html',
                classes=classes,
                form=request.form
            )

        dob = None
        if dob_value:
            try:
                dob = datetime.strptime(dob_value, '%Y-%m-%d').date()
            except ValueError:
                flash("Enter a valid date of birth.", "warning")
                return render_template(
                    'admission_portal.html',
                    classes=classes,
                    form=request.form
                )
            if dob > date.today():
                flash("Date of birth cannot be in the future.", "warning")
                return render_template(
                    'admission_portal.html',
                    classes=classes,
                    form=request.form
                )

        formatted_phone = format_ghana_phone(phone)
        formatted_parent_phone = format_ghana_phone(parent_phone)
        if phone and not formatted_phone:
            flash("Enter a valid Ghana student phone number.", "warning")
            return render_template(
                'admission_portal.html',
                classes=classes,
                form=request.form
            )
        if parent_phone and not formatted_parent_phone:
            flash("Enter a valid Ghana parent/guardian phone number.", "warning")
            return render_template(
                'admission_portal.html',
                classes=classes,
                form=request.form
            )

        uploaded_file = request.files.get('passport_photo')
        if uploaded_file is None or not uploaded_file.filename:
            flash("Upload the learner's passport photo to submit the application.", "warning")
            return render_template(
                'admission_portal.html',
                classes=classes,
                form=request.form
            )

        try:
            passport_photo = _save_student_passport_photo(
                uploaded_file.stream
            )
        except ValueError as error:
            flash(str(error), "warning")
            return render_template(
                'admission_portal.html',
                classes=classes,
                form=request.form
            )
        except OSError:
            app.logger.exception(
                "Failed to save the passport photo for an admission application."
            )
            flash("The passport photo could not be saved. Please try again.", "danger")
            return render_template(
                'admission_portal.html',
                classes=classes,
                form=request.form
            )

        application = AdmissionApplication(
            admission_no=f"TMP{uuid.uuid4().hex[:27]}",
            school_id=classroom.school_id,
            class_id=classroom.id,
            name=name,
            gender=gender,
            dob=dob,
            passport_photo=passport_photo,
            phone=formatted_phone,
            parent_phone=formatted_parent_phone
        )
        try:
            db.session.add(application)
            db.session.flush()

            prefix = _school_admission_prefix(classroom.school.name)
            sequence = application.id
            while True:
                admission_no = f"{prefix}-{sequence:05d}"
                existing_student = Student.query.filter_by(
                    admission_no=admission_no
                ).first()
                existing_application = AdmissionApplication.query.filter(
                    AdmissionApplication.admission_no == admission_no,
                    AdmissionApplication.id != application.id
                ).first()
                if existing_student is None and existing_application is None:
                    break
                sequence += 1
            application.admission_no = admission_no
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Failed to save an admission application.")
            photo_path = os.path.join(
                app.static_folder,
                *passport_photo.split("/")
            )
            try:
                os.remove(photo_path)
            except FileNotFoundError:
                pass
            except OSError:
                app.logger.exception(
                    "Failed to remove an orphaned admission passport photo."
                )
            flash("The admission application could not be submitted. Please try again.", "danger")
            return render_template(
                'admission_portal.html',
                classes=classes,
                form=request.form
            )

        return redirect(
            url_for(
                'admission_submitted',
                admission_no=application.admission_no
            )
        )

    return render_template(
        'admission_portal.html',
        classes=classes,
        form={}
    )


@app.route('/admission-submitted/<admission_no>')
def admission_submitted(admission_no):
    application = AdmissionApplication.query.filter_by(
        admission_no=admission_no
    ).first_or_404()
    return render_template(
        'admission_submitted.html',
        application=application
    )


@app.route('/admissions')
@login_required
@role_required('admin')
def admissions():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    applications = AdmissionApplication.query.filter_by(
        school_id=current_user.school_id
    ).order_by(
        AdmissionApplication.created_at.desc(),
        AdmissionApplication.id.desc()
    ).all()
    return render_template(
        'admissions.html',
        applications=applications
    )


@app.route('/admissions/<int:application_id>/<action>', methods=['POST'])
@login_required
@role_required('admin')
def review_admission(application_id, action):
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect
    if action not in {'approve', 'reject'}:
        return redirect(url_for('admissions'))

    application = AdmissionApplication.query.filter_by(
        id=application_id,
        school_id=current_user.school_id
    ).first_or_404()
    if application.status != 'Pending':
        flash("This admission application has already been reviewed.", "info")
        return redirect(url_for('admissions'))

    if action == 'approve':
        classroom = SchoolClass.query.filter_by(
            id=application.class_id,
            school_id=current_user.school_id
        ).first()
        if classroom is None:
            flash(
                "The selected class is no longer available; application was not approved.",
                "warning"
            )
            return redirect(url_for('admissions'))

        student = Student(
            admission_no=application.admission_no,
            name=application.name,
            gender=application.gender,
            dob=application.dob,
            photo=application.passport_photo,
            phone=application.phone,
            parent_phone=application.parent_phone,
            class_id=classroom.id,
            school_id=current_user.school_id
        )
        db.session.add(student)
        application.status = 'Approved'
        application.reviewed_at = datetime.utcnow()
        db.session.flush()
        application.student_id = student.id
        db.session.commit()

        flash(
            f"Admission approved. Student ID: {student.admission_no}.",
            "success"
        )
        if student.parent_phone:
            sent, reason = _notify_parent(
                student,
                f"{student.school.name}: {student.name} has been admitted. "
                f"Student ID: {student.admission_no}."
            )
            if not sent:
                flash(
                    "Admission was approved, but the parent/guardian SMS "
                    f"could not be sent ({reason.replace('_', ' ')}).",
                    "warning"
                )
    else:
        application.status = 'Rejected'
        application.reviewed_at = datetime.utcnow()
        db.session.commit()
        flash("Admission application rejected.", "success")

    return redirect(url_for('admissions'))


# ================= AUTO CREATE FIRST ADMIN =================

from werkzeug.security import generate_password_hash


def _ensure_invoice_schema():
    """Add invoice columns introduced after existing databases were created."""
    invoice_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("invoices")
    }
    missing_columns = {
        "term": "VARCHAR(50) NULL",
        "academic_year": "VARCHAR(20) NULL",
        "tuition_fee": "FLOAT NOT NULL DEFAULT 0",
        "sports_fee": "FLOAT NOT NULL DEFAULT 0",
        "library_fee": "FLOAT NOT NULL DEFAULT 0",
        "ict_fee": "FLOAT NOT NULL DEFAULT 0",
    }

    with db.engine.begin() as connection:
        for column_name, definition in missing_columns.items():
            if column_name not in invoice_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE invoices ADD COLUMN "
                        f"{column_name} {definition}"
                    )
                )


def _ensure_student_parent_phone_schema():
    """Add the optional guardian contact field to existing student tables."""
    student_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("students")
    }
    if "parent_phone" not in student_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE students "
                    "ADD COLUMN parent_phone VARCHAR(20) NULL"
                )
            )


def _ensure_admission_application_photo_schema():
    """Add the uploaded passport photo path to existing admission applications."""
    application_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("admission_applications")
    }
    if "passport_photo" not in application_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE admission_applications "
                    "ADD COLUMN passport_photo VARCHAR(255) NULL"
                )
            )


def _ensure_user_recovery_schema():
    """Add recovery and temporary-password fields to existing user tables."""
    user_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("users")
    }

    with db.engine.begin() as connection:
        if "email" not in user_columns:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN email VARCHAR(254) NULL")
            )
        if "reset_token_expires_at" not in user_columns:
            date_type = db.DateTime().compile(dialect=db.engine.dialect)
            connection.execute(
                text(
                    "ALTER TABLE users ADD COLUMN "
                    f"reset_token_expires_at {date_type} NULL"
                )
            )
        if "must_change_password" not in user_columns:
            boolean_type = db.Boolean().compile(dialect=db.engine.dialect)
            connection.execute(
                text(
                    "ALTER TABLE users ADD COLUMN "
                    f"must_change_password {boolean_type} "
                    "NOT NULL DEFAULT FALSE"
                )
            )

        indexes = {
            index["name"]
            for index in inspect(connection).get_indexes("users")
        }
        if "uq_users_email" not in indexes:
            connection.execute(
                text("CREATE UNIQUE INDEX uq_users_email ON users (email)")
            )


def _ensure_super_admin_account():
    """Create the initial global Super Admin from environment or development defaults."""
    if User.query.filter_by(role='super_admin').first():
        return

    username = (os.environ.get('SUPER_ADMIN_USERNAME') or '').strip()
    password = os.environ.get('SUPER_ADMIN_PASSWORD') or ''
    configured_email = os.environ.get('SUPER_ADMIN_EMAIL')
    email = _normalize_email(configured_email) if configured_email else None

    if not username or not password:
        if app.config["APP_ENV"] == "production":
            raise RuntimeError(
                "SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD, and "
                "SUPER_ADMIN_EMAIL must be configured until the first "
                "Super Admin account is created."
            )

        base_username = f"superadmin_{datetime.now().year}"
        username = base_username
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f"{base_username}_{counter}"
            counter += 1

        password = ''.join(
            secrets.choice(
                string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
            )
            for _ in range(20)
        )

        while not (
            any(character.islower() for character in password)
            and any(character.isupper() for character in password)
            and any(character.isdigit() for character in password)
            and any(character in "!@#$%^&*()-_=+" for character in password)
        ):
            password = ''.join(
                secrets.choice(
                    string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
                )
                for _ in range(20)
            )

        generated_credentials = True
    else:
        generated_credentials = False

    if configured_email and not email:
        raise RuntimeError("SUPER_ADMIN_EMAIL must be a valid email address.")
    if app.config["APP_ENV"] == "production" and not email:
        raise RuntimeError(
            "SUPER_ADMIN_EMAIL must be configured until the first "
            "Super Admin account is created."
        )
    if len(username) > 100 or len(password) < 12:
        raise RuntimeError(
            "Super Admin credentials are invalid: username must be at most "
            "100 characters and password must be at least 12 characters."
        )
    if User.query.filter_by(username=username).first():
        raise RuntimeError(
            "Super Admin was not created: configured username is already in use."
        )
    if _email_in_use(email):
        raise RuntimeError(
            "Super Admin was not created: configured email is already in use."
        )

    super_admin = User(
        email=email,
        username=username,
        password=generate_password_hash(password),
        role='super_admin',
        school_id=None
    )
    try:
        db.session.add(super_admin)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create the initial Super Admin.")
        raise

    if generated_credentials:
        credentials_file = os.path.join(
            app.root_path,
            "super_admin_credentials.txt"
        )
        try:
            with open(credentials_file, "w", encoding="utf-8") as file:
                file.write(
                    "SUPER ADMIN LOGIN CREDENTIALS\n"
                    f"{'=' * 35}\n"
                    f"Username: {username}\n"
                    f"Password: {password}\n"
                    "Role: Super Admin\n"
                    f"Created: {datetime.now().isoformat(timespec='seconds')}\n"
                    "\nKeep these credentials private and secure.\n"
                )
        except OSError:
            current_app.logger.exception(
                "Super Admin was created, but the credentials file could not be saved."
            )

    current_app.logger.info("Initial Super Admin account created.")


with app.app_context():

    db.create_all()
    _ensure_invoice_schema()
    _ensure_student_parent_phone_schema()
    _ensure_admission_application_photo_schema()
    _ensure_user_recovery_schema()

    schools = School.query.order_by(School.id.asc()).all()
    if not schools:
        default_school = School(
            name=app.config.get("REPORT_SCHOOL_NAME") or "My School",
            address=app.config.get("REPORT_SCHOOL_ADDRESS")
        )
        db.session.add(default_school)
        db.session.commit()
        schools = [default_school]

    _ensure_super_admin_account()

    # Backfill legacy tenant ownership only when there is one unambiguous
    # school, so existing records are not assigned across multiple schools.
    if len(schools) == 1:
        school_id = schools[0].id
        User.query.filter(
            User.school_id.is_(None),
            User.role != 'super_admin'
        ).update(
            {User.school_id: school_id},
            synchronize_session=False
        )
        SchoolClass.query.filter(
            SchoolClass.school_id.is_(None)
        ).update(
            {SchoolClass.school_id: school_id},
            synchronize_session=False
        )
        Student.query.filter(
            Student.school_id.is_(None)
        ).update(
            {Student.school_id: school_id},
            synchronize_session=False
        )
        Invoice.query.filter(
            Invoice.school_id.is_(None)
        ).update(
            {Invoice.school_id: school_id},
            synchronize_session=False
        )
        Expense.query.filter(
            Expense.school_id.is_(None)
        ).update(
            {Expense.school_id: school_id},
            synchronize_session=False
        )
        OtherIncome.query.filter(
            OtherIncome.school_id.is_(None)
        ).update(
            {OtherIncome.school_id: school_id},
            synchronize_session=False
        )
        db.session.commit()


def _require_current_school():
    if current_user.school_id is None:
        if current_user.role != 'admin':
            flash(
                "Your school has not been assigned yet. Please ask an administrator to assign it.",
                "warning"
            )
            return redirect(url_for('login'))
        flash(
            "Assign this admin account to a school before managing school records.",
            "warning"
        )
        return redirect(url_for('assign_school'))
    return None


@app.route('/assign-school', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def assign_school():
    if current_user.school_id is not None:
        return redirect(url_for('finance'))

    schools = School.query.order_by(School.name.asc()).all()
    if request.method == 'POST':
        school_id = request.form.get('school_id', type=int)
        school = School.query.get(school_id) if school_id else None
        if school is None:
            flash("Select a valid school.", "warning")
            return redirect(url_for('assign_school'))

        current_user.school_id = school.id
        db.session.commit()
        session['school_id'] = school.id
        flash(f"Your account is now assigned to {school.name}.", "success")
        return redirect(url_for('finance'))

    return render_template('assign_school.html', schools=schools)


@app.route('/admin_profile', methods=['GET', 'POST'])
@login_required
def admin_profile():

    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])

    if request.method == 'POST':

        username = request.form.get('username')

        user.username = username

        db.session.commit()

        session['user'] = username

        flash("Profile updated successfully ✅")

        return redirect(url_for('admin_profile'))

    return render_template(
        'admin_profile.html',
        user=user
    )

@app.route('/register_admin', methods=['GET', 'POST'])
@login_required
def register_admin():

    if session.get('role') != 'super_admin':
        return redirect(url_for('login'))

    schools = School.query.order_by(School.name.asc()).all()

    if request.method == 'POST':

        school_id = request.form.get('school_id', type=int)
        school = db.session.get(School, school_id) if school_id else None
        username = (request.form.get('username') or '').strip()
        email = _normalize_email(request.form.get('email'))
        password = request.form.get('password') or ''

        if school is None:
            flash("Select a valid school.", "danger")
            return render_template('register_admin.html', schools=schools, form=request.form)

        if not username or len(username) > 100:
            flash("Enter an admin username up to 100 characters.", "danger")
            return render_template('register_admin.html', schools=schools, form=request.form)

        if not email:
            flash("Enter a valid admin email address.", "danger")
            return render_template('register_admin.html', schools=schools, form=request.form)

        if len(password) < 8:
            flash("Admin password must be at least 8 characters.", "danger")
            return render_template('register_admin.html', schools=schools, form=request.form)

        if _email_in_use(email):
            flash("That email address is already in use.", "danger")
            return render_template('register_admin.html', schools=schools, form=request.form)

        existing = User.query.filter_by(
            username=username
        ).first()

        if existing:
            flash("That username is already in use. Choose another.", "danger")
            return render_template('register_admin.html', schools=schools, form=request.form)

        admin = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role='admin',
            school_id=school.id
        )

        try:
            db.session.add(admin)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Failed to create a school admin.")
            flash("The school admin account could not be created.", "danger")
            return render_template('register_admin.html', schools=schools, form=request.form)

        flash(f"Admin account created for {school.name}.", "success")

        return redirect(url_for('saas_dashboard'))

    return render_template('register_admin.html', schools=schools, form={})

@app.route('/register_teacher', methods=['GET', 'POST'])
@login_required
def register_teacher():

    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    classes = SchoolClass.query.filter_by(
        school_id=current_user.school_id
    ).order_by(SchoolClass.name.asc()).all()

    if request.method == 'POST':

        username = (request.form.get('username') or '').strip()
        email = _normalize_email(request.form.get('email'))
        password = request.form.get('password') or ''
        class_id_value = request.form.get('class_id', '').strip()
        class_id = request.form.get('class_id', type=int)
        classroom = SchoolClass.query.filter_by(
            id=class_id,
            school_id=current_user.school_id
        ).first() if class_id else None

        if class_id_value and (class_id is None or not classroom):
            flash("Please select a valid class ❌")
            return redirect(url_for('register_teacher'))

        if not email:
            flash("Enter a valid teacher email address.", "danger")
            return redirect(url_for('register_teacher'))

        if _email_in_use(email):
            flash("That email address is already in use.", "danger")
            return redirect(url_for('register_teacher'))

        # CHECK USERNAME
        existing = User.query.filter_by(
            username=username
        ).first()

        if existing:
            flash("Username already exists ❌")
            return redirect(url_for('register_teacher'))

        # CREATE TEACHER
        teacher = User(
            email=email,
            username=username,
            password=generate_password_hash(password),
            role='teacher',
            school_id=current_user.school_id
        )

        db.session.add(teacher)
        if classroom:
            teacher.classes.append(classroom)
        db.session.commit()

        flash("Teacher registered successfully ✅")

        return redirect(url_for('manage_staff'))

    return render_template('register_teacher.html', classes=classes)


@app.route('/register_accountant', methods=['GET', 'POST'])
@login_required
def register_accountant():

    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    if request.method == 'POST':

        username = (request.form.get('username') or '').strip()
        email = _normalize_email(request.form.get('email'))
        password = request.form.get('password') or ''

        if not email:
            flash("Enter a valid accountant email address.", "danger")
            return redirect(url_for('register_accountant'))

        if _email_in_use(email):
            flash("That email address is already in use.", "danger")
            return redirect(url_for('register_accountant'))

        # CHECK USERNAME
        existing = User.query.filter_by(
            username=username
        ).first()

        if existing:
            flash("Username already exists ❌")
            return redirect(url_for('register_accountant'))

        # CREATE ACCOUNTANT
        accountant = User(
            email=email,
            username=username,
            password=generate_password_hash(password),
            role='accountant',
            school_id=current_user.school_id
        )

        db.session.add(accountant)
        db.session.commit()

        flash("Accountant registered successfully ✅")

        return redirect(url_for('manage_staff'))

    return render_template('register_accountant.html')


@app.route('/register_headmaster', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def register_headmaster():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = _normalize_email(request.form.get('email'))
        password = request.form.get('password') or ''

        if not username or len(username) > 100:
            flash("Enter a username of at most 100 characters.", "danger")
            return redirect(url_for('register_headmaster'))
        if not email:
            flash("Enter a valid headmaster email address.", "danger")
            return redirect(url_for('register_headmaster'))
        if len(password) < 12:
            flash("Use a password with at least 12 characters.", "danger")
            return redirect(url_for('register_headmaster'))
        if User.query.filter_by(username=username).first():
            flash("That username is already in use.", "danger")
            return redirect(url_for('register_headmaster'))
        if _email_in_use(email):
            flash("That email address is already in use.", "danger")
            return redirect(url_for('register_headmaster'))

        headmaster = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role='headmaster',
            school_id=current_user.school_id,
            must_change_password=True
        )
        try:
            db.session.add(headmaster)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Failed to create a headmaster account.")
            flash("The headmaster account could not be created.", "danger")
            return redirect(url_for('register_headmaster'))

        flash(
            "Headmaster account created. The user must change the password "
            "at first sign-in.",
            "success"
        )
        return redirect(url_for('manage_staff'))

    return render_template('register_headmaster.html')


@app.route('/manage_users')
@login_required
def manage_users():

    role = (current_user.role or '').strip().lower()
    if role not in {'admin', 'super_admin'}:
        return redirect(url_for('login'))

    users_query = User.query
    if role == 'admin':
        school_redirect = _require_current_school()
        if school_redirect:
            return school_redirect
        users_query = users_query.filter_by(school_id=current_user.school_id)

    return render_template(
        'manage_users.html',
        users=users_query.order_by(User.id.desc()).all()
    )


@app.route('/manage_staff')
@login_required
@role_required('admin')
def manage_staff():

    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    teachers = User.query.filter_by(
        role='teacher',
        school_id=current_user.school_id
    ).all()
    accountants = User.query.filter_by(
        role='accountant',
        school_id=current_user.school_id
    ).all()
    admins = User.query.filter_by(
        role='admin',
        school_id=current_user.school_id
    ).all()
    headmasters = User.query.filter_by(
        role='headmaster',
        school_id=current_user.school_id
    ).all()
    return render_template(
        'manage_staff.html',
        teachers=teachers,
        accountants=accountants,
        admins=admins,
        headmasters=headmasters
    )

@app.route('/delete_user/<int:user_id>')
@login_required
@role_required('admin')
def delete_user(user_id):

    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    user = User.query.filter_by(
        id=user_id,
        school_id=current_user.school_id
    ).first_or_404()

    db.session.delete(user)
    db.session.commit()

    flash("User deleted successfully")
    return redirect(url_for('manage_staff'))

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_user(user_id):

    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    user = User.query.filter_by(
        id=user_id,
        school_id=current_user.school_id
    ).first_or_404()
    classes = SchoolClass.query.filter_by(
        school_id=current_user.school_id
    ).order_by(SchoolClass.name.asc()).all()

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = _normalize_email(request.form.get('email'))
        existing_user = User.query.filter(
            User.username == username,
            User.id != user.id
        ).first()

        if not username:
            flash("Username is required.", "danger")
            return render_template('edit_user.html', user=user, classes=classes)

        if not email:
            flash("Enter a valid email address.", "danger")
            return render_template('edit_user.html', user=user, classes=classes)

        if existing_user:
            flash("That username is already in use.", "danger")
            return render_template('edit_user.html', user=user, classes=classes)

        if _email_in_use(email, exclude_user_id=user.id):
            flash("That email address is already in use.", "danger")
            return render_template('edit_user.html', user=user, classes=classes)

        user.username = username
        user.email = email

        if user.role == 'teacher':
            class_ids = set(request.form.getlist('class_ids', type=int))
            selected_classes = SchoolClass.query.filter(
                SchoolClass.id.in_(class_ids),
                SchoolClass.school_id == current_user.school_id
            ).all() if class_ids else []

            if len(selected_classes) != len(class_ids):
                flash("One or more selected classes are invalid.", "danger")
                return render_template('edit_user.html', user=user, classes=classes)

            user.classes = selected_classes

        db.session.commit()

        flash("User updated successfully")
        return redirect(url_for('manage_staff'))

    return render_template('edit_user.html', user=user, classes=classes)


def _temporary_password():
    return secrets.token_urlsafe(18)


@app.route('/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
def reset_user_password(user_id):
    actor_role = (current_user.role or '').strip().lower()
    if actor_role not in {'admin', 'super_admin'}:
        flash("You are not allowed to reset user passwords.", "danger")
        return redirect(url_for('login'))

    if actor_role == 'admin':
        school_redirect = _require_current_school()
        if school_redirect:
            return school_redirect
        target_user = User.query.filter_by(
            id=user_id,
            school_id=current_user.school_id
        ).first_or_404()
        if target_user.role not in {
            'teacher', 'accountant', 'admin', 'headmaster'
        }:
            flash("You cannot reset this account's password.", "danger")
            return redirect(url_for('manage_staff'))
        destination = 'manage_staff'
    else:
        target_user = db.session.get(User, user_id)
        if target_user is None:
            flash("User account not found.", "danger")
            return redirect(url_for('manage_users'))
        destination = 'manage_users'

    if target_user.id == current_user.id:
        flash("Use Change Password to update your own password.", "danger")
        return redirect(url_for(destination))

    temporary_password = _temporary_password()
    target_user.password = generate_password_hash(temporary_password)
    target_user.must_change_password = True
    target_user.reset_token = None
    target_user.reset_token_expires_at = None

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to reset password for user %s.", target_user.id
        )
        flash("The password could not be reset. Please try again.", "danger")
        return redirect(url_for(destination))

    if actor_role == 'admin':
        teachers = User.query.filter_by(
            role='teacher',
            school_id=current_user.school_id
        ).all()
        accountants = User.query.filter_by(
            role='accountant',
            school_id=current_user.school_id
        ).all()
        admins = User.query.filter_by(
            role='admin',
            school_id=current_user.school_id
        ).all()
        headmasters = User.query.filter_by(
            role='headmaster',
            school_id=current_user.school_id
        ).all()
        response = make_response(render_template(
            'manage_staff.html',
            teachers=teachers,
            accountants=accountants,
            admins=admins,
            headmasters=headmasters,
            temporary_username=target_user.username,
            temporary_password=temporary_password
        ))
    else:
        response = make_response(render_template(
            'manage_users.html',
            users=User.query.order_by(User.id.desc()).all(),
            temporary_username=target_user.username,
            temporary_password=temporary_password
        ))

    response.headers["Cache-Control"] = "no-store"
    return response


# =========================
# LOGIN
# =========================
@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        username = (request.form.get('username') or '').strip()
        email = _normalize_email(request.form.get('email'))
        password = request.form.get('password') or ''

        if not email:
            flash("Enter a valid email address.", "danger")
            return redirect(url_for('login'))

        user = User.query.filter_by(
            username=username
        ).first()

        # USER NOT FOUND
        if not user:

            flash(
                "User not found ❌",
                "danger"
            )

            return redirect(
                url_for('login')
            )

        # WRONG PASSWORD
        if not check_password_hash(
            user.password,
            password
        ):

            flash(
                "Wrong password ❌",
                "danger"
            )

            return redirect(
                url_for('login')
            )

        if user.email:
            if user.email.strip().lower() != email:
                flash("Email address does not match this account.", "danger")
                return redirect(url_for('login'))
        else:
            if _email_in_use(email):
                flash("That email address is already in use.", "danger")
                return redirect(url_for('login'))
            user.email = email
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception(
                    "Failed to save the email for an existing user."
                )
                flash(
                    "Your email could not be saved. Please try again.",
                    "danger"
                )
                return redirect(url_for('login'))

        # LOGIN USER
        login_user(user)

        role = user.role.strip().lower()

        # STORE SESSION
        session['user'] = user.username
        session['user_id'] = user.id
        session['role'] = role
        session['school_id'] = user.school_id

        if user.must_change_password:
            flash(
                "Your password was reset by an administrator. "
                "Choose a new password to continue.",
                "warning"
            )
            return redirect(url_for('change_password'))

        flash(
            f"Welcome {user.username} ✅",
            "success"
        )

        # =========================
        # ROLE REDIRECTS
        # =========================

        if role == 'admin':

            return redirect(
                url_for('super_dashboard')
            )

        elif role == 'accountant':

            return redirect(
                url_for('accountant_dashboard')
            )

        elif role == 'teacher':

            return redirect(
                url_for('teacher_dashboard')
            )

        elif role == 'headmaster':

            return redirect(
                url_for('headmaster_dashboard')
            )

        elif role == 'super_admin':

            return redirect(
                url_for('saas_dashboard')
            )

        else:

            flash(
                "Invalid role ❌",
                "danger"
            )

            logout_user()

            return redirect(
                url_for('login')
            )

    return render_template(
        'login.html'
    )
# =========================
# CHANGE PASSWORD
# =========================
@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():

    user = User.query.get(session['user_id'])
    forced_change = user.must_change_password

    if request.method == 'POST':

        current_password = request.form.get('current_password') or ''
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if not forced_change and not check_password_hash(
            user.password,
            current_password
        ):
            flash("Current password is incorrect ❌", "danger")
            return redirect(url_for('change_password'))

        if new_password != confirm_password:
            flash("Passwords do not match ❌", "danger")
            return redirect(url_for('change_password'))

        if len(new_password) < 12:
            flash("Use a password with at least 12 characters.", "danger")
            return redirect(url_for('change_password'))

        user.password = generate_password_hash(new_password)
        user.must_change_password = False

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to change password for user %s.", user.id
            )
            flash("The password could not be changed. Please try again.", "danger")
            return redirect(url_for('change_password'))

        flash("Password changed successfully ✅")

        dashboard_by_role = {
            'admin': 'super_dashboard',
            'accountant': 'accountant_dashboard',
            'teacher': 'teacher_dashboard',
            'super_admin': 'saas_dashboard',
        }
        return redirect(url_for(dashboard_by_role.get(user.role, 'login')))

    return render_template(
        'change_password.html',
        forced_change=forced_change
    )


@app.route('/update_recovery_email', methods=['POST'])
@login_required
def update_recovery_email():
    email = (request.form.get('email') or '').strip().lower()
    current_password = request.form.get('current_password') or ''

    if (
        len(email) > 254
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    ):
        flash("Enter a valid recovery email address.", "danger")
        return redirect(url_for('change_password'))

    if not check_password_hash(current_user.password, current_password):
        flash("Current password is incorrect.", "danger")
        return redirect(url_for('change_password'))

    existing_email = User.query.filter(
        func.lower(User.email) == email,
        User.id != current_user.id
    ).first()
    if existing_email:
        flash("That recovery email is already in use.", "danger")
        return redirect(url_for('change_password'))

    current_user.email = email
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update a user's recovery email.")
        flash("The recovery email could not be updated.", "danger")
        return redirect(url_for('change_password'))

    flash("Recovery email updated successfully.", "success")
    return redirect(url_for('change_password'))


@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():

    if request.method == 'POST':

        if not app.config.get("MAIL_DEFAULT_SENDER"):
            current_app.logger.error(
                "Password reset email is unavailable because no mail sender "
                "is configured."
            )
            flash(
                "Password reset is temporarily unavailable. "
                "Please contact the system administrator.",
                "danger"
            )
            return redirect(url_for('forgot_password'))

        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        user = User.query.filter_by(username=username).first()

        if user and user.email and user.email.lower() == email:
            token = secrets.token_urlsafe(32)
            user.reset_token = hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest()
            user.reset_token_expires_at = datetime.utcnow() + timedelta(
                minutes=30
            )
            db.session.commit()

            reset_link = url_for('reset_password', token=token, _external=True)

            msg = Message(
                "Password Reset Request",
                sender=app.config["MAIL_DEFAULT_SENDER"],
                recipients=[email]
            )

            msg.body = (
                "We received a request to reset your password. "
                "Use the link below within 30 minutes:\n\n"
                f"{reset_link}\n\n"
                "If you did not request this, you can ignore this email."
            )
            try:
                mail.send(msg)
            except (OSError, smtplib.SMTPException):
                current_app.logger.exception(
                    "Failed to send a password reset email."
                )
                user.reset_token = None
                user.reset_token_expires_at = None
                db.session.commit()

        flash(
            "If the username and recovery email match, a reset link "
            "will be sent.",
            "info"
        )
        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')


@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user = User.query.filter_by(reset_token=token_hash).first()

    if (
        not user
        or not user.reset_token_expires_at
        or user.reset_token_expires_at <= datetime.utcnow()
    ):
        flash("This password reset link is invalid or has expired.", "danger")
        return redirect(url_for('login'))

    if request.method == 'POST':

        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if len(new_password) < 12:
            flash("Use a password with at least 12 characters.", "danger")
            return render_template('reset_password.html', token=token)

        if new_password != confirm_password:
            flash("The passwords do not match.", "danger")
            return render_template('reset_password.html', token=token)

        user.password = generate_password_hash(new_password)
        user.reset_token = None
        user.reset_token_expires_at = None

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Failed to reset a user's password.")
            flash("The password could not be reset. Please try again.", "danger")
            return render_template('reset_password.html', token=token)

        flash("Password reset successfully. You can now sign in.", "success")
        return redirect(url_for('login'))

    return render_template('reset_password.html')

# =========================
# LOGOUT
# =========================
@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out")
    return redirect(url_for('login'))


@app.route('/register_school', methods=['GET', 'POST'])
@login_required
def register_school():
    if session.get('role') != 'super_admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        school_name = (request.form.get('school_name') or '').strip()
        address = (request.form.get('address') or '').strip()
        plan = (request.form.get('plan') or 'basic').strip().lower()
        username = (request.form.get('admin_username') or '').strip()
        email = _normalize_email(request.form.get('admin_email'))
        password = request.form.get('admin_password') or ''

        if not school_name or len(school_name) > 150:
            flash("Enter a school name up to 150 characters.", "danger")
            return render_template('register_school.html', form=request.form)
        if len(address) > 200:
            flash("School address must be 200 characters or fewer.", "danger")
            return render_template('register_school.html', form=request.form)
        if not username or len(username) > 100:
            flash("Enter an admin username up to 100 characters.", "danger")
            return render_template('register_school.html', form=request.form)
        if not email:
            flash("Enter a valid admin email address.", "danger")
            return render_template('register_school.html', form=request.form)
        if len(password) < 8:
            flash("Admin password must be at least 8 characters.", "danger")
            return render_template('register_school.html', form=request.form)
        if plan not in {'basic', 'standard', 'premium'}:
            flash("Select a valid school plan.", "danger")
            return render_template('register_school.html', form=request.form)
        if User.query.filter_by(username=username).first():
            flash("That username is already in use. Choose another.", "danger")
            return render_template('register_school.html', form=request.form)
        if _email_in_use(email):
            flash("That email address is already in use.", "danger")
            return render_template('register_school.html', form=request.form)

        school = School(
            name=school_name,
            address=address or None,
            plan=plan,
            subscription_status='active'
        )
        try:
            db.session.add(school)
            db.session.flush()
            admin = User(
                email=email,
                username=username,
                password=generate_password_hash(password),
                role='admin',
                school_id=school.id
            )
            db.session.add(admin)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to register a school and its initial admin."
            )
            flash(
                "The school could not be registered. No school or admin account was created.",
                "danger"
            )
            return render_template('register_school.html', form=request.form)

        flash(
            f"{school.name} and its school admin account were registered.",
            "success"
        )
        return redirect(url_for('saas_dashboard'))

    return render_template('register_school.html', form={})


@app.route('/schools/<int:school_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_school(school_id):
    if session.get('role') != 'super_admin':
        return redirect(url_for('login'))

    school = School.query.get_or_404(school_id)

    if request.method == 'POST':
        school_name = (request.form.get('school_name') or '').strip()
        address = (request.form.get('address') or '').strip()
        plan = (request.form.get('plan') or '').strip().lower()
        status = (request.form.get('subscription_status') or '').strip().lower()

        if not school_name or len(school_name) > 150:
            flash("Enter a school name up to 150 characters.", "danger")
            return render_template('edit_school.html', school=school, form=request.form)
        if len(address) > 200:
            flash("School address must be 200 characters or fewer.", "danger")
            return render_template('edit_school.html', school=school, form=request.form)
        if plan not in {'basic', 'standard', 'premium'}:
            flash("Select a valid school plan.", "danger")
            return render_template('edit_school.html', school=school, form=request.form)
        if status not in {'active', 'inactive'}:
            flash("Select a valid subscription status.", "danger")
            return render_template('edit_school.html', school=school, form=request.form)

        school.name = school_name
        school.address = address or None
        school.plan = plan
        school.subscription_status = status
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Failed to update school %s.", school_id)
            flash("The school could not be updated.", "danger")
            return render_template('edit_school.html', school=school, form=request.form)

        flash(f"{school.name} was updated.", "success")
        return redirect(url_for('saas_dashboard'))

    return render_template('edit_school.html', school=school, form={})


@app.route('/schools/<int:school_id>/dashboard')
@login_required
def school_admin_dashboard(school_id):
    if session.get('role') != 'super_admin':
        return redirect(url_for('login'))

    school = School.query.get_or_404(school_id)
    classes = SchoolClass.query.filter_by(
        school_id=school.id
    ).order_by(SchoolClass.name.asc()).all()
    class_student_counts = dict(
        db.session.query(
            Student.class_id,
            func.count(Student.id)
        ).filter(
            Student.school_id == school.id
        ).group_by(
            Student.class_id
        ).all()
    )
    recent_students = Student.query.filter_by(
        school_id=school.id
    ).order_by(
        Student.id.desc()
    ).limit(10).all()

    return render_template(
        'school_admin_dashboard.html',
        school=school,
        admins=User.query.filter_by(
            school_id=school.id,
            role='admin'
        ).order_by(User.username.asc()).all(),
        teachers=User.query.filter_by(
            school_id=school.id,
            role='teacher'
        ).count(),
        students=Student.query.filter_by(school_id=school.id).count(),
        classes=classes,
        class_student_counts=class_student_counts,
        recent_students=recent_students
    )


@app.route('/schools/<int:school_id>/delete', methods=['GET', 'POST'])
@login_required
def delete_school(school_id):
    if session.get('role') != 'super_admin':
        return redirect(url_for('login'))

    school = School.query.get_or_404(school_id)

    if request.method == 'GET':
        return render_template('delete_school.html', school=school)

    confirmation = (request.form.get('confirm_school_name') or '').strip()
    if confirmation != school.name:
        flash("Enter the exact school name to confirm permanent deletion.", "danger")
        return render_template('delete_school.html', school=school), 400

    class_ids = [
        class_id for (class_id,) in db.session.query(SchoolClass.id).filter_by(
            school_id=school.id
        ).all()
    ]
    student_ids = [
        student_id for (student_id,) in db.session.query(Student.id).filter(
            or_(
                Student.school_id == school.id,
                Student.class_id.in_(class_ids)
            )
        ).all()
    ]
    invoice_ids = [
        invoice_id for (invoice_id,) in db.session.query(Invoice.id).filter(
            or_(
                Invoice.school_id == school.id,
                Invoice.student_id.in_(student_ids)
            )
        ).all()
    ]
    user_ids = [
        user_id for (user_id,) in db.session.query(User.id).filter_by(
            school_id=school.id
        ).all()
    ]

    try:
        AdmissionApplication.query.filter(
            or_(
                AdmissionApplication.school_id == school.id,
                AdmissionApplication.class_id.in_(class_ids),
                AdmissionApplication.student_id.in_(student_ids)
            )
        ).delete(synchronize_session=False)
        Payment.query.filter(
            or_(
                Payment.student_id.in_(student_ids),
                Payment.invoice_id.in_(invoice_ids)
            )
        ).delete(synchronize_session=False)
        Attendance.query.filter(
            or_(
                Attendance.school_id == school.id,
                Attendance.student_id.in_(student_ids)
            )
        ).delete(synchronize_session=False)
        Performance.query.filter(
            or_(
                Performance.school_id == school.id,
                Performance.student_id.in_(student_ids)
            )
        ).delete(synchronize_session=False)
        ReportCard.query.filter(
            ReportCard.student_id.in_(student_ids)
        ).delete(synchronize_session=False)
        OtherIncome.query.filter_by(
            school_id=school.id
        ).delete(synchronize_session=False)
        Expense.query.filter_by(
            school_id=school.id
        ).delete(synchronize_session=False)
        AcademicPeriodSetting.query.filter_by(
            school_id=school.id
        ).delete(synchronize_session=False)
        Invoice.query.filter(
            Invoice.id.in_(invoice_ids)
        ).delete(synchronize_session=False)
        Student.query.filter(
            Student.id.in_(student_ids)
        ).delete(synchronize_session=False)
        FeeStructure.query.filter(
            FeeStructure.class_id.in_(class_ids)
        ).delete(synchronize_session=False)
        TermFeeSchedule.query.filter(
            TermFeeSchedule.class_id.in_(class_ids)
        ).delete(synchronize_session=False)

        association_filters = []
        if class_ids:
            association_filters.append(teacher_class.c.class_id.in_(class_ids))
        if user_ids:
            association_filters.append(teacher_class.c.teacher_id.in_(user_ids))
        if association_filters:
            db.session.execute(
                teacher_class.delete().where(or_(*association_filters))
            )

        User.query.filter_by(
            school_id=school.id
        ).delete(synchronize_session=False)
        SchoolClass.query.filter_by(
            school_id=school.id
        ).delete(synchronize_session=False)
        db.session.delete(school)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to permanently delete school %s and its data.",
            school_id
        )
        flash("The school and its data could not be deleted.", "danger")
        return render_template('delete_school.html', school=school), 500

    flash("The school and all its stored data were permanently deleted.", "success")
    return redirect(url_for('saas_dashboard'))



@app.route('/saas_dashboard')
@login_required
def saas_dashboard():

    # -----------------------------------------
    # 1. Verify Super Admin access
    # -----------------------------------------
    if session.get('role') != 'super_admin':
        flash(
            'Access denied. Only the Super Admin can access this dashboard.',
            'danger'
        )
        return redirect(url_for('login'))

    # -----------------------------------------
    # 2. Retrieve all registered schools
    # -----------------------------------------
    schools = School.query.order_by(
        School.name.asc()
    ).all()

    # -----------------------------------------
    # 3. Retrieve administrators for each school
    # -----------------------------------------
    school_admins = {
        school.id: [
            user for user in school.users
            if user.role == 'admin'
        ]
        for school in schools
    }

    # -----------------------------------------
    # 4. Calculate system statistics
    # -----------------------------------------
    total_users = User.query.count()
    total_students = Student.query.count()

    active_schools = sum(
        1 for school in schools
        if school.subscription_status == 'active'
    )

    inactive_schools = len(schools) - active_schools

    # -----------------------------------------
    # 5. Display the Super Admin dashboard
    # -----------------------------------------
    return render_template(
        'saas_dashboard.html',

        # School information
        schools=schools,
        school_admins=school_admins,

        # System statistics
        total_users=total_users,
        total_students=total_students,
        active_schools=active_schools,
        inactive_schools=inactive_schools,

        # Super Admin account information
        super_admin=current_user
    )


@app.route('/super-admin/account-settings', methods=['GET', 'POST'])
@login_required
def super_admin_account_settings():

    # Check Super Admin permissions
    if session.get('role') != 'super_admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('login'))

    # Process submitted forms
    if request.method == 'POST':

        action = request.form.get('action', '').strip()

        current_password = request.form.get(
            'current_password', ''
        )

        # Verify the current password
        if not check_password_hash(
            current_user.password,
            current_password
        ):
            flash('Your current password is incorrect.', 'danger')
            return redirect(
                url_for('super_admin_account_settings')
            )

        # =================================
        # CHANGE USERNAME
        # =================================
        if action == 'change_username':

            new_username = request.form.get(
                'new_username', ''
            ).strip()

            if not new_username or len(new_username) > 100:
                flash(
                    'Username must contain 1 to 100 characters.',
                    'danger'
                )

            elif User.query.filter(
                User.username == new_username,
                User.id != current_user.id
            ).first():

                flash(
                    'That username is already in use.',
                    'danger'
                )

            else:
                try:
                    current_user.username = new_username
                    db.session.commit()

                    flash(
                        'Username updated successfully.',
                        'success'
                    )

                except SQLAlchemyError:
                    db.session.rollback()
                    app.logger.exception(
                        'Failed to update Super Admin username.'
                    )

                    flash(
                        'Unable to update username. Please try again.',
                        'danger'
                    )

        # =================================
        # CHANGE PASSWORD
        # =================================
        elif action == 'change_password':

            new_password = request.form.get(
                'new_password', ''
            )

            confirm_password = request.form.get(
                'confirm_password', ''
            )

            if len(new_password) < 12:
                flash(
                    'Password must contain at least 12 characters.',
                    'danger'
                )

            elif new_password != confirm_password:
                flash(
                    'The new passwords do not match.',
                    'danger'
                )

            elif new_password == current_password:
                flash(
                    'Your new password must differ from your current password.',
                    'danger'
                )

            else:
                try:
                    current_user.password = generate_password_hash(
                        new_password
                    )

                    db.session.commit()

                    flash(
                        'Password changed successfully.',
                        'success'
                    )

                except SQLAlchemyError:
                    db.session.rollback()
                    app.logger.exception(
                        'Failed to update Super Admin password.'
                    )

                    flash(
                        'Unable to change password. Please try again.',
                        'danger'
                    )

        else:
            flash('Invalid account settings action.', 'danger')

        return redirect(
            url_for('super_admin_account_settings')
        )

    # Display account settings
    return render_template('account_settings.html')




# =========================
# HEADMASTER ACADEMIC VIEWS
# =========================
def _headmaster_school_id():
    if current_user.school_id is None:
        flash("Your account is not assigned to a school.", "warning")
        return None
    return current_user.school_id


def _headmaster_academic_filter_options(model, school_id):
    def values_for(column):
        return [
            value
            for (value,) in db.session.query(column).join(
                Student,
                model.student_id == Student.id
            ).filter(
                Student.school_id == school_id,
                column.isnot(None),
                column != ''
            ).distinct().order_by(column.desc()).all()
        ]

    return values_for(model.term), values_for(model.academic_year)


def _headmaster_date_filters():
    start_value = (request.args.get('start_date') or '').strip()
    end_value = (request.args.get('end_date') or '').strip()
    start_date = None
    end_date = None

    if start_value:
        try:
            start_date = date.fromisoformat(start_value)
        except ValueError:
            flash("Enter a valid start date.", "warning")

    if end_value:
        try:
            end_date = date.fromisoformat(end_value)
        except ValueError:
            flash("Enter a valid end date.", "warning")

    if start_date and end_date and start_date > end_date:
        flash("The start date must be on or before the end date.", "warning")
        start_date = None
        end_date = None
        start_value = ''
        end_value = ''

    return start_date, end_date, start_value, end_value


@app.route('/headmaster_dashboard')
@login_required
@role_required('headmaster')
def headmaster_dashboard():
    school_id = _headmaster_school_id()
    if school_id is None:
        return redirect(url_for('login'))

    today = date.today()
    attendance_query = Attendance.query.join(Student).filter(
        Student.school_id == school_id
    )
    performance_average = db.session.query(
        func.avg(Performance.total_score)
    ).join(Student).filter(
        Student.school_id == school_id
    ).scalar() or 0

    return render_template(
        'headmaster_dashboard.html',
        school=current_user.school,
        student_count=Student.query.filter_by(school_id=school_id).count(),
        class_count=SchoolClass.query.filter_by(school_id=school_id).count(),
        teacher_count=User.query.filter_by(
            role='teacher',
            school_id=school_id
        ).count(),
        headmaster_count=User.query.filter_by(
            role='headmaster',
            school_id=school_id
        ).count(),
        present_today=attendance_query.filter(
            Attendance.date == today,
            Attendance.status == 'Present'
        ).count(),
        absent_today=attendance_query.filter(
            Attendance.date == today,
            Attendance.status == 'Absent'
        ).count(),
        performance_average=float(performance_average)
    )


@app.route('/headmaster/students')
@login_required
@role_required('headmaster')
def headmaster_students():
    school_id = _headmaster_school_id()
    if school_id is None:
        return redirect(url_for('login'))

    classes = SchoolClass.query.filter_by(
        school_id=school_id
    ).order_by(SchoolClass.name.asc()).all()
    selected_class_id = request.args.get('class_id', type=int)
    search = (request.args.get('search') or '').strip()
    query = Student.query.filter_by(school_id=school_id)

    if selected_class_id:
        query = query.filter(Student.class_id == selected_class_id)
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(or_(
            Student.name.ilike(search_pattern),
            Student.admission_no.ilike(search_pattern)
        ))

    students = query.order_by(Student.name.asc()).all()
    return render_template(
        'headmaster_students.html',
        students=students,
        classes=classes,
        selected_class_id=selected_class_id,
        search=search
    )


@app.route('/headmaster/classes')
@login_required
@role_required('headmaster')
def headmaster_classes():
    school_id = _headmaster_school_id()
    if school_id is None:
        return redirect(url_for('login'))

    search = (request.args.get('search') or '').strip()
    query = SchoolClass.query.filter_by(school_id=school_id)

    if search:
        search_pattern = f'%{search}%'
        query = query.filter(or_(
            SchoolClass.name.ilike(search_pattern),
            SchoolClass.teachers.any(User.username.ilike(search_pattern))
        ))

    classes = query.order_by(SchoolClass.name.asc()).all()
    return render_template(
        'headmaster_classes.html',
        classes=classes,
        search=search
    )


@app.route('/headmaster/attendance')
@login_required
@role_required('headmaster')
def headmaster_attendance():
    school_id = _headmaster_school_id()
    if school_id is None:
        return redirect(url_for('login'))

    classes = SchoolClass.query.filter_by(
        school_id=school_id
    ).order_by(SchoolClass.name.asc()).all()
    terms, academic_years = _headmaster_academic_filter_options(
        Attendance,
        school_id
    )
    selected_class_id = request.args.get('class_id', type=int)
    selected_status = (request.args.get('status') or '').strip()
    selected_term = (request.args.get('term') or '').strip()
    selected_year = (request.args.get('academic_year') or '').strip()
    search = (request.args.get('search') or '').strip()
    start_date, end_date, start_value, end_value = _headmaster_date_filters()
    query = Attendance.query.join(Student).filter(
        Student.school_id == school_id
    )

    if selected_class_id:
        query = query.filter(Student.class_id == selected_class_id)
    if selected_status:
        query = query.filter(Attendance.status == selected_status)
    if selected_term:
        query = query.filter(Attendance.term == selected_term)
    if selected_year:
        query = query.filter(Attendance.academic_year == selected_year)
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(or_(
            Student.name.ilike(search_pattern),
            Student.admission_no.ilike(search_pattern)
        ))
    if start_date:
        query = query.filter(Attendance.date >= start_date)
    if end_date:
        query = query.filter(Attendance.date <= end_date)

    records = query.order_by(
        Attendance.date.desc(),
        Attendance.id.desc()
    ).all()
    return render_template(
        'headmaster_attendance.html',
        records=records,
        classes=classes,
        terms=terms,
        academic_years=academic_years,
        selected_class_id=selected_class_id,
        selected_status=selected_status,
        selected_term=selected_term,
        selected_year=selected_year,
        search=search,
        start_value=start_value,
        end_value=end_value
    )


@app.route('/headmaster/performance')
@login_required
@role_required('headmaster')
def headmaster_performance():
    school_id = _headmaster_school_id()
    if school_id is None:
        return redirect(url_for('login'))

    classes = SchoolClass.query.filter_by(
        school_id=school_id
    ).order_by(SchoolClass.name.asc()).all()
    terms, academic_years = _headmaster_academic_filter_options(
        Performance,
        school_id
    )
    selected_class_id = request.args.get('class_id', type=int)
    selected_term = (request.args.get('term') or '').strip()
    selected_year = (request.args.get('academic_year') or '').strip()
    search = (request.args.get('search') or '').strip()
    subject = (request.args.get('subject') or '').strip()
    start_date, end_date, start_value, end_value = _headmaster_date_filters()
    query = Performance.query.join(Student).filter(
        Student.school_id == school_id
    )

    if selected_class_id:
        query = query.filter(Student.class_id == selected_class_id)
    if selected_term:
        query = query.filter(Performance.term == selected_term)
    if selected_year:
        query = query.filter(Performance.academic_year == selected_year)
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(or_(
            Student.name.ilike(search_pattern),
            Student.admission_no.ilike(search_pattern)
        ))
    if subject:
        query = query.filter(Performance.subject.ilike(f'%{subject}%'))
    if start_date:
        query = query.filter(
            Performance.date >= datetime.combine(
                start_date,
                datetime.min.time()
            )
        )
    if end_date:
        query = query.filter(
            Performance.date < datetime.combine(
                end_date + timedelta(days=1),
                datetime.min.time()
            )
        )

    records = query.order_by(
        Performance.date.desc(),
        Performance.id.desc()
    ).all()
    return render_template(
        'headmaster_performance.html',
        records=records,
        classes=classes,
        terms=terms,
        academic_years=academic_years,
        selected_class_id=selected_class_id,
        selected_term=selected_term,
        selected_year=selected_year,
        search=search,
        subject=subject,
        start_value=start_value,
        end_value=end_value
    )


# =========================
# DASHBOARDS
# =========================
@app.route('/super_dashboard')
@app.route('/schools/<int:school_id>/admin-dashboard')
@login_required
def super_dashboard(school_id=None):

    role = session.get('role')
    if role == 'admin':
        school_redirect = _require_current_school()
        if school_redirect:
            return school_redirect
        school_id = current_user.school_id
    elif role != 'super_admin' or school_id is None:
        return redirect(url_for('login'))

    school = School.query.get_or_404(school_id)
    school_name = school.name

    # =========================
    # COUNTS
    # =========================
    students = Student.query.filter_by(school_id=school_id).count()

    teachers = User.query.filter_by(
        role='teacher',
        school_id=school_id
    ).count()

    invoices = Invoice.query.filter_by(school_id=school_id).count()

    # Match the Finance page's student fee totals, including fees for
    # students who do not yet have an invoice.
    total_fees = 0.0
    total_paid_fees = 0.0
    outstanding_fees = 0.0
    for student in Student.query.filter_by(school_id=school_id).all():
        student_invoices = Invoice.query.filter_by(
            student_id=student.id,
            school_id=school_id
        ).all()

        if student_invoices:
            student_fees = sum(
                float(invoice.amount or 0)
                for invoice in student_invoices
            )
            student_paid = sum(
                float(invoice.partial_paid or 0)
                for invoice in student_invoices
            )
            student_outstanding = sum(
                max(
                    float(invoice.amount or 0) - float(invoice.partial_paid or 0),
                    0
                )
                for invoice in student_invoices
            )
        else:
            schedule = (
                TermFeeSchedule.query
                .filter_by(class_id=student.class_id)
                .order_by(
                    TermFeeSchedule.academic_year.desc(),
                    TermFeeSchedule.id.desc()
                )
                .first()
            )
            if schedule:
                student_fees = sum(
                    float(getattr(schedule, field) or 0)
                    for field in (
                        "tuition_fee",
                        "sports_fee",
                        "library_fee",
                        "ict_fee"
                    )
                )
            else:
                fee_structure = FeeStructure.query.filter_by(
                    class_id=student.class_id
                ).first()
                student_fees = float(
                    (
                        fee_structure.amount
                        if fee_structure
                        else student.fee_amount
                    ) or 0
                )
            student_paid = float(
                db.session.query(func.sum(Payment.amount_paid))
                .filter_by(student_id=student.id)
                .scalar() or 0
            )
            student_outstanding = max(student_fees - student_paid, 0)

        total_fees += student_fees
        total_paid_fees += student_paid
        outstanding_fees += student_outstanding

    # =========================
    # FINANCIAL OVERVIEW
    # Match the Finance page: revenue is money received, not invoiced.
    # =========================
    revenue = float(
        db.session.query(
            func.sum(Payment.amount_paid)
        ).join(
            Invoice,
            Payment.invoice_id == Invoice.id
        ).filter(
            Invoice.school_id == school_id
        ).scalar() or 0
    )
    total_expenses = float(
        db.session.query(
            func.sum(Expense.amount)
        ).filter(
            Expense.school_id == school_id
        ).scalar() or 0
    )
    profit = revenue - total_expenses

    today_start = datetime.combine(date.today(), datetime.min.time())
    tomorrow_start = today_start + timedelta(days=1)
    active_period = AcademicPeriodSetting.query.filter_by(
        school_id=school_id
    ).first()
    school_payments = db.session.query(
        func.sum(Payment.amount_paid)
    ).join(
        Invoice,
        Payment.invoice_id == Invoice.id
    ).filter(
        Invoice.school_id == school_id
    )
    received_today = float(
        school_payments.filter(
            Payment.payment_date >= today_start,
            Payment.payment_date < tomorrow_start
        ).scalar() or 0
    )
    received_this_term = 0.0
    received_this_year = 0.0
    if active_period:
        period_payments = school_payments.filter(
            Invoice.academic_year == active_period.academic_year
        )
        received_this_year = float(period_payments.scalar() or 0)
        received_this_term = float(
            period_payments.filter(
                Invoice.term == active_period.term
            ).scalar() or 0
        )

    # =========================
    # CHART DATA
    # =========================
    received_payments = Payment.query.join(
        Invoice,
        Payment.invoice_id == Invoice.id
    ).filter(
        Invoice.school_id == school_id
    ).order_by(
        Payment.payment_date.asc()
    ).all()
    chart_data = [float(payment.amount_paid) for payment in received_payments]

    # =========================
    # RECENT STUDENTS
    # =========================
    recent_students = Student.query.filter_by(
        school_id=school_id
    ).order_by(
        Student.id.desc()
    ).limit(5).all()

    student_counts_by_class = dict(
        db.session.query(
            Student.class_id,
            func.count(Student.id)
        ).filter(
            Student.school_id == school_id
        ).group_by(
            Student.class_id
        ).all()
    )
    student_names_by_class = defaultdict(list)
    for class_id, student_name in Student.query.with_entities(
        Student.class_id,
        Student.name
    ).filter(
        Student.school_id == school_id
    ).order_by(
        Student.name.asc()
    ).all():
        student_names_by_class[class_id].append(student_name)

    class_student_summaries = [
        {
            "classroom": classroom,
            "count": student_counts_by_class.get(classroom.id, 0),
            "student_names": student_names_by_class[classroom.id]
        }
        for classroom in SchoolClass.query.filter_by(
            school_id=school_id
        ).order_by(
            SchoolClass.name.asc()
        ).all()
    ]

    # =========================
    # RECENT INVOICES
    # =========================
    recent_invoices = Invoice.query.filter_by(
        school_id=school_id
    ).order_by(
        Invoice.id.desc()
    ).limit(5).all()

    # =========================
    # CLASS ATTENDANCE SUMMARY
    # =========================
    attendance_classes = SchoolClass.query.filter_by(
        school_id=school_id
    ).order_by(
        SchoolClass.name.asc()
    ).all()
    selected_attendance_class_id = request.args.get(
        'attendance_class_id',
        type=int
    )
    selected_attendance_class = None

    if selected_attendance_class_id:
        selected_attendance_class = SchoolClass.query.filter_by(
            id=selected_attendance_class_id,
            school_id=school_id
        ).first()
        if not selected_attendance_class:
            flash("Selected class was not found.", "warning")
            selected_attendance_class_id = None

    attendance_totals = {
        classroom.id: {"present": 0, "absent": 0}
        for classroom in attendance_classes
    }
    class_attendance_counts = db.session.query(
        Student.class_id,
        Attendance.status,
        func.count(Attendance.id)
    ).join(
        Attendance,
        Attendance.student_id == Student.id
    ).filter(
        Student.school_id == school_id
    ).group_by(
        Student.class_id,
        Attendance.status
    ).all()

    for class_id, status, count in class_attendance_counts:
        if class_id in attendance_totals and status in {"Present", "Absent"}:
            attendance_totals[class_id][status.lower()] = count

    class_attendance_summaries = [
        {
            "classroom": classroom,
            "present": attendance_totals[classroom.id]["present"],
            "absent": attendance_totals[classroom.id]["absent"]
        }
        for classroom in attendance_classes
    ]

    # =========================
    # RECENT ATTENDANCE
    # =========================
    attendance_query = db.session.query(
        Attendance,
        Student
    ).join(
        Student,
        Attendance.student_id == Student.id
    ).filter(
        Student.school_id == school_id
    )
    if selected_attendance_class:
        attendance_query = attendance_query.filter(
            Student.class_id == selected_attendance_class.id
        )
    attendance_records = attendance_query.order_by(
        Attendance.date.desc(),
        Attendance.id.desc()
    ).limit(10).all()

    # =========================
    # ATTENDANCE STATS
    # =========================
    present_count = Attendance.query.join(
        Student,
        Attendance.student_id == Student.id
    ).filter(
        Student.school_id == school_id,
        Attendance.status == 'Present'
    ).count()

    absent_count = Attendance.query.join(
        Student,
        Attendance.student_id == Student.id
    ).filter(
        Student.school_id == school_id,
        Attendance.status == 'Absent'
    ).count()

    return render_template(

        'super_dashboard.html',

        read_only=(role == 'super_admin'),
        dashboard_back_url=(
            url_for('saas_dashboard') if role == 'super_admin' else None
        ),
        dashboard_url=request.path,
        school_name=school_name,
        students=students,
        teachers=teachers,
        invoices=invoices,
        total_fees=total_fees,
        total_paid_fees=total_paid_fees,
        outstanding_fees=outstanding_fees,
        revenue=revenue,
        total_expenses=total_expenses,
        profit=profit,
        received_today=received_today,
        received_this_term=received_this_term,
        received_this_year=received_this_year,
        active_period=active_period,

        chart_data=chart_data,

        recent_students=recent_students,
        class_student_summaries=class_student_summaries,
        recent_invoices=recent_invoices,

        attendance_records=attendance_records,
        attendance_classes=attendance_classes,
        class_attendance_summaries=class_attendance_summaries,
        selected_attendance_class=selected_attendance_class,
        selected_attendance_class_id=selected_attendance_class_id,

        present_count=present_count,
        absent_count=absent_count
    )


@app.route('/teacher_dashboard')
@login_required
@role_required('teacher', 'admin')
def teacher_dashboard():

    user = User.query.get(session['user_id'])
    role = session.get('role')

    # =========================
    # GET CLASSES
    # =========================

    if role == 'teacher':
        classes = user.classes
    else:
        classes = SchoolClass.query.filter_by(
            school_id=current_user.school_id
        ).all()

    class_ids = [c.id for c in classes]

    # =========================
    # FILTER CLASS
    # =========================

    class_id = request.args.get('class_id')

    if class_id:

        class_id = int(class_id)

        if role == 'teacher' and class_id not in class_ids:

            flash("Access denied.")
            return redirect(url_for('teacher_dashboard'))

        students = Student.query.filter_by(
            class_id=class_id
        ).all()

    else:

        students = Student.query.filter(
            Student.class_id.in_(class_ids)
        ).all()

    total_students = len(students)

    student_ids = [s.id for s in students]

    today = date.today()

    # =========================
    # ATTENDANCE
    # =========================

    if student_ids:

        present_today = Attendance.query.filter(
            Attendance.student_id.in_(student_ids),
            Attendance.date == today,
            Attendance.status == 'Present'
        ).count()

        absent_today = Attendance.query.filter(
            Attendance.student_id.in_(student_ids),
            Attendance.date == today,
            Attendance.status == 'Absent'
        ).count()

    else:

        present_today = 0
        absent_today = 0

    # =========================
    # AVERAGE SCORE
    # =========================

    if student_ids:

        avg_score = db.session.query(
            func.avg(Performance.total_score)
        ).filter(
            Performance.student_id.in_(student_ids)
        ).scalar() or 0

    else:

        avg_score = 0

    # =========================
    # WEAK STUDENTS
    # =========================

    weak_students = db.session.query(
        Student.name,
        func.avg(
            Performance.total_score
        ).label('avg')
    ).join(
        Performance
    ).filter(
        Student.id.in_(student_ids)
    ).group_by(
        Student.id
    ).having(
        func.avg(
            Performance.total_score
        ) < 50
    ).all()

    # =========================
    # TOP STUDENTS
    # =========================

    top_students = db.session.query(
        Student.name,
        func.avg(
            Performance.total_score
        ).label('avg')
    ).join(
        Performance
    ).filter(
        Student.id.in_(student_ids)
    ).group_by(
        Student.id
    ).order_by(
        func.avg(
            Performance.total_score
        ).desc()
    ).limit(5).all()

    # =========================
    # SUBJECT ANALYTICS
    # =========================

    subject_data = db.session.query(
        Performance.subject,
        func.avg(
            Performance.total_score
        )
    ).filter(
        Performance.student_id.in_(student_ids)
    ).group_by(
        Performance.subject
    ).all()

    subjects = [s[0] for s in subject_data]

    scores = [
        round(float(s[1]), 2)
        for s in subject_data
    ]

    # =========================
    # AT RISK COUNT
    # =========================

    at_risk = len(weak_students)

    # =========================
    # RENDER PAGE
    # =========================

    return render_template(
        'teacher_dashboard.html',

        classes=classes,
        students=students,

        total_students=total_students,

        present_today=present_today,
        absent_today=absent_today,

        avg_score=round(avg_score, 2),

        at_risk=at_risk,

        weak_students=weak_students,

        top_students=top_students,

        subjects=subjects,
        scores=scores,

        class_id=str(class_id)
        if class_id else ""
    )

# =========================
# ✅ MARK ALL PRESENT
# =========================
@app.route('/mark_all_present/<int:class_id>')
@login_required
@role_required('teacher', 'admin')
def mark_all_present(class_id):

    today = date.today()
    students = Student.query.filter_by(class_id=class_id).all()

    for s in students:
        existing = Attendance.query.filter_by(
            student_id=s.id,
            date=today
        ).first()

        if not existing:
            db.session.add(Attendance(
                student_id=s.id,
                status="Present",
                date=today
            ))

    db.session.commit()

    return redirect(url_for('teacher_dashboard', class_id=class_id))

# =========================
# ⚡ QUICK SCORE (AJAX)
# =========================
@app.route('/quick_score', methods=['POST'])
@login_required
def quick_score():
    if current_user.role == 'headmaster':
        return redirect(url_for('headmaster_dashboard'))

    db.session.add(Performance(
        student_id=int(request.form['student_id']),
        subject=request.form['subject'],
        score=int(request.form['score']),
        date=date.today()
    ))

    db.session.commit()
    return "OK"


@app.route('/teachers', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def teachers():

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = _normalize_email(request.form.get('email'))
        password = request.form.get('password') or ''

        if not email or _email_in_use(email):
            flash("Enter a valid, unused email address.", "danger")
            return redirect(url_for('teachers'))

        if username and password:
            new_teacher = User(
                email=email,
                username=username,
                password=generate_password_hash(password),
                role='teacher'
            )
            db.session.add(new_teacher)
            db.session.commit()

    teachers = User.query.filter(User.role.ilike('teacher')).all()
    classes = SchoolClass.query.filter_by(
        school_id=current_user.school_id
    ).all()

    return render_template('teachers.html', teachers=teachers, classes=classes)

@app.route('/assign_teacher_class', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def assign_teacher_class():

    teacher_id = request.form.get('teacher_id')
    class_id = request.form.get('class_id')

    teacher = User.query.get(teacher_id)
    classroom = SchoolClass.query.get(class_id)

    if teacher and classroom:
        if classroom not in teacher.classes:
            teacher.classes.append(classroom)
            db.session.commit()

    flash("Class assigned!", "success")
    return redirect(url_for('teachers'))



@app.route('/delete_teacher/<int:id>')
@login_required
@role_required('admin')
def delete_teacher(id):

    teacher = User.query.get_or_404(id)

    db.session.delete(teacher)
    db.session.commit()

    flash("Teacher deleted", "danger")
    return redirect(url_for('teachers'))

@app.route('/teacher/classes')
@login_required
def teacher_classes():

    user = User.query.get(session.get("user_id"))

    if not user:
        flash("User not found")
        return redirect(url_for('login'))

    # 🔥 THIS IS THE MAGIC
    classes = user.classes

    return render_template('teacher_classes.html', classes=classes)

# ==============================
# 🧠 AI WEAK STUDENT DETECTION
# ==============================
def get_weak_students(class_id):

    students = Student.query.filter_by(class_id=class_id).all()

    weak_students = []

    for s in students:

        scores = [
            p.score for p in
            Performance.query.filter_by(student_id=s.id).all()
        ]

        if not scores:
            continue

        avg = sum(scores) / len(scores)

        # AI RULE
        if avg < 50:

            weak_students.append({
                "name": s.name,
                "avg": round(avg, 2),
                "status": "Weak"
            })

    return weak_students


@app.route('/teacher/class/<int:class_id>')
@login_required
@role_required('teacher')
def class_dashboard(class_id):

    teacher_id = session.get('user_id')

    # ==================================================
    # 🔐 GET CLASS
    # ==================================================
    classroom = SchoolClass.query.get(class_id)

    if not classroom:
        return "Class not found", 404

    teacher = User.query.get(teacher_id)

    # ==================================================
    # 🔐 VERIFY TEACHER IS ASSIGNED TO THIS CLASS
    # ==================================================
    if classroom not in teacher.classes:
        return "Unauthorized", 403

    # ==================================================
    # 👨‍🎓 ONLY STUDENTS IN THIS CLASS
    # ==================================================
    students = Student.query.filter_by(class_id=class_id).all()
    total_students = len(students)
    student_ids = [s.id for s in students]

    # ==================================================
    # 📊 PERFORMANCE DATA
    # ==================================================
    performances = Performance.query.filter(
        Performance.student_id.in_(student_ids)
    ).all()

    # ==================================================
    # 📈 CLASS AVERAGE SCORE
    # ==================================================
    avg_score = db.session.query(
        func.avg(Performance.total_score)
    ).filter(
        Performance.student_id.in_(student_ids)
    ).scalar() or 0

    # ==================================================
    # 🏆 TOP STUDENTS
    # ==================================================
    top_students = db.session.query(
        Student.name,
        func.avg(Performance.total_score).label('avg')
    ).join(
        Performance,
        Student.id == Performance.student_id
    ).filter(
        Student.id.in_(student_ids)
    ).group_by(
        Student.id
    ).order_by(
        func.avg(Performance.total_score).desc()
    ).limit(5).all()

    # ==================================================
    # 📊 CHART DATA
    # ==================================================
    subject_data = db.session.query(
        Performance.subject,
        func.avg(Performance.total_score)
    ).filter(
        Performance.student_id.in_(student_ids)
    ).group_by(
        Performance.subject
    ).all()

    labels = [s[0] for s in subject_data]
    values = [round(float(s[1]), 2) for s in subject_data]

    # ==================================================
    # 🤖 WEAK STUDENTS (AI FEATURE HOOK)
    # ==================================================
    weak_students = []
    for s in students:
        scores = [p.total_score for p in performances if p.student_id == s.id]

        if scores:
            avg = sum(scores) / len(scores)
            if avg < 50:
                weak_students.append({
                    "name": s.name,
                    "avg": round(avg, 2)
                })

    # ==================================================
    # RENDER
    # ==================================================
    return render_template(
        'class_dashboard.html',

        classroom=classroom,
        students=students,

        total_students=total_students,
        avg_score=round(avg_score, 2),

        top_students=top_students,

        labels=labels,
        values=values,

        weak_students=weak_students,

        class_id=class_id
    )

@app.route('/class/chart/<int:class_id>')
@login_required
@role_required('teacher')
def class_chart(class_id):

    students = Student.query.filter_by(class_id=class_id).all()
    student_ids = [s.id for s in students]

    subject_data = db.session.query(
        Performance.subject,
        func.avg(Performance.total_score)
    ).filter(
        Performance.student_id.in_(student_ids)
    ).group_by(Performance.subject).all()

    labels = [s[0] for s in subject_data]
    values = [round(float(s[1]), 2) for s in subject_data]

    return render_template(
        "class_chart.html",
        labels=labels,
        values=values
    )

@app.route('/assign_class', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def assign_class():

    # =========================
    # GET ALL TEACHERS
    # =========================
    teachers = User.query.filter(
        User.role.ilike('teacher')
    ).all()

    # =========================
    # GET ALL CLASSES
    # =========================
    classes = SchoolClass.query.all()

    # =========================
    # ASSIGN CLASS
    # =========================
    if request.method == 'POST':

        teacher_id = request.form.get('teacher_id')
        class_id = request.form.get('class_id')

        # VALIDATION
        if not teacher_id or not class_id:
            flash("Please select teacher and class", "danger")
            return redirect(url_for('assign_class'))

        # GET OBJECTS
        teacher = User.query.get_or_404(teacher_id)
        classroom = SchoolClass.query.get_or_404(class_id)

        # CHECK ROLE
        if teacher.role.strip().lower() != 'teacher':
            flash("Selected user is not a teacher", "danger")
            return redirect(url_for('assign_class'))

        # PREVENT DUPLICATE
        if classroom in teacher.classes:
            flash("Class already assigned to this teacher", "warning")
            return redirect(url_for('assign_class'))

        # ASSIGN CLASS
        teacher.classes.append(classroom)

        db.session.commit()

        flash(
            f"{classroom.name} assigned to Sir {teacher.username} successfully!",
            "success"
        )

        return redirect(url_for('assign_class'))

    # =========================
    # SHOW PAGE
    # =========================
    return render_template(
        'assign_class.html',
        teachers=teachers,
        classes=classes
    )

@app.route('/add_teacher', methods=['GET', 'POST'])
def add_teacher():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']

        teacher = teacher(name=name, email=email)
        db.session.add(teacher)
        db.session.commit()

        return redirect(url_for('assign_class'))

    return render_template('add_teacher.html')

@app.route('/notify_parent/<int:student_id>')
@login_required
def notify_parent(student_id):

    student = Student.query.get(student_id)

    # Placeholder logic
    message = f"{student.name} is underperforming."

    print("📩 Sending message:", message)

    flash("Parent notified!", "success")

    return redirect(url_for('teacher_dashboard'))

@app.route('/accountant_dashboard')
@login_required
@role_required('accountant', 'admin')
def accountant_dashboard():

    # =========================
    # GET ALL INVOICES
    # =========================
    all_invoices = Invoice.query.all()

    # =========================
    # TOTAL INCOME
    # =========================
    total_income = 0

    for inv in all_invoices:

        paid = inv.partial_paid or 0

        total_income += paid

    # =========================
    # TOTAL EXPENSES
    # =========================
    all_expenses = Expense.query.all()

    total_expense = sum(
        e.amount or 0 for e in all_expenses
    )

    # =========================
    # PROFIT / LOSS
    # =========================
    profit = total_income - total_expense

    # =========================
    # PAID INVOICES COUNT
    # =========================
    paid_invoices = [
        i for i in all_invoices
        if i.status == 'Paid'
    ]

    total_invoices = len(all_invoices)

    total_paid = len(paid_invoices)

    # =========================
    # RECENT PAYMENTS
    # =========================
    recent_invoices = Invoice.query.order_by(
        Invoice.date_created.desc()
    ).limit(5).all()

    # =========================
    # DEBTORS SYSTEM
    # =========================
    debtors = []

    students = Student.query.all()

    for student in students:

        student_invoices = Invoice.query.filter_by(
            student_id=student.id
        ).all()

        balance = 0

        for inv in student_invoices:

            paid = inv.partial_paid or 0

            invoice_balance = inv.amount - paid

            balance += invoice_balance

        if balance > 0:

            debtors.append({

                "student": student,

                "balance": round(balance, 2)

            })

    # =========================
    # SORT DEBTORS (HIGHEST FIRST)
    # =========================
    debtors = sorted(
        debtors,
        key=lambda x: x['balance'],
        reverse=True
    )

    # =========================
    # RENDER TEMPLATE
    # =========================
    return render_template(

        'accountant_dashboard.html',

        total_income=round(total_income, 2),

        total_expense=round(total_expense, 2),

        profit=round(profit, 2),

        total_invoices=total_invoices,

        total_paid=total_paid,

        recent_invoices=recent_invoices,

        debtors=debtors
    )

@app.route('/expenses', methods=['GET', 'POST'])
@login_required
@role_required('accountant', 'admin')
def expenses():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', ''))
        except (TypeError, ValueError):
            flash("Enter a valid expense amount.", "warning")
            return redirect(url_for('expenses'))

        if not math.isfinite(amount) or amount <= 0:
            flash("Expense amount must be greater than zero.", "warning")
            return redirect(url_for('expenses'))

        title = (request.form.get('title') or '').strip()
        category = (request.form.get('category') or '').strip()
        if not title or not category:
            flash("Enter an expense title and category.", "warning")
            return redirect(url_for('expenses'))

        expense = Expense(
            title=title,
            amount=amount,
            category=category,
            school_id=current_user.school_id
        )

        db.session.add(expense)
        db.session.commit()

        flash("Expense added successfully")

        return redirect(url_for('expenses'))

    all_expenses = Expense.query.filter_by(
        school_id=current_user.school_id
    ).order_by(Expense.date_created.desc()).all()

    return render_template('expenses.html', expenses=all_expenses)


@app.route('/other-income', methods=['GET', 'POST'])
@login_required
@role_required('accountant', 'admin')
def other_income():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    if request.method == 'POST':
        category = (request.form.get('category') or '').strip()
        description = (request.form.get('description') or '').strip()
        source = (request.form.get('source') or '').strip()
        reference = (request.form.get('reference') or '').strip()
        payment_method = (request.form.get('payment_method') or '').strip()

        try:
            amount = float(request.form.get('amount', ''))
            received_on = date.fromisoformat(
                request.form.get('received_date', '')
            )
        except (TypeError, ValueError):
            flash("Enter a valid amount and date for the income record.", "warning")
            return redirect(url_for('other_income'))

        if not math.isfinite(amount) or amount <= 0:
            flash("Income amount must be greater than zero.", "warning")
            return redirect(url_for('other_income'))
        if not category or not description:
            flash("Enter an income category and description.", "warning")
            return redirect(url_for('other_income'))
        if payment_method not in {"Cash", "Momo", "Bank"}:
            flash("Select a valid payment method.", "warning")
            return redirect(url_for('other_income'))

        record = OtherIncome(
            category=category,
            description=description,
            amount=amount,
            received_date=datetime.combine(received_on, datetime.min.time()),
            payment_method=payment_method,
            source=source or None,
            reference=reference or None,
            remarks=(request.form.get('remarks') or '').strip() or None,
            recorded_by=current_user.id,
            school_id=current_user.school_id
        )
        db.session.add(record)
        db.session.commit()
        flash("Other income recorded successfully.", "success")
        return redirect(url_for('other_income'))

    records = OtherIncome.query.filter_by(
        school_id=current_user.school_id
    ).order_by(
        OtherIncome.received_date.desc(),
        OtherIncome.id.desc()
    ).all()
    return render_template(
        'other_income.html',
        records=records,
        today=date.today().isoformat()
    )

@app.route('/performance', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin')
def performance():

    role = session.get('role')
    user = User.query.get(session['user_id'])
    selected_class = None

    # =========================
    # GET STUDENTS
    # =========================
    if role == 'admin':
        selected_class_id = request.args.get('class_id', type=int)
        if not selected_class_id:
            return redirect(url_for('performance_classes'))
        selected_class = SchoolClass.query.get_or_404(selected_class_id)
        students = Student.query.filter_by(
            class_id=selected_class.id
        ).all()
    else:
        teacher_classes = user.classes
        class_ids = [c.id for c in teacher_classes]

        students = Student.query.filter(
            Student.class_id.in_(class_ids)
        ).all()

    # =========================
    # ADD PERFORMANCE
    # =========================
    if request.method == 'POST':

        try:

            student_id = request.form.get('student_id')
            subject = request.form.get('subject')

            class_score = request.form.get('class_score')
            exam_score = request.form.get('exam_score')

            term = request.form.get('term')
            year = request.form.get('academic_year')

            if not all([
                student_id,
                subject,
                class_score,
                exam_score,
                term,
                year
            ]):
                flash("Fill all fields", "warning")
                return redirect(url_for(
                    'performance',
                    **(
                        {'class_id': selected_class.id}
                        if selected_class
                        else {}
                    )
                ))

            selected_student = Student.query.get(int(student_id))
            if (
                selected_student is None
                or selected_student not in students
            ):
                flash("Select a student from the available class.", "danger")
                return redirect(url_for(
                    'performance',
                    **(
                        {'class_id': selected_class.id}
                        if selected_class
                        else {}
                    )
                ))

            class_score = float(class_score)
            exam_score = float(exam_score)

            total_score = class_score + exam_score

            new_perf = Performance(
                student_id=selected_student.id,
                subject=subject,
                class_score=class_score,
                exam_score=exam_score,
                total_score=total_score,
                term=term,
                academic_year=year,
                date=datetime.utcnow()
            )

            db.session.add(new_perf)
            db.session.commit()

            flash(
                "Performance added successfully",
                "success"
            )

            return redirect(url_for(
                'performance',
                **(
                    {'class_id': selected_class.id}
                    if selected_class
                    else {}
                )
            ))

        except Exception as e:
            db.session.rollback()
            print("PERFORMANCE ERROR:", e)

            flash(
                f"Error: {str(e)}",
                "danger"
            )

            return redirect(url_for(
                'performance',
                **(
                    {'class_id': selected_class.id}
                    if selected_class
                    else {}
                )
            ))

    # =========================
    # FETCH PERFORMANCE
    # =========================
    if role == 'admin':

        performances = Performance.query.join(Student).filter(
            Student.class_id == selected_class.id
        ).order_by(
            Performance.date.desc()
        ).all()

    else:

        performances = Performance.query.join(Student).filter(
            Student.class_id.in_(class_ids)
        ).order_by(
            Performance.date.desc()
        ).all()

    records = []

    for p in performances:

        student = Student.query.get(p.student_id)

        if student:
            records.append((p, student))

    # =========================
    # ANALYTICS
    # =========================
    student_names = []
    avg_scores = []
    low_performers = []

    for student in students:

        scores = [(p.class_score + p.exam_score) for p in performances if p.student_id == student.id]

        if scores:

            avg = sum(scores) / len(scores)

            student_names.append(student.name)
            avg_scores.append(round(avg, 2))

            if avg < 40:
                low_performers.append(student.name)

    # =========================
    # GPA
    # =========================
    def calculate_gpa(scores):

        if not scores:
            return 0

        total = 0

        for s in scores:

            if s >= 80:
                total += 4

            elif s >= 70:
                total += 3

            elif s >= 60:
                total += 2

            elif s >= 50:
                total += 1

        return round(total / len(scores), 2)

    # =========================
    # LEADERBOARD
    # =========================
    leaderboard = []

    for student in students:

        scores = [p.total_score for p in performances if p.student_id == student.id]

        if scores:

            leaderboard.append({

                "student": student.name,

                "avg": round(
                    sum(scores) / len(scores),
                    2
                ),

                "gpa": calculate_gpa(scores)

            })

    leaderboard.sort(
        key=lambda x: x["avg"],
        reverse=True
    )

    return render_template(
        'performance.html',
        students=students,
        selected_class=selected_class,
        records=records,
        student_names=student_names,
        avg_scores=avg_scores,
        low_performers=low_performers,
        leaderboard=leaderboard
    )


@app.route('/performance/classes')
@login_required
@role_required('admin')
def performance_classes():
    classes = SchoolClass.query.order_by(SchoolClass.name).all()
    return render_template(
        'performance_classes.html',
        classes=classes
    )


@app.route('/performance/<int:performance_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin')
def edit_performance(performance_id):
    performance_record = Performance.query.get_or_404(performance_id)
    student = Student.query.get_or_404(performance_record.student_id)

    if current_user.role == 'teacher' and student.classroom not in current_user.classes:
        flash("You can only edit records for students in your assigned classes.", "danger")
        return redirect(url_for('performance'))

    if request.method == 'POST':
        subject = (request.form.get('subject') or '').strip()
        term = (request.form.get('term') or '').strip()
        academic_year = (request.form.get('academic_year') or '').strip()

        try:
            class_score = float(request.form.get('class_score', ''))
            exam_score = float(request.form.get('exam_score', ''))
            if (
                not math.isfinite(class_score)
                or not math.isfinite(exam_score)
                or not 0 <= class_score <= 30
                or not 0 <= exam_score <= 70
            ):
                raise ValueError
        except ValueError:
            flash("Class score must be 0–30 and exam score must be 0–70.", "warning")
            return redirect(url_for(
                'edit_performance',
                performance_id=performance_record.id
            ))

        if not subject or not term or not academic_year:
            flash("Subject, term, and academic year are required.", "warning")
            return redirect(url_for('edit_performance', performance_id=performance_record.id))

        performance_record.subject = subject
        performance_record.class_score = class_score
        performance_record.exam_score = exam_score
        performance_record.total_score = class_score + exam_score
        performance_record.term = term
        performance_record.academic_year = academic_year
        db.session.commit()

        flash("Performance record updated successfully.", "success")
        return redirect(url_for(
            'performance',
            **(
                {'class_id': student.class_id}
                if current_user.role == 'admin'
                else {}
            )
        ))

    return render_template(
        'edit_performance.html',
        performance=performance_record,
        student=student,
        back_class_id=(
            student.class_id
            if current_user.role == 'admin'
            else None
        )
    )
    


@app.route('/report_card/<int:student_id>', methods=['GET', 'POST'])
@login_required
def report_card(student_id):

    student = Student.query.get_or_404(student_id)
    class_roll_number = Student.query.filter(
        Student.class_id == student.class_id,
        Student.id <= student.id
    ).count()

    latest_performance = (
        Performance.query
        .filter_by(student_id=student.id)
        .order_by(Performance.id.desc())
        .first()
    )
    latest_fee_schedule = None
    if latest_performance is None:
        latest_fee_schedule = (
            TermFeeSchedule.query
            .filter_by(class_id=student.class_id)
            .order_by(
                TermFeeSchedule.academic_year.desc(),
                TermFeeSchedule.id.desc()
            )
            .first()
        )

    term = (
        request.args.get('term')
        or (
            latest_performance.term
            if latest_performance
            else latest_fee_schedule.term
            if latest_fee_schedule
            else 'Term 1'
        )
    ).strip()
    year = (
        request.args.get('year')
        or (
            latest_performance.academic_year
            if latest_performance
            else latest_fee_schedule.academic_year
            if latest_fee_schedule
            else str(datetime.now().year)
        )
    ).strip()

    performances = Performance.query.filter_by(
        student_id=student.id,
        term=term,
        academic_year=year
    ).all()

    class_name = student.classroom.name if student.classroom else "N/A"
    current_date = datetime.now().strftime("%d/%m/%Y")

    if request.method == 'POST' and request.form.get('action') == 'upload_passport_photo':
        role = (session.get('role') or '').strip().lower()
        if role not in {'admin', 'teacher'}:
            flash("Only teachers and administrators can upload learner photos.", "danger")
            return redirect(url_for(
                'report_card',
                student_id=student.id,
                term=term,
                year=year
            ))

        if role == 'teacher':
            teacher = User.query.get(session.get('user_id'))
            if teacher is None or student.classroom not in teacher.classes:
                flash("You can only upload photos for learners in your assigned classes.", "danger")
                return redirect(url_for(
                    'report_card',
                    student_id=student.id,
                    term=term,
                    year=year
                ))

        uploaded_file = request.files.get('passport_photo')
        try:
            if uploaded_file is None:
                raise ValueError("Choose a passport photo to upload.")
            student.photo = _save_student_passport_photo(uploaded_file.stream)
        except ValueError as error:
            flash(str(error), "warning")
            return redirect(url_for(
                'report_card',
                student_id=student.id,
                term=term,
                year=year
            ))

        db.session.commit()
        flash("Learner passport photo uploaded successfully.", "success")
        return redirect(url_for(
            'report_card',
            student_id=student.id,
            term=term,
            year=year
        ))

    # =========================
    # SUBJECT RESULTS
    # =========================
    subject_results = []

    for p in performances:

        class_score = p.class_score or 0
        exam_score = p.exam_score or 0
        total = p.total_score or (class_score + exam_score)

        grade, remark = _grade_and_remark(total)

        # =========================
        # SUBJECT POSITION
        # =========================
        subject_scores = Performance.query.filter_by(
            subject=p.subject,
            term=term,
            academic_year=year
        ).all()

        ranking = sorted(
            [s.total_score for s in subject_scores],
            reverse=True
        )

        subject_position = ranking.index(total) + 1 if total in ranking else "-"

        subject_results.append({
            "subject": p.subject,
            "class_score": class_score,
            "exam_score": exam_score,
            "total_score": total,
            "grade": grade,
            "remark": remark,
            "position": subject_position
        })

    # =========================
    # TOTALS
    # =========================
    scores = [r["total_score"] for r in subject_results]

    total_score = sum(scores)
    average = round(total_score / len(scores), 2) if scores else 0
    average_remark = _grade_and_remark(average)[1]

    # =========================
    # CLASS POSITION
    # =========================
    class_students = Student.query.filter_by(
        class_id=student.class_id
    ).all()

    student_averages = []

    for s in class_students:

        records = Performance.query.filter_by(
            student_id=s.id,
            term=term,
            academic_year=year
        ).all()

        totals = [r.total_score for r in records]
        avg = sum(totals) / len(totals) if totals else 0

        student_averages.append({
            "id": s.id,
            "avg": avg
        })

    student_averages.sort(key=lambda x: x["avg"], reverse=True)

    position = next(
        (i + 1 for i, item in enumerate(student_averages) if item["id"] == student.id),
        "-"
    )

    # =========================
    # GPA
    # =========================
    def calculate_gpa(scores):

        if not scores:
            return 0

        total_points = 0

        for s in scores:
            if s >= 80:
                total_points += 4
            elif s >= 70:
                total_points += 3
            elif s >= 60:
                total_points += 2
            elif s >= 50:
                total_points += 1

        return round(total_points / len(scores), 2)

    gpa = calculate_gpa(scores)

    # =========================
    # ATTENDANCE (FIXED DEFAULT)
    # =========================
    total_attendance = Attendance.query.filter_by(
        student_id=student.id
    ).count()

    present_attendance = Attendance.query.filter_by(
        student_id=student.id,
        status="Present"
    ).count()

    absent_attendance = Attendance.query.filter_by(
        student_id=student.id,
        status="Absent"
    ).count()

    # =========================
    # LEARNER'S BILL
    # =========================
    bill_term, bill_year = _next_academic_period(term, year)
    tuition_fee, sports_fee, library_fee, ict_fee = _term_fee_amounts(
        TermFeeSchedule,
        FeeStructure,
        student,
        bill_term,
        bill_year
    )
    arrears = _invoice_arrears(Invoice, student)
    total_bill = tuition_fee + sports_fee + library_fee + ict_fee + arrears

    # =========================
    # PROMOTION
    # =========================
    if average >= 80:
        promotion = "Excellent (A)"
    elif average >= 70:
        promotion = "Very Good (B)"
    elif average >= 60:
        promotion = "Good (C)"
    elif average >= 50:
        promotion = "Pass (D)"
    elif average >= 40:
        promotion = "Weak Pass (E)"
    else:
        promotion = "Fail (F)"

    school_classes = SchoolClass.query.filter_by(
        school_id=student.school_id
    ).order_by(SchoolClass.id.asc()).all()
    promoted_to = _promotion_target(
        class_name,
        average,
        bool(subject_results),
        [school_class.name for school_class in school_classes],
        term=term
    )
    promotion_label, promotion_value = _promotion_display(
        promoted_to
    )
    saved_report_card = ReportCard.query.filter_by(
        student_id=student.id,
        term=term,
        academic_year=year
    ).order_by(ReportCard.id.desc()).first()
    if (
        saved_report_card is not None
        and saved_report_card.promoted_to != promoted_to
    ):
        saved_report_card.promoted_to = promoted_to
        db.session.commit()

    if request.method == 'POST':
        next_term_begins = (request.form.get('next_term_begins') or '').strip()
        if not next_term_begins:
            flash("Enter the date the next term begins.", "warning")
            return redirect(url_for(
                'report_card',
                student_id=student.id,
                term=term,
                year=year
            ))
        try:
            datetime.strptime(next_term_begins, "%Y-%m-%d")
        except ValueError:
            flash("Enter a valid next-term start date.", "warning")
            return redirect(url_for(
                'report_card',
                student_id=student.id,
                term=term,
                year=year
            ))

        if saved_report_card is None:
            saved_report_card = ReportCard(
                student_id=student.id,
                term=term,
                academic_year=year
            )
            db.session.add(saved_report_card)

        saved_report_card.next_term_begins = next_term_begins
        saved_report_card.promoted_to = promoted_to
        db.session.commit()
        flash("Next-term date saved.", "success")
        return redirect(url_for(
            'report_card',
            student_id=student.id,
            term=term,
            year=year
        ))

    next_term_begins = (
        saved_report_card.next_term_begins
        if saved_report_card and saved_report_card.next_term_begins
        else ''
    )
    if next_term_begins:
        try:
            next_term_begins_display = datetime.strptime(
                next_term_begins,
                "%Y-%m-%d"
            ).strftime("%d %B %Y")
        except ValueError:
            next_term_begins_display = next_term_begins
    else:
        next_term_begins_display = "Date not set"

    return render_template(
        'report_card.html',

        student=student,
        class_roll_number=class_roll_number,
        school_logo_url=(
            url_for(
                'static',
                filename=app.config["REPORT_LOGO_PATH"]
            )
            if os.path.isfile(
                os.path.join(
                    app.static_folder,
                    app.config["REPORT_LOGO_PATH"]
                )
            )
            else None
        ),
        student_photo_url=(
            url_for('static', filename=student.photo)
            if student.photo
            else None
        ),
        class_name=class_name,

        subject_results=subject_results,

        total_score=total_score,
        average=average,
        average_remark=average_remark,
        gpa=gpa,

        term=term,
        year=year,

        promotion=promotion,
        promotion_label=promotion_label,
        promoted_to=promotion_value,
        next_term_begins=next_term_begins_display,
        next_term_begins_input=next_term_begins,
        position=position,

        current_date=current_date,

        # NEW VARIABLES (IMPORTANT FOR YOUR HTML)
        attendance=present_attendance,
        total_attendance=total_attendance,
        absent_attendance=absent_attendance,
        tuition_fee=tuition_fee,
        sports_fee=sports_fee,
        library_fee=library_fee,
        ict_fee=ict_fee,
        arrears=arrears,
        bill_term=bill_term,
        bill_year=bill_year,
        total_bill=total_bill
    )


    
@app.route('/download_report/<int:student_id>')
@login_required
def download_report(student_id):

    student = Student.query.get_or_404(student_id)

    term = request.args.get('term', 'Term 1')
    year = request.args.get('year', '2025/2026')

    # =========================
    # PERFORMANCE QUERY
    # =========================
    performances = Performance.query.filter_by(
        student_id=student_id,
        term=term,
        academic_year=year
    ).all()

    # =========================
    # CALCULATIONS
    # =========================
    scores = [p.total_score for p in performances]

    total_score = sum(scores)

    average = round(
        total_score / len(scores),
        2
    ) if scores else 0

    def get_gpa(scores):

        if not scores:
            return 0

        total = 0

        for s in scores:

            if s >= 80:
                total += 4

            elif s >= 70:
                total += 3

            elif s >= 60:
                total += 2

            elif s >= 50:
                total += 1

        return round(total / len(scores), 2)

    gpa = get_gpa(scores)

    # =========================
    # CLASS POSITION
    # =========================
    classmates = Student.query.filter_by(
        class_id=student.class_id
    ).all()

    ranking = []

    for s in classmates:

        results = Performance.query.filter_by(
            student_id=s.id,
            term=term,
            academic_year=year
        ).all()

        avg = 0

        if results:
            avg = sum(
                r.total_score for r in results
            ) / len(results)

        ranking.append({
            "student_id": s.id,
            "avg": avg
        })

    ranking.sort(
        key=lambda x: x["avg"],
        reverse=True
    )

    position = "-"

    for pos, item in enumerate(ranking, start=1):

        if item["student_id"] == student.id:
            position = pos
            break

    # =========================
    # PDF FILE
    # =========================
    file_path = f"static/report_{student.id}.pdf"

    doc = SimpleDocTemplate(file_path)

    styles = getSampleStyleSheet()

    # =========================
    # TABLE DATA
    # =========================
  
    table_data = [[
        "S/N",
        "Subject",
        "Class Score (30%)",
        "Exam Score (70%)",
        "Total Score (100%)",
        "Grade",
        "Remark",
        "Subject Pos."
    ]]

    for i, p in enumerate(performances, start=1):

        subject_pos = Performance.subject_position(
            p.subject,
            student.id
        )

        table_data.append([
            str(i),
            p.subject,
            str(p.class_score),
            str(p.exam_score),
            str(p.total_score),
            p.grade,
            p.remark,
            str(subject_pos)
        ])

    # =========================
    # TABLE
    # =========================
    table = Table(
    table_data,
    colWidths=[
            30,   # SN
            90,   # Subject
            65,   # Class Score
            65,   # Exam Score
            65,   # Total
            45,   # Grade
            70,   # Remark
            50    # Subject Position
        ]
    )

    table.setStyle(TableStyle([

        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),

        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),

        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),

        ('GRID', (0, 0), (-1, -1), 1, colors.black),

        ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke)

    ]))

    # =========================
    # CLASS NAME
    # =========================
    class_obj = SchoolClass.query.get(
        student.class_id
    )

    # =========================
    # PDF CONTENT
    # =========================
    content = [

        Paragraph(
            "SENIOR VICTORY COLLEGE",
            styles['Title']
        ),

        Paragraph(
            "OFFICIAL STUDENT REPORT CARD",
            styles['Heading2']
        ),

        Spacer(1, 12),

        Paragraph(
            f"<b>Student:</b> {student.name}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>Class:</b> {class_obj.name if class_obj else 'N/A'}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>Term:</b> {term}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>Academic Year:</b> {year}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>Position:</b> {position}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>Date:</b> {datetime.now().strftime('%d-%m-%Y')}",
            styles['Normal']
        ),

        Spacer(1, 15),

        table,

        Spacer(1, 15),

        Paragraph(
            f"<b>Total Score:</b> {total_score}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>Average Score:</b> {average}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>GPA:</b> {gpa}",
            styles['Normal']
        ),

        Paragraph(
            f"<b>Class Position:</b> {position}",
            styles['Normal']
        ),


        Spacer(1, 20),

        Paragraph(
            "<b>Class Teacher Signature:</b> ____________________",
            styles['Normal']
        ),

        Spacer(1, 15),

        Paragraph(
            "<b>Headmaster Signature:</b> ____________________",
            styles['Normal']
        )
    ]

    # =========================
    # BUILD PDF
    # =========================
    doc.build(content)

    return send_file(
        file_path,
        as_attachment=True,
        download_name=f"{student.name}_report.pdf"
    ) 
    
@app.route('/leaderboard')
@login_required
def leaderboard():

    students_query = Student.query
    if current_user.role == 'headmaster':
        school_id = _headmaster_school_id()
        if school_id is None:
            return redirect(url_for('login'))
        students_query = students_query.filter_by(school_id=school_id)
    students = students_query.all()
    data = []

    for student in students:
        performances = Performance.query.filter_by(student_id=student.id).all()
        scores = [p.score for p in performances]

        avg = sum(scores)/len(scores) if scores else 0
        gpa = get_gpa(scores)

        data.append({
            "name": student.name,
            "class": student.classroom.name if student.classroom else "N/A",
            "avg": round(avg,2),
            "gpa": gpa
        })

    # SORT BY GPA
    data = sorted(data, key=lambda x: x['gpa'], reverse=True)

    return render_template("leaderboard.html", data=data)    

    return redirect(f"/{file_path}")

# =========================================================
# 👨‍🎓 STUDENTS ROUTE
# =========================================================
@app.route('/students', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'teacher')
def students():

    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    classes = SchoolClass.query.filter_by(
        school_id=current_user.school_id
    ).all()

    role = session.get('role')

    # =====================================================
    # ➕ ADD STUDENT (ADMIN ONLY)
    # =====================================================
    if request.method == 'POST':

        # 🚨 SECURITY CHECK
        if role != 'admin':
            flash("❌ Only admin can add students", "danger")
            return redirect(url_for('students'))

        name = request.form.get('name')
        class_id = request.form.get('class_id', type=int)
        phone = request.form.get('phone')
        parent_phone = request.form.get('parent_phone')
        gender = (request.form.get('gender') or '').strip().title()

        # =================================================
        # VALIDATION
        # =================================================
        classroom = SchoolClass.query.filter_by(
            id=class_id,
            school_id=current_user.school_id
        ).first() if class_id else None
        if not name or classroom is None or gender not in {'Male', 'Female'}:
            flash("Fill all required fields ❌", "warning")
            return redirect(url_for('students'))

        # =================================================
        # FORMAT PHONE
        # =================================================
        formatted_phone = format_ghana_phone(phone)
        formatted_parent_phone = format_ghana_phone(parent_phone)

        if phone and not formatted_phone:
            flash("Invalid Ghana phone number ❌", "danger")
            return redirect(url_for('students'))
        if parent_phone and not formatted_parent_phone:
            flash("Invalid parent/guardian Ghana phone number ❌", "danger")
            return redirect(url_for('students'))

        # =================================================
        # CREATE STUDENT
        # =================================================
        new_student = Student(
            name=name,
            gender=gender,
            class_id=class_id,
            phone=formatted_phone,
            parent_phone=formatted_parent_phone,
            created_at=datetime.now(),
            school_id=current_user.school_id
        )

        db.session.add(new_student)
        db.session.commit()

        flash("Student added successfully ✅", "success")
        if new_student.parent_phone:
            school_name = (
                new_student.school.name
                if new_student.school
                else "School"
            )
            sent, reason = _notify_parent(
                new_student,
                f"{school_name}: {new_student.name} has been registered "
                "as a student. Please contact the school if you have questions."
            )
            if sent:
                flash("Registration SMS sent to the parent/guardian.", "success")
            else:
                flash(
                    "Student was registered, but the parent/guardian SMS "
                    f"could not be sent ({reason.replace('_', ' ')}).",
                    "warning"
                )
        else:
            flash(
                "Add a parent/guardian phone number to enable SMS notifications.",
                "info"
            )
        return redirect(url_for('students'))

    # =====================================================
    # 🔍 FILTER STUDENTS BY CLASS
    # =====================================================
    selected_class = request.args.get('class_id')

    query = Student.query.filter_by(school_id=current_user.school_id)

    if selected_class:
        query = query.filter(Student.class_id == selected_class)

    students = query.order_by(Student.name.asc()).all()

    return render_template(
        'students.html',
        students=students,
        classes=classes,
        selected_class=selected_class
    )

@app.route('/student_profile/<int:student_id>')
@login_required
def student_profile(student_id):

    student = Student.query.get_or_404(student_id)

    performances = Performance.query.filter_by(student_id=student_id).all()
    attendance = Attendance.query.filter_by(student_id=student_id).all()

    scores = [p.score for p in performances]
    subjects = [p.subject for p in performances]

    present_days = sum(1 for a in attendance if a.status == "Present")
    total_days = len(attendance)

    attendance_rate = (present_days / total_days * 100) if total_days else 0

    return {
        "name": student.name,
        "subjects": subjects,
        "scores": scores,
        "attendance": round(attendance_rate, 2)
    }

@app.route('/attendance_data/<int:student_id>')
def attendance_data(student_id):

    records = Attendance.query.filter_by(student_id=student_id).all()

    data = {}
    for r in records:
        data[str(r.date)] = r.status

    return data

@app.route('/classes', methods=['GET', 'POST'])
@login_required
def manage_classes():
    if current_user.role == 'headmaster':
        return redirect(url_for('headmaster_dashboard'))

    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()

        if name:
            exists = SchoolClass.query.filter_by(
                name=name,
                school_id=current_user.school_id
            ).first()
            if exists:
                flash("Class already exists", "danger")
            else:
                db.session.add(SchoolClass(
                    name=name,
                    school_id=current_user.school_id
                ))
                db.session.commit()
                flash("Class added successfully", "success")

        return redirect(url_for('manage_classes'))

    classes = SchoolClass.query.filter_by(
        school_id=current_user.school_id
    ).all()

    return render_template('classes.html', classes=classes)

@app.route('/delete_class/<int:id>')
@login_required
def delete_class(id):
    if current_user.role == 'headmaster':
        return redirect(url_for('headmaster_dashboard'))

    cls = SchoolClass.query.get_or_404(id)

    if cls.students:
        flash("Cannot delete class with students!", "danger")
        return redirect(url_for('manage_classes'))

    db.session.delete(cls)
    db.session.commit()

    flash("Class deleted", "success")
    return redirect(url_for('manage_classes'))

@app.route('/edit_class/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_class(id):
    if current_user.role == 'headmaster':
        return redirect(url_for('headmaster_dashboard'))

    cls = SchoolClass.query.get_or_404(id)

    if request.method == 'POST':
        new_name = request.form.get('name')

        if new_name:
            cls.name = new_name
            db.session.commit()
            flash("Class updated", "success")
            return redirect(url_for('manage_classes'))

    return render_template('edit_class.html', cls=cls)

@app.route('/delete_student/<int:id>')
@login_required
def delete_student(id):
    if current_user.role == 'headmaster':
        return redirect(url_for('headmaster_dashboard'))

    student = Student.query.get_or_404(id)
    AdmissionApplication.query.filter_by(
        student_id=student.id
    ).update(
        {AdmissionApplication.student_id: None},
        synchronize_session=False
    )
    db.session.delete(student)
    db.session.commit()
    flash(
        "Student deleted. The admission application was retained as history.",
        "success"
    )
    return redirect(url_for('students'))

@app.route('/edit_student/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_student(id):
    if current_user.role == 'headmaster':
        return redirect(url_for('headmaster_dashboard'))

    student = Student.query.get_or_404(id)

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        class_id = request.form.get('class_id')
        gender = (request.form.get('gender') or '').strip().title()
        phone = request.form.get('phone')
        parent_phone = request.form.get('parent_phone')
        formatted_phone = format_ghana_phone(phone)
        formatted_parent_phone = format_ghana_phone(parent_phone)
        if not name or not class_id or gender not in {'Male', 'Female'}:
            flash("Enter a name, class, and valid gender.", "warning")
            return redirect(url_for('edit_student', id=student.id))
        if phone and not formatted_phone:
            flash("Invalid Ghana phone number.", "warning")
            return redirect(url_for('edit_student', id=student.id))
        if parent_phone and not formatted_parent_phone:
            flash("Invalid parent/guardian Ghana phone number.", "warning")
            return redirect(url_for('edit_student', id=student.id))

        student.name = name
        student.class_id = int(class_id)
        student.gender = gender
        student.phone = formatted_phone
        student.parent_phone = formatted_parent_phone
        db.session.commit()
        flash("Student details updated.", "success")
        return redirect(url_for('students'))

    return render_template(
        'Edit_student.html',
        student=student,
        classes=SchoolClass.query.order_by(SchoolClass.name).all()
    )

# =========================
# ATTENDANCE CLASS LIST
# =========================
@app.route('/attendance_classes')
@login_required
def attendance_classes():

    if current_user.role == 'teacher':
        classes = [
            classroom for classroom in current_user.classes
            if classroom.school_id == current_user.school_id
        ]
    elif current_user.role == 'admin':
        classes = SchoolClass.query.filter_by(
            school_id=current_user.school_id
        ).all()
    else:
        flash("Only teachers and administrators can access attendance.", "danger")
        return redirect(url_for('login'))

    return render_template(
        'attendance_classes.html',
        classes=classes
    )

# =========================
# ATTENDANCE
# =========================
@app.route('/attendance/<int:class_id>', methods=['GET', 'POST'])
@login_required
def attendance(class_id):
    if current_user.role not in {"admin", "teacher"}:
        flash("Only teachers and administrators can access attendance.", "danger")
        return redirect(url_for('login'))

    if current_user.school_id is None:
        flash("Your school has not been assigned yet. Please contact an administrator.", "warning")
        return redirect(url_for('login'))

    classroom = SchoolClass.query.filter_by(
        id=class_id,
        school_id=current_user.school_id
    ).first_or_404()

    if current_user.role == "teacher" and current_user not in classroom.teachers:
        flash("Access denied")
        return redirect(url_for("teacher_dashboard"))

    if request.method == "GET" and current_user.role == "admin":
        selected_class_id = request.args.get("class_id", type=int)
        if selected_class_id and selected_class_id != class_id:
            selected_class = SchoolClass.query.filter_by(
                id=selected_class_id,
                school_id=current_user.school_id
            ).first_or_404()
            return redirect(url_for(
                "attendance",
                class_id=selected_class.id,
                attendance_date=request.args.get("attendance_date", "today"),
                term=request.args.get("term", ""),
                academic_year=request.args.get("academic_year", "")
            ))

    students = Student.query.filter_by(
        class_id=class_id,
        school_id=current_user.school_id
    ).all()

    if request.method == 'POST':
        term = (request.form.get("term") or "").strip()
        academic_year = (request.form.get("academic_year") or "").strip()
        if term not in {"Term 1", "Term 2", "Term 3"}:
            flash("Select a valid term before saving attendance.", "warning")
            return redirect(url_for('attendance', class_id=class_id))
        if not re.fullmatch(r'\d{4}(?:[/\-]\d{2,4})?', academic_year):
            flash("Enter a valid academic year, for example 2026/2027.", "warning")
            return redirect(url_for(
                'attendance',
                class_id=class_id,
                term=term
            ))

        today = datetime.now().date()

        for student in students:
            status = request.form.get(
                f"status_{student.id}"
            )
            if status not in {"Present", "Absent"}:
                flash("Select Present or Absent for every student.", "warning")
                return redirect(url_for(
                    'attendance',
                    class_id=class_id,
                    term=term,
                    academic_year=academic_year
                ))

            existing = Attendance.query.filter_by(
                student_id=student.id,
                date=today,
                term=term,
                academic_year=academic_year
            ).first()

            if existing:

                # UPDATE EXISTING RECORD
                existing.status = status
                existing.term = term
                existing.academic_year = academic_year
                existing.school_id = current_user.school_id

            else:

                # CREATE NEW RECORD
                attendance = Attendance(
                    student_id=student.id,
                    status=status,
                    date=today,
                    term=term,
                    academic_year=academic_year,
                    school_id=current_user.school_id
                )

                db.session.add(attendance)

        db.session.commit()

        flash("Daily attendance saved successfully")

        return redirect(
            url_for(
                'attendance',
                class_id=class_id,
                attendance_date="today",
                term=term,
                academic_year=academic_year
            )
        )

    attendance_date = (request.args.get("attendance_date") or "today").strip()
    active_period = AcademicPeriodSetting.query.filter_by(
        school_id=current_user.school_id
    ).first()
    selected_term = (request.args.get("term") or "").strip()
    selected_year = (request.args.get("academic_year") or "").strip()
    if not selected_term and active_period:
        selected_term = active_period.term
    if not selected_year and active_period:
        selected_year = active_period.academic_year

    year_query = db.session.query(
        Attendance.academic_year
    ).join(
        Student, Student.id == Attendance.student_id
    ).filter(
        Student.school_id == current_user.school_id,
        Student.class_id == class_id,
        Attendance.academic_year.isnot(None),
        Attendance.academic_year != ""
    ).distinct().all()
    academic_years = sorted(
        {row[0] for row in year_query},
        reverse=True
    )
    if selected_year and selected_year not in academic_years:
        academic_years.append(selected_year)
        academic_years.sort(reverse=True)

    record_query = db.session.query(
        Attendance,
        Student
    ).join(
        Student,
        Attendance.student_id == Student.id
    ).filter(
        Student.class_id == class_id,
        Student.school_id == current_user.school_id
    )
    if attendance_date == "today":
        record_query = record_query.filter(
            Attendance.date == datetime.now().date()
        )
    elif attendance_date != "all":
        try:
            selected_date = date.fromisoformat(attendance_date)
        except ValueError:
            flash("Select a valid attendance date.", "warning")
            attendance_date = "today"
            record_query = record_query.filter(
                Attendance.date == datetime.now().date()
            )
        else:
            record_query = record_query.filter(
                Attendance.date == selected_date
            )
    if selected_term:
        record_query = record_query.filter(Attendance.term == selected_term)
    if selected_year:
        record_query = record_query.filter(
            Attendance.academic_year == selected_year
        )
    records = record_query.order_by(
        Attendance.date.desc()
    ).all()

    selected_date = (
        datetime.now().date()
        if attendance_date == "today"
        else date.fromisoformat(attendance_date)
        if attendance_date != "all"
        else None
    )
    daily_present = 0
    daily_absent = 0
    if selected_date:
        daily_query = Attendance.query.join(
            Student, Student.id == Attendance.student_id
        ).filter(
            Student.school_id == current_user.school_id,
            Student.class_id == class_id,
            Attendance.date == selected_date
        )
        if selected_term:
            daily_query = daily_query.filter(Attendance.term == selected_term)
        if selected_year:
            daily_query = daily_query.filter(
                Attendance.academic_year == selected_year
            )
        daily_present = daily_query.filter(
            Attendance.status == "Present"
        ).count()
        daily_absent = daily_query.filter(
            Attendance.status == "Absent"
        ).count()

    term_summaries = []
    for term_name in ("Term 1", "Term 2", "Term 3"):
        term_query = Attendance.query.join(
            Student, Student.id == Attendance.student_id
        ).filter(
            Student.school_id == current_user.school_id,
            Student.class_id == class_id,
            Attendance.term == term_name
        )
        if selected_year:
            term_query = term_query.filter(
                Attendance.academic_year == selected_year
            )
        term_summaries.append({
            "term": term_name,
            "present": term_query.filter(
                Attendance.status == "Present"
            ).count(),
            "absent": term_query.filter(
                Attendance.status == "Absent"
            ).count()
        })

    stats = []

    for student in students:
        student_attendance = Attendance.query.filter_by(
            student_id=student.id
        )
        if selected_term:
            student_attendance = student_attendance.filter(
                Attendance.term == selected_term
            )
        if selected_year:
            student_attendance = student_attendance.filter(
                Attendance.academic_year == selected_year
            )
        total = student_attendance.count()
        present = student_attendance.filter(
            Attendance.status == 'Present'
        ).count()

        percentage = 0

        if total > 0:
            percentage = round(
                (present / total) * 100,
                2
            )

        stats.append({
            "name": student.name,
            "percentage": percentage
        })

    return render_template(
        'attendance.html',
        classroom=classroom,
        classes=(
            SchoolClass.query.filter_by(
                school_id=current_user.school_id
            ).order_by(SchoolClass.name).all()
            if current_user.role == "admin"
            else []
        ),
        attendance_date=attendance_date,
        selected_date=selected_date,
        selected_term=selected_term,
        selected_year=selected_year,
        academic_years=academic_years,
        daily_present=daily_present,
        daily_absent=daily_absent,
        term_summaries=term_summaries,
        students=students,
        records=records,
        stats=stats,
        today=datetime.now().date()
    )
# =========================
# INVOICES
# =========================
@app.route('/invoices', methods=['GET', 'POST'])
@login_required
@role_required('accountant', 'admin')
def invoices():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    students = Student.query.filter_by(
        school_id=current_user.school_id
    ).order_by(Student.name).all()

    if request.method == 'POST':
        student_id = request.form.get('student_id', type=int)
        term = (request.form.get('term') or '').strip()
        academic_year = (request.form.get('academic_year') or '').strip()

        if not student_id or not term or not academic_year:
            flash("Select a student, term, and academic year.", "warning")
            return redirect(url_for('invoices'))

        student = Student.query.filter_by(
            id=student_id,
            school_id=current_user.school_id
        ).first()

        if not student:
            flash("Student not found", "danger")
            return redirect(url_for('invoices'))

        schedule = TermFeeSchedule.query.filter_by(
            class_id=student.class_id,
            term=term,
            academic_year=academic_year
        ).first()

        if schedule is None:
            flash(
                f"No fee schedule is set for {student.classroom.name}, {term} ({academic_year}).",
                "warning"
            )
            return redirect(url_for('invoices'))

        existing_invoice = Invoice.query.filter_by(
            student_id=student.id,
            term=term,
            academic_year=academic_year
        ).first()

        if existing_invoice:
            flash("An invoice already exists for this learner and period.", "warning")
            return redirect(url_for('invoices'))

        fee_items = {
            "tuition_fee": float(schedule.tuition_fee or 0),
            "sports_fee": float(schedule.sports_fee or 0),
            "library_fee": float(schedule.library_fee or 0),
            "ict_fee": float(schedule.ict_fee or 0),
        }
        invoice_total = round(sum(fee_items.values()), 2)
        if invoice_total <= 0:
            flash("The selected term fee schedule has no charges to invoice.", "warning")
            return redirect(url_for('invoices'))

        new_invoice = Invoice(
            student_id=student.id,
            amount=invoice_total,
            status='Pending',
            term=term,
            academic_year=academic_year,
            school_id=current_user.school_id,
            **fee_items
        )

        db.session.add(new_invoice)
        db.session.commit()

        flash(
            f"Invoice generated for {student.name}",
            "success"
        )

        return redirect(url_for('invoices'))

    # =========================
    # FILTERS
    # =========================
    selected_class = request.args.get('class_id')
    selected_status = request.args.get('status')

    classes = SchoolClass.query.filter_by(
        school_id=current_user.school_id
    ).all()

    query = Invoice.query.join(Student)
    query = query.filter(Student.school_id == current_user.school_id)

    if selected_class:
        query = query.filter(
            Student.class_id == selected_class
        )

    if selected_status:
        query = query.filter(
            Invoice.status == selected_status
        )

    invoices = query.order_by(
        Invoice.id.desc()
    ).all()

    students = Student.query.filter_by(
        school_id=current_user.school_id
    ).order_by(Student.name).all()
    selected_student_id = request.args.get("student_id", type=int)

    return render_template(
        'invoices.html',
        invoices=invoices,
        students=students,
        classes=classes,
        selected_student_id=selected_student_id,
        selected_class=selected_class,
        selected_status=selected_status
    )


@app.route('/payment-system')
@login_required
@role_required('accountant', 'admin')
def payment_system():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    school_id = current_user.school_id
    unpaid_invoices_query = Invoice.query.join(
        Student,
        Invoice.student_id == Student.id
    ).filter(
        Student.school_id == school_id,
        Invoice.amount > func.coalesce(Invoice.partial_paid, 0)
    )
    total_outstanding = float(
        db.session.query(
            func.sum(
                Invoice.amount - func.coalesce(Invoice.partial_paid, 0)
            )
        )
        .join(Student, Invoice.student_id == Student.id)
        .filter(
            Student.school_id == school_id,
            Invoice.amount > func.coalesce(Invoice.partial_paid, 0)
        )
        .scalar() or 0
    )

    student_search = (request.args.get('q') or '').strip()
    if student_search:
        search_pattern = f"%{student_search}%"
        unpaid_invoices_query = unpaid_invoices_query.filter(
            or_(
                Student.name.ilike(search_pattern),
                Student.admission_no.ilike(search_pattern),
                Student.phone.ilike(search_pattern)
            )
        )
    unpaid_invoices = unpaid_invoices_query.order_by(
        Invoice.date_created.asc(),
        Invoice.id.asc()
    ).all()
    recent_payments = Payment.query.join(
        Student,
        Payment.student_id == Student.id
    ).order_by(
        Payment.payment_date.desc(),
        Payment.id.desc()
    ).filter(
        Student.school_id == school_id
    ).all()
    total_fees = float(
        db.session.query(func.sum(Invoice.amount))
        .join(Student, Invoice.student_id == Student.id)
        .filter(Student.school_id == school_id)
        .scalar() or 0
    )
    total_received = float(
        db.session.query(func.sum(Payment.amount_paid))
        .join(Student, Payment.student_id == Student.id)
        .filter(Student.school_id == school_id)
        .scalar() or 0
    )
    total_expenses = float(
        db.session.query(func.sum(Expense.amount))
        .filter(Expense.school_id == school_id)
        .scalar() or 0
    )
    profit = total_received - total_expenses

    return render_template(
        'payment_system.html',
        unpaid_invoices=unpaid_invoices,
        student_search=student_search,
        recent_payments=recent_payments,
        total_fees=total_fees,
        total_received=total_received,
        total_outstanding=total_outstanding,
        total_expenses=total_expenses,
        profit=profit
    )


def _record_invoice_payment(invoice, amount, method):
    """Record one payment transaction and keep its invoice balance in sync."""
    remaining = max(
        float(invoice.amount or 0) - float(invoice.partial_paid or 0),
        0
    )
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Enter a payment amount greater than zero.")
    if amount > remaining:
        raise ValueError("Payment amount cannot exceed the invoice balance.")
    if method not in {"Cash", "Momo", "Bank"}:
        raise ValueError("Select a valid payment method.")

    payment = Payment(
        student_id=invoice.student_id,
        invoice_id=invoice.id,
        amount_paid=amount,
        method=method
    )
    invoice.partial_paid = float(invoice.partial_paid or 0) + amount
    invoice.status = (
        'Paid'
        if invoice.partial_paid >= float(invoice.amount or 0)
        else 'Partial'
    )
    db.session.add(payment)
    db.session.commit()

    student = invoice.student
    if student is None:
        flash(
            "Payment was saved, but no student was available for the parent SMS.",
            "warning"
        )
        return

    school_name = student.school.name if student.school else "School"
    message = (
        f"{school_name}: GHS {amount:.2f} payment received for "
        f"{student.name}. Term {invoice.term or 'N/A'}, "
        f"{invoice.academic_year or 'N/A'}. Remaining balance: "
        f"GHS {max(float(invoice.amount or 0) - float(invoice.partial_paid or 0), 0):.2f}."
    )
    sent, reason = _notify_parent(student, message)
    if not sent:
        flash(
            "Payment was saved, but the parent/guardian SMS could not be sent "
            f"({reason.replace('_', ' ')}).",
            "warning"
        )


@app.route(
    '/pay_school_fee/<int:student_id>',
    methods=['POST']
)
@login_required
@role_required('accountant', 'admin')
def pay_school_fee(student_id):

    student = Student.query.get_or_404(student_id)
    invoice = Invoice.query.filter(
        Invoice.student_id == student.id,
        Invoice.amount > func.coalesce(Invoice.partial_paid, 0)
    ).order_by(
        Invoice.date_created.asc(),
        Invoice.id.asc()
    ).first()
    if invoice is None:
        flash("This learner has no outstanding invoice to pay.", "warning")
        return redirect(url_for('payment_system'))

    try:
        amount = float(request.form.get('amount', ''))
        method = request.form.get('method', 'Cash')
        _record_invoice_payment(invoice, amount, method)
    except ValueError as error:
        flash(str(error), "warning")
        return redirect(url_for('payment_system'))

    flash("Payment recorded successfully.", "success")
    return redirect(url_for('payment_system'))

# =========================
# DOWNLOAD PDF
# =========================
@app.route('/download_invoice/<int:id>')
@login_required
def download_invoice(id):
    invoice = Invoice.query.get_or_404(id)

    file_path = f"static/invoice_{id}.pdf"
    doc = SimpleDocTemplate(file_path)
    styles = getSampleStyleSheet()

    content = [
        Paragraph(f"Invoice ID: {invoice.id}", styles['Normal']),
        Paragraph(f"Student ID: {invoice.student_id}", styles['Normal']),
        Paragraph(f"Amount: {invoice.amount}", styles['Normal']),
    ]

    doc.build(content)

    return redirect(f"/{file_path}")


@app.route('/finance_dashboard')
@login_required
def finance_dashboard():

    invoices = Invoice.query.filter_by(status="Paid").all()

    monthly_data = {}

    for inv in invoices:
        month = inv.date_created.strftime("%Y-%m")
        monthly_data[month] = monthly_data.get(month, 0) + inv.amount

    return render_template(
        "finance_dashboard.html",
        labels=list(monthly_data.keys()),
        values=list(monthly_data.values())
    )

@app.route('/finance')
@login_required
@role_required('accountant', 'admin')
def finance():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    school_id = current_user.school_id
    selected_class = request.args.get('class_id', type=int)
    selected_year = (request.args.get('academic_year') or '').strip()
    selected_term = (request.args.get('term') or '').strip()
    selected_method = (request.args.get('payment_method') or '').strip()
    start_value = (request.args.get('start_date') or '').strip()
    end_value = (request.args.get('end_date') or '').strip()

    try:
        start_date = date.fromisoformat(start_value) if start_value else None
        end_date = date.fromisoformat(end_value) if end_value else None
    except ValueError:
        flash("Enter valid start and end dates.", "warning")
        return redirect(url_for('finance'))
    if start_date and end_date and start_date > end_date:
        flash("The start date must be on or before the end date.", "warning")
        return redirect(url_for('finance'))
    if selected_method and selected_method not in {"Cash", "Momo", "Bank"}:
        flash("Select a valid payment method.", "warning")
        return redirect(url_for('finance'))

    start_datetime = (
        datetime.combine(start_date, datetime.min.time())
        if start_date else None
    )
    end_datetime = (
        datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        if end_date else None
    )

    def apply_date_range(query, column):
        if start_datetime:
            query = query.filter(column >= start_datetime)
        if end_datetime:
            query = query.filter(column < end_datetime)
        return query

    classes = SchoolClass.query.filter_by(
        school_id=school_id
    ).order_by(SchoolClass.name).all()
    students_query = Student.query.filter_by(school_id=school_id)
    if selected_class:
        students_query = students_query.filter_by(class_id=selected_class)
    students = students_query.order_by(Student.name).all()

    years_from_invoices = db.session.query(
        Invoice.academic_year
    ).join(
        Student, Student.id == Invoice.student_id
    ).filter(
        Student.school_id == school_id,
        Invoice.academic_year.isnot(None),
        Invoice.academic_year != ''
    ).distinct().all()
    years_from_schedules = db.session.query(
        TermFeeSchedule.academic_year
    ).join(
        SchoolClass, SchoolClass.id == TermFeeSchedule.class_id
    ).filter(
        SchoolClass.school_id == school_id
    ).distinct().all()
    academic_years = sorted(
        {value[0] for value in years_from_invoices + years_from_schedules},
        reverse=True
    )
    terms_from_invoices = db.session.query(
        Invoice.term
    ).join(
        Student, Student.id == Invoice.student_id
    ).filter(
        Student.school_id == school_id,
        Invoice.term.isnot(None),
        Invoice.term != ''
    ).distinct().all()
    terms_from_schedules = db.session.query(
        TermFeeSchedule.term
    ).join(
        SchoolClass, SchoolClass.id == TermFeeSchedule.class_id
    ).filter(
        SchoolClass.school_id == school_id
    ).distinct().all()
    terms = sorted(
        {value[0] for value in terms_from_invoices + terms_from_schedules}
    )

    finance_data = []
    total_fees_all = 0.0
    total_paid_all = 0.0
    total_balance_all = 0.0
    fee_breakdown_all = {
        "tuition": 0.0,
        "sports": 0.0,
        "library": 0.0,
        "ict": 0.0,
        "other": 0.0,
    }
    students_with_outstanding = 0

    for student in students:
        invoice_query = Invoice.query.filter_by(student_id=student.id)
        if selected_year:
            invoice_query = invoice_query.filter(
                Invoice.academic_year == selected_year
            )
        if selected_term:
            invoice_query = invoice_query.filter(Invoice.term == selected_term)
        invoice_query = apply_date_range(invoice_query, Invoice.date_created)
        invoices = invoice_query.order_by(Invoice.date_created.desc()).all()

        fee_breakdown = {
            "tuition": 0.0,
            "sports": 0.0,
            "library": 0.0,
            "ict": 0.0,
            "other": 0.0,
        }
        period_label = ""

        if invoices:
            period_label = ", ".join(dict.fromkeys(
                f"{invoice.term} ({invoice.academic_year})"
                if invoice.term and invoice.academic_year
                else "Legacy invoice"
                for invoice in invoices
            ))
            total_fees = sum(float(invoice.amount or 0) for invoice in invoices)
            paid = sum(float(invoice.partial_paid or 0) for invoice in invoices)
            for invoice in invoices:
                for item, field in (
                    ("tuition", "tuition_fee"),
                    ("sports", "sports_fee"),
                    ("library", "library_fee"),
                    ("ict", "ict_fee"),
                ):
                    fee_breakdown[item] += float(getattr(invoice, field) or 0)
            fee_breakdown["other"] = max(
                total_fees - sum(fee_breakdown[item] for item in (
                    "tuition", "sports", "library", "ict"
                )),
                0
            )
            balance = sum(
                max(float(invoice.amount or 0) - float(invoice.partial_paid or 0), 0)
                for invoice in invoices
            )
        else:
            schedule_query = TermFeeSchedule.query.filter_by(
                class_id=student.class_id
            )
            if selected_year:
                schedule_query = schedule_query.filter(
                    TermFeeSchedule.academic_year == selected_year
                )
            if selected_term:
                schedule_query = schedule_query.filter(
                    TermFeeSchedule.term == selected_term
                )
            schedule_query = schedule_query.join(
                SchoolClass,
                SchoolClass.id == TermFeeSchedule.class_id
            ).filter(SchoolClass.school_id == school_id)
            schedule = None if start_date or end_date else schedule_query.order_by(
                TermFeeSchedule.academic_year.desc(),
                TermFeeSchedule.id.desc()
            ).first()

            if schedule:
                period_label = f"{schedule.term} ({schedule.academic_year})"
                fee_breakdown = {
                    "tuition": float(schedule.tuition_fee or 0),
                    "sports": float(schedule.sports_fee or 0),
                    "library": float(schedule.library_fee or 0),
                    "ict": float(schedule.ict_fee or 0),
                    "other": 0.0,
                }
            elif not selected_year and not selected_term and not start_date and not end_date:
                fee_structure = FeeStructure.query.filter_by(
                    class_id=student.class_id
                ).first()
                fee_breakdown["tuition"] = float(
                    (fee_structure.amount if fee_structure else 0)
                    or student.fee_amount
                    or 0
                )

            total_fees = sum(fee_breakdown.values())
            student_payment_query = Payment.query.join(
                Invoice, Invoice.id == Payment.invoice_id
            ).filter(Payment.student_id == student.id)
            if selected_year:
                student_payment_query = student_payment_query.filter(
                    Invoice.academic_year == selected_year
                )
            if selected_term:
                student_payment_query = student_payment_query.filter(
                    Invoice.term == selected_term
                )
            if selected_method:
                student_payment_query = student_payment_query.filter(
                    Payment.method == selected_method
                )
            student_payment_query = apply_date_range(
                student_payment_query,
                Payment.payment_date
            )
            paid = float(
                student_payment_query.with_entities(
                    func.sum(Payment.amount_paid)
                ).scalar() or 0
            )
            balance = max(total_fees - paid, 0)

        payment_status = (
            "Unpaid" if paid <= 0
            else "Paid" if balance <= 0
            else "Partial"
        )
        total_fees_all += total_fees
        total_paid_all += paid
        total_balance_all += balance
        if balance > 0:
            students_with_outstanding += 1
        for item, amount in fee_breakdown.items():
            fee_breakdown_all[item] += amount

        finance_data.append({
            "student": student,
            "total": round(total_fees, 2),
            "fee_breakdown": {
                item: round(amount, 2)
                for item, amount in fee_breakdown.items()
            },
            "paid": round(paid, 2),
            "balance": round(balance, 2),
            "status": payment_status,
            "period": period_label,
            "history": invoices,
            "unpaid_invoice": next(
                (invoice for invoice in invoices if invoice.status != "Paid"),
                None
            ),
        })

    payment_query = Payment.query.join(
        Student, Student.id == Payment.student_id
    ).join(
        Invoice, Invoice.id == Payment.invoice_id
    ).filter(Student.school_id == school_id)
    if selected_class:
        payment_query = payment_query.filter(Student.class_id == selected_class)
    if selected_year:
        payment_query = payment_query.filter(
            Invoice.academic_year == selected_year
        )
    if selected_term:
        payment_query = payment_query.filter(Invoice.term == selected_term)
    if selected_method:
        payment_query = payment_query.filter(Payment.method == selected_method)
    payment_query = apply_date_range(payment_query, Payment.payment_date)
    total_collected = float(
        payment_query.with_entities(
            func.sum(Payment.amount_paid)
        ).scalar() or 0
    )

    expense_query = Expense.query.filter_by(school_id=school_id)
    expense_query = apply_date_range(expense_query, Expense.date_created)
    expenses = expense_query.all()
    total_expenses = sum(float(item.amount or 0) for item in expenses)
    salary_expenses = sum(
        float(item.amount or 0)
        for item in expenses
        if "salary" in (item.category or "").lower()
    )

    income_query = OtherIncome.query.filter_by(school_id=school_id)
    income_query = apply_date_range(income_query, OtherIncome.received_date)
    if selected_method:
        income_query = income_query.filter(
            OtherIncome.payment_method == selected_method
        )
    other_income_total = float(
        income_query.with_entities(
            func.sum(OtherIncome.amount)
        ).scalar() or 0
    )
    net_cash_flow = total_collected + other_income_total - total_expenses

    return render_template(
        'finance.html',
        finance_data=finance_data,
        total_fees=round(total_fees_all, 2),
        total_paid=round(total_paid_all, 2),
        total_collected=round(total_collected, 2),
        total_balance=round(total_balance_all, 2),
        total_expenses=round(total_expenses, 2),
        salary_expenses=round(salary_expenses, 2),
        other_income_total=round(other_income_total, 2),
        net_cash_flow=round(net_cash_flow, 2),
        students_with_outstanding=students_with_outstanding,
        fee_breakdown_total={
            item: round(amount, 2)
            for item, amount in fee_breakdown_all.items()
        },
        classes=classes,
        selected_class=selected_class,
        academic_years=academic_years,
        terms=terms,
        selected_year=selected_year,
        selected_term=selected_term,
        selected_method=selected_method,
        start_date=start_value,
        end_date=end_value
    )

@app.route('/student_payment/<int:student_id>')
@login_required
def student_payment(student_id):

    student = Student.query.get_or_404(student_id)

    fee_structure = FeeStructure.query.filter_by(
        class_id=student.class_id
    ).first()

    total_fee = fee_structure.amount if fee_structure else 0

    paid = db.session.query(
        func.sum(Payment.amount_paid)
    ).filter_by(
        student_id=student.id
    ).scalar() or 0

    balance = total_fee - paid

    return render_template(
        'student_payment.html',
        student=student,
        total_fee=total_fee,
        paid=paid,
        balance=balance
    )

@app.route('/update-fee/<int:student_id>', methods=['POST'])
@login_required
@role_required('admin', 'accountant')
def update_fee(student_id):

    student = Student.query.get_or_404(student_id)

    student.fee_amount = float(
        request.form['fee_amount']
    )

    db.session.commit()

    flash(
        "Student fee updated successfully",
        "success"
    )

    return redirect(url_for('finance'))

@app.route('/debtors')
@login_required
def debtors():

    data = []

    for s in Student.query.all():

        invoices = Invoice.query.filter_by(student_id=s.id).all()

        balance = sum(
            (i.amount - (i.partial_paid or 0))
            for i in invoices
        )

        if balance > 0:
            data.append({
                "student": s,
                "balance": balance
            })

    return render_template('debtors.html', debtors=data)


@app.route('/manage-school-logo', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def manage_school_logo():
    logo_path = app.config["REPORT_LOGO_PATH"]
    logo_file = os.path.join(app.static_folder, logo_path)

    if request.method == 'POST':
        uploaded_file = request.files.get('school_logo')
        try:
            _save_school_logo(uploaded_file)
        except ValueError as error:
            flash(str(error), "warning")
            return redirect(url_for('manage_school_logo'))

        flash("School logo updated successfully.", "success")
        return redirect(url_for('manage_school_logo'))

    return render_template(
        'manage_school_logo.html',
        school_logo_url=(
            url_for('static', filename=logo_path)
            if os.path.isfile(logo_file)
            else None
        )
    )


@app.route('/manage-academic-period', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def manage_academic_period():
    active_period = AcademicPeriodSetting.query.filter_by(
        school_id=current_user.school_id
    ).first()

    if request.method == 'POST':
        term = (request.form.get('term') or '').strip()
        academic_year = (request.form.get('academic_year') or '').strip()
        if term not in {'Term 1', 'Term 2', 'Term 3'}:
            flash("Select a valid active term.", "warning")
            return redirect(url_for('manage_academic_period'))
        if not re.fullmatch(r'\d{4}(?:[/\-]\d{2,4})?', academic_year):
            flash("Enter the academic year, for example 2026/2027.", "warning")
            return redirect(url_for('manage_academic_period'))

        if active_period is None:
            active_period = AcademicPeriodSetting(
                school_id=current_user.school_id,
                term=term,
                academic_year=academic_year
            )
            db.session.add(active_period)
        else:
            active_period.term = term
            active_period.academic_year = academic_year

        db.session.commit()
        flash("Active term and academic year saved.", "success")
        return redirect(url_for('manage_academic_period'))

    return render_template(
        'manage_academic_period.html',
        active_period=active_period
    )


@app.route('/manage-fees', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'accountant')
def manage_fees():
    school_redirect = _require_current_school()
    if school_redirect:
        return school_redirect

    classes = SchoolClass.query.filter_by(
        school_id=current_user.school_id
    ).all()

    if request.method == 'POST':
        class_id = request.form.get('class_id', type=int)
        term = (request.form.get('term') or '').strip()
        academic_year = (request.form.get('academic_year') or '').strip()
        if not class_id or not term or not academic_year:
            flash("Select a class and enter the term and academic year.", "warning")
            return redirect(url_for('manage_fees'))

        classroom = SchoolClass.query.filter_by(
            id=class_id,
            school_id=current_user.school_id
        ).first()
        if classroom is None:
            flash("The selected class was not found.", "danger")
            return redirect(url_for('manage_fees'))

        try:
            amounts = {}
            for field in ("tuition_fee", "sports_fee", "library_fee", "ict_fee"):
                raw_amount = (request.form.get(field) or "0").strip()
                amount = float(raw_amount)
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError
                amounts[field] = amount
        except ValueError:
            flash("Enter valid non-negative amounts for all fee items.", "warning")
            return redirect(url_for('manage_fees'))

        schedule = TermFeeSchedule.query.filter_by(
            class_id=class_id,
            term=term,
            academic_year=academic_year
        ).first()
        if schedule is None:
            schedule = TermFeeSchedule(
                class_id=class_id,
                term=term,
                academic_year=academic_year
            )
            db.session.add(schedule)

        for field, amount in amounts.items():
            setattr(schedule, field, amount)

        # Keep older class-based billing screens in sync with tuition.
        legacy_fee = FeeStructure.query.filter_by(
            class_id=class_id
        ).first()
        if legacy_fee is None:
            legacy_fee = FeeStructure(class_id=class_id)
            db.session.add(legacy_fee)
        legacy_fee.amount = amounts["tuition_fee"]
        db.session.commit()

        flash(
            f"Fees saved for {classroom.name}, {term} ({academic_year}).",
            "success"
        )

        return redirect(
            url_for('manage_fees')
        )

    fees = (
        TermFeeSchedule.query
        .join(SchoolClass, SchoolClass.id == TermFeeSchedule.class_id)
        .filter(SchoolClass.school_id == current_user.school_id)
        .order_by(
            TermFeeSchedule.academic_year.desc(),
            TermFeeSchedule.term,
            TermFeeSchedule.class_id
        )
        .all()
    )

    return render_template(
        'manage_fees.html',
        classes=classes,
        fees=fees
    )

@app.route('/pay_all/<int:student_id>', methods=['POST'])
@login_required
@role_required('accountant', 'admin')
def pay_all(student_id):

    invoices = Invoice.query.filter(
        Invoice.student_id == student_id,
        Invoice.amount > func.coalesce(Invoice.partial_paid, 0)
    ).all()
    if not invoices:
        flash("This learner has no outstanding invoices.", "warning")
        return redirect(url_for('payment_system'))

    for invoice in invoices:
        balance = max(
            float(invoice.amount or 0) - float(invoice.partial_paid or 0),
            0
        )
        _record_invoice_payment(invoice, balance, "Cash")

    flash("Payments recorded for all outstanding invoices.", "success")
    return redirect(url_for('payment_system'))

@app.route('/pay_partial/<int:invoice_id>', methods=['POST'])
@login_required
@role_required('accountant', 'admin')
def pay_partial(invoice_id):

    invoice = Invoice.query.get_or_404(invoice_id)

    try:
        amount = float(request.form.get('amount', ''))
        _record_invoice_payment(
            invoice,
            amount,
            request.form.get('method', 'Cash')
        )
    except ValueError as error:
        flash(str(error), "warning")
        return redirect(url_for('payment_system'))

    flash("Payment recorded successfully.", "success")
    return redirect(url_for('payment_system'))

@app.route('/receipt/<int:student_id>')
@login_required
def receipt(student_id):

    student = Student.query.get_or_404(student_id)
    invoices = Invoice.query.filter_by(student_id=student_id).all()

    total_paid = sum(i.amount for i in invoices if i.status == "Paid")

    return render_template(
        "receipt.html",
        student=student,
        invoices=invoices,
        total_paid=total_paid
    )

@app.route('/download_receipt/<int:student_id>')
@login_required
def download_receipt(student_id):

    student = Student.query.get_or_404(student_id)
    invoices = Invoice.query.filter_by(student_id=student_id).all()

    response = make_response()
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=receipt.pdf'

    pdf = canvas.Canvas(response)

    pdf.drawString(100, 800, f"Receipt for {student.name}")

    y = 750
    for inv in invoices:
        pdf.drawString(100, y, f"{inv.id} - GHS {inv.amount} - {inv.status}")
        y -= 20

    pdf.save()
    return response

@app.route('/delete_finance/<int:id>')
@login_required
def delete_finance(id):
    invoice = Invoice.query.get_or_404(id)

    db.session.delete(invoice)
    db.session.commit()

    flash("Finance record deleted successfully", "success")
    return redirect(url_for('finance'))

@app.route('/pay_invoice/<int:invoice_id>', methods=['POST'])
@login_required
@role_required('accountant', 'admin')
def pay_invoice(invoice_id):

    invoice = Invoice.query.get_or_404(invoice_id)

    try:
        amount = float(request.form.get('amount', ''))
        _record_invoice_payment(
            invoice,
            amount,
            request.form.get('method', 'Cash')
        )
    except ValueError as error:
        flash(str(error), "warning")
        return redirect(url_for('invoices'))

    flash("Payment recorded successfully.", "success")
    if request.form.get('return_to') == 'payment_system':
        return redirect(url_for('payment_system'))
    return redirect(url_for('invoices'))

@app.route("/api/students")
@jwt_required()
def students_api():
    return {"message": "API working"}

@app.route('/print_invoice/<int:id>')
@login_required
def print_invoice(id):

    invoice = Invoice.query.get_or_404(id)

    payments = Payment.query.filter_by(
        invoice_id=invoice.id
    ).order_by(
        Payment.payment_date.desc()
    ).all()

    total_paid = float(invoice.partial_paid or 0)

    balance = float(invoice.amount) - total_paid

    return render_template(
        'print_invoice.html',
        invoice=invoice,
        student=invoice.student,
        payments=payments,
        total_paid=total_paid,
        balance=balance,
        school_name="SENIOR VICTORY COLLEGE"
    )



@app.route('/delete_invoice/<int:id>')
def delete_invoice(id):

    invoice = Invoice.query.get_or_404(id)

    Payment.query.filter_by(
        invoice_id=invoice.id
    ).delete()

    db.session.delete(invoice)

    db.session.commit()

    flash("Invoice deleted successfully")

    return redirect(url_for('finance'))

# =========================
# AI PREDICTION
# =========================

@app.route('/ai_prediction/<int:class_id>')
@login_required
@role_required('teacher', 'admin')
def ai_prediction(class_id):

    student = Student.query.filter_by(class_id=class_id).all()

    # EXAMPLE VALUES
    attendance = 85
    assignment = 78
    midterm = 80

    # LOAD MODELS
    grade_model = joblib.load('grade_model.pkl')
    weak_model = joblib.load('weak_student_model.pkl')

    features = np.array([[attendance, assignment, midterm]])

    # 📊 PREDICT FINAL SCORE
    predicted_score = grade_model.predict(features)[0]

    # ⚠️ WEAK STUDENT CHECK
    weak_prediction = weak_model.predict(features)[0]

    risk = "Weak Student" if weak_prediction == 1 else "Performing Well"

    return render_template(
        'ai_prediction.html',

        student=student,
        predicted_score=round(predicted_score, 2),
        risk=risk
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() == "true"
    use_reloader = app.config["APP_ENV"] == "development"

    if not use_reloader or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        local_url = f"http://127.0.0.1:{port}"
        threading.Timer(1, lambda: webbrowser.open(local_url)).start()

    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug,
        use_reloader=use_reloader
    )