"""Build an honest progress report from saved measurements; no inferred passes."""
from datetime import datetime,timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from tools.common import ROOT,write_json


def main():
    reports=ROOT/"reports"; reports.mkdir(exist_ok=True)
    rows=[]
    for name in ["baseline_original_players_legacy","baseline_original_players","baseline_original_ranks","diagnostic_lgd_tiled",
                 "diagnostic_bjg_tiled","diagnostic_github_tiled","diagnostic_original_ranks_int8",
                 "diagnostic_original_ranks_u8s8"]:
        path=reports/name/"metrics.json"
        if not path.exists(): continue
        d=json.loads(path.read_text())
        predictions=[json.loads(l) for l in (path.parent/"predictions.jsonl").read_text().splitlines()]
        rows.append({"name":name,"weights":d["command_parameters"]["weights"],"frames":d["frames"],
            "boxes":sum(len(p["predictions"]) for p in predictions),
            "p95":d["latency_p95_ms"],"cpu":d["process_cpu_percent_of_machine"],"rss":d["peak_rss_mb"],
            "reviewed":d["reviewed_frames"],"accuracy":None})
    tests=ET.parse(reports/"unit_tests.xml").getroot()
    totals={k:sum(int(s.get(k,0)) for s in tests.iter("testsuite")) for k in ["tests","failures","errors","skipped"]}
    table=["| Candidato | Frames | Cajas* | p95 ms | CPU %** | RAM MB** | Precisión |",
        "|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        table.append(f"| {row['name']} | {row['frames']} | {row['boxes']} | {row['p95']:.0f} | {row['cpu']:.1f} | {row['rss']:.0f} | NO MEDIDA |")
    table+=["", "*Observaciones repetidas del mismo clip, no cartas únicas ni aciertos. No hay etiquetas revisadas.",
            "**Recursos del proceso de replay; no incluyen la app completa ni un soak en vivo."]
    baseline=["# Baseline diagnóstico", "", "Se usaron los dos modelos originales descargados con autorización válida, en ONNX Runtime CPU (2 hilos).",
        "30 frames nativos del clip de 10 segundos que ya estaba en el repo; recorte de mesa y HUD enmascarado antes de persistir.",
        "Esto verifica ejecución, mapeo y latencia; no sustituye la sesión actual de Betano ni mide exactitud sin ground truth.",
        "",*table,"", "Metadatos: el modelo de rangos exportó nombres numéricos; su CLASS_MAP autorizado fue aplicado desde el sidecar.",
        "El modelo de jugadores requiere Stretch to; el de rangos Fit within (fallback de Roboflow a borde negro).",
        "La fila players_legacy reproduce confianza .70 y NMS IoU 1.0 (overlap=100). La fila players usa el NMS candidato fijo .45; no confundirlas.",
        "El modelo dealer v17 autorizado tiene 13 clases de rango y NO tiene cuttingcard. El reset manual debe mantenerse.",
        "", "La cuantización S8S8 empeoró la latencia y fue rechazada como candidata de velocidad. U8S8 reduced-range se conserva solo para validación de precisión.",
        "La variante U8S8 fue calibrada con el clip diagnóstico, que por eso no puede servir como evaluación de su precisión."]
    (reports/"baseline.md").write_text("\n".join(baseline)+"\n",encoding="utf-8")
    (reports/("eval_"+datetime.now().strftime("%Y%m%d")+".md")).write_text("\n".join(baseline)+"\n",encoding="utf-8")
    text=["# Estado del contador — INCOMPLETO", "",f"Actualizado UTC: {datetime.now(timezone.utc).isoformat()}",
        "", "La clave privada actual pasó HTTP 200 para ambos modelos originales. La clave vive en el entorno de usuario de Windows, no en git ni en estos reportes.",
        "No se enviaron imágenes al validar acceso o descargar pesos. Las pruebas de visión se procesaron localmente.",
        "", f"Pruebas: {totals['tests']} ejecutadas; {totals['failures']} fallos; {totals['errors']} errores. Evidencia: unit_tests.xml.",
        "Cubren adaptadores, Hi-Lo, true count, ocho mazos, duplicados, frames repetidos/reordenados, cartas iguales adyacentes, unknown, tres resets manuales, privacidad y OCR.",
        "La lógica temporal candidata todavía NO fue integrada ni validada en el motor en vivo; estas pruebas no prueban exactitud de la app.",
        "", "Mesa actual/proveedor: SIN VERIFICAR. Históricamente se vio VIP Blackjack en Español 7 y un shoe aparente. No se deduce proveedor de una alianza comercial del casino.",
        "Host: 1920×1200. Resolución del invitado y altura efectiva del índice actuales: NO MEDIDAS. Penetración y tipo exacto de barajado: NO VERIFICADOS.",
        "El observador mss esperó cinco minutos sin poder identificar una mesa legible; no inició una grabación de datos privados por suposición.",
        "La captura vigente no está aprobada: data/table.json conserva geometry_verified=false y privacy_reviewed=false.",
        "",*table,"", "## Umbrales", "", "| Criterio | Estado |", "|---|---|",
        "| >=300 cartas únicas, rango >=98% | NO MEDIDO |",
        "| Bucket Hi-Lo >=99%; recall por rango >=90% | NO MEDIDO |",
        "| >=40 rondas, running count correcto >=95% | NO MEDIDO EN VIVO |",
        "| Doble conteo 0 | 0 en propiedades y simulaciones ejecutadas; falta replay real |",
        "| Tres shoes / fallback manual | 3 resets probados en núcleo candidato; falta verificar integración real |",
        "| Ciclo completo p95 <=800 ms | NO MEDIDO; solo detector candidato U8S8 ~767 ms |",
        "| App completa CPU <=35%, RAM <=1.5 GB | NO MEDIDO; solo recursos de replay |",
        "| Soak en vivo 30 min sin crash/fuga | NO EJECUTADO |",
        "| >=600 frames útiles, >=2 sesiones independientes | 0 sesiones en vivo verificadas; 30 frames históricos diagnósticos |",
        "", "## Iteraciones de conteo", "",
        "Con 30 shoes sintéticos de ocho mazos y 780 rondas por condición, 10% de pérdidas, 3% de confusiones y 15% de cajas duplicadas:",
        "una confirmación: 18.85% de rondas con running exacto; tres confirmaciones y voto 0.8: 52.44%; voto 0.7: 90.00%.",
        "El último cambio reduce rechazos causados por outliers aislados. Aun así no supera 95%, y no se aflojó más el umbral para forzar aprobación.",
        "El valor 0.7 queda experimental. No se sustituyó el contador de producción con resultados sintéticos.",
        "", "## Qué falta y cómo continuar", "",
        "1. Mesa al frente: aprobar ROI, máscaras y al menos dos anchors estáticos, identificar proveedor/tipo de shoe y medir índices >=24–30 px.",
        "2. Grabar 30–60 min como espectador en dos sesiones; el grabador no mueve el cursor y pausa guardados si los anchors no coinciden o la mesa se vuelve negra.",
        "3. Etiquetar cajas/rangos/identidades por ronda y revisar cruzadamente >=10%; evaluar la sesión excluida de entrenamiento y calibración.",
        "4. Integrar solo el candidato que cumpla métricas, corregir errores de tracking con replay y ejecutar el soak de 30 min / 15 rondas manuales.",
        "5. Si falta exactitud, tools.prepare_training genera un ZIP con separación por sesión/ronda y training/train_rank_colab.ipynb entrena un YOLO nano de 13 rangos fuera de esta PC.",
        "No hay dataset.zip legítimo todavía: faltan datos etiquetados. No se inventaron rondas ni etiquetas para cumplir tamaño.",
        "", "## Ventaja y condiciones de cuenta", "",
        "La ventaja realista de ESTA mesa no se puede estimar: faltan reglas verificadas, penetración observada y distribución de true counts.",
        "Penetración × true count medio no es una fórmula de expectativa. El -0.596% del log histórico no es una medición de la mesa actual.",
        "No se pudo verificar una cláusula vigente de Betano Argentina en una fuente oficial accesible; no se usaron imitaciones del sitio ni afiliados como términos oficiales.",
        "Si sus términos prohíben asistencia externa, existe riesgo de cuenta; esto debe comprobarse antes del uso real.",
        "", "## Reproducción", "",
        "`powershell -File .\\start.ps1 -Mode doctor`", "",
        "`powershell -File .\\start.ps1 -Mode record -Session session_a -Role train -Seconds 1800`", "",
        "`python -m tools.eval --manifest data/session_b/frames.jsonl --labels data/session_b/labels.jsonl --weights models/roboflow/carddetection-v1hqz_17_u8s8.onnx --final`", "",
        "Ejecutar herramientas con .venv312, desde la raíz. Ver COUNTER_README.md. Pesos/datos/reportes son privados y están ignorados por git.",
        "", "Referencias técnicas: [artefactos originales de Roboflow](https://github.com/roboflow/inference/blob/main/inference/core/roboflow_api.py), "
        "[cuantización y reduced-range](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html)."]
    (reports/"FINAL.md").write_text("\n".join(text)+"\n",encoding="utf-8")
    write_json(reports/"status.json",{"complete":False,"tests":totals,"benchmark":rows,
        "live_sessions":0,"reviewed_live_cards":0,"live_rounds":0,"blocker":"Current table ROI not yet visible/verified; labels and independent sessions absent"})
    print(json.dumps({"complete":False,"tests":totals,"report":"reports/FINAL.md"}))


if __name__=="__main__": main()
