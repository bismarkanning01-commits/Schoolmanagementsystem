from __future__ import annotations

import os
import re
from datetime import date, datetime
from io import BytesIO
from typing import Any

from flask import Blueprint, Response, current_app, flash, make_response, redirect, request, session, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from sqlalchemy import func


report_bp = Blueprint("report_bp", __name__)

PAGE_W, PAGE_H = A4
MARGIN = 0.45 * cm
BLUE = colors.HexColor("#1E79A8")
GREEN = colors.HexColor("#008767")
LIGHT_BLUE = colors.HexColor("#DFF0F7")
LIGHT_GRAY = colors.HexColor("#F3F3F3")
GRID = colors.HexColor("#444444")
TEXT = colors.HexColor("#111111")
WHITE = colors.white
BLACK = colors.black


@report_bp.route("/download_report/<int:student_id>")
@report_bp.route("/students/<int:student_id>/download-report")
def download_report(student_id: int) -> Response:

    if not session.get("user_id"):
        flash("Login required")
        return redirect(url_for("login"))

    models = _get_models()

    Student = models["Student"]
    Performance = models["Performance"]

    student = Student.query.get_or_404(student_id)

    # Get the newest saved performance record.
    # This is used when the download URL does not
    # contain a term and academic year.
    latest_record = (
        Performance.query
        .filter(
            Performance.student_id == student.id
        )
        .order_by(
            Performance.id.desc()
        )
        .first()
    )

    # Use the term sent from the report page.
    # If no term was sent, use the latest saved term.
    term = (
        request.args.get("term")
        or (
            latest_record.term
            if latest_record
            else ""
        )
    )

    # Use the academic year sent from the report page.
    # If no year was sent, use the latest saved year.
    academic_year = (
        request.args.get("academic_year")
        or request.args.get("year")
        or (
            latest_record.academic_year
            if latest_record
            else ""
        )
    )

    term = str(
        term or "N/A"
    ).strip()

    academic_year = str(
        academic_year or "N/A"
    ).strip()

    print("\n" + "=" * 70)
    print("PDF REPORT")
    print("Student:", student.name)
    print("Term:", repr(term))
    print("Academic Year:", repr(academic_year))
    print("=" * 70 + "\n")

    data = _build_report_data(
        student,
        term,
        academic_year,
        models
    )

    buffer = BytesIO()

    _draw_report(
        buffer,
        data
    )

    pdf_bytes = buffer.getvalue()

    response = make_response(
        pdf_bytes
    )

    response.headers[
        "Content-Type"
    ] = "application/pdf"

    response.headers[
        "Content-Disposition"
    ] = (
        f'attachment; '
        f'filename="{_safe_filename(data["learner_name"])}_terminal_report.pdf"'
    )

    return response


def _get_models() -> dict[str, Any]:
    """
    Finds your app models without importing app.py and causing a circular import.

    Option A: it can discover models from the globals of your existing routes.
    Option B: you can set current_app.config["REPORT_MODELS"] yourself.
    """
    configured = current_app.config.get("REPORT_MODELS")
    if configured:
        return configured

    for view in current_app.view_functions.values():
        globals_ = getattr(view, "__globals__", {})
        required = ["db", "Student", "Performance", "Attendance", "Invoice", "Payment", "FeeStructure"]
        if all(name in globals_ for name in required):
            models = {name: globals_[name] for name in required}
            if "TermFeeSchedule" in globals_:
                models["TermFeeSchedule"] = globals_["TermFeeSchedule"]
            if "ReportCard" in globals_:
                models["ReportCard"] = globals_["ReportCard"]
            if "SchoolClass" in globals_:
                models["SchoolClass"] = globals_["SchoolClass"]
            return models

    raise RuntimeError(
        "Report models were not found. Set app.config['REPORT_MODELS'] after your model classes are created."
    )


