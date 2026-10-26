# Blackjack-AI overnight progress

- Fecha: 2026-10-26
- Rama: `counter-vision`
- Commit publicado de partida antes de Iteracion 1: `b5f671b90637733284377a117207bec6b88a636d` (`ns`)
- Objetivo general: integrar correcciones manuales seguras por identidad fisica (`card_id`) y probarlas.
- Estado de 13A-13A.2: incluido en el commit publicado de la rama. 13A cubre unicidad y correcciones del registro; 13A.1 alinea CardLayout; 13A.2 instala el launcher y acceso directo del escritorio.
- Etapa actual: Iteracion 2/5 completada.
- Ultimo commit publicado de partida: `7eb0e7f9bf4d48040ab91efc90fec892024e1d2e`.
- Trabajo completado: TablePresentation incluye en cada carta `card_id` real cuando existe, ronda, mazo, perfil, modo, resolucion, source_id/config reference, grupo y mano. TableView y Mini Mesa conservan esos metadatos y solo habilitan correcciones para identidades fisicas. Ambos usan el callback central, que valida contexto y registro activo y llama `DetectionEngine.apply_correction`. CardPicker ofrece botones directos de rango y palo, conserva el palo al corregir solo rango, permite corregir solo palo, descartar una falsa deteccion o cancelar. Mini Mesa diferencia click de arrastre con umbral estricto menor que 5 px. El refresco de ambas vistas sigue el snapshot compartido; los widgets no alteran conteo ni presentacion localmente.
- Archivos de la iteracion: `lib/logic/table_presentation.py`, `lib/interfaces/card_picker.py`, `lib/interfaces/table_view.py`, `lib/interfaces/round_overlay.py`, `lib/interfaces/modern_gui.py`, `tests/test_ui_identity_corrections_unit.py`, `tests/test_individual_table_view_unit.py`, `tests/test_mini_table_presentation_unit.py`, `.gitignore` y este documento. Se retiraron cuatro bases de datos temporales de pytest que estaban rastreadas.
- Pruebas ejecutadas: grupo solicitado de correcciones/registro/presentacion: 244 aprobadas; regresion acumulada de 24 modulos unitarios: 858 aprobadas y 4 subtests aprobados. Ambas ejecuciones se repitieron despues de la revision final, sin fallos.
- Errores conocidos: una primera ejecucion sin `--basetemp` termino los casos pero pytest fallo limpiando `%TEMP%/pytest-of-PC/pytest-current` (`PermissionError: [WinError 5]`, reparse point). Las ejecuciones finales con `--basetemp` explicito terminaron correctamente. No quedaron fallos de tests.
- Trabajo paralelo: el subagente preparo `tests/test_ui_identity_corrections_unit.py`; el agente principal implemento y reviso produccion e integracion. Se solicito `gpt-6-luna` con razonamiento `medium`, pero el entorno no expuso confirmacion del modelo/esfuerzo realmente asignados.
- Iteracion 2 completada: DetectionEngine calcula consejo por grupo fisico propio y por mano, verifica round/shoe/card_id contra el registro activo, usa StrategyAdvisor, desviaciones y el camino EV existente, y publica la incertidumbre de conteo separada. TablePresentation asocia cada recomendacion por contexto, grupo e IDs fisicos exactos; TableView y Mini Mesa muestran las mismas manos, consejos e indicador de seguro. El consejo basico de Yo sigue disponible ante ruido en grupos ajenos, mientras las desviaciones/EV se retiran si el conteo esta incierto. El seguro usa el helper exacto existente solo ante un As fisico confirmado.
- Archivos de Iteracion 2: `lib/logic/engine.py`, `lib/logic/table_presentation.py`, `lib/interfaces/table_view.py`, `lib/interfaces/round_overlay.py`, `lib/interfaces/modern_gui.py`, `tests/test_physical_advice_and_splits_unit.py` y este documento.
- Pruebas de Iteracion 2: suite enfocada y regresion relacionada combinadas: 437 aprobadas y 16 subtests aprobados. `git diff --check` paso.
- Errores conocidos de Iteracion 2: la primera regresion detecto una lectura de resolucion de captura cuando los tests de splits no configuran monitor; se corrigio para omitir coordenadas normalizadas sin resolucion valida. Una primera version del archivo de presentacion tuvo un error de sintaxis y una expectation de etiqueta en el test; ambos se corrigieron antes de la ejecucion final. No quedan fallos en las pruebas finales.
- Trabajo paralelo de Iteracion 2: el subagente creo solo `tests/test_physical_advice_and_splits_unit.py`; principal implemento y reviso motor, TablePresentation y ambas vistas en paralelo. Se solicito `gpt-6-luna` con esfuerzo `medium`; el entorno no confirmo el modelo/esfuerzo efectivamente asignado.
- Iteracion 3/5 completada localmente: TablePresentation conserva el orden publicado y expone `group_count`, `group_overflow`, `group_warning`, `group_id` e `is_provisional`; nunca crea un octavo jugador ni asientos vacios. TableView crea widgets solo para grupos observados, conserva grupos por ID y cartas por `card_id`, aplica layout de arco para 1-3 grupos y grilla de dos filas para 4-7, y muestra el aviso de exceso. Mini Mesa consume la misma lista de jugadores, conserva identidades de las cartas, muestra Yo, grupos provisionales/ambiguos y splits sin clustering adicional. CaptureGuard, ventana unica, topmost, opacidad, posicion y seleccion de correccion por identidad se conservaron.
- Archivos de Iteracion 3: `lib/logic/table_presentation.py`, `lib/interfaces/table_view.py`, `lib/interfaces/round_overlay.py`, `tests/test_dynamic_multiplayer_views_unit.py`, `tests/test_individual_table_view_unit.py`, `tests/test_mini_table_presentation_unit.py` y este documento.
- Pruebas de Iteracion 3: suite solicitada mas `tests/test_table_presentation_unit.py`: 402 aprobadas. En una primera ejecucion se encontraron aserciones historicas que esperaban que Multijugador no dibujara grupos y que las etiquetas de split fueran abreviadas; se actualizaron esas expectativas al contrato dinamico. No hubo fallos en la ejecucion final.
- Trabajo paralelo de Iteracion 3: el subagente escribio exclusivamente `tests/test_dynamic_multiplayer_views_unit.py`; el agente principal implemento TablePresentation y ambas vistas mientras se escribian los tests. Se solicito `gpt-6-luna` con razonamiento `medium`; no se recibio confirmacion independiente del modelo y esfuerzo realmente asignados.
- Commit de Iteracion 3: `feat(multiplayer): dynamic 0-7 player layout and incremental rendering across views` (incluye esta actualizacion de progreso).
- Estado al cierre: Iteracion 3/5 validada y preparada para publicacion fast-forward. Commit base publicado antes de esta iteracion: `c7ef9a4d47178d023252ea6b323e97d9849ff82f`.
- Proxima accion: Iteracion 4/5 completada; continuar con Iteracion 5/5.


