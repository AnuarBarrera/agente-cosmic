# Landing multi-página (agentecosmic/) — Diseño

## Contexto

La landing de Agente Cosmic (`agentecosmic/`, React 19 + Vite + TypeScript
+ Tailwind) es hoy una sola página de scroll continuo, recién rediseñada
visualmente en esta misma sesión (commits `8dd9daa`, `a646783`). Anuar
pidió separar las secciones en rutas reales navegables, en vez de un
scroll único — motivado por poder editar cada vista por separado y
porque algunas vistas (Nosotros, Reseñas) van a crecer con más contenido
después ("esas vistas las debemos llenar").

Este trabajo es solo de **navegación/routing** — no toca el contenido
visual, copy, colores, tipografía ni imágenes ya aprobados en el
rediseño anterior. Sigue en desarrollo, no se despliega a producción.

## Estructura de rutas (decidida con Anuar)

- **`/`** (Home) — el flujo de conversión, sin cambios de contenido:
  Header → Hero → CasoReal → Testimonial → Pricing → FinalCTA → Contact
  → Footer.
- **`/nuestroservicio`** — Services, página propia (sin cambio de
  contenido, solo se mueve de sección-en-Home a ruta propia).
- **`/nosotros`** — About, página propia (antes vivía en Home vía ancla
  `#nosotros`; sale de Home).
- **`/resenas`** — Reviews, página propia (antes vivía en Home vía ancla
  `#resenas`; sale de Home).
- **`/comofunciona`** — nueva: explica el pipeline real (4 datos → análisis
  de marca → generación → calendario listo). Contenido nuevo, con
  capturas reales de la app marcadas como **placeholder** (no se pudo
  acceder a la app real en esta sesión — ni producción con las
  credenciales demo del `.env`, ni un stack local corriendo — decisión
  explícita de Anuar: avanzar con placeholders, mismo patrón que la
  sección "Caso Real" del rediseño anterior).
- **`/dudas`** — nueva: FAQ redactado por Claude a partir de
  `MarcaCosmic.md` (objeciones reales del manual: ¿se ve genérico?,
  ¿qué tan personalizado es?, limitaciones honestas del producto).
  Se marca explícitamente como **borrador** para que Anuar lo revise
  antes de darlo por final — no se inventa nada fuera de lo que dice
  el manual de marca.

**Nota de resolución de ambigüedad** (Anuar mencionó "Reseñas" tanto en
la lista de contenido de Home como en la instrucción de que necesita
ruta propia — instrucciones que no pueden ser ambas ciertas a la vez).
Se resuelve a favor de la instrucción más específica y explícita: Reseñas
sale de Home igual que Nosotros, ambas por la misma razón dada ("esas
vistas las debemos llenar"). Si esto no es lo que Anuar quiso decir,
es un cambio de una línea en el router — se avisa explícitamente en el
resumen que acompaña esta spec.

## Navegación

`Header.tsx` dejará de tener anclas mixtas (`#servicios`) y usará
únicamente links de ruta real: Nuestro Servicio (`/nuestroservicio`),
Cómo Funciona (`/comofunciona`), Dudas (`/dudas`), Nosotros (`/nosotros`),
Reseñas (`/resenas`) — 5 items, en el nav de desktop y en el menú
hamburguesa de mobile (patrón ya construido, solo cambian los `href`).

`Header` y `Footer` se vuelven un **layout persistente** compartido por
todas las rutas (React Router: un componente `Layout` con `<Outlet />`
en medio), en vez de vivir sueltos dentro de `App.tsx` como hoy.

El logo grande del Hero → logo chico del Header al hacer scroll
(construido en el rediseño anterior vía `IntersectionObserver` sobre
`#hero-logo-sentinel`) sigue funcionando sin cambios: `Header.tsx` ya
tiene un fallback seguro (`logoVisible = true` por default) para cuando
el sentinel no existe en el DOM — que será el caso normal en todas las
rutas que no sean `/`, donde el logo chico simplemente se ve fijo desde
el inicio.

## Legal y cookies

No se construye ninguna página `/legal` en React. El backend de Django
**ya tiene** rutas reales y funcionales, verificadas en el código
(`core/brand_dna/urls.py:8-9`, vistas `privacy_policy`/`terms_of_service`
con templates completos): `https://agentecosmic.com/privacidad/` y
`https://agentecosmic.com/terminos/`. El Footer y el nuevo aviso de
cookies enlazan directo ahí — mismo patrón que `authUrls.login`/
`authUrls.register`, que ya son links externos a Django.

Se agrega un componente nuevo `CookieBanner.tsx`: banner flotante fijo
(mobile-first, abajo de la pantalla), aparece una vez por visitante,
botón "Aceptar" que lo descarta y un link a `/privacidad/` de Django.
El estado de aceptación se guarda en `localStorage` (clave
`cosmic-cookie-consent`) para no volver a mostrarse. Vive en el `Layout`
persistente, visible en todas las rutas.

## Técnico

**React Router** (`react-router-dom`, modo `createBrowserRouter` —
la app es un build estático de Vite sin backend propio detrás, servida
tal cual por el dev server / futura CDN, no hay razón para hash routing).
Nueva dependencia a instalar. `brand.config.ts` no cambia de rol ni de
forma — sigue siendo la única fuente de verdad de datos de marca.

`src/main.tsx` monta el router en vez de `<App />` directo.
`src/App.tsx` es reemplazado por un `Layout.tsx` (Header + Outlet +
Footer + CookieBanner) y un archivo de definición de rutas
(`src/router.tsx` o similar) que mapea cada path a su página.

Cada "página" (Home, Servicios, Nosotros, Reseñas, ComoFunciona, Dudas)
es un componente delgado en `src/pages/` que ensambla los componentes de
sección ya existentes (`src/components/*.tsx`, sin tocarlos salvo
Services/About/Reviews que pierden su `id` de ancla ya que dejan de
necesitarse como target de `href="#..."`).

## Componentes nuevos

- `src/router.tsx` — definición de rutas.
- `src/components/Layout.tsx` — Header + `<Outlet />` + Footer +
  CookieBanner.
- `src/components/CookieBanner.tsx` — aviso de cookies, descrito arriba.
- `src/pages/Home.tsx`, `ServiciosPage.tsx`, `NosotrosPage.tsx`,
  `ResenasPage.tsx`, `ComoFuncionaPage.tsx`, `DudasPage.tsx` — una por
  ruta, cada una ensamblando los componentes de sección existentes (o,
  para ComoFunciona/Dudas, contenido nuevo).

## Fuera de alcance

- Contenido real de `/comofunciona` (capturas de la app) — placeholder
  hasta que Anuar tenga acceso a un entorno real para capturarlas.
- Contenido final de `/dudas` — Claude redacta un borrador, Anuar lo
  aprueba o ajusta después.
- Activar Stripe, tocar backend Django, deploy a producción.
- SSR / prerendering — queda como CSR puro; si algún día se despliega
  detrás de un servidor sin fallback a `index.html` para rutas
  profundas, es un riesgo a resolver en ese momento, no ahora.

## Testing

Cada página nueva/movida lleva su test (patrón ya usado en el proyecto).
Los tests que hoy verifican anclas (`document.querySelector('#nosotros')`
en `App.test.tsx`) se actualizan porque `App.tsx` deja de existir tal
cual — se reemplazan por tests de `Layout.tsx` (Header/Footer/CookieBanner
presentes) y tests de ruteo (navegar a cada path renderiza el `<h1>`/
contenido correcto). `npm run test:run` y `npm run build` deben pasar
limpio al final.
