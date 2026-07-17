# Rediseño de landing page (agentecosmic/) — Diseño

## Contexto

`agentecosmic/` es una instancia del landing-template de Tu Web MX (React 19 +
Vite + TypeScript + Tailwind), ya poblada con datos reales de Agente Cosmic
vía `/generate-client` (`brand.config.ts` es la fuente de verdad; los
componentes en `src/components/` la consumen).

Estado actual: todo el contenido vive en una sola página sin secciones
claramente separadas (Hero → About → Services → Reviews → Contact → Footer),
sin navegación hacia la app real, con el CTA principal apuntando a WhatsApp
(número vacío, no funcional). GA4 measurement ID y número de WhatsApp están
hardcodeados/vacíos en `brand.config.ts`. Las 10 imágenes actuales son PNG de
5-7MB cada una (no WebP).

Este es trabajo de desarrollo — **no se despliega a producción todavía**.
El punto de partida limpio ya quedó commiteado (`0df70c2`) antes de este
rediseño.

## Objetivo

Reestructurar la landing para que tenga un flujo de conversión claro
(prueba gratis, no pago directo — Stripe se activa después), agregar
navegación hacia la app real (login/registro), y dejar el contenido
existente accesible sin que compita con el flujo principal.

## Estructura de página (nueva)

**Flujo principal, arriba de la página (`index`):**

1. **Header/Nav** (nuevo) — sticky, logo de Cosmic, links a Servicios /
   Nosotros / Reviews (anclas hacia las secciones que se mueven más abajo),
   botones "Iniciar sesión" y "Registrarse".
2. **Headline** (Hero rediseñado) — headline y subheadline exactos (ver
   Copy), CTA "Prueba gratis 7 días" → registro.
3. **Caso real** (nuevo, contenido placeholder) — combina (a) dogfooding:
   espacio para screenshots del propio calendario/contenido generado por
   Cosmic, y (b) espacio para 1 caso de un tester real. Ambos bloques se
   marcan visualmente como placeholder (p. ej. con un comentario en código
   y contenido de relleno obvio) hasta que Anuar consiga la aprobación de
   los testers — no se inventa contenido que aparente ser real.
4. **Testimonial** (nuevo) — 1 sola review destacada (se toma
   `brand.config.reviews[0]`), reemplaza el carrusel de 3 en el flujo
   principal.
5. **Precios** (nuevo) — precio $199 MXN/mes con badge "40% de descuento".
   Botón visible ahora: "Prueba gratis 7 días" → registro. El botón de
   Stripe (copy exacto: "Activar mi plan mensual con 40% de descuento")
   existe en el código pero no se renderiza — controlado por una constante
   local `SHOW_STRIPE_CHECKOUT = false` en el componente. Sin lógica de
   pago real (eso es el punto 1 pendiente en
   `project_monetization_and_landing_2026_07_17.md`, fuera de alcance
   aquí).
6. **CTA final** (nuevo) — banner de cierre con el mismo CTA de prueba
   gratis.

**Contenido existente, se mantiene, se mueve más abajo en la misma página
(fuera del flujo principal, accesible desde el nav):** Servicios, Nosotros
(About), el resto de Reviews (las que no se promovieron a Testimonial),
Contacto. Sin cambios de contenido, solo de posición en `App.tsx`.

**Footer** (modificado): agrega link a LinkedIn (placeholder), link a web
personal (placeholder), crédito "construida por Tu Web MX" + foto de Anuar
(placeholder). Mantiene los links existentes (Nosotros/Servicios/Contacto/
Aviso de privacidad).

## Copy exacto a usar

- Headline: "Olvídate de pensar qué publicar. Deja que Cosmic cree las
  imágenes y textos por ti."
- Subheadline: "Fotos profesionales y copys listos para vender. Tú solo
  descárgalos y prográmalos en la red social que prefieras. Mantén tus
  redes activas en minutos."
- CTA principal (mientras Stripe está deshabilitado): "Prueba gratis 7
  días"
- Texto del botón de Stripe (guardado en código, no renderizado todavía):
  "Activar mi plan mensual con 40% de descuento"
- Precio mostrado: "$199 MXN /mes" + badge "40% de descuento" (no se
  inventa un precio "original" tachado — no fue provisto)

## Config y variables de entorno

`brand.config.ts` sigue siendo la única fuente de verdad de datos de marca.
Cambios:

- `ga4MeasurementId` deja de estar hardcodeado: se lee de
  `import.meta.env.VITE_GA4_MEASUREMENT_ID` (Vite expone `import.meta.env`
  de forma nativa para cualquier var prefijada `VITE_`).
- `whatsapp` deja de estar hardcodeado: se lee de
  `import.meta.env.VITE_WHATSAPP_NUMBER`.
- Se agrega `.env.example` en `agentecosmic/` documentando ambas variables
  (vacías/placeholder). `.env` real queda gitignoreado como ya es
  convención en el resto del proyecto.
- Nuevos campos en `BrandConfig` (`src/types/brand.ts`):
  - `authUrls: { login: string; register: string }` — valores reales:
    `https://agentecosmic.com/auth/login/` y
    `https://agentecosmic.com/auth/register/` (rutas confirmadas en
    `core/brand_dna/urls.py:15-16` del repo Django; no son datos
    sensibles, se hardcodean directo en `brand.config.ts`, no vía env).
  - `founder: { linkedinUrl: string; personalSiteUrl: string; photoUrl:
    string }` — placeholders.
  - `partnerCredit: { name: string; url: string; logoUrl: string }` —
    placeholder ("Tu Web MX").
  - `pricing: { amountMXN: number; discountLabel: string;
    stripeButtonLabel: string }` — valores reales dados por Anuar (199,
    "40% de descuento", "Activar mi plan mensual con 40% de descuento").

## Componentes

**Nuevos** (cada uno con su test en `__tests__/`, seguiendo el patrón
existente — ver `Hero.test.tsx` como referencia de estilo):

- `Header.tsx` — nav sticky, logo, links de ancla, botones login/registro.
- `CasoReal.tsx` — sección de caso real, contenido placeholder.
- `Testimonial.tsx` — 1 review destacada (reutiliza el tipo `Review`).
- `Pricing.tsx` — sección de precio, botón de prueba gratis, botón de
  Stripe oculto tras `SHOW_STRIPE_CHECKOUT`.
- `FinalCTA.tsx` — banner de cierre.

**Modificados:**

- `App.tsx` — reordena la composición según la estructura de arriba.
- `Footer.tsx` — agrega LinkedIn, web personal, crédito Tu Web MX + foto.
- `brand.config.ts`, `src/types/brand.ts` — campos nuevos descritos arriba.
- `Hero.tsx` — nuevo copy (headline/subheadline/CTA).

**Sin cambios de contenido** (solo cambian de posición en `App.tsx`):
`Services.tsx`, `About.tsx`, `Reviews.tsx` (queda con las reviews restantes
tras promover una a `Testimonial`), `Contact.tsx`.

## Imágenes → WebP

Las 10 imágenes en `public/images/` (PNG, 5-7MB cada una) se convierten a
WebP y se redimensionan a un ancho máximo razonable: 1600px para
hero/about, 800px para las de galería, manteniendo aspect ratio, calidad
~80. Tarea mecánica sin decisión de producto — se ejecuta con un script
(`cwebp` o equivalente disponible en el sistema) y se actualizan las
referencias en `brand.config.ts` de `.png`/`.jpg` a `.webp`. Los
originales PNG se eliminan del repo tras confirmar que el build referencia
solo los `.webp`.

## Fuera de alcance

- Integración real de Stripe / webhook de pago (punto 1 pendiente,
  `project_monetization_and_landing_2026_07_17.md`).
- Contenido real de "Caso real" (depende de aprobación de testers,
  pendiente fuera de este trabajo).
- Assets reales del footer (foto de Anuar, logo de Tu Web MX, URLs de
  LinkedIn/web personal) — quedan como placeholder, a reemplazar después
  sin tocar código.
- Deploy a producción — este trabajo se hace y se verifica en el entorno
  de desarrollo (`deploy.anuarbarrera.dev`, puerto 3000).
- Migración de Vite a templates de Django (evaluado y descartado por ahora
  en `project_monetization_and_landing_2026_07_17.md`, punto 2).

## Testing

Cada componente nuevo lleva su test siguiendo el patrón existente
(render + aserciones sobre contenido/props visibles, estilo
`@testing-library/react`). `brand.test.ts` se actualiza para cubrir los
campos nuevos de `BrandConfig`. Se corre `npm run test:run` y `npm run
build` al final como verificación de que no se rompió nada existente.
