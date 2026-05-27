from dataclasses import dataclass
import os
from typing import Any, Dict, List

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class EvaluationResult:
    score: float
    max_score: float
    feedback: str
    code_transcription: str
    strengths: List[str]
    improvements: List[str]
    rubric_breakdown: List[Dict[str, Any]]


def _normalize_criteria(rubric_text: str) -> List[str]:
    lines = [ln.strip() for ln in rubric_text.splitlines() if ln.strip()]
    out: List[str] = []
    for ln in lines:
        cleaned = ln.replace("Criterio:", "").replace("criterio:", "").strip(" -")
        if cleaned:
            out.append(cleaned)
    return out or ["Lógica", "Sintaxis", "Buenas prácticas"]


def _programming_confidence(transcription: str) -> tuple[int, List[str]]:
    text = (transcription or "").strip()
    low = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    score = 0
    reasons: List[str] = []

    keywords = [
        "def", "return", "for", "while", "if", "else", "elif", "print", "input",
        "class", "import", "function", "var", "let", "const", "int", "float",
        "string", "lista", "array", "algoritmo", "programación",
    ]
    keyword_hits = sum(1 for keyword in keywords if f"{keyword} " in low or f"{keyword}(" in low or keyword in low.split())
    if keyword_hits >= 2:
        score += 2
        reasons.append("palabras clave de programación")
    elif keyword_hits == 1:
        score += 1

    symbols = ["=", "==", ">", "<", "(", ")", "[", "]", "{", "}", ":", ";"]
    symbol_hits = sum(text.count(symbol) for symbol in symbols)
    if symbol_hits >= 5:
        score += 2
        reasons.append("símbolos propios de código")
    elif symbol_hits >= 2:
        score += 1

    code_like_lines = 0
    for line in lines:
        line_low = line.lower()
        has_keyword = any(word in line_low.split() for word in ["def", "for", "while", "if", "return", "print", "class"])
        has_assignment = "=" in line and not line_low.startswith(("nombre", "código", "fecha"))
        has_call = "(" in line and ")" in line
        if has_keyword or has_assignment or has_call:
            code_like_lines += 1
    if code_like_lines >= 3:
        score += 2
        reasons.append("varias lineas con estructura de código")
    elif code_like_lines >= 1:
        score += 1

    if len(lines) >= 4:
        score += 1
    if any(line.startswith(("    ", "\t")) for line in text.splitlines()):
        score += 1
        reasons.append("indentación")

    return score, reasons


def _ensure_programming_exercise(transcription: str) -> None:
    score, reasons = _programming_confidence(transcription)
    if score >= 4:
        return
    raise ValueError("Imagen no válida, no corresponde a la actividad.")


def _code_features(code: str) -> Dict[str, Any]:
    low = code.lower()
    raw_lines = code.splitlines()
    lines = [line for line in raw_lines if line.strip()]
    stripped = [line.strip() for line in lines]
    assignments = [
        line for line in stripped
        if "=" in line and "==" not in line and not line.lower().startswith(("print", "return"))
    ]
    loops = [line for line in stripped if line.lower().startswith(("for ", "while "))]
    conditionals = [line for line in stripped if line.lower().startswith(("if ", "elif ", "else"))]
    function_defs = [line for line in stripped if line.lower().startswith(("def ", "function "))]
    returns = [line for line in stripped if line.lower().startswith("return")]
    outputs = [line for line in stripped if "print" in line.lower()]
    inputs = [line for line in stripped if "input" in line.lower()]
    comparisons = sum(low.count(op) for op in [">=", "<=", "==", "!=", ">", "<"])
    colon_lines = [line for line in stripped if line.endswith(":")]

    return {
        "line_count": len(lines),
        "assignments": assignments,
        "loops": loops,
        "conditionals": conditionals,
        "function_defs": function_defs,
        "returns": returns,
        "outputs": outputs,
        "inputs": inputs,
        "comparisons": comparisons,
        "colon_lines": colon_lines,
        "has_logic": bool(loops or conditionals or returns),
        "has_function": bool(function_defs),
        "has_indent": any(line.startswith((" ", "\t")) for line in raw_lines if line.strip()),
        "balanced_parentheses": code.count("(") == code.count(")"),
        "balanced_brackets": code.count("[") == code.count("]"),
        "has_list": "[" in code and "]" in code,
        "has_result_variable": any("resultado" in line.lower() or "result" in line.lower() for line in stripped),
        "has_spanish_pseudocode": any(word in low for word in ["algoritmo", "inicio", "fin", "entonces", "para cada"]),
    }


