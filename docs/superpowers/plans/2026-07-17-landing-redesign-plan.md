# Rediseño de landing page (agentecosmic/) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reestructurar la landing de `agentecosmic/` (React 19 + Vite + TypeScript + Tailwind) según la spec `docs/superpowers/specs/2026-07-17-landing-redesign-design.md`: nuevo flujo Headline → Caso real → Testimonial → Precios → CTA, nav con login/registro, contenido existente movido fuera del flujo principal, variables de entorno para GA4/WhatsApp, imágenes en WebP.

**Architecture:** `brand.config.ts` sigue siendo la única fuente de verdad de datos de marca (patrón ya existente en el repo). Se agregan campos nuevos al tipo `BrandConfig` y se crean 5 componentes nuevos (`Header`, `CasoReal`, `Testimonial`, `Pricing`, `FinalCTA`), cada uno con su test siguiendo el patrón `@testing-library/react` ya usado en el repo. `App.tsx` se reordena. Las imágenes se convierten a WebP con `cwebp` (confirmado instalado en el sistema, `/usr/bin/cwebp`).

**Tech Stack:** React 19, Vite 8, TypeScript, Tailwind CSS 3, Vitest 4, @testing-library/react, lucide-react, cwebp.

## Global Constraints

- Repo de trabajo: `/home/anuarbarrera/agente-cosmic/agentecosmic/` (git propio, remoto `landing-template.git`). Todos los comandos de este plan asumen ese directorio como cwd.
- Punto de partida: commit `0df70c2` ya en el repo — no tocarlo, es la base limpia.
- No se despliega a producción — solo se verifica en desarrollo (`npm run test:run`, `npm run build`, revisión visual local).
- Copy EXACTO a usar, literal, sin parafrasear:
  - Headline: `Olvídate de pensar qué publicar. Deja que Cosmic cree las imágenes y textos por ti.`
  - Subheadline (reemplaza el `tagline` actual): `Fotos profesionales y copys listos para vender. Tú solo descárgalos y prográmalos en la red social que prefieras. Mantén tus redes activas en minutos.`
  - CTA de prueba gratis (se repite en Hero, Pricing y FinalCTA): `Prueba gratis 7 días`
  - Precio: `$199 MXN /mes`, badge de descuento: `40% de descuento`
  - Texto del botón de Stripe (guardado en código, NO renderizado — `SHOW_STRIPE_CHECKOUT = false`): `Activar mi plan mensual con 40% de descuento`
- URLs reales (hardcodeadas en `brand.config.ts`, NO vía env var): login `https://agentecosmic.com/auth/login/`, registro `https://agentecosmic.com/auth/register/`.
- Placeholders explícitos (NO inventar datos reales): `founder.linkedinUrl`, `founder.personalSiteUrl`, `partnerCredit.url` → usar `'#'`. `founder.photoUrl` → usar un SVG placeholder inline (data URI, círculo gris), no un archivo nuevo. Contenido de "Caso real" → texto placeholder visible entre corchetes.
- Variables de entorno nuevas: `VITE_GA4_MEASUREMENT_ID`, `VITE_WHATSAPP_NUMBER` — leídas vía `import.meta.env` en el código cliente, y vía `loadEnv()` de Vite (no `import.meta.env`) dentro de `vite.config.ts`, porque `import.meta.env` es `undefined` en el contexto Node donde Vite carga su propio archivo de configuración (verificado empíricamente: usar `import.meta.env.X` directo ahí revienta el build con `TypeError: Cannot read properties of undefined`).
- Fuera de alcance: integración de Stripe/webhook, contenido real de Caso Real, assets reales de founder/partnerCredit, deploy a producción, migración a templates de Django.

---

### Task 1: Variables de entorno + campos nuevos de BrandConfig

**Files:**
- Modify: `src/types/brand.ts`
- Modify: `brand.config.ts`
- Modify: `vite.config.ts`
- Modify: `.gitignore`
- Create: `.env.example`
- Modify: `src/types/__tests__/brand.test.ts`

**Interfaces:**
- Produces: campos nuevos en `BrandConfig` — `headline: string`, `authUrls: {login: string; register: string}`, `founder: {linkedinUrl: string; personalSiteUrl: string; photoUrl: string}`, `partnerCredit: {name: string; url: string}`, `pricing: {amountMXN: number; discountLabel: string; stripeButtonLabel: string}`. Todas las tareas siguientes (2-10) consumen estos campos vía `import brand from '../../brand.config'` (o `'../../../brand.config'` desde `__tests__/`).

- [ ] **Step 1: Agregar las interfaces y campos nuevos a `src/types/brand.ts`**

Reemplazar el contenido completo del archivo:

```ts
export interface Service {
  title: string
  description: string
  icon: string
}

export interface Review {
  author: string
  rating: number
  text: string
  date: string
}

export interface AuthUrls {
  login: string
  register: string
}

export interface FounderInfo {
  linkedinUrl: string
  personalSiteUrl: string
  photoUrl: string
}

export interface PartnerCredit {
  name: string
  url: string
}

export interface PricingInfo {
  amountMXN: number
  discountLabel: string
  stripeButtonLabel: string
}

export interface BrandConfig {
  businessName: string
  headline: string
  tagline: string
  description: string
  category: string
  whatsapp: string
  phone: string
  email: string
  address: string
  googleMapsEmbed: string
  colors: {
    primary: string
    secondary: string
    accent: string
    background: string
    text: string
  }
  fonts: {
    heading: string
    body: string
  }
  services: Service[]
  reviews: Review[]
  images: {
    hero: string
    about: string
    gallery: string[]
  }
  ga4MeasurementId: string
  authUrls: AuthUrls
  founder: FounderInfo
  partnerCredit: PartnerCredit
  pricing: PricingInfo
  seo: {
    title: string
    description: string
    keywords: string[]
    ogImage: string
  }
}
```

- [ ] **Step 2: Actualizar `brand.config.ts` — agregar `headline`, cambiar `tagline`, leer `whatsapp`/`ga4MeasurementId` de env vars, agregar los 4 campos nuevos**

En `brand.config.ts`, reemplazar la línea:

```ts
  businessName: 'Agente Cosmic',
  tagline: 'De 4 datos de tu marca a una semana de contenido, en minutos.',
```

