# Rediseño del flujo de onboarding — diseño

Fecha: 2026-08-24
Estado: aprobado por Anuar en brainstorm, listo para plan de implementación

## Problema

La hipótesis de trabajo es que reducir la fricción entre "quiero probar" y
"veo mi contenido" sube las métricas de uso. Hoy esa distancia es larga y
está repartida en decisiones sueltas que nadie tomó en conjunto.

Un usuario que se registra por correo recorre cinco pasos y **dos
autenticaciones** antes de escribir la primera letra sobre su negocio:

1. `register_view` (`core/brand_dna/auth_views.py:125`) — el POST no crea
   el usuario, solo un `EmailVerificationToken`, y renderiza
   `verify_pending.html`.
2. El correo de verificación.
3. `verify_email_view` crea el usuario y hace `redirect('login')`
   (`auth_views.py:246`) — **el usuario escribe su contraseña otra vez**.
4. `login_view` redirige a `dashboard` (`auth_views.py:280`).
5. El dashboard está vacío; el usuario hace clic para llegar al
   formulario.

Con Google son dos pasos (`google_callback_view` → `dashboard` →
formulario), lo que confirma que el costo está en el camino por correo.

A esto se suman tres problemas independientes en el resto del flujo: el
usuario que vuelve aterriza en un dashboard que solo sirve para volver a
hacer clic; el banner de venta aparece antes de que el usuario haya
tocado su contenido gratis; y `regenerate_calendar_api` es un consumo de
IA sin tope ni límite por plan.

## Correcciones al diagnóstico previo

Dos afirmaciones del documento `cambiosUI.md` no coinciden con el código
y se corrigen aquí para que el plan no las herede:

**El ADN de marca NO está antes del calendario.** `analyze_submit`
redirige al dashboard (`core/brand_dna/views.py:216`), el calendario se
encola automáticamente al terminar el ADN
(`core/brand_dna/tasks.py:82`), sin paso manual, y los correos apuntan a
`calendar_review`, no a `results` (`core/content_pipeline/email_sender.py`,
9 de 11 destinos). `results` es una pantalla opcional a la que se llega
solo por clic. No hay nada que mover.

**Saltar el dashboard no compite con el estado "cocinando".** El banner
de generación en curso con polling (`dashboard.html:86-96`) se muestra
*después* de enviar el formulario, porque `analyze_submit` redirige ahí.
El dashboard sigue siendo la sala de espera en todos los casos; lo que
se elimina es su papel de peaje **antes** de generar, cuando está vacío y
no hay nada que esperar.

## Decisiones de producto

Tomadas por Anuar durante el brainstorm:

1. **Auto-login al verificar el correo.** El re-login se elimina.
2. **Aterrizaje según el estado del usuario**, no siempre al dashboard.
3. **No existe regeneración gratuita de contenido.** Ni antes ni después
   de descargar. El endpoint se elimina.
4. **Editar el ADN es siempre posible y no regenera nada**; para ver los
   cambios aplicados hay que pagar el mes.
5. **Eliminar calendario se cierra en los planes de negocio.** Razón:
   control de costo de IA. Se prefiere que el usuario haga contacto a que
   consuma generaciones sin control.
6. **El banner de venta anticipada aparece tras la primera descarga.**

### Ambigüedad resuelta

Durante el brainstorm se planteó que la primera descarga cerrara la
ventana de edición del ADN ("descarga = ADN dado por aprobado"). Esa
regla nació cuando todavía existía regeneración gratuita, para acotarla.
Al eliminarse la regeneración por completo (decisión 3), bloquear la
edición ya no protege nada: **editar el ADN no consume IA**. Por lo
tanto el ADN queda siempre editable y la primera descarga conserva un
solo trabajo: disparar la venta. Anuar quedó informado de esta
resolución y no pidió cambiarla.

## Diseño

### 1. Destino único tras autenticarse

Se agrega un helper en `core/brand_dna/auth_views.py`:

```
_post_auth_destination(user) -> str   # nombre de ruta o URL
```

Reglas, evaluadas en orden:

1. El usuario tiene algún `AnalysisJob` en `pending` o `processing`
   (excluyendo `deleted_at`) → `dashboard`. Es la sala de espera con
   polling, sin cambios.
2. El usuario tiene **exactamente un** `AnalysisJob` activo con
   calendario listo → `calendar_review` de ese job.
3. En cualquier otro caso → `new_analysis`.

La regla 2 exige "exactamente uno" en lugar de gatear por plan: con
varios calendarios hay algo que elegir, y elegir es la función del
dashboard. Tester y Admin conservan su lista sin caso especial; User y
Fundador van directo a su único calendario.

Puntos de entrada que pasan a usar el helper:

- `verify_email_view` (`auth_views.py:246`): además de crear el usuario,
  hace `login(request, user)` y redirige al destino.
- `login_view` (`auth_views.py:280`): cuando **no** hay `?next=`. Si hay
  `next`, se respeta como hoy.
- `google_callback_view` (`auth_views.py:479`).
- Las guardas de "ya autenticado" en `register_view`
  (`auth_views.py:128`), `login_view` (`auth_views.py:255`) y
  `forgot_password_view` (`auth_views.py:300`).

`magic_login_view` **no se toca**: tiene su propio `redirect_to`
guardado en la BD, que es más específico que cualquier regla general.

**Seguridad del auto-login.** El `EmailVerificationToken` es de un solo
uso (`is_used`) y expira en 24 horas. Ese token ya tiene hoy poder para
crear la cuenta; loguearla no amplía la superficie. El precedente es el
`LoginToken` del magic link, desplegado el 2026-08-23 con vigencia de 72
horas y reutilizable, es decir más permisivo que este caso.

El dashboard conserva todas sus funciones y sigue accesible desde la
navegación. Deja de ser paso obligatorio, no deja de existir.

### 2. Eliminación de la regeneración

**Se elimina:**

- `regenerate_calendar_api` (`core/brand_dna/views.py:918-978`).
- Su ruta `api/calendar/<uuid:job_id>/regenerate/` en
  `core/brand_dna/urls.py`.
- `regenerateCalendar()` y su manejo de botón
  (`core/brand_dna/templates/brand_dna/results.html:606-627`).
- Los tests que cubren ese endpoint.
- La métrica `POST_ACTIONS.labels(action='brand_dna_regenerated_calendar')`.

Con el endpoint desaparecen sus cuatro defectos, sin necesidad de
arreglar ninguno: solo regeneraba 7 días (los días 8-28 de un mes pagado
quedaban intactos), solo regeneraba la imagen del día 1 dejando seis
posts con imagen vieja y texto nuevo, corría síncrono dentro del request
con riesgo de timeout, y no tenía tope ni gate por plan.

**Se sustituye por un CTA de pago.** El botón de `results.html:340`
conserva su condición de aparición actual —todos los campos del ADN
aprobados, lógica ya presente en `results.html:506` y validada en
backend en `views.py:929`— y cambia su destino al flujo de pago.

**Partial compartido para el CTA de pago.** El botón de pago con su
modal de fotos vive hoy incrustado en `calendar_review.html:173-193`
junto a su JS (`openPhotoModal`). Se extrae a un partial incluible desde
`calendar_review.html` y `results.html`. Duplicarlo obligaría a mantener
dos copias de una pieza con lógica propia de cupo de fotos y estado del
botón.

**Aviso de ADN desincronizado.** Tras guardar cambios en el ADN,
`results` muestra que los cambios están guardados y que el contenido
actual se generó con la información anterior. Sin ese aviso, editar sin
pagar se lee como que la app ignoró los cambios.

**Métrica nueva:** una etiqueta de intención de pago desde el ADN, para
medir si editar la marca funciona como motor de conversión.

### 3. Borrado de calendario cerrado por plan

Campo nuevo en `core.tenant_management.models.Plan`:

```
allows_calendar_deletion = models.BooleanField(default=False)
```

La migración crea el campo en `False` y lo enciende solo en los planes
`Tester` y `Admin`. El default seguro significa que un plan nuevo no
hereda el permiso por accidente.