def _feature_level(features: Dict[str, Any], criterion: str) -> tuple[float, str]:
    c_low = criterion.lower()
    has_pairs = features["balanced_parentheses"] and features["balanced_brackets"]

    if "log" in c_low or "algorit" in c_low:
        factor = 0.45
        if features["has_function"]:
            factor += 0.15
        if features["loops"]:
            factor += 0.15
        if features["conditionals"]:
            factor += 0.15
        if features["returns"] or features["outputs"]:
            factor += 0.08
        comment = "Evalua la secuencia de pasos, estructuras de decisión/repetición y cierre de la solución."
    elif "sintax" in c_low or "estructura" in c_low:
        factor = 0.45
        if has_pairs:
            factor += 0.18
        if features["has_indent"]:
            factor += 0.12
        if features["colon_lines"]:
            factor += 0.1
        if features["assignments"]:
            factor += 0.08
        comment = "Revisa uso de paréntesis/corchetes, dos puntos, asignaciones e indentación."
    elif "practica" in c_low or "legib" in c_low or "estilo" in c_low:
        factor = 0.45
        if features["has_function"]:
            factor += 0.14
        if features["line_count"] >= 5:
            factor += 0.08
        if features["has_result_variable"]:
            factor += 0.08
        if features["has_indent"]:
            factor += 0.12
        comment = "Valora claridad, nombres de variables, organizacion visual y facilidad de lectura."
    else:
        factor = 0.45
        if features["has_logic"]:
            factor += 0.15
        if has_pairs:
            factor += 0.1
        if features["assignments"]:
            factor += 0.1
        comment = "Criterio evaluado según evidencias generales encontradas en el código."

    return max(0.2, min(0.96, factor)), comment


def _build_feedback_report(
    *,
    score: float,
    max_score: float,
    evidence: List[str],
    strengths: List[str],
    improvements: List[str],
    features: Dict[str, Any],
) -> str:
    if score >= max_score * 0.85:
        performance = "Desempeño alto"
        summary = "La solución evidencia una estructura sólida y varios elementos esperados para el ejercicio."
    elif score >= max_score * 0.65:
        performance = "Desempeño medio"
        summary = "La solución cumple parcialmente el objetivo, aúnque requiere ajustes para ganar precisión y claridad."
    else:
        performance = "Desempeño básico"
        summary = "La solución muestra una aproximacion inicial, pero necesita reforzar lógica, estructura y legibilidad."

    evidence_items = evidence[:4] or ["Se detecta una estructura inicial, pero con poca evidencia concreta en la transcripción OCR."]
    strengths_items = strengths[:4]
    improvement_items = improvements[:4]

    technical_notes = [
        f"Lineas transcritas con contenido: {features['line_count']}.",
        f"Paréntesis balanceados: {'sí' if features['balanced_parentheses'] else 'no'}.",
        f"Corchetes balanceados: {'sí' if features['balanced_brackets'] else 'no'}.",
        f"Indentación detectada: {'sí' if features['has_indent'] else 'no'}.",
    ]

    recommendation = (
        "Como siguiente paso, el estudiante debería corregir los puntos señalados, "
        "probar el algoritmo con al menos dos casos de entrada y verificar que la salida corresponda al resultado esperado."
    )

    def bullet_list(items: List[str]) -> str:
        return "\n".join(f"- {item}" for item in items)

    return (
        "INFORME DE RETROALIMENTACIÓN\n\n"
        f"1. Resultado general\n"
        f"- Nota estimada: {score}/{max_score}.\n"
        f"- Nivel: {performance}.\n"
        f"- Resumen: {summary}\n\n"
        f"2. Evidencias detectadas en el código\n"
        f"{bullet_list(evidence_items)}\n\n"
        f"3. Fortalezas\n"
        f"{bullet_list(strengths_items)}\n\n"
        f"4. Aspectos por mejorar\n"
        f"{bullet_list(improvement_items)}\n\n"
        f"5. Observaciones técnicas del OCR\n"
        f"{bullet_list(technical_notes)}\n\n"
        f"6. Recomendacion final\n"
        f"- {recommendation}"
    )