por:

```ts
  businessName: 'Agente Cosmic',
  headline: 'Olvídate de pensar qué publicar. Deja que Cosmic cree las imágenes y textos por ti.',
  tagline: 'Fotos profesionales y copys listos para vender. Tú solo descárgalos y prográmalos en la red social que prefieras. Mantén tus redes activas en minutos.',
```

Reemplazar la línea:

```ts
  whatsapp: '',
```

por (lee la variable de entorno `VITE_WHATSAPP_NUMBER`; `import.meta.env` sí existe en este contexto porque `brand.config.ts` es importado por componentes React que Vite procesa por su pipeline normal de cliente — el caso especial es solo cuando `vite.config.ts` importa este mismo archivo, resuelto en el Step 4):

```ts
  whatsapp: import.meta.env?.VITE_WHATSAPP_NUMBER ?? '',
```

Reemplazar la línea:

```ts
  ga4MeasurementId: '',
```

por:

```ts
  ga4MeasurementId: import.meta.env?.VITE_GA4_MEASUREMENT_ID ?? '',
  authUrls: {
    login: 'https://agentecosmic.com/auth/login/',
    register: 'https://agentecosmic.com/auth/register/',
  },
  founder: {
    // PLACEHOLDER: reemplazar con el LinkedIn real de Anuar
    linkedinUrl: '#',
    // PLACEHOLDER: reemplazar con la web personal real de Anuar
    personalSiteUrl: '#',
    // PLACEHOLDER: reemplazar con una foto real de Anuar
    photoUrl: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64'%3E%3Ccircle cx='32' cy='32' r='32' fill='%23666'/%3E%3C/svg%3E",
  },
  partnerCredit: {
    name: 'Tu Web MX',
    // PLACEHOLDER: reemplazar con el link real de Tu Web MX
    url: '#',
  },
  pricing: {
    amountMXN: 199,
    discountLabel: '40% de descuento',
    stripeButtonLabel: 'Activar mi plan mensual con 40% de descuento',
  },
```

- [ ] **Step 3: Crear `.env.example` y actualizar `.gitignore`**

Crear `.env.example`:

```
# GA4 Measurement ID (Google Analytics) - vacio hasta configurar el real
VITE_GA4_MEASUREMENT_ID=

# Numero de WhatsApp en formato internacional sin signos (ej: 5215512345678) - vacio hasta configurar
VITE_WHATSAPP_NUMBER=
```

En `.gitignore`, después de la línea `*.local`, agregar:

```
.env
```

- [ ] **Step 4: Corregir `vite.config.ts` para leer GA4 vía `loadEnv()` (no vía `import.meta.env`, que es `undefined` en este contexto)**

Reemplazar el contenido completo del archivo:

```ts
import { defineConfig } from 'vitest/config'
import { loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import brand from './brand.config'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const ga4MeasurementId = env.VITE_GA4_MEASUREMENT_ID ?? ''

  return {
    plugins: [
      react(),
      {
        name: 'inject-brand-seo',
        transformIndexHtml(html: string) {
          const ga4Script = ga4MeasurementId
            ? `<script async src="https://www.googletagmanager.com/gtag/js?id=${ga4MeasurementId}"></script>
    <script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}gtag('js',new Date());gtag('config','${ga4MeasurementId}');</script>`
            : ''

          return html
            .replace(/__SEO_TITLE__/g, brand.seo.title)
            .replace(/__SEO_DESCRIPTION__/g, brand.seo.description)
            .replace(/__SEO_KEYWORDS__/g, brand.seo.keywords.join(', '))
            .replace(/__SEO_OG_IMAGE__/g, brand.seo.ogImage)
            .replace(/__GA4_SCRIPT__/g, ga4Script)
        },
      },
    ],
    server: {
      allowedHosts: ['deploy.anuarbarrera.dev'],
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test-setup.ts'],
    },
  }
})
```

Nota: `brand.ga4MeasurementId` (el campo del objeto `brand` importado por `vite.config.ts`) queda sin usar dentro de este archivo a propósito — en este contexto Node, `import.meta.env` fue `undefined` al evaluarse `brand.config.ts`, así que ese campo específico terminaría vacío aquí aunque el `.env` real tenga un valor. Por eso el plugin usa `env.VITE_GA4_MEASUREMENT_ID` (de `loadEnv`) en vez de `brand.ga4MeasurementId`. El campo `brand.ga4MeasurementId` sigue siendo válido y correcto para cualquier uso del lado cliente (dentro de componentes React), donde sí se ejecuta a través del pipeline normal de Vite.

- [ ] **Step 5: Verificar que el build no se rompe y que GA4 se inyecta correctamente con un valor real**

Run:
```bash
VITE_GA4_MEASUREMENT_ID=G-TEST123 npx vite build --mode test-ga4 && grep -o "gtag/js?id=[^\"]*" dist/index.html
```
Expected: build exitoso (`✓ built in ...ms`) y la salida del grep es `gtag/js?id=G-TEST123`.

Run:
```bash
npx vite build && grep -c "gtag/js" dist/index.html
```
Expected: build exitoso, y la salida del grep es `0` (sin `VITE_GA4_MEASUREMENT_ID` seteada, no se inyecta script).

Limpiar: `rm -rf dist`

- [ ] **Step 6: Actualizar `src/types/__tests__/brand.test.ts` — reemplazar la aserción de whatsapp truthy (ahora es intencionalmente un placeholder vacío) y agregar tests de los campos nuevos**

Reemplazar el contenido completo del archivo:

```ts
import { describe, it, expect } from 'vitest'
import brand from '../../../brand.config'

describe('brand.config', () => {
  it('tiene businessName definido', () => {
    expect(brand.businessName).toBeTruthy()
  })

  it('tiene headline definido', () => {
    expect(brand.headline).toBeTruthy()
  })

  it('whatsapp es un string (puede estar vacío hasta configurar VITE_WHATSAPP_NUMBER)', () => {
    expect(typeof brand.whatsapp).toBe('string')
  })

  it('tiene exactamente 5 colores definidos', () => {
    const { primary, secondary, accent, background, text } = brand.colors
    expect(primary).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(secondary).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(accent).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(background).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(text).toMatch(/^#[0-9a-fA-F]{6}$/)
  })

  it('tiene al menos un servicio', () => {
    expect(brand.services.length).toBeGreaterThan(0)
  })

  it('tiene al menos una reseña', () => {
    expect(brand.reviews.length).toBeGreaterThan(0)
  })

  it('las reseñas tienen rating entre 1 y 5', () => {
    brand.reviews.forEach(r => {
      expect(r.rating).toBeGreaterThanOrEqual(1)
      expect(r.rating).toBeLessThanOrEqual(5)
    })
  })

  it('tiene URLs de login y registro válidas', () => {
    expect(brand.authUrls.login).toMatch(/^https:\/\//)
    expect(brand.authUrls.register).toMatch(/^https:\/\//)
  })

  it('tiene datos de pricing válidos', () => {
    expect(brand.pricing.amountMXN).toBeGreaterThan(0)
    expect(brand.pricing.discountLabel).toBeTruthy()
    expect(brand.pricing.stripeButtonLabel).toBeTruthy()
  })

  it('tiene campos de founder y partnerCredit definidos (aunque sean placeholders)', () => {
    expect(typeof brand.founder.linkedinUrl).toBe('string')
    expect(typeof brand.founder.personalSiteUrl).toBe('string')
    expect(typeof brand.founder.photoUrl).toBe('string')
    expect(typeof brand.partnerCredit.name).toBe('string')
    expect(typeof brand.partnerCredit.url).toBe('string')
  })
})
```

- [ ] **Step 7: Correr los tests y confirmar que pasan**

Run: `npm run test:run`
Expected: todos los test files pasan, incluyendo `src/types/__tests__/brand.test.ts` (ya no falla el test de whatsapp).

- [ ] **Step 8: Commit**

```bash
git add src/types/brand.ts brand.config.ts vite.config.ts .gitignore .env.example src/types/__tests__/brand.test.ts
git commit -m "feat: variables de entorno para GA4/WhatsApp + campos nuevos de BrandConfig (authUrls, founder, partnerCredit, pricing, headline)"
```

---

### Task 2: Imágenes → WebP

**Files:**
- Modify: `public/images/*.png`, `public/images/*.jpg` → nuevos `.webp` (y `git rm` de los originales)
- Modify: `brand.config.ts` (referencias de imágenes)

**Interfaces:**
- Consumes: ninguna de las tareas anteriores.
- Produces: `brand.images.hero`, `brand.images.about`, `brand.images.gallery[]` apuntando a rutas `.webp`. Las tareas de componentes (Hero ya existe, no se toca su lectura de `brand.images.hero`) siguen funcionando igual, solo cambia el archivo referenciado.

**Herramienta confirmada instalada:** `cwebp` (`/usr/bin/cwebp`). Todas las imágenes actuales miden ≥2048px de ancho (verificado con Pillow), así que ningún resize hace upscale.

- [ ] **Step 1: Convertir hero y about (máx 1600px de ancho, calidad 80)**

Run (desde `public/images/`):
```bash
cd public/images
cwebp -q 80 -resize 1600 0 hero-contenido-tarjetas.png -o hero-contenido-tarjetas.webp
cwebp -q 80 -resize 1600 0 about-equipo-reunion.png -o about-equipo-reunion.webp
```
Expected: cada comando imprime `Saving file '...webp'` con `Dimension: 1600 x <alto>`.

- [ ] **Step 2: Convertir las 7 imágenes de galería (máx 800px de ancho, calidad 80)**

Run:
```bash
cwebp -q 80 -resize 800 0 gallery-calendario-ia.png -o gallery-calendario-ia.webp
cwebp -q 80 -resize 800 0 gallery-cubos-datos.png -o gallery-cubos-datos.webp
cwebp -q 80 -resize 800 0 gallery-lineas-doradas.png -o gallery-lineas-doradas.webp
cwebp -q 80 -resize 800 0 gallery-panel-dashboard.png -o gallery-panel-dashboard.webp
cwebp -q 80 -resize 800 0 gallery-prisma-luz.png -o gallery-prisma-luz.webp
cwebp -q 80 -resize 800 0 gallery-topografia-fondo.png -o gallery-topografia-fondo.webp
cwebp -q 80 -resize 800 0 gallery-escritorio-trabajo.png -o gallery-escritorio-trabajo.webp
cwebp -q 80 -resize 800 0 gallery-whatsapp-contacto.png -o gallery-whatsapp-contacto.webp
```
Expected: 8 archivos `.webp` nuevos (7 de galería + el noveno elemento del array de galería es el logo, convertido en el Step 3).

- [ ] **Step 3: Convertir el logo (669x680, ya es pequeño — sin resize, calidad 85)**

Run:
```bash
cwebp -q 85 logo-agente-cosmico.jpg -o logo-agente-cosmico.webp
cd ../..
```

- [ ] **Step 4: Actualizar las referencias en `brand.config.ts`**

Reemplazar el bloque `images`:

```ts
  images: {
    hero: '/images/hero-contenido-tarjetas.png',
    about: '/images/about-equipo-reunion.png',
    gallery: [
      '/images/gallery-calendario-ia.png',
      '/images/gallery-cubos-datos.png',
      '/images/gallery-lineas-doradas.png',
      '/images/gallery-panel-dashboard.png',
      '/images/gallery-prisma-luz.png',
      '/images/gallery-topografia-fondo.png',
      '/images/gallery-escritorio-trabajo.png',
      '/images/gallery-whatsapp-contacto.png',
      '/images/logo-agente-cosmico.jpg',
    ],
  },
```

por:

```ts
  images: {
    hero: '/images/hero-contenido-tarjetas.webp',
    about: '/images/about-equipo-reunion.webp',
    gallery: [
      '/images/gallery-calendario-ia.webp',
      '/images/gallery-cubos-datos.webp',
      '/images/gallery-lineas-doradas.webp',
      '/images/gallery-panel-dashboard.webp',
      '/images/gallery-prisma-luz.webp',
      '/images/gallery-topografia-fondo.webp',
      '/images/gallery-escritorio-trabajo.webp',
      '/images/gallery-whatsapp-contacto.webp',
      '/images/logo-agente-cosmico.webp',
    ],
  },
```