Se prefiere un campo a comparar contra `plan.name in ('Tester','Admin')`
—patrón que existe hoy en `auth_views.py:534`— porque los planes se
administran desde Django Admin, donde Anuar ya opera precios y cupos de
fotos.

**El gate va en el backend.** `delete_calendar_api`
(`core/brand_dna/views.py:456`) hoy solo valida propiedad del job y es
llamable directo; rechaza con 403 cuando el plan no permite borrar. El
botón de `calendar_review.html:136` se oculta según la misma condición,
como reflejo de la regla, no como la regla.

**Consecuencia asumida:** `can_create_calendar`
(`core/brand_dna/rate_limits.py:33`) cuenta los jobs no borrados contra
el cupo del plan. Sin borrado, cada calendario ocupa cupo de forma
permanente. Un usuario con cupo 2 conserva un segundo intento creando
otra marca; con cupo 1 se queda con el que tiene y la salida es
contactar a soporte —que es el control de costo buscado. El mensaje de
límite ya existente (`views.py:119`) dirige a soporte.

### 4. Primera descarga y banner de venta

Campo nuevo en el calendario:

```
first_download_at = models.DateTimeField(null=True, blank=True)
```

`download_post_image` (`core/brand_dna/views.py:485`) lo estampa la
primera vez que se descarga cualquier post del calendario, y no lo
vuelve a tocar. La descarga es el acto deliberado que prueba que el
producto entregó valor.

**Banner de venta anticipada (`early_cta`):**

- En `calendar_review.html:183-193`: se mueve debajo de los posts y solo
  se muestra si `first_download_at` no es nulo.
- En `dashboard.html:152-159`: misma condición, para que la oferta no
  aparezca en una pantalla mientras en la otra todavía no corresponde.

**Banner de vencimiento (`payment_needed`, `calendar_review.html:173`):
sin cambios.** Se queda arriba: ahí llegan desde el correo de
vencimiento y pagar es la acción que fueron a hacer.

### 5. Estado vacío del dashboard

`dashboard.html:146` deja de abrir hablando de lo que falta ("Aún no
tienes ningún calendario de contenido") y pasa a decir lo que va a
ocurrir: cuéntanos de tu negocio y en unos minutos tienes tu primera
semana lista. El botón conserva su texto actual, "Generar mi contenido".

Con el aterrizaje de la sección 1, esta pantalla solo la ve quien entra
al dashboard a propósito antes de generar.

## Datos y migraciones

Dos migraciones, ambas aditivas y sin backfill de contenido:

1. `tenant_management`: `Plan.allows_calendar_deletion` (default `False`)
   más migración de datos que lo activa en `Tester` y `Admin`.
2. `content_pipeline`: `first_download_at` en el calendario.

Ninguna borra datos existentes. La eliminación del endpoint de
regeneración no requiere migración: no tiene modelo propio.

## Pruebas

- `_post_auth_destination` en sus tres ramas: job procesando, un solo
  calendario listo, ningún calendario. Más el caso de varios
  calendarios, que debe seguir yendo al dashboard.
- Verificación de correo: el usuario queda autenticado y aterriza en el
  destino correcto, sin pasar por login.
- `login_view` con `?next=` explícito sigue respetándolo.
- `delete_calendar_api` responde 403 cuando el plan no permite borrar, y
  sigue funcionando en Tester/Admin.
- `download_post_image` estampa `first_download_at` la primera vez y no
  lo modifica en descargas posteriores.
- El banner `early_cta` no se renderiza antes de la primera descarga y sí
  después, en calendario y dashboard.
- La ruta de regeneración ya no resuelve.

## Fuera de alcance

- Los seis puntos de la landing (`/home/anuarbarrera/agentecosmic`):
  Anuar los dejó como hipótesis a validar el 2026-08-24.
- La barra de progreso congelada en 87% y el copy de "unos minutos" que
  debería decir ~20. Son piezas independientes de `cambiosUI.md`.
- Edición en línea del ADN (quitar los 3 botones por sección).
- Validación proactiva de contraseña en el registro.
- El modal de fotos solo agrega y nunca reemplaza fotos del pool
  —marcado por Anuar como tema aparte el 2026-08-23.
