# Evaluador OCR de Codigo Manuscrito

Aplicacion Flask con interfaz HTML limpia para:

- cargar rubricas,
- subir foto del codigo escrito a mano,
- evaluar con OCR.Space,
- guardar historial en SQLite por actividad y semestre,
- generar alertas si el estudiante no mejora en sus ultimas 3 notas.
- preprocesar automaticamente la imagen (max 1000px, grises, JPG 70%) antes del OCR.

## Ejecutar

```powershell
cd "C:\Users\ACER 609283\OneDrive\Desktop\U\13-1\ia\agente_tesis"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env -Force
.\.venv\Scripts\python.exe run.py
```

Abrir: http://127.0.0.1:5000

## .env

```env
OCRSPACE_API_KEY=helloworld
```

version final

## Estructura

```text
app/
  db/
    repository.py
  services/
    grading.py
    image_processing.py
  web/
    server.py
    templates/
    static/
run.py
data/
legacy/
```