- [ ] **Step 5: Confirmar que el build ya no referencia los PNG/JPG originales, luego eliminarlos**

Run: `npm run build && grep -rE "\.(png|jpg)" dist/assets/*.js dist/index.html`
Expected: build exitoso, el grep no encuentra ninguna coincidencia (exit code 1, sin output) — confirma que ningún `.png`/`.jpg` quedó referenciado en el bundle final.

Run: `rm -rf dist`

Run:
```bash
git rm public/images/hero-contenido-tarjetas.png public/images/about-equipo-reunion.png \
  public/images/gallery-calendario-ia.png public/images/gallery-cubos-datos.png \
  public/images/gallery-lineas-doradas.png public/images/gallery-panel-dashboard.png \
  public/images/gallery-prisma-luz.png public/images/gallery-topografia-fondo.png \
  public/images/gallery-escritorio-trabajo.png public/images/gallery-whatsapp-contacto.png \
  public/images/logo-agente-cosmico.jpg
```

- [ ] **Step 6: Correr los tests (no deberían verse afectados — ninguno depende del formato de imagen) y confirmar**

Run: `npm run test:run`
Expected: mismos resultados que al final de la Task 1 (todos los tests existentes pasan).

- [ ] **Step 7: Commit**

```bash
git add public/images/*.webp brand.config.ts
git commit -m "perf: convertir imagenes de PNG/JPG a WebP y redimensionar (hero/about 1600px, galeria 800px)"
```

---

### Task 3: Header.tsx (nav con login/registro)

**Files:**
- Create: `src/components/Header.tsx`
- Create: `src/components/__tests__/Header.test.tsx`

**Interfaces:**
- Consumes: `brand.businessName` (existente), `brand.authUrls.login`, `brand.authUrls.register` (Task 1). Ancla a los ids `#servicios`, `#nosotros`, `#resenas` (ya existen en `Services.tsx`, `About.tsx`, `Reviews.tsx`, sin cambios).
- Produces: componente `Header` exportado por defecto, sin props. Consumido por `App.tsx` en la Task 10.

- [ ] **Step 1: Escribir el test (falla porque el componente no existe todavía)**

Crear `src/components/__tests__/Header.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Header from '../Header'
import brand from '../../../brand.config'

describe('Header', () => {
  it('muestra el nombre del negocio', () => {
    render(<Header />)
    expect(screen.getByText(brand.businessName)).toBeInTheDocument()
  })

  it('tiene un link de Iniciar sesión que apunta a authUrls.login', () => {
    render(<Header />)
    const link = screen.getByRole('link', { name: /iniciar sesión/i })
    expect(link).toHaveAttribute('href', brand.authUrls.login)
  })

  it('tiene un link de Registrarse que apunta a authUrls.register', () => {
    render(<Header />)
    const link = screen.getByRole('link', { name: /registrarse/i })
    expect(link).toHaveAttribute('href', brand.authUrls.register)
  })

  it('tiene links de navegación a Servicios, Nosotros y Reseñas', () => {
    render(<Header />)
    expect(screen.getByRole('link', { name: /servicios/i })).toHaveAttribute('href', '#servicios')
    expect(screen.getByRole('link', { name: /nosotros/i })).toHaveAttribute('href', '#nosotros')
    expect(screen.getByRole('link', { name: /reseñas/i })).toHaveAttribute('href', '#resenas')
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Header.test.tsx`
Expected: FAIL — `Failed to resolve import "../Header"`.

- [ ] **Step 3: Crear `src/components/Header.tsx`**

```tsx
import brand from '../../brand.config'

export default function Header() {
  return (
    <header className="sticky top-0 z-50 bg-brand-primary/95 backdrop-blur text-white">
      <div className="container mx-auto px-6 py-4 flex items-center justify-between">
        <span className="font-heading font-extrabold text-lg">{brand.businessName}</span>

        <nav className="hidden md:flex items-center gap-6 text-sm text-white/70">
          <a href="#servicios" className="hover:text-white transition-colors">Servicios</a>
          <a href="#nosotros" className="hover:text-white transition-colors">Nosotros</a>
          <a href="#resenas" className="hover:text-white transition-colors">Reseñas</a>
        </nav>

        <div className="flex items-center gap-3">
          <a
            href={brand.authUrls.login}
            className="text-sm font-heading font-bold text-white/80 hover:text-white transition-colors"
          >
            Iniciar sesión
          </a>
          <a
            href={brand.authUrls.register}
            className="text-sm bg-brand-accent text-white font-heading font-bold px-4 py-2 rounded-full hover:opacity-90 transition-opacity"
          >
            Registrarse
          </a>
        </div>
      </div>
    </header>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Header.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/Header.tsx src/components/__tests__/Header.test.tsx
git commit -m "feat: agregar Header con nav sticky y botones de login/registro"
```

---

### Task 4: Hero.tsx — nuevo copy y CTA de prueba gratis

**Files:**
- Modify: `src/components/Hero.tsx`
- Modify: `src/components/__tests__/Hero.test.tsx`

**Interfaces:**
- Consumes: `brand.headline`, `brand.tagline` (ambos redefinidos en Task 1), `brand.authUrls.register` (Task 1), `brand.images.hero`, `brand.category`, `brand.businessName` (ya existentes).
- Produces: `Hero` ya no renderiza un link de WhatsApp — cualquier test que buscaba `getByRole('link', {name: /whatsapp/i})` dentro de `Hero` deja de aplicar (el sticky WhatsApp de `App.tsx` no se toca, ver Task 10).

- [ ] **Step 1: Reemplazar `src/components/__tests__/Hero.test.tsx`**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Hero from '../Hero'
import brand from '../../../brand.config'

