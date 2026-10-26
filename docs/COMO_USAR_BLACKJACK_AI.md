# Guía práctica de Blackjack AI

## Abrir y diagnosticar

Hacé doble clic en **Blackjack AI** en el escritorio. El acceso directo inicia `scripts/launch_blackjack.py` con el Python del entorno `.venv312`, sin abrir una consola.

Si la aplicación no inicia, revisá `logs/startup_errors.log`. En instalaciones sin permiso de escritura en el repositorio, el launcher utiliza `%LOCALAPPDATA%\Blackjack-AI\startup_errors.log`.

Para diagnosticar desde PowerShell y ver excepciones en la consola:

```powershell
cd "C:\ruta\al\repositorio\Blackjack-AI"
.\.venv312\Scripts\python.exe -B scripts\launch_blackjack.py
```

## Elegir modo y calibrar

- **Individual**: calibrá `mine` (tu zona) y `dealer`.
- **Multijugador**: calibrá `table` (mesa general), `mine` y `dealer`.

Seleccioná la fuente de captura, el perfil y el modo. Presioná **Calibrate Regions**, dibujá las regiones requeridas y guardá la calibración en el perfil activo. Una resolución o fuente diferente puede necesitar calibración propia. Usá **Preview Regions** para revisar las regiones antes de iniciar.

## Iniciar y leer el HUD

Presioná **Start Detection** solo después de que la calibración válida esté aplicada. **Stop** detiene el worker y retira las cartas obsoletas de la mesa.

El banner muestra:

- `RC` (Running Count) y `TC` (True Count).
- Cartas vistas y mazos restantes estimados.
- Una alerta cuando hay lecturas o conteo inciertos.
- El consejo de `Yo`, si la mano física está confirmada; si hay split, cada mano conserva su consejo.
- `SEGURO / INSURANCE` cuando corresponda.
- Ronda, modo y estado del motor.

Un `?` representa una lectura sin rango confirmado. Una lectura pendiente no debe tratarse como carta confirmada.

## Corregir una carta

Hacé clic en una carta con identidad física válida (`card_id`) en la mesa principal o en Mini Mesa. En el selector podés elegir directamente un rango, opcionalmente cambiar el palo, guardar, descartar una falsa detección o cancelar. Cambiar solo el rango conserva el palo conocido o desconocido existente. Las correcciones se validan contra el registro actual; una carta obsoleta se rechaza.

Una observación transitoria sin `card_id` se puede mostrar, pero no se puede corregir manualmente hasta que tenga identidad verificable.

## Mini Mesa

Activá **Mini Mesa** desde el interruptor principal. Arrastrala desde su zona de movimiento; un desplazamiento del cursor no debe abrir el selector de cartas. Ajustá la opacidad con el control de la ventana. CaptureGuard la oculta temporalmente durante capturas compatibles para evitar que la mesa se detecte a sí misma. Cerrarla con `X` sincroniza el único interruptor de activación.

## Ronda y zapato

Al terminar la mano actual, presioná **New Round**: borra las cartas de esa ronda y conserva el conteo acumulado del zapato. Presioná **New Shoe** cuando se baraje o cambie el zapato: reinicia el conteo y sus identidades activas. Si todavía hay cartas visibles al reiniciar, el sistema puede marcar incertidumbre; despejá la mesa antes de empezar el nuevo zapato.