def _rule_based_eval(transcription: str, rubric_text: str, max_score: float) -> EvaluationResult:
    code = (transcription or "").strip()
    criteria = _normalize_criteria(rubric_text)
    features = _code_features(code)

    factors: List[float] = []
    criterion_notes: List[str] = []
    for c in criteria:
        f, note = _feature_level(features, c)
        factors.append(f)
        criterion_notes.append(note)

    per_max = max_score / max(len(criteria), 1)
    breakdown: List[Dict[str, Any]] = []
    total = 0.0
    for i, c in enumerate(criteria):
        s = round(per_max * factors[i], 2)
        total += s
        if factors[i] >= 0.82:
            level = "Alto"
        elif factors[i] >= 0.65:
            level = "Medio"
        else:
            level = "Básico"
        comment = f"Nivel {level}. {criterion_notes[i]}"
        breakdown.append({"criterion": c, "score": s, "max": round(per_max, 2), "comment": comment})

    score = round(min(max_score, total), 2)

    strengths: List[str] = []
    improvements: List[str] = []
    if features["has_function"]:
        strengths.append("Define una función, lo que ayuda a encapsular la solución y reutilizarla.")
    if features["loops"]:
        strengths.append("Usa estructura repetitiva para recorrer o procesar datos.")
    if features["conditionals"]:
        strengths.append("Incluye toma de decisiónes mediante condicionales.")
    if features["returns"]:
        strengths.append("La solución devuelve un resultado, adecuado para ejercicios con funciónes.")
    if features["outputs"]:
        strengths.append("Presenta una salida visible para verificar el resultado.")
    if features["balanced_parentheses"] and features["balanced_brackets"]:
        strengths.append("Los paréntesis y corchetes se observan balanceados en la transcripción.")

    if not features["has_function"]:
        improvements.append("Encapsular la solución en una función con nombre claro y parámetros definidos.")
    if not features["loops"] and features["has_list"]:
        improvements.append("Si el ejercicio trabaja con listas, conviene recorrerlas con for o while.")
    if not features["conditionals"] and features["comparisons"] == 0:
        improvements.append("Agregar comparaciónes o condicionales cuando el problema requiera decidir entre valores.")
    if not features["returns"] and features["has_function"]:
        improvements.append("Incluir return para entregar el resultado de la función.")
    if not features["has_indent"]:
        improvements.append("Mejorar la indentación para que se distingan claramente bloques como if, for o def.")
    if not features["balanced_parentheses"]:
        improvements.append("Revisar que todos los paréntesis abiertos tengan su cierre correspondiente.")
    if not features["balanced_brackets"]:
        improvements.append("Revisar que todos los corchetes de listas o índices esten completos.")
    if features["line_count"] < 4:
        improvements.append("Desarrollar mas pasos de la solución; la transcripción tiene muy pocas lineas evaluables.")

    strengths = strengths or ["Se identifica intento de estructura de solución."]
    improvements = improvements or ["Agregar casos de prueba y comentarios cortos para mayor claridad."]

    evidence = []
    if features["function_defs"]:
        evidence.append(f"función detectada: {features['function_defs'][0]}")
    if features["loops"]:
        evidence.append(f"ciclo detectado: {features['loops'][0]}")
    if features["conditionals"]:
        evidence.append(f"condicional detectado: {features['conditionals'][0]}")
    if features["assignments"]:
        evidence.append(f"asignacion detectada: {features['assignments'][0]}")
    feedback = _build_feedback_report(
        score=score,
        max_score=max_score,
        evidence=evidence,
        strengths=strengths,
        improvements=improvements,
        features=features,
    )
    return EvaluationResult(
        score=score,
        max_score=max_score,
        feedback=feedback,
        code_transcription=code or "No se pudo transcribir texto legible desde la imagen.",
        strengths=strengths,
        improvements=improvements,
        rubric_breakdown=breakdown,
    )


def evaluate_with_ocr_space(
    *, api_key: str, rubric_text: str, image_bytes: bytes, filename: str, max_score: float
) -> EvaluationResult:
    for proxy_var in (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY",
    ):
        os.environ.pop(proxy_var, None)
    os.environ["NO_PROXY"] = "*"

    url = "https://api.ocr.space/parse/image"
    data = {
        "apikey": api_key,
        "language": "eng",
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
    }

    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    files = {"file": (filename, image_bytes, "image/jpeg")}
    try:
        resp = session.post(
            url,
            files=files,
            data=data,
            timeout=60,
            proxies={"http": "", "https": ""},
            verify=False,
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "No se pudo conectar con OCR.Space. Revisa tu conexión a internet, "
            "desactiva VPN/proxy si aplica o intenta nuevamente en unos segundos. "
            f"Detalle tecnico: {exc}"
        ) from exc

    if resp.status_code >= 400:
        raise RuntimeError(f"Error OCR.Space ({resp.status_code}): {resp.text}")

    payload = resp.json()
    if payload.get("IsErroredOnProcessing"):
        msgs = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "Fallo de OCR.Space"
        raise RuntimeError(f"OCR.Space no pudo procesar la imagen: {msgs}")

    parsed = payload.get("ParsedResults", [])
    text = str(parsed[0].get("ParsedText", "")).strip() if parsed else ""
    _ensure_programming_exercise(text)
    return _rule_based_eval(text, rubric_text, max_score)


def evaluate_demo(*, rubric_text: str, max_score: float) -> EvaluationResult:
    _ = rubric_text
    score = round(max_score * 0.72, 2)
    return EvaluationResult(
        score=score,
        max_score=max_score,
        feedback=(
            "La solución tiene una estructura funciónal, pero requiere mejorar manejo de errores "
            "y claridad en nombres de variables."
        ),
        code_transcription="def suma(a,b):\n  return a+b\n\nprint(suma(2,3))",
        strengths=[
            "La lógica principal del algoritmo esta presente.",
            "Uso correcto de función simple y retorno.",
        ],
        improvements=[
            "Agregar válidacion de tipos de entrada.",
            "Mejorar indentación y nombres de variables.",
            "Incluir casos de prueba adicionales.",
        ],
        rubric_breakdown=[
            {
                "criterion": "Lógica",
                "score": round(max_score * 0.32, 2),
                "max": round(max_score * 0.4, 2),
                "comment": "Cumple parcialmente.",
            },
            {
                "criterion": "Sintaxis",
                "score": round(max_score * 0.2, 2),
                "max": round(max_score * 0.3, 2),
                "comment": "Presenta detalles menores.",
            },
            {
                "criterion": "Buenas prácticas",
                "score": round(max_score * 0.2, 2),
                "max": round(max_score * 0.3, 2),
                "comment": "Puede mejorar legibilidad.",
            },
        ],
    )