def _build_report_data(
    student: Any,
    term: str,
    academic_year: str,
    models: dict[str, Any]
) -> dict[str, Any]:

    db = models["db"]
    Student = models["Student"]
    Performance = models["Performance"]
    Attendance = models["Attendance"]
    Payment = models["Payment"]
    Invoice = models["Invoice"]
    FeeStructure = models["FeeStructure"]
    TermFeeSchedule = models.get("TermFeeSchedule")
    ReportCard = models.get("ReportCard")
    SchoolClass = models.get("SchoolClass")

    # ==================================================
    # CLEAN TERM AND ACADEMIC YEAR
    # ==================================================

    term = str(term or "").strip()
    academic_year = str(academic_year or "").strip()

    school = getattr(student, "school", None)
    classroom = getattr(student, "classroom", None)
    class_id = getattr(student, "class_id", None)

    # ==================================================
    # GET ONLY THE STUDENT'S RESULTS FOR THE
    # SELECTED TERM AND ACADEMIC YEAR
    # ==================================================

    performances = (
    Performance.query
    .filter(
        Performance.student_id == student.id,
        Performance.term == term,
        Performance.academic_year == academic_year
    )
    .order_by(
        Performance.subject.asc()
    )
    .all()
)

    print("=" * 60)
    print("REPORT STUDENT:", student.name)
    print("REPORT TERM:", term)
    print("REPORT YEAR:", academic_year)
    print("SUBJECTS FOUND:", len(performances))

    for performance in performances:

        print(
            performance.subject,
            performance.total_score,
            performance.term,
            performance.academic_year
        )

    print("=" * 60)

    # IMPORTANT:
    # Do not add the old fallback query here.
    #
    # The old fallback loaded marks from other terms:
    #
    # if not performances:
    #     performances = Performance.query.filter_by(
    #         student_id=student.id
    #     ).all()

    # ==================================================
    # DEBUG INFORMATION
    # ==================================================

    print("=" * 60)
    print("Student:", getattr(student, "name", "N/A"))
    print("Requested Term:", term)
    print("Requested Academic Year:", academic_year)
    print("Subjects Found:", len(performances))

    for p in performances:
        print(
            "Subject:", p.subject,
            "| Class Score:", p.class_score,
            "| Exam Score:", p.exam_score,
            "| Total:", p.total_score,
            "| Term:", p.term,
            "| Academic Year:", p.academic_year
        )

    print("=" * 60)

    # ==================================================
    # NUMBER ON ROLL
    # COUNT ALL STUDENTS IN THE LEARNER'S CLASS
    # ==================================================

    if class_id is not None:

        number_on_roll = (
            Student.query
            .filter(
                Student.class_id == class_id
            )
            .count()
        )

    else:

        number_on_roll = 0

    # ==================================================
    # BUILD SUBJECT ROWS
    # ==================================================

    subject_rows = []
    total_marks = 0.0

    for index, performance in enumerate(
        performances,
        start=1
    ):

        class_score = float(
            getattr(
                performance,
                "class_score",
                0
            ) or 0
        )

        exam_score = float(
            getattr(
                performance,
                "exam_score",
                0
            ) or 0
        )

        saved_total = getattr(
            performance,
            "total_score",
            None
        )

        if saved_total is None:

            total = class_score + exam_score

        else:

            total = float(
                saved_total or 0
            )

        grade, remark = _grade_and_remark(
            total
        )

        total_marks += total

        # ==============================================
        # CALCULATE SUBJECT POSITION
        # ==============================================

        subject_position = _subject_position(
            Performance,
            performance,
            student,
            term,
            academic_year
        )

        # ==============================================
        # ADD SUBJECT TO REPORT
        # ==============================================

        subject_rows.append(
            {
                "sn": index,

                "subject": getattr(
                    performance,
                    "subject",
                    ""
                ),

                "class_score": class_score,

                "exam_score": exam_score,

                # Keep both names because different
                # PDF drawing code may use either one.
                "total": total,
                "total_score": total,

                "grade": grade,

                "remark": remark,

                # Keep both names so the PDF can
                # display the subject position.
                "position": subject_position,
                "subject_position": subject_position,
            }
        )

    # ==================================================
    # AVERAGE MARK
    # ==================================================

    if subject_rows:

        average = round(
            total_marks / len(subject_rows),
            1
        )

    else:

        average = 0.0

    # ==================================================
    # OVERALL CLASS POSITION
    # ==================================================

    overall_position = _overall_position(
        Student,
        Performance,
        student,
        term,
        academic_year
    )

    if isinstance(
        overall_position,
        int
    ):

        position_in_class = _ordinal(
            overall_position
        )

    else:

        position_in_class = "-"

    # ==================================================
    # ATTENDANCE
    # ==================================================

    attendance_present, attendance_total = (
        _attendance_totals(
            Attendance,
            student,
            term,
            academic_year
        )
    )

    # ==================================================
    # FEES
    # ==================================================

    fee_total, fee_paid, fee_balance = (
        _fee_summary(
            db,
            FeeStructure,
            Payment,
            Invoice,
            student
        )
    )
    bill_term, bill_academic_year = _next_academic_period(
        term,
        academic_year
    )
    tuition_fee, sports_fee, library_fee, ict_fee = _term_fee_amounts(
        TermFeeSchedule,
        FeeStructure,
        student,
        bill_term,
        bill_academic_year
    )
    arrears = _invoice_arrears(Invoice, student)
    bill_total = round(
        tuition_fee + sports_fee + library_fee + ict_fee + arrears,
        2
    )

    # ==================================================
    # STAFF INFORMATION
    # ==================================================

    headteacher_name = (
        current_app.config.get(
            "REPORT_HEADTEACHER_NAME",
            "Headteacher"
        )
    )

    class_teacher_name = (
        _class_teacher_name(
            classroom
        )
    )

    # ==================================================
    # PROMOTION
    # ==================================================

    current_class = _value(
        classroom,
        "name",
        _value(
            student,
            "student_class",
            ""
        )
    )

    class_names = []
    if SchoolClass is not None:
        class_names = [
            school_class.name
            for school_class in (
                SchoolClass.query
                .filter_by(school_id=getattr(student, "school_id", None))
                .order_by(SchoolClass.id.asc())
                .all()
            )
        ]

    promoted_to = _promotion_target(
        str(current_class or ""),
        average,
        bool(subject_rows),
        class_names,
        term=term
    )
    promotion_label, promotion_value = _promotion_display(
        promoted_to
    )

    report_card = None
    if ReportCard is not None:
        report_card = (
            ReportCard.query
            .filter_by(
                student_id=student.id,
                term=term,
                academic_year=academic_year
            )
            .order_by(ReportCard.id.desc())
            .first()
        )

    configured_term_date = current_app.config.get(
        "REPORT_NEXT_TERM_BEGINS",
        ""
    )
    next_term_begins = (
        getattr(report_card, "next_term_begins", None)
        or configured_term_date
        or "Date not set"
    )

    # ==================================================
    # REPORT DATA
    # ==================================================

    return {

        # ----------------------------------------------
        # SCHOOL INFORMATION
        # ----------------------------------------------

        "school_name": _value(
            school,
            "name",
            current_app.config.get(
                "REPORT_SCHOOL_NAME",
                "SCHOOL NAME"
            )
        ),

        "school_address": _value(
            school,
            "address",
            current_app.config.get(
                "REPORT_SCHOOL_ADDRESS",
                ""
            )
        ),

        "school_phone": (
            current_app.config.get(
                "REPORT_SCHOOL_PHONE",
                ""
            )
        ),

        "school_email": (
            current_app.config.get(
                "REPORT_SCHOOL_EMAIL",
                ""
            )
        ),

        "school_motto": (
            current_app.config.get(
                "REPORT_SCHOOL_MOTTO",
                "Raising the Standard"
            )
        ),

        "logo_path": (
            current_app.config.get(
                "REPORT_LOGO_PATH",
                ""
            )
        ),

        # ----------------------------------------------
        # LEARNER INFORMATION
        # ----------------------------------------------

        "learner_photo_path": _value(
            student,
            "photo",
            _value(
                student,
                "photo_path",
                ""
            )
        ),

        "learner_name": _value(
            student,
            "name",
            ""
        ),

        "gender": (
            getattr(
                student,
                "gender",
                None
            )
            or "N/A"
        ),

        "class_name": _value(
            classroom,
            "name",
            _value(
                student,
                "student_class",
                "-"
            )
        ),

        # ----------------------------------------------
        # REPORT INFORMATION
        # ----------------------------------------------

        "number_on_roll": number_on_roll,

        "academic_year": (
            academic_year
            or "N/A"
        ),

        "term": (
            term
            or "N/A"
        ),

        "position_in_class": (
            position_in_class
        ),

        "average": average,

        "average_remark": (
            _average_remark(
                average
            )
        ),

        "next_term_begins": (
            _format_term_date(str(next_term_begins))
        ),

        "promotion_label": promotion_label,
        "promoted_to": promotion_value,

        # ----------------------------------------------
        # ATTENDANCE
        # ----------------------------------------------

        "attendance_present": (
            attendance_present
            if attendance_total
            else "-"
        ),

        "attendance_total": (
            attendance_total
            if attendance_total
            else "-"
        ),

        # ----------------------------------------------
        # LEARNER CHARACTER
        # ----------------------------------------------

        "conduct": (
            current_app.config.get(
                "REPORT_DEFAULT_CONDUCT",
                "Sociable"
            )
        ),

        "attitude": (
            current_app.config.get(
                "REPORT_DEFAULT_ATTITUDE",
                "Hardworking"
            )
        ),

        "interest": (
            current_app.config.get(
                "REPORT_DEFAULT_INTEREST",
                "Reading"
            )
        ),

        # ----------------------------------------------
        # TEACHER INFORMATION
        # ----------------------------------------------

        "class_teacher_name": (
            class_teacher_name
        ),

        "class_teacher_remark": (
            _teacher_remark(
                average
            )
        ),

        "headteacher_name": (
            headteacher_name
        ),

        "headteacher_remark": (
            _headteacher_remark(
                average
            )
        ),

        "date_line": (
            current_app.config.get(
                "REPORT_DATE",
                date.today().strftime(
                    "%d %B %Y"
                )
            )
        ),

        # ----------------------------------------------
        # SUBJECT RESULTS
        # ----------------------------------------------

        "subjects": subject_rows,

        # ----------------------------------------------
        # TOTALS
        # ----------------------------------------------

        "overall_total": round(
            total_marks,
            1
        ),

        # Two subjects = 200.
        # Ten subjects = 1000.
        # No subjects = 0.
        "overall_out_of": (
            len(subject_rows) * 100
            if subject_rows
            else 0
        ),

        # ----------------------------------------------
        # LEARNER'S BILL
        # ----------------------------------------------

        "bill": [
            ("Tuition Fee", tuition_fee),
            ("Sports Levy", sports_fee),
            ("Library", library_fee),
            ("ICT", ict_fee),
            ("Previous Arrears", arrears),
        ],

        "bill_term": bill_term,
        "bill_academic_year": bill_academic_year,
        "bill_total": bill_total,

        "fee_paid": fee_paid,
    }