describe('Hero', () => {
  it('muestra el headline principal como h1', () => {
    render(<Hero />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(brand.headline)
  })

  it('muestra el subheadline', () => {
    render(<Hero />)
    expect(screen.getByText(brand.tagline)).toBeInTheDocument()
  })

  it('el CTA de prueba gratis apunta a la URL de registro', () => {
    render(<Hero />)
    const link = screen.getByRole('link', { name: /prueba gratis/i })
    expect(link).toHaveAttribute('href', brand.authUrls.register)
  })

  it('muestra la imagen hero con alt text', () => {
    render(<Hero />)
    const img = screen.getByRole('img')
    expect(img).toHaveAttribute('src', brand.images.hero)
    expect(img).toHaveAttribute('alt', brand.businessName)
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Hero.test.tsx`
Expected: FAIL — el h1 actual tiene `brand.businessName`, no `brand.headline`; no existe ningún link con nombre `/prueba gratis/i`.

- [ ] **Step 3: Reemplazar `src/components/Hero.tsx`**

```tsx
import brand from '../../brand.config'

export default function Hero() {
  return (
    <section className="relative min-h-screen flex items-center bg-brand-primary overflow-hidden">
      {/* Imagen de fondo con overlay */}
      <div className="absolute inset-0">
        <img
          src={brand.images.hero}
          alt={brand.businessName}
          className="w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-brand-primary opacity-75" />
      </div>

      {/* Contenido */}
      <div className="relative z-10 container mx-auto px-6 py-20 text-white">
        <p className="text-brand-accent font-heading text-sm uppercase tracking-widest mb-3">
          {brand.category}
        </p>
        <h1 className="font-heading text-4xl md:text-6xl font-extrabold leading-tight mb-4">
          {brand.headline}
        </h1>
        <p className="text-xl md:text-2xl text-white/80 mb-8 max-w-xl">
          {brand.tagline}
        </p>
        <a
          href={brand.authUrls.register}
          className="inline-flex items-center gap-3 bg-brand-accent text-white font-heading font-bold px-8 py-4 rounded-full text-lg hover:opacity-90 transition-opacity"
        >
          Prueba gratis 7 días
        </a>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Hero.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/Hero.tsx src/components/__tests__/Hero.test.tsx
git commit -m "feat: nuevo headline/subheadline en Hero, CTA de prueba gratis en vez de WhatsApp"
```

---

### Task 5: CasoReal.tsx (placeholder)

**Files:**
- Create: `src/components/CasoReal.tsx`
- Create: `src/components/__tests__/CasoReal.test.tsx`

**Interfaces:**
- Consumes: ninguna (contenido estático de placeholder, sin datos de `brand.config.ts`).
- Produces: componente `CasoReal` exportado por defecto, sin props, sección con `id="caso-real"`. Consumido por `App.tsx` en la Task 10.

- [ ] **Step 1: Escribir el test**

Crear `src/components/__tests__/CasoReal.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import CasoReal from '../CasoReal'

describe('CasoReal', () => {
  it('renderiza la sección con id caso-real', () => {
    render(<CasoReal />)
    expect(document.querySelector('#caso-real')).toBeInTheDocument()
  })

  it('muestra un título', () => {
    render(<CasoReal />)
    expect(screen.getByRole('heading', { level: 2 })).toBeInTheDocument()
  })

  it('muestra los 2 bloques de placeholder marcados como tal', () => {
    render(<CasoReal />)
    const placeholders = screen.getAllByText(/\[Placeholder\]/i)
    expect(placeholders).toHaveLength(2)
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/CasoReal.test.tsx`
Expected: FAIL — `Failed to resolve import "../CasoReal"`.

- [ ] **Step 3: Crear `src/components/CasoReal.tsx`**

```tsx
export default function CasoReal() {
  return (
    <section id="caso-real" className="py-20 bg-brand-background">
      <div className="container mx-auto px-6">
        <div className="text-center mb-14">
          <h2 className="font-heading text-3xl md:text-4xl font-extrabold text-brand-primary mb-4">
            Así se ve Cosmic en acción
          </h2>
        </div>

        <div className="grid md:grid-cols-2 gap-8 max-w-4xl mx-auto">
          {/* PLACEHOLDER: reemplazar con screenshot real del calendario/dashboard generado por Cosmic */}
          <div className="rounded-2xl bg-white/5 border border-white/10 h-64 flex items-center justify-center text-white/40 text-sm p-6 text-center">
            [Placeholder] Screenshot real del calendario de contenido generado por Cosmic
          </div>
          {/* PLACEHOLDER: reemplazar con un caso de un tester real, pendiente de su aprobación */}
          <div className="rounded-2xl bg-white/5 border border-white/10 h-64 flex items-center justify-center text-white/40 text-sm p-6 text-center">
            [Placeholder] Caso real de un tester (pendiente de aprobación para publicar)
          </div>
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/CasoReal.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/CasoReal.tsx src/components/__tests__/CasoReal.test.tsx
git commit -m "feat: agregar seccion Caso Real (placeholder, pendiente de contenido real y aprobacion de testers)"
```

---

### Task 6: Testimonial.tsx + ajuste de Reviews.tsx (evitar duplicar la reseña promovida)

**Files:**
- Create: `src/components/Testimonial.tsx`
- Create: `src/components/__tests__/Testimonial.test.tsx`
- Modify: `src/components/Reviews.tsx`
- Modify: `src/components/__tests__/Reviews.test.tsx`

**Interfaces:**
- Consumes: `brand.reviews` (existente, sin cambios de tipo).
- Produces: `Testimonial` muestra `brand.reviews[0]`. `Reviews` (el carrusel, que se mueve fuera del flujo principal) pasa a mostrar `brand.reviews.slice(1)` para no repetir esa misma reseña.

- [ ] **Step 1: Escribir el test de Testimonial**

Crear `src/components/__tests__/Testimonial.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Testimonial from '../Testimonial'
import brand from '../../../brand.config'

describe('Testimonial', () => {
  it('muestra el autor de la primera reseña', () => {
    render(<Testimonial />)
    expect(screen.getByText(brand.reviews[0].author)).toBeInTheDocument()
  })

  it('muestra el texto de la primera reseña', () => {
    render(<Testimonial />)
    expect(screen.getByText(`"${brand.reviews[0].text}"`)).toBeInTheDocument()
  })

  it('no tiene botones de navegación de carrusel', () => {
    render(<Testimonial />)
    expect(screen.queryByLabelText(/anterior reseña/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/siguiente reseña/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Testimonial.test.tsx`
Expected: FAIL — `Failed to resolve import "../Testimonial"`.

- [ ] **Step 3: Crear `src/components/Testimonial.tsx`**

```tsx
import brand from '../../brand.config'
import { Star } from 'lucide-react'

export default function Testimonial() {
  const review = brand.reviews[0]

  return (
    <section className="py-20 bg-brand-primary text-white">
      <div className="container mx-auto px-6 text-center max-w-2xl">
        <div className="flex justify-center gap-1 mb-6">
          {Array.from({ length: review.rating }).map((_, i) => (
            <Star key={i} size={20} className="fill-brand-accent text-brand-accent" />
          ))}
        </div>

        <blockquote className="text-xl md:text-2xl text-white/90 leading-relaxed mb-8">
          "{review.text}"
        </blockquote>

        <p className="font-heading font-bold text-brand-accent">{review.author}</p>
        <p className="text-white/50 text-sm mt-1">{review.date}</p>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Correr el test de Testimonial y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Testimonial.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Actualizar `src/components/__tests__/Reviews.test.tsx` para reflejar que ahora excluye `reviews[0]`**

Reemplazar el contenido completo del archivo:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Reviews from '../Reviews'
import brand from '../../../brand.config'

describe('Reviews', () => {
  it('muestra el autor de la segunda reseña de brand.config (la primera se promueve a Testimonial)', () => {
    render(<Reviews />)
    expect(screen.getByText(brand.reviews[1].author)).toBeInTheDocument()
  })

  it('no repite la reseña que ya se muestra en Testimonial', () => {
    render(<Reviews />)
    expect(screen.queryByText(brand.reviews[0].author)).not.toBeInTheDocument()
  })

  it('muestra botones de navegación del carrusel', () => {
    render(<Reviews />)
    expect(screen.getByLabelText('Anterior reseña')).toBeInTheDocument()
    expect(screen.getByLabelText('Siguiente reseña')).toBeInTheDocument()
  })
})
```

- [ ] **Step 6: Correr el test de Reviews y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Reviews.test.tsx`
Expected: FAIL — `Reviews` todavía muestra `brand.reviews[0]` como la primera reseña del carrusel.

- [ ] **Step 7: Modificar `src/components/Reviews.tsx` — usar `reviews.slice(1)` con guarda de array vacío**

Reemplazar la línea:

```ts
  const reviews = brand.reviews
```

por:

```ts
  const reviews = brand.reviews.slice(1)

  if (reviews.length === 0) {
    return null
  }
```

(Esto va dentro del componente, antes de `const review = reviews[current]` — el resto del archivo no cambia.)

- [ ] **Step 8: Correr el test de Reviews y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Reviews.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 9: Commit**

```bash
git add src/components/Testimonial.tsx src/components/__tests__/Testimonial.test.tsx \
  src/components/Reviews.tsx src/components/__tests__/Reviews.test.tsx
git commit -m "feat: agregar Testimonial (reseña destacada) y evitar que Reviews repita la misma reseña"
```

---

### Task 7: Pricing.tsx

**Files:**
- Create: `src/components/Pricing.tsx`
- Create: `src/components/__tests__/Pricing.test.tsx`

**Interfaces:**
- Consumes: `brand.pricing.amountMXN`, `brand.pricing.discountLabel`, `brand.pricing.stripeButtonLabel`, `brand.authUrls.register` (todos de Task 1).
- Produces: componente `Pricing` exportado por defecto, sin props, sección con `id="precios"`. Constante local `SHOW_STRIPE_CHECKOUT = false` — cuando cambie a `true` en el futuro (fuera de este plan), se renderiza el botón de Stripe en vez del de prueba gratis.

- [ ] **Step 1: Escribir el test**

Crear `src/components/__tests__/Pricing.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Pricing from '../Pricing'
import brand from '../../../brand.config'

describe('Pricing', () => {
  it('muestra el precio en MXN', () => {
    render(<Pricing />)
    expect(screen.getByText(new RegExp(`\\$${brand.pricing.amountMXN}`))).toBeInTheDocument()
  })

  it('muestra el badge de descuento', () => {
    render(<Pricing />)
    expect(screen.getByText(brand.pricing.discountLabel)).toBeInTheDocument()
  })

  it('muestra el CTA de prueba gratis apuntando a registro (Stripe deshabilitado)', () => {
    render(<Pricing />)
    const link = screen.getByRole('link', { name: /prueba gratis/i })
    expect(link).toHaveAttribute('href', brand.authUrls.register)
  })

  it('NO muestra el botón de Stripe mientras SHOW_STRIPE_CHECKOUT es false', () => {
    render(<Pricing />)
    expect(screen.queryByText(brand.pricing.stripeButtonLabel)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Pricing.test.tsx`
Expected: FAIL — `Failed to resolve import "../Pricing"`.

- [ ] **Step 3: Crear `src/components/Pricing.tsx`**

```tsx
import brand from '../../brand.config'

const SHOW_STRIPE_CHECKOUT = false

export default function Pricing() {
  return (
    <section id="precios" className="py-20 bg-brand-background">
      <div className="container mx-auto px-6 text-center">
        <h2 className="font-heading text-3xl md:text-4xl font-extrabold text-brand-primary mb-14">
          Precios
        </h2>

        <div className="max-w-md mx-auto bg-white/5 rounded-2xl p-10 border border-white/10">
          <p className="inline-block bg-brand-accent/10 text-brand-accent font-heading text-sm font-bold uppercase tracking-widest px-3 py-1 rounded-full mb-4">
            {brand.pricing.discountLabel}
          </p>
          <p className="font-heading text-5xl font-extrabold text-brand-primary mb-8">
            ${brand.pricing.amountMXN} MXN
            <span className="text-lg text-white/60 font-normal"> /mes</span>
          </p>

          {SHOW_STRIPE_CHECKOUT ? (
            <a
              href="#"
              className="inline-flex items-center justify-center bg-brand-accent text-white font-heading font-bold px-8 py-4 rounded-full text-lg hover:opacity-90 transition-opacity w-full"
            >
              {brand.pricing.stripeButtonLabel}
            </a>
          ) : (
            <a
              href={brand.authUrls.register}
              className="inline-flex items-center justify-center bg-brand-accent text-white font-heading font-bold px-8 py-4 rounded-full text-lg hover:opacity-90 transition-opacity w-full"
            >
              Prueba gratis 7 días
            </a>
          )}
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Pricing.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/Pricing.tsx src/components/__tests__/Pricing.test.tsx
git commit -m "feat: agregar seccion Pricing con CTA de prueba gratis (boton de Stripe listo pero oculto tras SHOW_STRIPE_CHECKOUT)"
```

---

### Task 8: FinalCTA.tsx

**Files:**
- Create: `src/components/FinalCTA.tsx`
- Create: `src/components/__tests__/FinalCTA.test.tsx`

**Interfaces:**
- Consumes: `brand.authUrls.register` (Task 1).
- Produces: componente `FinalCTA` exportado por defecto, sin props. Consumido por `App.tsx` en la Task 10.

- [ ] **Step 1: Escribir el test**

Crear `src/components/__tests__/FinalCTA.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import FinalCTA from '../FinalCTA'
import brand from '../../../brand.config'

describe('FinalCTA', () => {
  it('muestra un título de cierre', () => {
    render(<FinalCTA />)
    expect(screen.getByRole('heading', { level: 2 })).toBeInTheDocument()
  })

  it('el CTA apunta a la URL de registro', () => {
    render(<FinalCTA />)
    const link = screen.getByRole('link', { name: /prueba gratis/i })
    expect(link).toHaveAttribute('href', brand.authUrls.register)
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/FinalCTA.test.tsx`
Expected: FAIL — `Failed to resolve import "../FinalCTA"`.

- [ ] **Step 3: Crear `src/components/FinalCTA.tsx`**

```tsx
import brand from '../../brand.config'

export default function FinalCTA() {
  return (
    <section className="py-20 bg-brand-primary text-white text-center">
      <div className="container mx-auto px-6">
        <h2 className="font-heading text-3xl md:text-4xl font-extrabold mb-6">
          ¿Listo para dejar de improvisar tus redes?
        </h2>
        <a
          href={brand.authUrls.register}
          className="inline-flex items-center gap-3 bg-brand-accent text-white font-heading font-bold px-8 py-4 rounded-full text-lg hover:opacity-90 transition-opacity"
        >
          Prueba gratis 7 días
        </a>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/FinalCTA.test.tsx`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/FinalCTA.tsx src/components/__tests__/FinalCTA.test.tsx
git commit -m "feat: agregar banner de cierre FinalCTA"
```

---

### Task 9: Footer.tsx — LinkedIn, web personal, crédito Tu Web MX

**Files:**
- Modify: `src/components/Footer.tsx`
- Modify: `src/components/__tests__/Footer.test.tsx`

**Interfaces:**
- Consumes: `brand.founder.linkedinUrl`, `brand.founder.personalSiteUrl`, `brand.founder.photoUrl`, `brand.partnerCredit.name`, `brand.partnerCredit.url` (Task 1). Mantiene `brand.businessName`, `brand.email` (existentes).
- Produces: sin cambios de interfaz pública (sigue siendo `Footer` sin props).

- [ ] **Step 1: Actualizar `src/components/__tests__/Footer.test.tsx`**

Reemplazar el contenido completo del archivo:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import Footer from '../Footer'
import brand from '../../../brand.config'

describe('Footer', () => {
  it('muestra el nombre del negocio', () => {
    render(<Footer />)
    expect(screen.getByText(brand.businessName)).toBeInTheDocument()
  })

  it('tiene link de aviso de privacidad', () => {
    render(<Footer />)
    expect(screen.getByRole('link', { name: /aviso de privacidad/i })).toBeInTheDocument()
  })

  it('muestra el email si está definido', () => {
    render(<Footer />)
    if (brand.email) {
      expect(screen.getByText(brand.email)).toBeInTheDocument()
    }
  })

  it('tiene links a LinkedIn y web personal', () => {
    render(<Footer />)
    expect(screen.getByRole('link', { name: /linkedin/i })).toHaveAttribute('href', brand.founder.linkedinUrl)
    expect(screen.getByRole('link', { name: /web personal/i })).toHaveAttribute('href', brand.founder.personalSiteUrl)
  })

  it('muestra el crédito de Tu Web MX con link y foto', () => {
    render(<Footer />)
    expect(screen.getByText(new RegExp(brand.partnerCredit.name))).toBeInTheDocument()
    const img = screen.getByAltText(/tu web mx/i)
    expect(img).toHaveAttribute('src', brand.founder.photoUrl)
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Footer.test.tsx`
Expected: FAIL — no existen los links de LinkedIn/web personal ni el crédito de Tu Web MX todavía.

- [ ] **Step 3: Reemplazar `src/components/Footer.tsx`**

```tsx
import brand from '../../brand.config'

export default function Footer() {
  const year = new Date().getFullYear()

  return (
    <footer className="bg-brand-primary text-white py-10">
      <div className="container mx-auto px-6">
        <div className="flex flex-col md:flex-row justify-between items-center gap-6">
          {/* Nombre y año */}
          <div>
            <p className="font-heading font-extrabold text-lg">{brand.businessName}</p>
            {brand.email && (
              <p className="text-white/50 text-sm mt-1">{brand.email}</p>
            )}
          </div>

          {/* Links */}
          <nav className="flex flex-wrap justify-center gap-6 text-sm text-white/60">
            <a href="#nosotros" className="hover:text-white transition-colors">Nosotros</a>
            <a href="#servicios" className="hover:text-white transition-colors">Servicios</a>
            <a href="#contacto" className="hover:text-white transition-colors">Contacto</a>
            <a href="/privacidad" className="hover:text-white transition-colors">
              Aviso de privacidad
            </a>
            <a
              href={brand.founder.linkedinUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors"
            >
              LinkedIn
            </a>
            <a
              href={brand.founder.personalSiteUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors"
            >
              Web personal
            </a>
          </nav>
        </div>

        {/* Crédito de Tu Web MX */}
        <div className="border-t border-white/10 mt-8 pt-6 flex flex-col md:flex-row items-center justify-center gap-3 text-center text-white/40 text-xs">
          <img
            src={brand.founder.photoUrl}
            alt={`Anuar Barrera, ${brand.partnerCredit.name}`}
            className="w-8 h-8 rounded-full object-cover"
          />
          <a
            href={brand.partnerCredit.url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-white transition-colors"
          >
            Construida por {brand.partnerCredit.name}
          </a>
        </div>

        <div className="text-center text-white/30 text-xs mt-4">
          © {year} {brand.businessName}. Todos los derechos reservados.
        </div>
      </div>
    </footer>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Footer.test.tsx`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/Footer.tsx src/components/__tests__/Footer.test.tsx
git commit -m "feat: agregar LinkedIn, web personal y credito de Tu Web MX al footer (placeholders)"
```

---

### Task 10: App.tsx — reordenar la composición

**Files:**
- Modify: `src/App.tsx`
- Modify: `src/components/__tests__/App.test.tsx`

**Interfaces:**
- Consumes: `Header` (Task 3), `Hero` (Task 4, ya modificado), `CasoReal` (Task 5), `Testimonial` (Task 6), `Pricing` (Task 7), `FinalCTA` (Task 8), `Services`/`About`/`Reviews`/`Contact`/`Footer` (existentes, sin cambios de contenido — `Footer` sí cambió en Task 9 pero su interfaz pública es la misma).
- Produces: composición final de la página, consumida solo por `main.tsx` (no se toca, fuera de alcance).

- [ ] **Step 1: Actualizar `src/components/__tests__/App.test.tsx`**

Reemplazar el contenido completo del archivo:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import App from '../../App'
import brand from '../../../brand.config'

describe('App', () => {
  it('renderiza el headline en el hero', () => {
    render(<App />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(brand.headline)
  })

  it('renderiza el botón WhatsApp sticky', () => {
    render(<App />)
    const stickyBtns = screen.getAllByRole('link', { name: /whatsapp/i })
    expect(stickyBtns.length).toBeGreaterThanOrEqual(1)
  })

  it('renderiza el nav con botones de login y registro', () => {
    render(<App />)
    expect(screen.getByRole('link', { name: /iniciar sesión/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /registrarse/i })).toBeInTheDocument()
  })

  it('todas las secciones están presentes', () => {
    render(<App />)
    expect(document.querySelector('#caso-real')).toBeInTheDocument()
    expect(document.querySelector('#precios')).toBeInTheDocument()
    expect(document.querySelector('#nosotros')).toBeInTheDocument()
    expect(document.querySelector('#servicios')).toBeInTheDocument()
    expect(document.querySelector('#resenas')).toBeInTheDocument()
    expect(document.querySelector('#contacto')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/App.test.tsx`
Expected: FAIL — `App.tsx` todavía no importa `Header`/`CasoReal`/`Testimonial`/`Pricing`/`FinalCTA`, y el h1 sigue siendo `brand.businessName`.

- [ ] **Step 3: Reemplazar `src/App.tsx`**

```tsx
import brand from '../brand.config'
import Header from './components/Header'
import Hero from './components/Hero'
import CasoReal from './components/CasoReal'
import Testimonial from './components/Testimonial'
import Pricing from './components/Pricing'
import FinalCTA from './components/FinalCTA'
import Services from './components/Services'
import About from './components/About'
import Reviews from './components/Reviews'
import Contact from './components/Contact'
import Footer from './components/Footer'
import { MessageCircle } from 'lucide-react'

export default function App() {
  return (
    <>
      <Header />

      {/* Flujo principal: Headline -> Caso real -> Testimonial -> Precios -> CTA */}
      <Hero />
      <CasoReal />
      <Testimonial />
      <Pricing />
      <FinalCTA />

      {/* Contenido existente, fuera del flujo principal, accesible desde el nav */}
      <Services />
      <About />
      <Reviews />
      <Contact />

      <Footer />

      {/* Botón WhatsApp sticky — visible en mobile */}
      <a
        href={`https://wa.me/${brand.whatsapp}`}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="WhatsApp"
        className="fixed bottom-6 right-6 z-50 bg-green-500 text-white w-14 h-14 rounded-full flex items-center justify-center shadow-lg hover:bg-green-600 transition-colors md:hidden"
      >
        <MessageCircle size={28} />
      </a>
    </>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/App.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/App.tsx src/components/__tests__/App.test.tsx
git commit -m "feat: reordenar App.tsx segun estructura Headline->Caso real->Testimonial->Precios->CTA, con Header y contenido existente movido fuera del flujo principal"
```

---

### Task 11: Verificación final (ejecutada por el controlador de la sesión, no delegada)

**Files:** ninguno (solo verificación).

**Interfaces:** N/A — task de verificación end-to-end de todas las tareas anteriores.

- [ ] **Step 1: Correr la suite completa de tests**

Run: `npm run test:run`
Expected: todos los test files pasan (0 failures) — incluye los 8 archivos de test existentes más los 5 nuevos (`Header`, `CasoReal`, `Testimonial`, `Pricing`, `FinalCTA`).

- [ ] **Step 2: Correr el build de producción**

Run: `npm run build`
Expected: `tsc` sin errores de tipos, `vite build` exitoso, sin warnings de módulos no resueltos.

- [ ] **Step 3: Revisión visual real con Playwright — desktop y mobile**

Levantar el preview del build (`npm run preview -- --port 4321 &`) y tomar screenshots reales (no mockeados) con Playwright, desktop (1440x900, full-page) y mobile (390x844), como ya se hizo en el brainstorm para revisar la landing actual. Confirmar visualmente:
- El nav superior muestra logo, links de ancla, y botones "Iniciar sesión"/"Registrarse".
- El orden de secciones visibles al hacer scroll es: Headline (Hero) → Caso Real (con los 2 placeholders visibles) → Testimonial → Precios → CTA final → Servicios → Nosotros → Reviews → Contacto → Footer.
- El footer muestra los links de LinkedIn/Web personal y el crédito "Construida por Tu Web MX" con la imagen placeholder (círculo gris, no un ícono de imagen rota).
- Las imágenes de Hero/About cargan correctamente en formato WebP (confirmar en la pestaña Network o inspeccionando `src` en el DOM).

Detener el preview al terminar (`kill` del proceso).

- [ ] **Step 4: Confirmar el peso total de `public/images/` antes/después**

Run: `du -sh public/images/`
Expected: notablemente menor a los ~50MB originales (el hero de prueba en la Task 2 dio 43KB vs 5MB original — se espera un total final de unos pocos MB, no decenas).

- [ ] **Step 5: Actualizar `.superpowers/sdd/progress.md` con el resumen de las 11 tareas**

No requiere commit (el ledger es scratch, git-ignorado).
