1. Windows, Python 3.12 mediante `uv`; ejecutar desde la raíz del repo.
2. Arrancar la app: `powershell -File .\start.ps1` (la validación de precisión está pendiente).
3. La clave privada se lee de `ROBOFLOW_API_KEY`; no pegarla en archivos ni en el chat.
4. Revisar `reports/phase0.md` y configurar ROI, máscaras y anchors en `data/table.json`.
5. El casino debe permanecer visible; todo control del casino lo hace el usuario.
6. Grabar: `.\start.ps1 -Mode record -Session session_a -Role train -Seconds 1800`.
7. Repetir en otra sesión con `-Session session_b -Role eval`.
8. Revisar: `.\start.ps1 -Mode review -Manifest data/session_b/frames.jsonl`.
9. Evaluar: `.\start.ps1 -Mode eval -Manifest data/session_b/frames.jsonl -Labels data/session_b/labels.jsonl`.
10. Criterios y pendientes en `reports/FINAL.md`; executor siempre Off.