def _term_fee_amounts(
    TermFeeSchedule: Any,
    FeeStructure: Any,
    student: Any,
    term: str,
    academic_year: str
) -> tuple[float, float, float, float]:
    if TermFeeSchedule is not None:
        schedule = TermFeeSchedule.query.filter_by(
            class_id=student.class_id,
            term=term,
            academic_year=academic_year
        ).first()
        if schedule is not None:
            return (
                float(schedule.tuition_fee or 0),
                float(schedule.sports_fee or 0),
                float(schedule.library_fee or 0),
                float(schedule.ict_fee or 0)
            )

    legacy_fee = FeeStructure.query.filter_by(
        class_id=student.class_id
    ).first()
    tuition_fee = (
        getattr(legacy_fee, "amount", 0)
        or getattr(student, "fee_amount", 0)
        or 0
    )
    return float(tuition_fee), 0.0, 0.0, 0.0


def _next_academic_period(term: str, academic_year: str) -> tuple[str, str]:
    term_match = re.fullmatch(r"term\s*([1-3])", term.strip(), re.IGNORECASE)
    if term_match is None:
        return term, academic_year

    term_number = int(term_match.group(1))
    if term_number < 3:
        return f"Term {term_number + 1}", academic_year

    year = academic_year.strip()
    year_match = re.fullmatch(r"(\d{4})([/\-])(\d{2}|\d{4})", year)
    if year_match:
        start_year = int(year_match.group(1)) + 1
        end_year = start_year + 1
        suffix = str(end_year)[-len(year_match.group(3)):]
        return "Term 1", f"{start_year}{year_match.group(2)}{suffix}"

    single_year_match = re.fullmatch(r"\d{4}", year)
    if single_year_match:
        return "Term 1", str(int(year) + 1)

    return "Term 1", academic_year


