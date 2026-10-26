# Guía práctica de Blackjack AI

## Abrir y diagnosticar

Hacé doble clic en **Blackjack AI** en el escritorio o en **Open Blackjack AI.cmd** dentro del proyecto. Para verificar la instalación sin abrir la aplicación, ejecutá `./start.ps1 -CheckOnly` en PowerShell. La guía de instalación está en `docs/INSTALLATION.md`.

Si la aplicación no inicia, revisá `logs/startup_errors.log` o, si el proyecto no permite escribir, `%LOCALAPPDATA%\Blackjack-AI\startup_errors.log`. Para diagnosticar desde PowerShell:

```powershell
cd "C:\ruta\al\repositorio\Blackjack-AI"
.\.venv312\Scripts\python.exe -B scripts\launch_blackjack.py
```

## Fuente, modo y calibración

Seleccioná el monitor o ventana, el perfil y uno de los dos modos:

- **Individual** requiere las regiones `mine` y `dealer`.
- **Multijugador** requiere `table`, `mine` y `dealer`.

Presioná **Calibrate Regions**, dibujá las regiones y guardalas en el perfil activo. Una fuente o resolución diferente puede necesitar otra calibración. Revisá las regiones con **Preview Regions** y presioná **Start Detection**. **Stop** detiene la lectura y retira las cartas obsoletas de la mesa.

## Lecturas, conteo y consejos

El banner muestra Running Count (`RC`), True Count (`TC`), cartas vistas, mazos restantes estimados y una alerta de lectura dudosa. Solo se presenta consejo cuando hay información suficiente sobre la mano propia y el crupier. Si hay split, cada mano conserva su consejo; el seguro se indica cuando corresponde.

Un `?` indica un rango pendiente. En la mesa principal, el borde naranja señala una lectura pendiente, el rojo una contradicción y el verde una corrección manual. Revisá la alerta de incertidumbre aunque el RC parezca correcto.

Abrí **Lecturas / Corregir / Anadir** para consultar identidad, grupo, confianza, observaciones válidas, estado, momentos y cambios de cada carta del mazo. Los tiempos de seguimiento son valores de reloj monotónico, útiles para comparar eventos dentro de la sesión. Por defecto hacen falta dos observaciones válidas consecutivas del mismo rango para confirmar y contar una candidata; una lectura ambigua o una corrección manual siguen reglas distintas. La confirmación no garantiza que el modelo haya leído la carta real correctamente.

## Corregir, añadir o descartar una carta

Hacé clic en una carta con identidad física en la mesa principal o Mini Mesa. En el selector elegí el rango, cambiá el palo si corresponde y guardá; también podés descartar una falsa detección o cancelar. Si cambiás solo el rango, se conserva el palo conocido o desconocido. Una observación sin identidad verificable no admite corrección por clic.

Para agregar una carta omitida, abrí **Lecturas / Corregir / Anadir** y elegí **Anadir carta omitida** con la detección activa. Arrastrá un rectángulo sobre su índice en la imagen reciente y elegí rango y palo opcional. La ubicación debe estar dentro de una región calibrada; una asociación ambigua se rechaza para evitar un doble conteo.

En la misma lista podés seleccionar una carta de una ronda anterior del mazo actual y corregirla o descartarla. El historial se invalida al iniciar un mazo nuevo. Tras guardar, verificá RC, TC y vistas: cambiar un 6 por un 10 reduce RC en 2 y mantiene la cantidad de cartas vistas; descartar una falsa detección revierte su contribución y reduce vistas en 1.

## Mini Mesa

Activá **Mini Mesa** desde el interruptor principal. Arrastrala desde la barra **DRAG TO MOVE**. **LOCKED / UNLOCKED** bloquea o habilita el movimiento y **− / +** ajusta la opacidad. La posición y el bloqueo se guardan; si la ventana quedó fuera de pantalla, se recupera una posición accesible al abrirla. Un clic sobre una carta abre su selector; un arrastre no lo abre.

CaptureGuard usa exclusión de captura de Windows cuando está disponible y oculta temporalmente la ventana como alternativa. Una fuente que no permita una captura limpia puede producir pausas o parpadeos durante esa alternativa. Cerrarla con `X` sincroniza el interruptor de activación.

## Ronda y zapato

Al terminar la mano, presioná **New Round**: se limpian las cartas de la ronda y se conserva el conteo acumulado. Presioná **New Shoe** cuando se baraje o cambie el zapato: se reinician conteo e identidades. Si aún hay cartas visibles, el sistema puede marcar incertidumbre; despejá la mesa antes de empezar el nuevo zapato.