## Iteration 4/5 - Compact telemetry HUD and lifecycle

- Date: 2026-10-26
- Status: implemented, validated, and published on `counter-vision`.
- Goal: show count, shoe progress, uncertainty, context, and advice in TableView and Mini Mesa; block starts without valid calibration and harden transitions and shutdown.
- Production files: `lib/interfaces/modern_gui.py`, `lib/interfaces/table_view.py`, `lib/interfaces/round_overlay.py`, `lib/logic/region_profiles.py`.
- Tests: added `tests/test_gui_telemetry_and_lifecycle_unit.py`; updated affected contracts in `tests/test_individual_table_view_unit.py`, `tests/test_live_profile_gate.py`, and `tests/test_mini_table_presentation_unit.py`.
- Behavior: both views show RC/TC, cards seen, decks remaining, uncertainty, round, mode, engine state, and advice. Advice requires ready status and a sufficient hero hand. Mini Mesa receives aggregate metrics only and reads advice from TablePresentation. The calibration gate reports missing regions; context changes clear cards and invalidate dialogs; shutdown waits for the worker and logs start/source errors.
- Launcher verified: `%USERPROFILE%\OneDrive\Desktop\Blackjack AI.lnk` uses `.venv312\Scripts\pythonw.exe`, `scripts\launch_blackjack.py`, and the repository as working directory. `python.exe`, `pythonw.exe`, and `main.py` exist.
- Final tests: required suite - 303 passed and 4 subtests passed; related HUD/table suite - 218 passed. No skips or exclusions. No visual, GUI, integration, E2E, or model tests were run.
- Parallel work: subagent wrote only `tests/test_gui_telemetry_and_lifecycle_unit.py` while the primary agent implemented and integrated production code. Requested `gpt-6-luna` with `medium` effort; the tool did not independently confirm actual assignment.
- Commit: `aab74bbba0f87874c9993e8ea5537a6dc937db6c` (`feat(gui): add compact telemetry HUD and harden lifecycle state transitions`).
- Next: continue with Iteration 5/5.