def _invoice_arrears(Invoice: Any, student: Any) -> float:
    invoices = Invoice.query.filter_by(student_id=student.id).all()
    outstanding = sum(
        max(
            float(invoice.amount or 0) - float(invoice.partial_paid or 0),
            0.0
        )
        for invoice in invoices
        if str(invoice.status or "").strip().lower() != "paid"
    )
    return round(outstanding, 2)


def _draw_report(buffer: BytesIO, data: dict[str, Any]) -> None:
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"{data['learner_name']} Terminal Report")

    x = MARGIN
    y = PAGE_H - MARGIN
    page_w = PAGE_W - (2 * MARGIN)

    _outer_border(pdf)
    y = _draw_school_header(pdf, x, y, page_w, data)
    y = _draw_learner_summary(pdf, x, y, page_w, data)
    y = _draw_subject_table(pdf, x, y, page_w, data)
    y = _draw_lower_section(pdf, x, y, page_w, data)
    _draw_grade_footer(pdf, x, data)

    pdf.showPage()
    pdf.save()
    buffer.seek(0)


def _outer_border(pdf: canvas.Canvas) -> None:
    pdf.setStrokeColor(GRID)
    pdf.setLineWidth(1)
    pdf.rect(MARGIN / 2, MARGIN / 2, PAGE_W - MARGIN, PAGE_H - MARGIN, stroke=1, fill=0)


def _draw_school_header(pdf: canvas.Canvas, x: float, y: float, w: float, data: dict[str, Any]) -> float:
    title_h = 1.0 * cm
    pdf.setFillColor(BLUE)
    pdf.rect(x, y - title_h, w, title_h, stroke=1, fill=1)
    pdf.setFillColor(WHITE)
    pdf.setFont("Helvetica-Bold", 17)
    pdf.drawCentredString(PAGE_W / 2, y - 0.65 * cm, data["school_name"].upper())

    y -= title_h
    header_h = 3.05 * cm
    pdf.setStrokeColor(GRID)
    pdf.rect(x, y - header_h, w, header_h, stroke=1, fill=0)

    logo_x = x + 0.85 * cm
    logo_y = y - header_h + 0.42 * cm
    _draw_logo_or_badge(pdf, logo_x, logo_y, 2.2 * cm, data.get("logo_path"))

    info_x = x + 5.0 * cm
    info_y = y - 0.55 * cm
    contact_rows = [
        data.get("school_phone") or "0244587091 / 0246897677",
        data.get("school_address") or "School Address",
        data.get("school_email") or "school@email.com",
        data.get("school_motto") or "Raising the Standard",
    ]
    icons = ["TEL", "LOC", "MAIL", "MOTTO"]
    pdf.setFont("Helvetica", 8)
    pdf.setFillColor(TEXT)
    for icon, row in zip(icons, contact_rows):
        pdf.setFont("Helvetica-Bold", 5.8)
        pdf.drawString(info_x, info_y, icon)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(info_x + 0.9 * cm, info_y, str(row))
        info_y -= 0.42 * cm

    photo_w = 2.35 * cm
    photo_x = x + w - photo_w - 0.55 * cm
    photo_y = y - header_h + 0.33 * cm
    _draw_photo_box(pdf, photo_x, photo_y, photo_w, 2.45 * cm, data.get("learner_photo_path"))

    strip_w = 6.0 * cm
    strip_h = 0.42 * cm
    strip_x = x + (w - strip_w) / 2
    strip_y = y - header_h + 0.27 * cm
    pdf.setFillColor(BLUE)
    pdf.rect(strip_x, strip_y, strip_w, strip_h, stroke=0, fill=1)
    pdf.setFillColor(WHITE)
    pdf.setFont("Helvetica-Bold", 7.4)
    pdf.drawCentredString(strip_x + strip_w / 2, strip_y + 0.13 * cm, "LEARNER'S TERMINAL REPORT")

    return y - header_h


