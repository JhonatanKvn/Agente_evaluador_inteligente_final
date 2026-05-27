import csv
import io
import json
import os
import subprocess
import threading
import webbrowser
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, Response, redirect, render_template, request, url_for
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from app.db.repository import (
    delete_student,
    get_evaluation,
    get_student,
    init_db,
    list_evaluations,
    list_latest_evaluations,
    list_student_evaluations,
    list_student_alerts,
    list_student_summary,
    list_students,
    save_evaluation,
    save_student,
)
from app.services.grading import evaluate_with_ocr_space
from app.services.image_processing import prepare_image_for_ocr


BASE_DIR = Path(__file__).resolve().parent
ALLOWED_COURSES = ["Algoritmia y Programación 1", "Algoritmia y Programación 2"]
EVIDENCE_DIR = BASE_DIR / "static" / "uploads"

load_dotenv()
init_db()

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _to_result_dict(result: Any) -> Dict[str, Any]:
    return {
        "score": result.score,
        "max_score": result.max_score,
        "feedback": result.feedback,
        "code_transcription": result.code_transcription,
        "strengths": result.strengths or [],
        "improvements": result.improvements or [],
        "rubric_breakdown": result.rubric_breakdown or [],
    }


def _load_json_list(value: str) -> list:
    try:
        loaded = json.loads(value or "[]")
        return loaded if isinstance(loaded, list) else []
    except json.JSONDecodeError:
        return []