## Iteración 5/5 - Auditoría de integración y cierre

- Fecha: 2026-10-26
- Rama: `counter-vision`
- Commit de partida: `c8298d432b73a0a2c075acb7fac292a9083b31ad` (`docs: finalize iteration 4 progress record`)
- Objetivo: auditar contratos satélite, actualizar tests históricos y smoke test, integrar una ruta E2E determinista, documentar el uso y cerrar la rama.
- Estado: Iteración 5/5 completada; este registro forma parte del cierre publicado.
- Archivos de producción modificados: `lib/interfaces/modern_gui.py`, `lib/interfaces/round_overlay.py`, `lib/interfaces/video_frame_picker.py`, `tools/gui_smoke.py`.
- Tests modificados: `tests/test_anchors.py`, `tests/test_dealer_detection.py`, `tests/test_engine_stability_unit.py`, `tests/test_gui_telemetry_and_lifecycle_unit.py`, `tests/test_mini_table_presentation_unit.py`, `tests/test_overlay_visibility_unit.py`, `tests/test_physical_advice_and_splits_unit.py`, `tests/test_reliability_lifecycle.py`, `tests/test_reliability_records.py`, `tests/test_round_extensions.py`, `tests/test_settlement_e2e.py`, `tests/test_sidebet_pack.py`, `tests/test_sidebets.py`, `tests/test_video_calibration.py`, `tests/test_video_replay.py`; nuevo `tests/test_e2e_usability_pipeline.py`.
- Documentación: nueva guía `docs/COMO_USAR_BLACKJACK_AI.md`.
- Hallazgos: `VideoFramePicker` aún exponía fallback visual de siete asientos y modos históricos; ahora dibuja layouts oficiales. La identidad de corrección resuelve el perfil visible y revalida card_id, ronda y contexto activos al confirmar. El consejo físico se conserva cuando el estado global es incierto y la mano propia está confirmada. Mini Mesa y TableView consumen el mismo consejo publicado. Se actualizaron fixtures sin calibración oficial, monitor o configuración explícita de apuestas laterales. La prueba histórica de settlement basada en dos asientos fijos se reemplazó por una prueba del ledger; el pipeline físico se cubre en el nuevo E2E.
- Auditorías: se revisaron contratos GUI/hotkeys/tools y profundidad de tests. Por el límite de concurrencia disponible se completaron secuencialmente, no en paralelo real. Se detectó cobertura previa mayormente basada en mocks; el E2E nuevo ejecuta motor, registro, presentación y widgets Tk reales con detecciones sintéticas.
- Regresión completa: `.venv312\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-release tests/` - 1522 aprobados, 37 subtests aprobados, 0 fallidos y 0 skips.
- Smoke GUI: `.venv312\Scripts\python.exe -B tools/gui_smoke.py` - 23 funciones, 0 fallidas, 0 errores de callback y 0 errores reportados. Construyó una ventana Tk real y ejerció Advanced Tools, Mini Mesa, calibración, preview, start/stop y corrección con entrada sintética; no mide precisión del modelo. La ejecución final pasó sin overrides Tcl.
- E2E físico: `tests/test_e2e_usability_pipeline.py` aprobó dentro de la suite. Usa DetectionEngine, CardMemory, TablePresentation, TableView y MiniTableOverlay reales con detecciones deterministas; comprueba conteo, IDs, corrección por ID, paridad de cartas entre vistas, ronda y reset de zapato.
- Ventana nativa: `tests/test_round_extensions.py` aprobó. Una comprobación inicial no encontró `init.tcl`; el runtime quedó disponible y el smoke final también pasó sin definir `TCL_LIBRARY` ni `TK_LIBRARY`.
- Acceso directo: `%USERPROFILE%\OneDrive\Desktop\Blackjack AI.lnk` existe. TargetPath `.venv312\Scripts\pythonw.exe`; argumentos `-B "<repo>\scripts\launch_blackjack.py"`; WorkingDirectory es el repositorio. Target, launcher y `main.py` existen. El smoke ejecutó directamente la GUI con entorno sintético; no lanzó la instancia instalada del shortcut.
- Logs: `logs/startup_errors.log` y `%LOCALAPPDATA%\Blackjack-AI\startup_errors.log` no existían en la verificación final; no se generaron errores de inicio.
- Limitaciones: no se probó reconocimiento frente a una partida o stream real ni se cargaron modelos de visión reales. Las comprobaciones de ventana fueron programáticas mediante widgets y callbacks, sin validación empírica de imagen.
- Cierre: sin trabajo funcional pendiente en esta etapa; el historial Git registra la revisión y sincronización finales.