def _draw_learner_summary(pdf: canvas.Canvas, x: float, y: float, w: float, data: dict[str, Any]) -> float:
    h = 3.15 * cm
    pdf.setStrokeColor(GRID)
    pdf.rect(x, y - h, w, h, stroke=1, fill=0)

    left = [
        ("Name Of Learner", data["learner_name"]),
        ("Gender", data["gender"]),
        ("Class", data["class_name"]),
        ("Position In Class", data["position_in_class"]),
        ("Learner's Average Mark", data["average"]),
        ("Next Term Begins", data["next_term_begins"]),
    ]
    right = [
        ("Number On Roll", data["number_on_roll"]),
        ("Academic Year", data["academic_year"]),
        ("Term", data["term"]),
        ("Average Remark", data["average_remark"]),
        (data["promotion_label"], data["promoted_to"]),
    ]

    row_y = y - 0.62 * cm
    for label, value in left:
        _draw_label_value(pdf, x + 0.35 * cm, row_y, label, value, 5.1 * cm)
        row_y -= 0.42 * cm

    row_y = y - 0.62 * cm
    for label, value in right:
        _draw_label_value(pdf, x + 10.1 * cm, row_y, label, value, 4.8 * cm)
        row_y -= 0.42 * cm

    return y - h


def _draw_subject_table(pdf: canvas.Canvas, x: float, y: float, w: float, data: dict[str, Any]) -> float:
    headers = [
        ("S/N", 0.95 * cm),
        ("SUBJECTS", 5.15 * cm),
        ("CLASS\nSCORE\n(30%)", 1.7 * cm),
        ("EXAM\nSCORE\n(70%)", 1.7 * cm),
        ("TOTAL\nSCORE\n(100%)", 1.75 * cm),
        ("GRADE", 1.55 * cm),
        ("REMARK", 3.05 * cm),
        ("POSITION\nIN SUBJECT", w - (0.95 + 5.15 + 1.7 + 1.7 + 1.75 + 1.55 + 3.05) * cm),
    ]
    header_h = 1.55 * cm
    row_h = 0.5 * cm
    max_rows = 10
    table_h = header_h + (row_h * max_rows)

    pdf.setStrokeColor(GRID)
    pdf.rect(x, y - table_h, w, table_h, stroke=1, fill=0)
    pdf.setFillColor(LIGHT_GRAY)
    pdf.rect(x, y - header_h, w, header_h, stroke=1, fill=1)

    col_x = x
    for label, col_w in headers:
        pdf.setStrokeColor(GRID)
        pdf.rect(col_x, y - header_h, col_w, header_h, stroke=1, fill=0)
        _draw_multiline_center(
            pdf,
            label,
            col_x,
            y - header_h,
            col_w,
            header_h,
            7.0,
            bold=True,
            color=BLACK,
        )
        col_x += col_w

    subjects = list(data["subjects"])[:max_rows]
    while len(subjects) < max_rows:
        subjects.append({"sn": len(subjects) + 1, "subject": "", "class_score": "", "exam_score": "", "total": "", "grade": "", "remark": "", "position": ""})

    row_y = y - header_h
    for row in subjects:
        row_y -= row_h
        col_x = x
        values = [
            row["sn"],
            row["subject"],
            _score(row["class_score"]),
            _score(row["exam_score"]),
            _score(row["total"]),
            row["grade"],
            row["remark"],
            row["position"],
        ]
        for index, ((_, col_w), value) in enumerate(zip(headers, values)):
            pdf.rect(col_x, row_y, col_w, row_h, stroke=1, fill=0)
            if index == 1:
                pdf.setFillColor(TEXT)
                pdf.setFont("Helvetica-Bold", 6.7)
                _fit_left(pdf, str(value), col_x + 0.12 * cm, row_y + 0.15 * cm, col_w - 0.18 * cm, 6.7, color=TEXT)
            elif index in {5, 7}:
                pdf.setFillColor(GREEN)
                pdf.setFont("Helvetica-Bold", 6.8)
                pdf.drawCentredString(col_x + col_w / 2, row_y + 0.15 * cm, str(value))
                pdf.setFillColor(TEXT)
            else:
                pdf.setFillColor(TEXT)
                pdf.setFont("Helvetica", 6.8)
                pdf.drawCentredString(col_x + col_w / 2, row_y + 0.15 * cm, str(value))
            col_x += col_w

    return y - table_h