def _evaluation_for_view(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    evaluation["strengths"] = _load_json_list(evaluation.get("strengths_json", "[]"))
    evaluation["improvements"] = _load_json_list(evaluation.get("improvements_json", "[]"))
    evaluation["rubric_breakdown"] = _load_json_list(evaluation.get("rubric_breakdown_json", "[]"))
    return evaluation


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    wrapped: list[str] = []
    for paragraph in str(text or "").splitlines():
        words = paragraph.split()
        if not words:
            wrapped.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                line = candidate
            else:
                wrapped.append(line)
                line = word
        if line:
            wrapped.append(line)
    return wrapped


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    font_name = "arialbd.ttf" if bold else "arial.ttf"
    font_path = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / font_name
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default()


def _generate_evaluation_pdf(evaluation: Dict[str, Any]) -> bytes:
    width, height = 1240, 1754
    margin = 80
    pages: list[Image.Image] = []
    page = Image.new("RGB", (width, height), "#fffaf2")
    draw = ImageDraw.Draw(page)
    title_font = _font(38, bold=True)
    heading_font = _font(26, bold=True)
    body_font = _font(22)
    small_font = _font(18)
    y = 70

    def new_page() -> None:
        nonlocal page, draw, y
        pages.append(page)
        page = Image.new("RGB", (width, height), "#fffaf2")
        draw = ImageDraw.Draw(page)
        y = 70

    def write(text: str, font: ImageFont.ImageFont = body_font, fill: str = "#1f2937", gap: int = 10) -> None:
        nonlocal y
        for line in _wrap_text(draw, text, font, width - margin * 2):
            if y > height - 120:
                new_page()
            draw.text((margin, y), line, font=font, fill=fill)
            y += int(font.size * 1.45) if hasattr(font, "size") else 28
        y += gap

    write("Informe de evaluación", title_font, "#8a2f07", 18)
    write(f"Estudiante: {evaluation['student_name']} ({evaluation['student_code']})", body_font)
    write(f"Curso: {evaluation['course_name']}", body_font)
    write(f"Actividad: {evaluation['activity_name']} - {evaluation['activity_type']}", body_font)
    write(f"Semestre: {evaluation['semester']} | Fecha: {evaluation['created_at']}", small_font)
    write(f"Nota: {evaluation['score']} / {evaluation['max_score']}", heading_font, "#9e4b2a", 22)

    write("Retroalimentación", heading_font, "#4b3428", 10)
    write(evaluation.get("feedback", ""), body_font)

    write("Rúbrica punto por punto", heading_font, "#4b3428", 10)
    for row in evaluation.get("rubric_breakdown", []):
        write(
            f"- {row.get('criterion', '')}: {row.get('score', '')}/{row.get('max', '')}. {row.get('comment', '')}",
            body_font,
            gap=4,
        )

    image_name = evaluation.get("image_filename", "")
    image_path = EVIDENCE_DIR / image_name if image_name else None
    if image_path and image_path.exists():
        if y > height - 700:
            new_page()
        write("Imagen evaluada", heading_font, "#4b3428", 12)
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((width - margin * 2, 760), Image.Resampling.LANCZOS)
            page.paste(img, (margin, y))
            y += img.height + 20
        write(f"Archivo: {image_name}", small_font, "#52606d")

    pages.append(page)
    out = io.BytesIO()
    first, rest = pages[0], pages[1:]
    first.save(out, format="PDF", save_all=True, append_images=rest)
    return out.getvalue()


def _generate_student_report_pdf(student: Dict[str, Any], evaluations: list[Dict[str, Any]]) -> bytes:
    width, height = 1240, 1754
    margin = 80
    pages: list[Image.Image] = []
    page = Image.new("RGB", (width, height), "#fffaf2")
    draw = ImageDraw.Draw(page)
    title_font = _font(38, bold=True)
    heading_font = _font(26, bold=True)
    body_font = _font(22)
    small_font = _font(18)
    y = 70

    def new_page() -> None:
        nonlocal page, draw, y
        pages.append(page)
        page = Image.new("RGB", (width, height), "#fffaf2")
        draw = ImageDraw.Draw(page)
        y = 70

    def write(text: str, font: ImageFont.ImageFont = body_font, fill: str = "#1f2937", gap: int = 10) -> None:
        nonlocal y
        for line in _wrap_text(draw, text, font, width - margin * 2):
            if y > height - 120:
                new_page()
            draw.text((margin, y), line, font=font, fill=fill)
            y += int(font.size * 1.45) if hasattr(font, "size") else 28
        y += gap

    write("Informe general del estudiante", title_font, "#8a2f07", 18)
    write(f"Estudiante: {student['student_name']} ({student['student_code']})", body_font)
    write(f"Curso: {student['course_name']}", body_font)
    if student.get("course_description"):
        write(f"Descripción del curso: {student['course_description']}", small_font)

    if not evaluations:
        write("Este estudiante aún no tiene evaluaciónes registradas.", body_font)
    else:
        scores = [float(item["score"]) for item in evaluations]
        max_scores = [float(item["max_score"]) for item in evaluations]
        avg_score = round(sum(scores) / len(scores), 2)
        avg_max = round(sum(max_scores) / len(max_scores), 2)
        best_score = round(max(scores), 2)
        first_score = round(scores[0], 2)
        last_score = round(scores[-1], 2)
        delta = round(last_score - first_score, 2)

        write("Resumen de rendimiento", heading_font, "#4b3428", 10)
        write(f"- Evaluaciónes registradas: {len(evaluations)}", body_font, gap=4)
        write(f"- Promedio: {avg_score} / {avg_max}", body_font, gap=4)
        write(f"- Mejor nota: {best_score}", body_font, gap=4)
        write(f"- Variación entre primera y última evaluación: {delta}", body_font, gap=16)

        write("Historial de actividades", heading_font, "#4b3428", 10)
        for index, item in enumerate(evaluations, start=1):
            write(
                f"{index}. {item['activity_name']} ({item['activity_type']}) - "
                f"{item['score']} / {item['max_score']} - {item['created_at']}",
                body_font,
                gap=4,
            )

        write("Retroalimentación por evaluación", heading_font, "#4b3428", 10)
        for index, raw_item in enumerate(evaluations, start=1):
            item = _evaluation_for_view(dict(raw_item))
            write(
                f"Evaluación {index}: {item['activity_name']} - Nota {item['score']} / {item['max_score']}",
                heading_font,
                "#9e4b2a",
                8,
            )
            write(item.get("feedback", ""), body_font, gap=12)
            image_name = item.get("image_filename", "")
            image_path = EVIDENCE_DIR / image_name if image_name else None
            if image_path and image_path.exists():
                if y > height - 520:
                    new_page()
                write("Imagen asociada", small_font, "#4b3428", 6)
                with Image.open(image_path) as img:
                    img = img.convert("RGB")
                    img.thumbnail((width - margin * 2, 460), Image.Resampling.LANCZOS)
                    page.paste(img, (margin, y))
                    y += img.height + 18

    pages.append(page)
    out = io.BytesIO()
    first, rest = pages[0], pages[1:]
    first.save(out, format="PDF", save_all=True, append_images=rest)
    return out.getvalue()


def _normalize_header(value: str) -> str:
    value = value.strip().lower()
    replacements = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _looks_like_code(value: str) -> bool:
    compact = value.replace("-", "").replace(".", "").strip()
    return compact.isdigit() and len(compact) >= 2


def _row_to_student(row: list[str], code_index: Optional[int], name_index: Optional[int]) -> Optional[Dict[str, str]]:
    values = [cell.strip() for cell in row if cell and cell.strip()]
    if len(values) < 2:
        return None

    if code_index is not None and name_index is not None:
        if code_index >= len(row) or name_index >= len(row):
            return None
        student_code = row[code_index].strip()
        student_name = row[name_index].strip()
    else:
        code_candidates = [cell for cell in values if _looks_like_code(cell)]
        name_candidates = [cell for cell in values if not _looks_like_code(cell)]
        if not code_candidates or not name_candidates:
            student_code, student_name = values[0], values[1]
        else:
            student_code, student_name = code_candidates[0], name_candidates[0]

    if not student_code or not student_name:
        return None
    return {"student_code": student_code, "student_name": student_name}


def _extract_students_from_rows(rows: list[list[str]]) -> list[Dict[str, str]]:
    clean_rows = [[str(cell or "").strip() for cell in row] for row in rows if any(str(cell or "").strip() for cell in row)]
    if not clean_rows:
        return []

    first_row = [_normalize_header(cell) for cell in clean_rows[0]]
    code_headers = {"codigo", "cod", "code", "id", "identificacion", "documento"}
    name_headers = {"nombre", "nombres", "estudiante", "alumno", "student", "name"}
    code_index = next((i for i, cell in enumerate(first_row) if cell in code_headers or "codigo" in cell), None)
    name_index = next((i for i, cell in enumerate(first_row) if cell in name_headers or "nombre" in cell), None)
    has_header = code_index is not None and name_index is not None

    data_rows = clean_rows[1:] if has_header else clean_rows
    students = []
    seen = set()
    for row in data_rows:
        student = _row_to_student(row, code_index, name_index)
        if not student:
            continue
        key = student["student_code"]
        if key in seen:
            continue
        seen.add(key)
        students.append(student)
    return students


def _parse_delimited_students(content: str) -> list[Dict[str, str]]:
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if ";" in sample else ","
    rows = list(csv.reader(io.StringIO(content), dialect))
    return _extract_students_from_rows(rows)


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t", "")
    value = cell.find("main:v", namespace)
    inline = cell.find("main:is/main:t", namespace)
    if inline is not None and inline.text:
        return inline.text
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        index = int(value.text)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return value.text


def _parse_xlsx_students(file_bytes: bytes) -> list[Dict[str, str]]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as workbook:
        shared_strings = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            shared_root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(text.itertext()).strip()
                for text in shared_root.findall(".//main:si", namespace)
            ]

        sheet_name = next((name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")), "")
        if not sheet_name:
            return []
        sheet_root = ElementTree.fromstring(workbook.read(sheet_name))
        rows = []
        for row in sheet_root.findall(".//main:sheetData/main:row", namespace):
            rows.append([_xlsx_cell_value(cell, shared_strings, namespace) for cell in row.findall("main:c", namespace)])
    return _extract_students_from_rows(rows)


def _parse_students_file(file_bytes: bytes, filename: str) -> list[Dict[str, str]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        return _parse_xlsx_students(file_bytes)
    content = file_bytes.decode("utf-8-sig", errors="ignore")
    return _parse_delimited_students(content)


@app.get("/")
def index():
    return redirect(url_for("students_page"))


@app.get("/students")
def students_page():
    course_filter = request.args.get("course_filter", "").strip()
    student_filter = request.args.get("student_filter", "").strip()
    message = request.args.get("message", "").strip()
    return render_template(
        "students.html",
        message=message,
        allowed_courses=ALLOWED_COURSES,
        students=list_students(course_name=course_filter, query=student_filter),
        course_filter=course_filter,
        student_filter=student_filter,
    )


@app.get("/evaluate")
def evaluate_page():
    course_filter = request.args.get("course_filter", "").strip()
    student_filter = request.args.get("student_filter", "").strip()
    students = list_students(course_name=course_filter, query=student_filter)
    return render_template(
        "evaluate.html",
        error=request.args.get("error", "").strip(),
        students=students,
        allowed_courses=ALLOWED_COURSES,
        course_filter=course_filter,
        student_filter=student_filter,
        selected_student_id="",
        activity_name="Actividad 1",
        activity_type="Taller",
        semester="2026-1",
        max_score=5.0,
        rubric_text=(
            "Criterio: Lógica del algoritmo (40%)\n"
            "Criterio: Sintaxis y estructura en Python (30%)\n"
            "Criterio: Buenas prácticas y legibilidad (30%)\n"
        ),
    )


@app.get("/history")
def history_page():
    filter_name = request.args.get("filter_name", "").strip()
    course_filter = request.args.get("course_filter", "").strip()
    history = list_evaluations(student_name=filter_name, course_name=course_filter, limit=50)
    summary = list_student_summary(student_name=filter_name, course_name=course_filter, limit=50)
    alerts = list_student_alerts(student_name=filter_name, course_name=course_filter, limit=50)
    return render_template(
        "history.html",
        courses=[{"course_name": course, "course_description": "", "students": 0} for course in ALLOWED_COURSES],
        history=history,
        summary=summary,
        alerts=alerts,
        total_evaluations=len(history),
        total_students=len(summary),
        filter_name=filter_name,
        course_filter=course_filter,
    )


@app.get("/history/evaluation/<int:evaluation_id>")
def evaluation_detail_page(evaluation_id: int):
    evaluation = get_evaluation(evaluation_id)
    if not evaluation:
        return redirect(url_for("history_page", filter_name="", course_filter=""))
    evaluation = _evaluation_for_view(evaluation)
    return render_template("evaluation_detail.html", evaluation=evaluation)


@app.get("/history/evaluation/<int:evaluation_id>/pdf")
def evaluation_pdf(evaluation_id: int):
    evaluation = get_evaluation(evaluation_id)
    if not evaluation:
        return redirect(url_for("history_page"))
    evaluation = _evaluation_for_view(evaluation)
    pdf_bytes = _generate_evaluation_pdf(evaluation)
    filename = f"informe_{evaluation['student_code']}_{evaluation['id']}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/report")
def report_page():
    selected_student_id = request.args.get("student_id", "").strip()
    course_filter = request.args.get("course_filter", "").strip()
    student_filter = request.args.get("student_filter", "").strip()
    message = request.args.get("message", "").strip()
    students = list_students(course_name=course_filter, query=student_filter)
    student = get_student(int(selected_student_id)) if selected_student_id else None
    evaluations = []
    latest = None
    first = None
    comparison = None

    if student:
        evaluations = list_student_evaluations(student["student_code"], student["course_name"], limit=100)
        if evaluations:
            first = evaluations[0]
            latest = evaluations[-1]
            latest = _evaluation_for_view(latest)
            if len(evaluations) == 1:
                comparison = {
                    "status": "first",
                    "text": "Primera evaluación registrada. Aún no hay comparativas de rendimiento.",
                }
            else:
                delta = round(float(latest["score"]) - float(first["score"]), 2)
                comparison = {
                    "status": "comparison",
                    "delta": delta,
                    "text": f"Comparación entre Evaluación 1 y la evaluación mas reciente: variación de {delta:.2f} puntos.",
                }

    return render_template(
        "report.html",
        message=message,
        students=students,
        allowed_courses=ALLOWED_COURSES,
        selected_student_id=selected_student_id,
        course_filter=course_filter,
        student_filter=student_filter,
        student=student,
        evaluations=evaluations,
        latest=latest,
        first=first,
        comparison=comparison,
        latest_evaluations=list_latest_evaluations(limit=10),
    )


@app.get("/report/student/<int:student_id>/pdf")
def student_report_pdf(student_id: int):
    student = get_student(student_id)
    if not student:
        return redirect(url_for("report_page"))
    evaluations = list_student_evaluations(student["student_code"], student["course_name"], limit=200)
    pdf_bytes = _generate_student_report_pdf(student, evaluations)
    filename = f"informe_estudiante_{student['student_code']}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/students")
def create_student():
    student_name = request.form.get("student_name", "").strip()
    student_code = request.form.get("student_code", "").strip()
    course_name = request.form.get("course_name", "").strip()
    course_description = request.form.get("course_description", "").strip()

    if not student_name or not student_code or not course_name:
        return redirect(url_for("students_page", message="Completa nombre, código y curso del estudiante."))
    if course_name not in ALLOWED_COURSES:
        return redirect(url_for("students_page", message="Curso no permitido. Usa Algoritmia y Programación 1 o 2."))

    save_student(
        {
            "student_name": student_name,
            "student_code": student_code,
            "course_name": course_name,
            "course_description": course_description,
        }
    )
    return redirect(url_for("students_page", course_filter=course_name, message="Estudiante guardado."))


@app.post("/students/import")
def import_students():
    course_name = request.form.get("course_name", "").strip()
    course_description = request.form.get("course_description", "").strip()
    uploaded = request.files.get("students_file")

    if course_name not in ALLOWED_COURSES:
        return redirect(url_for("students_page", message="Selecciona un curso permitido para importar."))
    if not uploaded or not uploaded.filename:
        return redirect(url_for("students_page", course_filter=course_name, message="Debes subir un archivo de estudiantes."))

    supported_formats = {".txt", ".csv", ".tsv", ".xlsx"}
    file_suffix = Path(uploaded.filename).suffix.lower()
    if file_suffix not in supported_formats:
        return redirect(
            url_for(
                "students_page",
                course_filter=course_name,
                message="Formato no soportado. Usa TXT, CSV, TSV o XLSX.",
            )
        )

    parsed_students = _parse_students_file(uploaded.read(), uploaded.filename)
    if not parsed_students:
        return redirect(
            url_for(
                "students_page",
                course_filter=course_name,
                message="No se encontraron nombres y códigos en el archivo.",
            )
        )
    imported = 0
    for student in parsed_students:
        save_student(
            {
                "student_name": student["student_name"],
                "student_code": student["student_code"],
                "course_name": course_name,
                "course_description": course_description,
            }
        )
        imported += 1

    return redirect(url_for("students_page", course_filter=course_name, message=f"Importados {imported} estudiantes."))


@app.post("/students/<int:student_id>/delete")
def remove_student(student_id: int):
    course_filter = request.form.get("course_filter", "").strip()
    student_filter = request.form.get("student_filter", "").strip()
    deleted = delete_student(student_id)
    if not deleted:
        message = "No se encontro el estudiante para borrar."
    else:
        message = f"Estudiante {deleted['student_name']} eliminado del listado. Sus evaluaciónes históricas se conservan."
        course_filter = course_filter or deleted["course_name"]
    return redirect(
        url_for(
            "students_page",
            course_filter=course_filter,
            student_filter=student_filter,
            message=message,
        )
    )


@app.post("/evaluate")
def evaluate():
    selected_student_id = request.form.get("student_id", "").strip()
    activity_name = request.form.get("activity_name", "").strip()
    activity_type = request.form.get("activity_type", "").strip()
    semester = request.form.get("semester", "").strip()
    rubric_text = request.form.get("rubric_text", "").strip()
    course_filter = request.form.get("course_filter", "").strip()

    try:
        max_score = float(request.form.get("max_score", "5"))
    except ValueError:
        max_score = 5.0

    uploaded = request.files.get("code_image")
    error: Optional[str] = None
    result = None
    eval_id = None
    selected_student = None

    if not selected_student_id:
        error = "Debes seleccionar un estudiante."
    else:
        selected_student = get_student(int(selected_student_id))
        if not selected_student:
            error = "El estudiante seleccionado no existe."

    if not error and not activity_name:
        error = "Debes escribir el nombre de la evaluación."
    elif not error and not activity_type:
        error = "Debes escribir el tipo de actividad."
    elif not activity_name:
        error = "Debes escribir el nombre de la evaluación."
    elif not activity_type:
        error = "Debes escribir el tipo de actividad."
    elif not semester:
        error = "Debes escribir el semestre (ej: 2026-1)."
    elif not rubric_text:
        error = "Debes escribir las rúbricas de evaluación."
    elif not uploaded or not uploaded.filename:
        error = "Debes subir una imagen del código."

    if not error:
        try:
            api_key = os.getenv("OCRSPACE_API_KEY", "helloworld").strip()
            processed_bytes, processed_name = prepare_image_for_ocr(uploaded.read(), uploaded.filename)
            safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in processed_name)
            evidence_name = f"{selected_student['student_code']}_{activity_name}_{safe_name}"
            evidence_name = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in evidence_name)
            (EVIDENCE_DIR / evidence_name).write_bytes(processed_bytes)
            raw_result = evaluate_with_ocr_space(
                api_key=api_key,
                rubric_text=rubric_text,
                image_bytes=processed_bytes,
                filename=evidence_name,
                max_score=max_score,
            )
            result = _to_result_dict(raw_result)
            eval_id = save_evaluation(
                {
                    "student_name": selected_student["student_name"],
                    "student_code": selected_student["student_code"],
                    "activity_name": activity_name,
                    "activity_type": activity_type,
                    "semester": semester,
                    "course_name": selected_student["course_name"],
                    "course_description": selected_student["course_description"],
                    "mode": "ocrspace",
                    "score": result["score"],
                    "max_score": result["max_score"],
                    "feedback": result["feedback"],
                    "code_transcription": result["code_transcription"],
                    "strengths_json": json.dumps(result["strengths"], ensure_ascii=False),
                    "improvements_json": json.dumps(result["improvements"], ensure_ascii=False),
                    "rubric_breakdown_json": json.dumps(result["rubric_breakdown"], ensure_ascii=False),
                    "rubric_text": rubric_text,
                    "image_filename": evidence_name,
                }
            )
            return redirect(
                url_for(
                    "report_page",
                    student_id=selected_student_id,
                    message=f"Evaluación guardada con ID {eval_id}.",
                )
            )
        except UnidentifiedImageError:
            error = "El archivo no es una imagen válida. Sube JPG, JPEG, PNG o WEBP."
        except ValueError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"No se pudo evaluar la entrega: {exc}"

    return render_template(
        "evaluate.html",
        max_score=max_score,
        selected_student_id=selected_student_id,
        activity_name=activity_name,
        activity_type=activity_type,
        semester=semester,
        rubric_text=rubric_text,
        error=error,
        students=list_students(course_name=course_filter),
        allowed_courses=ALLOWED_COURSES,
        course_filter=course_filter,
        student_filter="",
    )


@app.get("/new")
def new_evaluation():
    return redirect(url_for("evaluate_page"))


def run() -> None:
    debug_mode = os.getenv("FLASK_DEBUG", "0").strip() == "1"
    port = int(os.getenv("APP_PORT", "5000"))
    auto_open = os.getenv("AUTO_OPEN_BROWSER", "1").strip() == "1"
    if auto_open:
        url = f"http://127.0.0.1:{port}/students"

        def open_app() -> None:
            commands = [
                ["cmd", "/c", "start", "", "chrome", url],
                ["cmd", "/c", "start", "", url],
            ]
            for command in commands:
                try:
                    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except Exception:
                    continue
            try:
                os.startfile(url)  # type: ignore[attr-defined]
            except Exception:
                webbrowser.open(url)

        threading.Timer(1.2, open_app).start()
    app.run(host="127.0.0.1", port=port, debug=debug_mode, use_reloader=False)