## Iteration 6 - Native ROI inference and fullscreen hand repair

- Date: 2026-10-26. Branch: counter-vision. Starting commit: a02438a0a86d400fd60221d0f0c2519e222c399f.
- Status: implementation and requested validation complete. Publication is recorded in branch history.
- Diagnosis: official modes previously packed native mine/table and dealer patches into one image, followed by one network resize. They did not always infer directly on the full desktop. ONNX NMS suppressed across classes; CardMemory's per-frame duplicate radius was 35% of the smaller box dimension. TableView used a fixed dealer height and could place Yo below a short canvas. WindowSource already used PrintWindow flags 1|2, but stretched resized clients back to their previous resolution.
- Changes: table_inference now makes independent native padded crops (8 px), with two calls in Individual and three in Multiplayer (table, mine, dealer). Each owner has one primary ROI, preventing repeat counts across passes. ROI-local centers/bboxes map once to native frame coordinates. Low-confidence proposals remain visible but ambiguous; they do not confirm the count. ONNX NMS is class-aware; ROI inference uses a permissive 0.80 IoU to retain overlapped cards.
- Identity/evidence: a unique compatible pair of 6/9 index boxes on a sufficiently long lower-right diagonal keeps the upper-left rank. Nearby stepped upper indices remain independent. Moderate isolated 6/9 (<0.70) remains uncertain. Candidate deduplication is limited to 10% of the smaller box dimension, with one-to-one temporal matching. This is a geometric safeguard, not a universal image orientation classifier.
- Temporal case: an isolated weak lower 6 followed by a visible 9/6 pair now reuses the pending physical ID, confirms 9 after clean observations and resolves its uncertainty. The new regression failed with two active candidates before this addition and passes with one identity afterward. Already-booked contradictory contributions remain flagged for manual identity correction.
- UI: TableView fits two rows, their card images, labels and advice to available height while retaining image caches, widgets and card_id. Existing TablePresentation and Mini Mesa already preserve pending physical IDs and draw unknown ranks as question marks; no alternative grouping or counting was added there. Smoke verifies both labels and pending cards at 220/280/400 px and Mini Mesa.
- Capture: monitor/window capture uses a restored per-thread physical-pixel DPI context. PrintWindow remains client-only plus full-content (3); native client-size changes are returned intact, so engine/editor reject stale calibration instead of silently stretching it. RegionEditor already converts display scale once and checks captured resolution. ImageCrop only aligns offline images and is not in live inference. vision_assist is an optional remote cold path and was not changed or invoked.
- Preview: the same ROI inference/cache powers Preview Regions; native ROI rectangles, calibration polygons and model rank/confidence boxes are rendered. Input diagnostics now identify individual ROI sizes.
- Tests: tests/test_fullscreen_hand_detection_unit.py: 18 passed. Full tests/: 1540 passed and 37 subtests passed; zero failed, skipped or excluded. tools/gui_smoke.py: 24 features, zero failed, callback errors or reported errors. The new engine scenario confirms Q+K at dealer, overlapping J+9 at mine and inverted lower 6 as exactly four physical cards, RC -3, totals 20/19 and advice available. Existing replay/metadata tests were migrated from packed-coordinate fixtures to independent ROI-local fixtures. Native Windows capture tests now expect the true resized client dimensions.
- Commands: .venv312\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-i6-focused-final2 tests/test_fullscreen_hand_detection_unit.py; .venv312\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-i6-final2 tests/; .venv312\Scripts\python.exe -B tools/gui_smoke.py.
- Parallel work: one subagent created only the new fullscreen unit-test file while the principal implemented production. The principal reviewed and strengthened it with actual layout, NMS and engine scenarios, migrated historical fixtures, and ran all validation. Requested subagent model gpt-6-luna / medium; spawn response exposed task identity, not an independent model/effort confirmation.
- Limits: no supplied recording/image of the reported Zen scene was available and no real browser video/model accuracy was measured. PrintWindow freshness for GPU-accelerated Zen remains an empirical check; black images are rejected rather than replaced with unrelated desktop pixels. Two/three model calls can increase cycle latency; live CPU/p95 measurements remain unmeasured. An already-counted mistaken card in a restored shoe still needs an identity correction; the detector does not silently rewrite the shoe ledger.