def _draw_lower_section(pdf: canvas.Canvas, x: float, y: float, w: float, data: dict[str, Any]) -> float:
    h = 5.25 * cm
    left_w = 12.6 * cm
    right_w = w - left_w
    pdf.setStrokeColor(GRID)
    pdf.rect(x, y - h, w, h, stroke=1, fill=0)
    pdf.line(x + left_w, y, x + left_w, y - h)

    left_rows = [
        ("Attendance", f"{data['attendance_present']}      Out Of      {data['attendance_total']}"),
        ("Conduct", data["conduct"]),
        ("Attitude", data["attitude"]),
        ("Interest", data["interest"]),
        ("Class Teacher's Name", data["class_teacher_name"]),
        ("Class Teacher's Remark", data["class_teacher_remark"]),
        ("Headteacher's Name", data["headteacher_name"]),
        ("Headteacher's Remark", data["headteacher_remark"]),
        ("Date", data["date_line"]),
    ]

    row_y = y - 0.6 * cm
    for label, value in left_rows:
        row_x = x + 0.35 * cm
        _draw_label_value(pdf, row_x, row_y, label, value, 8.1 * cm)
        row_y -= 0.4 * cm

    qr_size = 1.1 * cm
    _draw_qr_placeholder(
        pdf,
        x + left_w - qr_size - 0.25 * cm,
        y - h + 0.15 * cm,
        qr_size
    )

    bill_x = x + left_w + 0.18 * cm
    bill_y = y - 0.15 * cm
    bill_w = right_w - 0.36 * cm
    pdf.setFillColor(BLUE)
    pdf.rect(bill_x, bill_y - 0.45 * cm, bill_w, 0.45 * cm, stroke=1, fill=1)
    pdf.setFillColor(WHITE)
    pdf.setFont("Helvetica-Bold", 7)
    bill_heading = (
        f"UPCOMING BILL: {data['bill_term']} "
        f"({data['bill_academic_year']}) - GHC"
    )
    pdf.drawCentredString(
        bill_x + bill_w / 2,
        bill_y - 0.31 * cm,
        bill_heading
    )

    row_h = 0.39 * cm
    current_y = bill_y - 0.45 * cm
    for label, amount in data["bill"]:
        current_y -= row_h
        pdf.setFillColor(WHITE)
        pdf.rect(bill_x, current_y, bill_w, row_h, stroke=1, fill=1)
        pdf.setFillColor(TEXT)
        pdf.setFont("Helvetica-Bold", 6.2)
        pdf.drawString(bill_x + 0.08 * cm, current_y + 0.12 * cm, label)
        pdf.line(bill_x + bill_w * 0.6, current_y, bill_x + bill_w * 0.6, current_y + row_h)
        pdf.setFont("Helvetica", 6.2)
        pdf.drawRightString(bill_x + bill_w - 0.1 * cm, current_y + 0.12 * cm, _money(amount))

    current_y -= row_h
    pdf.rect(bill_x, current_y, bill_w, row_h, stroke=1, fill=0)
    pdf.setFont("Helvetica-Bold", 6.5)
    pdf.drawString(bill_x + 0.08 * cm, current_y + 0.12 * cm, "TOTAL")
    pdf.drawRightString(bill_x + bill_w - 0.1 * cm, current_y + 0.12 * cm, _money(data["bill_total"]))

    signature_y = y - h - 1.5 * cm
    signature_midpoint = x + w / 2
    signature_inset = 1.0 * cm
    signature_gap = 1.0 * cm
    signature_left_start = x + signature_inset
    signature_left_end = signature_midpoint - signature_gap / 2
    signature_right_start = signature_midpoint + signature_gap / 2
    signature_right_end = x + w - signature_inset

    pdf.setStrokeColor(GRID)
    pdf.line(
        signature_left_start,
        signature_y,
        signature_left_end,
        signature_y
    )
    pdf.line(
        signature_right_start,
        signature_y,
        signature_right_end,
        signature_y
    )
    pdf.setFillColor(TEXT)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawCentredString(
        (signature_left_start + signature_left_end) / 2,
        signature_y - 0.35 * cm,
        "Class Teacher's Signature"
    )
    pdf.drawCentredString(
        (signature_right_start + signature_right_end) / 2,
        signature_y - 0.35 * cm,
        "Headmaster's Signature"
    )

    return signature_y - 0.5 * cm


def _draw_grade_footer(pdf: canvas.Canvas, x: float, data: dict[str, Any]) -> None:
    footer_y = 0.75 * cm
    box_x = x + 1.9 * cm
    box_w = PAGE_W - (2 * x) - 3.8 * cm
    box_h = 1.1 * cm
    pdf.rect(box_x, footer_y, box_w, box_h, stroke=1, fill=0)
    pdf.setFont("Helvetica-Bold", 5.9)
    pdf.drawCentredString(box_x + box_w / 2, footer_y + 0.77 * cm, "GRADE INTERPRETATION")
    pdf.setFont("Helvetica", 5.5)
    pdf.drawCentredString(
        box_x + box_w / 2,
        footer_y + 0.48 * cm,
        "80 to 100 (A: Excellent), 70 to 79 (B: Very Good), 60 to 69 (C: Good),",
    )
    pdf.drawCentredString(
        box_x + box_w / 2,
        footer_y + 0.23 * cm,
        "45 to 59 (D: Pass), 35 to 44 (E: Weak), 0 to 34 (F: Very Weak)",
    )
    pdf.setFont("Helvetica", 4.8)
    pdf.drawCentredString(PAGE_W / 2, 0.35 * cm, current_app.config.get("REPORT_DEVELOPER_TEXT", "Generated by School Management System"))


def _draw_logo_or_badge(pdf: canvas.Canvas, x: float, y: float, size: float, path: str | None) -> None:
    if _draw_image_if_exists(pdf, path, x, y, size, size):
        return
    pdf.setStrokeColor(BLUE)
    pdf.setFillColor(LIGHT_BLUE)
    pdf.rect(x, y, size, size, stroke=1, fill=1)
    pdf.setFillColor(BLUE)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(x + size / 2, y + size * 0.58, "SCHOOL")
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(x + size / 2, y + size * 0.38, "LOGO")


def _draw_photo_box(pdf: canvas.Canvas, x: float, y: float, w: float, h: float, path: str | None) -> None:
    if _draw_image_if_exists(pdf, path, x, y, w, h):
        pdf.rect(x, y, w, h, stroke=1, fill=0)
        return
    pdf.setFillColor(colors.HexColor("#E7D5C9"))
    pdf.rect(x, y, w, h, stroke=1, fill=1)
    pdf.setFillColor(colors.HexColor("#B88777"))
    pdf.circle(x + w / 2, y + h * 0.63, 0.36 * cm, stroke=0, fill=1)
    pdf.roundRect(x + w * 0.3, y + h * 0.18, w * 0.4, h * 0.28, 5, stroke=0, fill=1)
    pdf.setFillColor(colors.HexColor("#87A9B5"))
    pdf.rect(x + w * 0.18, y + h * 0.53, w * 0.64, 0.23 * cm, stroke=0, fill=1)


