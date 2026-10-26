# Blackjack-AI overnight progress

- Fecha: 2026-10-26
- Rama: `counter-vision`
- Commit publicado de partida antes de Iteracion 1: `b5f671b90637733284377a117207bec6b88a636d` (`ns`)
- Objetivo general: integrar correcciones manuales seguras por identidad fisica (`card_id`) y probarlas.
- Estado de 13A-13A.2: incluido en el commit publicado de la rama. 13A cubre unicidad y correcciones del registro; 13A.1 alinea CardLayout; 13A.2 instala el launcher y acceso directo del escritorio.
- Etapa actual: Iteracion 1/5 completada.
- Ultimo commit local/publicado de partida: `b5f671b90637733284377a117207bec6b88a636d`; el commit de esta iteracion se registrara al finalizar la publicacion.
- Trabajo completado: TablePresentation incluye en cada carta `card_id` real cuando existe, ronda, mazo, perfil, modo, resolucion, source_id/config reference, grupo y mano. TableView y Mini Mesa conservan esos metadatos y solo habilitan correcciones para identidades fisicas. Ambos usan el callback central, que valida contexto y registro activo y llama `DetectionEngine.apply_correction`. CardPicker ofrece botones directos de rango y palo, conserva el palo al corregir solo rango, permite corregir solo palo, descartar una falsa deteccion o cancelar. Mini Mesa diferencia click de arrastre con umbral estricto menor que 5 px. El refresco de ambas vistas sigue el snapshot compartido; los widgets no alteran conteo ni presentacion localmente.
- Archivos de la iteracion: `lib/logic/table_presentation.py`, `lib/interfaces/card_picker.py`, `lib/interfaces/table_view.py`, `lib/interfaces/round_overlay.py`, `lib/interfaces/modern_gui.py`, `tests/test_ui_identity_corrections_unit.py`, `tests/test_individual_table_view_unit.py`, `tests/test_mini_table_presentation_unit.py`, `.gitignore` y este documento. Se retiraron cuatro bases de datos temporales de pytest que estaban rastreadas.
- Pruebas ejecutadas: grupo solicitado de correcciones/registro/presentacion: 244 aprobadas; regresion acumulada de 24 modulos unitarios: 858 aprobadas y 4 subtests aprobados. Ambas ejecuciones se repitieron despues de la revision final, sin fallos.
- Errores conocidos: una primera ejecucion sin `--basetemp` termino los casos pero pytest fallo limpiando `%TEMP%/pytest-of-PC/pytest-current` (`PermissionError: [WinError 5]`, reparse point). Las ejecuciones finales con `--basetemp` explicito terminaron correctamente. No quedaron fallos de tests.
- Trabajo paralelo: el subagente preparo `tests/test_ui_identity_corrections_unit.py`; el agente principal implemento y reviso produccion e integracion. Se solicito `gpt-6-luna` con razonamiento `medium`, pero el entorno no expuso confirmacion del modelo/esfuerzo realmente asignados.
- Proxima accion: Iteracion 2/5, validar y completar la siguiente etapa de correcciones por identidad segun su alcance.