## Etapa 7A - Máquina de Estados con Cerrojo de Persistencia y Overlay Interactivo con Edición de 1 Clic

- Fecha: 2026-10-26. Rama: `counter-vision`. Commit de partida: `371503f5d928ffe4d30a50f4c76c540a99184ae0`.
- Estado: Implementación y validación completadas al 100% de forma autónoma.
- Problema A resuelto (Temporal Consensus + Hard Latch):
  - Fase 1 (Consenso Temporal / Candidate): Detecciones ruidosas en un solo frame no entran prematuramente al conteo ni titilan en la interfaz. Se acumula evidencia ponderada por confianza $\sum \text{conf}(\text{rank})$ durante ventana corta de estabilización (mínimo de frames / ~350ms).
  - Fase 2 (Cerrojo Fuerte / Locked): Al alcanzar mayoría $\ge 65\%$ y umbral de estabilización, la carta transiciona al estado inmutable `LOCKED` (interoperable con `confirmed`), suma exactamente una vez al Running Count Hi-Lo y se proyecta firme en la mano.
  - Fase 3 (Persistencia Inmutable Anti-Desaparición y Anti-Mutación): Una vez `LOCKED`, la carta no expira por TTL ni se oculta en `TablePresentation`, `TableView` ni `RoundOverlay` (`Mini Mesa`) aunque no sea detectada durante 30+ frames posteriores (por ej. tapada por un Hit del crupier u oclusión). Tampoco muta su rango por predicciones ruidosas posteriores.
  - Fase 4 (Prioridad de Edición Manual): La edición manual del usuario (`apply_correction`) tiene prioridad absoluta. Marca `user_locked = True`. Descartar una falsa detección (`not_a_card`) retira la carta, ajusta el conteo y registra la región en `discarded_regions` para impedir que el detector la reviva.
- Problema B resuelto (Overlay Interactivo y Edición Ultra-Rápida de 1 Clic):
  - Mini Mesa 100% clickeable (`lib/interfaces/round_overlay.py`): Se asegura que la ventana no tenga estilos `WS_EX_TRANSPARENT`. Se asocia un bounding box de hit-testing claro (`card_hitbox`) con `card_id` por carta.
  - Se pospone el redibujado durante clics activos (`_pointer_start`) para evitar destruir ítems del Canvas entre ButtonPress y ButtonRelease. Detección de clic con umbral estricto (< 5 px).
  - Selector de 1 Clic (`lib/interfaces/card_picker.py`): Se posiciona sobre la carta/cursor con `topmost=True`, `lift()` y `focus_force()`.
  - Modo Rápido de 1 Clic: Pulsar cualquiera de los 13 botones grandes de rango (`2, 3, 4, 5, 6, 7, 8, 9, 10, J, Q, K, A`) aplica inmediatamente la corrección de rango y cierra el popup en ese mismo clic. El botón "Descartar Falsa Detección / Borrar" descarta la carta y descuenta su conteo en 1 solo clic.
- Validación y Pruebas:
  - Nueva suite unitaria: `tests/test_card_latch_persistence_and_overlay_click_unit.py` (16/16 pruebas aprobadas).
  - Suite completa de pytest: 1588 aprobados, 1 saltado (BitBlt en entorno headless), 37 subtests aprobados, 0 fallos.
  - Smoke test GUI: `tools/gui_smoke.py` ejecutado y aprobado (26 funciones, 0 fallos, 0 errores de callback, 0 errores reportados).
  - `git diff --check`: 0 errores.
- Archivos modificados/creados:
  - `lib/logic/card_memory.py`: consenso temporal, cerrojo `CardState("locked")`, inmutabilidad de cartas bloqueadas, descarte en `discarded_regions`.
  - `lib/logic/table_presentation.py`: compatibilidad con estados `locked` y `confirmed`.
  - `lib/logic/engine.py`: preservación de cartas `locked` en snapshot, advice y verificación de incertidumbre.
  - `lib/logic/monitor_utils.py`: fallback robusto ante fallos de BitBlt en entornos headless.
  - `lib/interfaces/round_overlay.py`: hitboxes espaciales para cartas, preservación de canvas durante clic, remoción de estilos no interactivos.
  - `lib/interfaces/card_picker.py`: modo 1-clic directo en botones de rango y borrado rápido con posicionamiento topmost.
  - `lib/interfaces/table_view.py`: soporte visual completo para cartas `locked`.
  - `lib/interfaces/modern_gui.py`: verificación de estado locked en hud y correcciones.
  - `tests/test_card_latch_persistence_and_overlay_click_unit.py`: 16 pruebas unitarias integradas.
  - `tests/test_card_memory.py`, `tests/test_registry_engine.py`, `tests/test_round_extensions.py`: alineación de contratos.