def _draw_image_if_exists(pdf: canvas.Canvas, path: str | None, x: float, y: float, w: float, h: float) -> bool:
    if not path:
        return False
    try:
        if os.path.isabs(path):
            image_path = path
        else:
            static_image_path = os.path.join(current_app.static_folder, path)
            image_path = (
                static_image_path
                if os.path.exists(static_image_path)
                else os.path.join(current_app.root_path, path)
            )
        if not os.path.exists(image_path):
            return False
        pdf.drawImage(ImageReader(image_path), x, y, width=w, height=h, preserveAspectRatio=True, anchor="c")
        return True
    except Exception:
        return False


def _draw_qr_placeholder(pdf: canvas.Canvas, x: float, y: float, size: float) -> None:
    pdf.rect(x, y, size, size, stroke=1, fill=0)
    cell = size / 7
    for row in range(7):
        for col in range(7):
            if row in {0, 1, 5, 6} and col in {0, 1, 5, 6}:
                pdf.setFillColor(BLACK)
                pdf.rect(x + col * cell, y + row * cell, cell * 0.75, cell * 0.75, stroke=0, fill=1)
    pdf.setFillColor(TEXT)


def _draw_label_value(pdf: canvas.Canvas, x: float, y: float, label: str, value: Any, max_value_width: float) -> None:
    pdf.setFillColor(TEXT)
    pdf.setFont("Helvetica-Bold", 7.2)
    label_text = f"{label}:"
    pdf.drawString(x, y, label_text)
    label_w = 4.25 * cm if len(label) > 16 else 3.05 * cm
    pdf.setFont("Helvetica-Bold" if label in {"Position In Class", "Learner's Average Mark", "Average Remark"} else "Helvetica", 7.2)
    if label in {"Position In Class", "Learner's Average Mark", "Average Remark"}:
        pdf.setFillColor(GREEN)
    value_color = GREEN if label in {"Position In Class", "Learner's Average Mark", "Average Remark"} else TEXT
    _fit_left(pdf, str(value or "-"), x + label_w, y, max_value_width, 7.2, color=value_color)
    pdf.setFillColor(TEXT)


def _draw_multiline_center(
    pdf: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    size: float,
    bold: bool = False,
    color: colors.Color = TEXT,
) -> None:
    lines = text.split("\n")
    pdf.setFillColor(color)
    pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    line_gap = size + 1.0
    total_text_height = line_gap * (len(lines) - 1)
    start_y = y + (h / 2) + (total_text_height / 2) - (size * 0.28)
    for index, line in enumerate(lines):
        pdf.drawCentredString(x + w / 2, start_y - (index * line_gap), line)


def _fit_left(
    pdf: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    width: float,
    size: float,
    color: colors.Color = TEXT,
) -> None:
    font = pdf._fontname
    clean = " ".join(str(text).split())
    while size > 4.8 and stringWidth(clean, font, size) > width:
        size -= 0.25
    if stringWidth(clean, font, size) > width:
        clean = _truncate(clean, font, size, width)
    pdf.setFillColor(color)
    pdf.setFont(font, size)
    pdf.drawString(x, y, clean)


def _truncate(text: str, font: str, size: float, width: float) -> str:
    suffix = "..."
    allowed = width - stringWidth(suffix, font, size)
    result = ""
    for char in text:
        if stringWidth(result + char, font, size) > allowed:
            break
        result += char
    return result.rstrip() + suffix


def _subject_position(
    Performance: Any,
    performance: Any,
    student: Any,
    term: str,
    academic_year: str
) -> str:

    class_id = getattr(student, "class_id", None)
    if class_id is None:
        return "-"

    records = (
        Performance.query
        .filter(
            Performance.subject == performance.subject,
            Performance.term == term,
            Performance.academic_year == academic_year,
            Performance.student.has(class_id=class_id),
        )
        .order_by(
            Performance.total_score.desc()
        )
        .all()
    )

    if not records:
        return "-"

    current_rank = 1
    previous_score = None

    for index, record in enumerate(records, start=1):
        current_score = float(
            record.total_score or 0
        )

        if previous_score is None:
            current_rank = 1
        elif current_score < previous_score:
            current_rank = index

        if record.student_id == student.id:
            return _ordinal(current_rank)

        previous_score = current_score

    return "-"


def _overall_position(
    Student,
    Performance,
    student,
    term,
    academic_year,
):
    """
    Calculate overall class position for a specific term and academic year.
    Students with equal averages receive the same position.
    """

    classmates = Student.query.filter_by(
        class_id=student.class_id
    ).all()

    ranking = []

    for learner in classmates:

        results = (
            Performance.query
            .filter_by(
                student_id=learner.id,
                term=term,
                academic_year=academic_year
            )
            .all()
        )

        # If there are no results for the selected term,
        # ignore this student.
        if not results:
            continue

        average = sum(
            float(r.total_score or 0)
            for r in results
        ) / len(results)

        ranking.append({
            "student_id": learner.id,
            "average": round(average, 2)
        })

    # Highest average first
    ranking.sort(
        key=lambda x: x["average"],
        reverse=True
    )

    previous_average = None
    current_rank = 1

    for index, item in enumerate(ranking):

        if previous_average is None:
            current_rank = 1

        elif item["average"] < previous_average:
            current_rank = index + 1

        if item["student_id"] == student.id:
            return current_rank

        previous_average = item["average"]

    return "-"


def _attendance_totals(
    Attendance,
    student,
    term,
    academic_year
):

    attendance = Attendance.query.filter_by(
        student_id=student.id
    ).all()

    present = sum(
        1 for record in attendance
        if str(record.status or "").strip().lower() == "present"
    )

    total = len(attendance)

    return present, total


def _fee_summary(db: Any, FeeStructure: Any, Payment: Any, Invoice: Any, student: Any) -> tuple[float, float, float]:
    fee = FeeStructure.query.filter_by(class_id=student.class_id).first()
    total_fee = float(getattr(fee, "amount", 0) or getattr(student, "fee_amount", 0) or 0)
    total_paid = db.session.query(func.sum(Payment.amount_paid)).filter_by(student_id=student.id).scalar() or 0
    total_paid = float(total_paid)

    invoice_balance = 0.0
    for invoice in Invoice.query.filter_by(student_id=student.id).all():
        amount = float(getattr(invoice, "amount", 0) or 0)
        partial_paid = float(getattr(invoice, "partial_paid", 0) or 0)
        invoice_balance += max(amount - partial_paid, 0)

    balance = invoice_balance if invoice_balance else max(total_fee - total_paid, 0)
    return round(total_fee, 2), round(total_paid, 2), round(balance, 2)


def _latest_value(model: Any, field_name: str, student_id: int) -> str | None:
    field = getattr(model, field_name)
    record = model.query.filter_by(student_id=student_id).order_by(model.id.desc()).first()
    return getattr(record, field_name, None) if record and field is not None else None


def _class_teacher_name(classroom):
    teachers = list(getattr(classroom, "teachers", []) or [])

    if teachers:
        return getattr(
            teachers[0],
            "username",
            "Class Teacher"
        )

    return "Class Teacher"


def _promotion_target(
    current_class: str,
    average: float,
    has_results: bool,
    class_names: list[str] | None = None,
    *,
    term: str
) -> str:
    normalized_term = "".join(term.casefold().split())
    if normalized_term != "term3":
        return "Pending until Term 3"

    if not has_results:
        return "Pending Results"

    current_class = " ".join(current_class.split())
    if not current_class:
        return "Not Available"

    if average < 50:
        return f"Repeat {current_class}"

    normalized_class = "".join(current_class.casefold().split())
    if class_names:
        normalized_classes = [
            (name, "".join(name.casefold().split()))
            for name in class_names
            if name
        ]
        for index, (_, normalized_name) in enumerate(normalized_classes):
            if normalized_name == normalized_class:
                if index + 1 < len(normalized_classes):
                    return normalized_classes[index + 1][0]
                return "Completed"

    promotion_map = {
        "nursery1": "Nursery 2",
        "nursery2": "KG 1",
        "kg1": "KG 2",
        "kg2": "Basic 1",
        "basic1": "Basic 2",
        "basic2": "Basic 3",
        "basic3": "Basic 4",
        "basic4": "Basic 5",
        "basic5": "Basic 6",
        "basic6": "JHS 1",
        "jhs1": "JHS 2",
        "jhs2": "JHS 3",
        "jhs3": "Completed",
    }
    return promotion_map.get(normalized_class, "Not Available")


def _promotion_display(target: str) -> tuple[str, str]:
    repeat_prefix = "Repeat "
    if target.startswith(repeat_prefix):
        return "Repeated", target[len(repeat_prefix):]
    return "Promoted To", target


def _format_term_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
    except ValueError:
        return value


def _grade_and_remark(score: float) -> tuple[str, str]:
    if score >= 80:
        return "A", "Excellent"
    if score >= 70:
        return "B", "Very Good"
    if score >= 60:
        return "C", "Good"
    if score >= 45:
        return "D", "Pass"
    if score >= 35:
        return "E", "Weak"
    return "F", "Very Weak"


def _average_remark(score: float) -> str:
    return _grade_and_remark(score)[1]


def _teacher_remark(score: float) -> str:
    if score >= 80:
        return "Excellent! Keep up the good work"
    if score >= 60:
        return "Good performance. Work harder for excellence"
    if score >= 45:
        return "Fair performance. More revision is needed"
    return "Needs serious attention and regular practice"


def _headteacher_remark(score: float) -> str:
    if score >= 80:
        return "Excellent! Keep up the good work"
    if score >= 60:
        return "Good. Parent/guardian should continue to support learning"
    if score >= 45:
        return "More effort is required next term"
    return "Parent/guardian should meet the class teacher"


def _value(obj: Any, attr: str, default: Any = "-") -> Any:
    value = getattr(obj, attr, None) if obj is not None else None
    return value if value not in (None, "") else default


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _score(value: Any) -> str:
    if value == "":
        return ""
    number = float(value or 0)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _money(value: Any) -> str:
    number = float(value or 0)
    return "-" if number == 0 else f"{number:,.2f}"


def _safe_filename(value: str) -> str:
    clean = []
    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            clean.append(char)
        elif char.isspace():
            clean.append("_")
    return "".join(clean).strip("_") or f"report_{datetime.now():%Y%m%d%H%M%S}"