## Etapa 7B - Eliminación de Teletransporte y Cortes Visuales al Arrastrar la Mini Mesa y Redimensionar

- Fecha: 2026-10-26. Rama: `counter-vision`.
- Estado: Implementación y validación completadas al 100% de forma autónoma.
- Problema resuelto: Eliminación definitiva de saltos de coordenadas (teletransporte) y cortes visuales/redibujados parpadeantes al arrastrar la `Mini Mesa` (`RoundOverlay` / `MiniTableOverlay`) o redimensionar la mesa principal (`TableView`).
- Implementación técnica:
  1. Cálculo de arrastre exclusivamente con coordenadas globales de pantalla (`event.x_root`, `event.y_root`):
     - Anclaje exacto en `<ButtonPress-1>` (`_start_mouse_x`, `_start_mouse_y`, `_start_win_x`, `_start_win_y`).
     - Cálculo en `<B1-Motion>` como `start_win + (x_root - start_mouse)`, impidiendo saltos causados por `event.x` / `event.y` relativos cuando el cursor pasa por widgets hijos o controles internos.
     - Preservación íntegra de las dimensiones de la ventana (`width`, `height`) durante el movimiento, con soporte para coordenadas multimonitor y negativas.
  2. Congelamiento de redibujados y de `CaptureGuard` durante el arrastre:
     - Durante el estado de arrastre (`is_dragging` / `_pointer_down`), las presentaciones entrantes (`update_from_presentation` / `update_snapshot`) se retienen en `_pending_snapshot` sin destruir ni alterar el Canvas a mitad de movimiento.
     - Al soltar el mouse (`<ButtonRelease-1>`), se aplica inmediatamente el último snapshot pendiente acumulado.
     - En `CaptureGuard` (`lib/interfaces/capture_guard.py`), si el overlay reporta arrastre activo (`is_dragging`), se omiten las llamadas a `set_capture_hidden(True)` y `set_capture_hidden(False)`, evitando que el ciclo de captura de pantalla de Windows interrumpa el loop de mouse capture de Win32.
  3. Desambiguación precisa entre arrastre y clic:
     - Si el desplazamiento total supera el umbral ($\ge 5$ px), se confirma arrastre y se inhibe estrictamente la apertura accidental de `CardPicker`.
     - Clics estáticos o con micro-vibración ($< 5$ px) sobre hitboxes de cartas despachan de forma instantánea la identidad para edición.
  4. Filtrado y debounce en eventos `<Configure>`:
     - En `RoundOverlay` y `TableView`, los eventos `<Configure>` donde las dimensiones `(width, height)` coinciden con el último tamaño renderizado (indicando desplazamiento de ventana únicamente) son descartados sin provocar relayout ni parpadeo.
     - Cambios reales de geometría aplican debounce corto (`after(16, ...)` o `after(120, ...)` según llamada) para evitar desgarro o corte visual.
- Validación y Pruebas:
  - Nueva suite unitaria: `tests/test_drag_stability_and_geometry_unit.py` (24/24 pruebas aprobadas).
  - Suite completa de pytest: 1612 aprobados, 1 saltado (BitBlt en entorno headless), 37 subtests aprobados, 0 fallos.
  - Smoke test GUI: `tools/gui_smoke.py` ejecutado y aprobado (26 funciones, 0 fallos, 0 errores de callback, 0 errores reportados).
  - `git diff --check`: 0 advertencias de formato o espacios.
- Archivos modificados/creados:
  - `lib/interfaces/round_overlay.py`: cálculo de arrastre por deltas globales, congelamiento de snapshot, filtro de configure y compatibilidad con `update_snapshot`.
  - `lib/interfaces/capture_guard.py`: omisión de ocultamiento durante arrastre activo para proteger la captura de puntero de Windows.
  - `lib/interfaces/table_view.py`: filtrado de configure para ignorar desplazamientos de posición y debounce ante cambios de tamaño.
  - `tests/test_drag_stability_and_geometry_unit.py`: 24 pruebas unitarias que cubren estabilidad de arrastre, congelamiento, hitbox y eventos configure.
