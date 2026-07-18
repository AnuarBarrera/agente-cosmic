# Landing multi-página (agentecosmic/) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir la landing de `agentecosmic/` de una sola página de scroll continuo a 6 rutas navegables reales con React Router, según `docs/superpowers/specs/2026-07-18-landing-routing-design.md`.

**Architecture:** `Layout.tsx` (Header + `<Outlet/>` + Footer + WhatsApp sticky + CookieBanner) persistente en todas las rutas, definidas en `src/router.tsx`. Cada ruta renderiza una página delgada en `src/pages/` que ensambla componentes de sección ya existentes (o contenido nuevo para `/comofunciona` y `/dudas`).

**Tech Stack:** React 19, React Router 7 (`react-router-dom`, nueva dependencia), Vite 8, TypeScript, Tailwind CSS, Vitest, @testing-library/react.

## Global Constraints

- Repo de trabajo: `/home/anuarbarrera/agente-cosmic/agentecosmic/`. Todos los comandos asumen ese directorio como cwd.
- Punto de partida: commit `cf7a7b5` — no tocarlo, es la base limpia.
- No se despliega a producción — solo se verifica en desarrollo.
- Rutas finales, exactas: `/` (Home), `/nuestroservicio`, `/nosotros`, `/resenas`, `/comofunciona`, `/dudas`.
- Home contiene, en este orden: Hero → CasoReal → Testimonial → Pricing → FinalCTA → Contact. Nosotros y Reseñas salen de Home a sus propias rutas.
- Legal: NO se construye página `/legal` en React. Los links de "Aviso de privacidad" y "Términos de uso" apuntan a `https://agentecosmic.com/privacidad/` y `https://agentecosmic.com/terminos/` (rutas reales de Django, confirmadas en `core/brand_dna/urls.py:8-9`).
- `/comofunciona` y `/dudas` tienen contenido nuevo (no existe hoy en ningún componente) — el texto exacto está en las tareas de este plan, no se delega su redacción.
- El botón de WhatsApp sticky (hoy al final de `App.tsx`) se mueve a `Layout.tsx` para persistir en todas las rutas.
- El mecanismo de logo animado Hero→Header (`#hero-logo-sentinel` + `IntersectionObserver` en `Header.tsx`) NO se modifica — ya tiene fallback seguro (`logoVisible = true`) para rutas sin Hero.
- `App.tsx` y `App.test.tsx` se eliminan al final (Task 8), reemplazados por `router.tsx` + `Layout.tsx` + páginas.

---

### Task 1: CookieBanner.tsx

**Files:**
- Create: `src/components/CookieBanner.tsx`
- Create: `src/components/__tests__/CookieBanner.test.tsx`

**Interfaces:**
- Consumes: nada de tareas anteriores (componente independiente).
- Produces: componente `CookieBanner` exportado por defecto, sin props. Consumido por `Layout.tsx` en la Task 3.

- [ ] **Step 1: Escribir el test**

Crear `src/components/__tests__/CookieBanner.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, beforeEach } from 'vitest'
import CookieBanner from '../CookieBanner'

const STORAGE_KEY = 'cosmic-cookie-consent'

describe('CookieBanner', () => {
  beforeEach(() => {
    localStorage.removeItem(STORAGE_KEY)
  })

  it('se muestra si no hay consentimiento guardado', () => {
    render(<CookieBanner />)
    expect(screen.getByText(/utilizamos cookies/i)).toBeInTheDocument()
  })

  it('tiene un link a la politica de privacidad real de Django', () => {
    render(<CookieBanner />)
    const link = screen.getByRole('link', { name: /privacidad/i })
    expect(link).toHaveAttribute('href', 'https://agentecosmic.com/privacidad/')
  })

  it('al aceptar, desaparece y guarda el consentimiento', async () => {
    const user = userEvent.setup()
    render(<CookieBanner />)
    await user.click(screen.getByRole('button', { name: /aceptar/i }))
    expect(screen.queryByText(/utilizamos cookies/i)).not.toBeInTheDocument()
    expect(localStorage.getItem(STORAGE_KEY)).toBe('accepted')
  })

  it('no se muestra si ya habia consentimiento guardado', () => {
    localStorage.setItem(STORAGE_KEY, 'accepted')
    render(<CookieBanner />)
    expect(screen.queryByText(/utilizamos cookies/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/CookieBanner.test.tsx`
Expected: FAIL — `Failed to resolve import "../CookieBanner"`.

- [ ] **Step 3: Crear `src/components/CookieBanner.tsx`**

```tsx
import { useState } from 'react'

const STORAGE_KEY = 'cosmic-cookie-consent'

export default function CookieBanner() {
  const [visible, setVisible] = useState(() => {
    if (typeof localStorage === 'undefined') return true
    return localStorage.getItem(STORAGE_KEY) !== 'accepted'
  })

  if (!visible) return null

  const accept = () => {
    localStorage.setItem(STORAGE_KEY, 'accepted')
    setVisible(false)
  }

  return (
    <div className="fixed bottom-0 inset-x-0 z-[60] bg-brand-surface border-t border-white/10 px-5 py-4 md:px-6">
      <div className="container mx-auto flex flex-col md:flex-row items-center gap-4">
        <p className="text-sm text-white/70 flex-1">
          Utilizamos cookies para mejorar tu experiencia. Al seguir navegando aceptas
          nuestro{' '}
          <a
            href="https://agentecosmic.com/privacidad/"
            className="text-brand-secondary hover:underline"
          >
            aviso de privacidad
          </a>
          .
        </p>
        <button
          type="button"
          onClick={accept}
          className="shrink-0 text-brand-background font-heading font-bold px-6 py-2.5 rounded-full text-sm"
          style={{ background: 'var(--gradient-signature)' }}
        >
          Aceptar
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/CookieBanner.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/CookieBanner.tsx src/components/__tests__/CookieBanner.test.tsx
git commit -m "feat: agregar CookieBanner con consentimiento persistido en localStorage"
```

---

### Task 2: react-router-dom + nav de Header/Footer con rutas reales

**Files:**
- Modify: `package.json`, `package-lock.json` (vía `npm install`)
- Modify: `src/types/brand.ts`
- Modify: `brand.config.ts`
- Modify: `src/components/Header.tsx`
- Modify: `src/components/Footer.tsx`
- Modify: `src/components/__tests__/Header.test.tsx`
- Modify: `src/components/__tests__/Footer.test.tsx`

**Interfaces:**
- Produces: `brand.legalUrls: { privacy: string; terms: string }` — nuevo campo en `BrandConfig`, consumido por `Footer.tsx` y por `CookieBanner.tsx` (Task 1 ya escrito con el link hardcodeado — no se retoca, ver nota abajo). `Header`/`Footer` ahora usan `Link` de `react-router-dom` para navegación interna — todas las tareas siguientes que agreguen rutas dependen de que `react-router-dom` ya esté instalado.

**Nota:** `CookieBanner.tsx` (Task 1) ya quedó con el link de privacidad hardcodeado en vez de leído de `brand.legalUrls.privacy` — es una inconsistencia menor aceptada a propósito (Task 1 se escribió sin depender de esta tarea para no bloquear el orden de ejecución); no la corrijas aquí, no es parte del alcance de esta tarea.

- [ ] **Step 1: Instalar react-router-dom**

Run: `npm install react-router-dom@^7.18.1`
Expected: se agrega a `dependencies` en `package.json`, sin errores.

- [ ] **Step 2: Agregar `legalUrls` al tipo `BrandConfig`**

En `src/types/brand.ts`, agregar esta interfaz nueva (junto a las otras interfaces como `AuthUrls`):

```ts
export interface LegalUrls {
  privacy: string
  terms: string
}
```

Y agregar el campo `legalUrls: LegalUrls` a la interfaz `BrandConfig`, justo después de `authUrls: AuthUrls`.

- [ ] **Step 3: Agregar el valor real en `brand.config.ts`**

En `brand.config.ts`, después del bloque `authUrls`, agregar:

```ts
  legalUrls: {
    privacy: 'https://agentecosmic.com/privacidad/',
    terms: 'https://agentecosmic.com/terminos/',
  },
```

- [ ] **Step 4: Actualizar el test de Header**

Reemplazar el contenido completo de `src/components/__tests__/Header.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import Header from '../Header'
import brand from '../../../brand.config'

function renderWithRouter() {
  return render(
    <MemoryRouter>
      <Header />
    </MemoryRouter>
  )
}

describe('Header', () => {
  it('muestra el logo con el nombre del negocio como texto accesible', () => {
    renderWithRouter()
    expect(screen.getByRole('img', { name: brand.businessName })).toBeInTheDocument()
  })

  it('tiene un link de Iniciar sesión que apunta a authUrls.login', () => {
    renderWithRouter()
    const link = screen.getByRole('link', { name: /iniciar sesión/i })
    expect(link).toHaveAttribute('href', brand.authUrls.login)
  })

  it('tiene un link de Registrarse que apunta a authUrls.register', () => {
    renderWithRouter()
    const link = screen.getByRole('link', { name: /registrarse/i })
    expect(link).toHaveAttribute('href', brand.authUrls.register)
  })

  it('tiene links de navegación a las 5 rutas', () => {
    renderWithRouter()
    expect(screen.getByRole('link', { name: 'Nuestro Servicio' })).toHaveAttribute('href', '/nuestroservicio')
    expect(screen.getByRole('link', { name: 'Cómo Funciona' })).toHaveAttribute('href', '/comofunciona')
    expect(screen.getByRole('link', { name: 'Dudas' })).toHaveAttribute('href', '/dudas')
    expect(screen.getByRole('link', { name: 'Nosotros' })).toHaveAttribute('href', '/nosotros')
    expect(screen.getByRole('link', { name: 'Reseñas' })).toHaveAttribute('href', '/resenas')
  })
})
```

- [ ] **Step 5: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Header.test.tsx`
Expected: FAIL — `Header.tsx` no usa `react-router-dom` todavía, y `MemoryRouter` puede no estar resuelto como import válido hasta reiniciar vitest, pero el fallo real esperado es que los links siguen siendo anclas (`#servicios`, etc.) en vez de las 5 rutas nuevas.

- [ ] **Step 6: Reemplazar `src/components/Header.tsx`**

```tsx
import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import brand from '../../brand.config'
import Logo from './Logo'
import { Menu, X } from 'lucide-react'

const NAV_LINKS = [
  { to: '/nuestroservicio', label: 'Nuestro Servicio' },
  { to: '/comofunciona', label: 'Cómo Funciona' },
  { to: '/dudas', label: 'Dudas' },
  { to: '/nosotros', label: 'Nosotros' },
  { to: '/resenas', label: 'Reseñas' },
]

export default function Header() {
  const [open, setOpen] = useState(false)
  // El logo grande vive en el Hero al abrir la página; este solo aparece
  // cuando ese logo sale de vista al hacer scroll (ver #hero-logo-sentinel
  // en Hero.tsx). Si no hay Hero en la página (u observer no disponible),
  // se queda visible por default — degradación segura.
  const [logoVisible, setLogoVisible] = useState(true)

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return
    const sentinel = document.getElementById('hero-logo-sentinel')
    if (!sentinel) return

    setLogoVisible(false)
    const observer = new IntersectionObserver(
      ([entry]) => setLogoVisible(!entry.isIntersecting),
      { threshold: 0 }
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [])

  return (
    <header className="sticky top-0 z-50 bg-brand-background/90 backdrop-blur border-b border-white/5">
      <div className="container mx-auto px-5 md:px-6 py-3 flex items-center justify-between">
        <Link to="/" className="flex items-center" aria-label={brand.businessName}>
          <Logo
            id="logo-header"
            size={30}
            withWordmark
            className={`transition-opacity duration-300 ${logoVisible ? 'opacity-100' : 'opacity-0'}`}
          />
        </Link>

        <nav className="hidden md:flex items-center gap-6 text-sm text-white/60">
          {NAV_LINKS.map((link) => (
            <Link key={link.to} to={link.to} className="hover:text-white transition-colors">
              {link.label}
            </Link>
          ))}
        </nav>

        <div className="hidden md:flex items-center gap-4">
          <a
            href={brand.authUrls.login}
            className="text-sm font-heading font-semibold text-white/70 hover:text-white transition-colors"
          >
            Iniciar sesión
          </a>
          <a
            href={brand.authUrls.register}
            className="text-sm font-heading font-bold text-brand-background px-4 py-2 rounded-full transition-transform hover:scale-105"
            style={{ background: 'var(--gradient-signature)' }}
          >
            Registrarse
          </a>
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? 'Cerrar menú' : 'Abrir menú'}
          aria-expanded={open}
          className="md:hidden text-white/80 p-1"
        >
          {open ? <X size={24} /> : <Menu size={24} />}
        </button>
      </div>

      {open && (
        <nav className="md:hidden border-t border-white/5 bg-brand-background px-5 py-4 flex flex-col gap-4 text-sm">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              onClick={() => setOpen(false)}
              className="text-white/70 hover:text-white transition-colors"
            >
              {link.label}
            </Link>
          ))}
          <div className="flex flex-col gap-3 pt-2 border-t border-white/5">
            <a
              href={brand.authUrls.login}
              className="text-white/70 font-heading font-semibold"
            >
              Iniciar sesión
            </a>
            <a
              href={brand.authUrls.register}
              className="text-center text-brand-background font-heading font-bold px-4 py-2.5 rounded-full"
              style={{ background: 'var(--gradient-signature)' }}
            >
              Registrarse
            </a>
          </div>
        </nav>
      )}
    </header>
  )
}
```

- [ ] **Step 7: Correr el test de Header y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Header.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 8: Actualizar el test de Footer**

Reemplazar el contenido completo de `src/components/__tests__/Footer.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import Footer from '../Footer'
import brand from '../../../brand.config'

function renderWithRouter() {
  return render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>
  )
}

describe('Footer', () => {
  it('muestra el logo con el nombre del negocio como texto accesible', () => {
    renderWithRouter()
    expect(screen.getByRole('img', { name: brand.businessName })).toBeInTheDocument()
  })

  it('tiene links de Nosotros y Nuestro Servicio como rutas reales', () => {
    renderWithRouter()
    expect(screen.getByRole('link', { name: 'Nosotros' })).toHaveAttribute('href', '/nosotros')
    expect(screen.getByRole('link', { name: 'Nuestro Servicio' })).toHaveAttribute('href', '/nuestroservicio')
  })

  it('tiene link de Contacto apuntando al ancla de Home', () => {
    renderWithRouter()
    expect(screen.getByRole('link', { name: 'Contacto' })).toHaveAttribute('href', '/#contacto')
  })

  it('tiene links de aviso de privacidad y terminos apuntando a Django real', () => {
    renderWithRouter()
    expect(screen.getByRole('link', { name: /aviso de privacidad/i })).toHaveAttribute('href', brand.legalUrls.privacy)
    expect(screen.getByRole('link', { name: /términos de uso/i })).toHaveAttribute('href', brand.legalUrls.terms)
  })

  it('muestra el email si está definido', () => {
    renderWithRouter()
    if (brand.email) {
      expect(screen.getByText(brand.email)).toBeInTheDocument()
    }
  })

  it('tiene links a LinkedIn y web personal', () => {
    renderWithRouter()
    expect(screen.getByRole('link', { name: /linkedin/i })).toHaveAttribute('href', brand.founder.linkedinUrl)
    expect(screen.getByRole('link', { name: /web personal/i })).toHaveAttribute('href', brand.founder.personalSiteUrl)
  })

  it('muestra el crédito de Tu Web MX con link y foto', () => {
    renderWithRouter()
    expect(screen.getByText(new RegExp(brand.partnerCredit.name))).toBeInTheDocument()
    const img = screen.getByAltText(/tu web mx/i)
    expect(img).toHaveAttribute('src', brand.founder.photoUrl)
  })
})
```

- [ ] **Step 9: Correr el test de Footer y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Footer.test.tsx`
Expected: FAIL — Footer todavía usa anclas y el link de privacidad local.

- [ ] **Step 10: Reemplazar `src/components/Footer.tsx`**

```tsx
import { Link } from 'react-router-dom'
import brand from '../../brand.config'
import Logo from './Logo'

export default function Footer() {
  const year = new Date().getFullYear()

  return (
    <footer className="bg-brand-background border-t border-white/5 py-10">
      <div className="container mx-auto px-5 md:px-6">
        <div className="flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="flex flex-col items-center md:items-start gap-1">
            <Logo id="logo-footer" size={26} withWordmark />
            {brand.email && (
              <p className="text-white/40 text-sm">{brand.email}</p>
            )}
          </div>

          <nav className="flex flex-wrap justify-center gap-x-6 gap-y-2 text-sm text-white/50">
            <Link to="/nosotros" className="hover:text-white transition-colors">Nosotros</Link>
            <Link to="/nuestroservicio" className="hover:text-white transition-colors">Nuestro Servicio</Link>
            {/* Contacto vive dentro de Home (ver Home.tsx) — ancla cruzada de ruta */}
            <a href="/#contacto" className="hover:text-white transition-colors">Contacto</a>
            <a
              href={brand.legalUrls.privacy}
              className="hover:text-white transition-colors"
            >
              Aviso de privacidad
            </a>
            <a
              href={brand.legalUrls.terms}
              className="hover:text-white transition-colors"
            >
              Términos de uso
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

        <div className="border-t border-white/5 mt-8 pt-6 flex flex-col md:flex-row items-center justify-center gap-3 text-center text-white/40 text-xs">
          <img
            src={brand.founder.photoUrl}
            alt={`Anuar Barrera, ${brand.partnerCredit.name}`}
            className="w-8 h-8 rounded-full object-cover border border-white/10"
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

- [ ] **Step 11: Correr ambos tests y confirmar que pasan**

Run: `npx vitest run src/components/__tests__/Header.test.tsx src/components/__tests__/Footer.test.tsx`
Expected: PASS, 10 tests en total (4 + 6).

- [ ] **Step 12: Commit**

```bash
git add package.json package-lock.json src/types/brand.ts brand.config.ts \
  src/components/Header.tsx src/components/Footer.tsx \
  src/components/__tests__/Header.test.tsx src/components/__tests__/Footer.test.tsx
git commit -m "feat: instalar react-router-dom, Header/Footer usan rutas reales, legalUrls apunta a Django"
```

---

### Task 3: Layout.tsx (Header + Outlet + Footer + WhatsApp sticky + CookieBanner)

**Files:**
- Create: `src/components/Layout.tsx`
- Create: `src/components/__tests__/Layout.test.tsx`

**Interfaces:**
- Consumes: `Header` (Task 2), `Footer` (Task 2), `CookieBanner` (Task 1) — todos ya existen.
- Produces: componente `Layout` exportado por defecto, sin props, renderiza `<Outlet />` de `react-router-dom` en medio de Header y Footer. Consumido por `router.tsx` en la Task 8.

- [ ] **Step 1: Escribir el test**

Crear `src/components/__tests__/Layout.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import Layout from '../Layout'
import brand from '../../../brand.config'

function renderWithRoute(path: string) {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: <Layout />,
        children: [{ index: true, element: <h1>Página de prueba</h1> }],
      },
    ],
    { initialEntries: [path] }
  )
  return render(<RouterProvider router={router} />)
}

describe('Layout', () => {
  it('renderiza el Header (logo) y el Footer (crédito) junto con el contenido de la ruta', () => {
    renderWithRoute('/')
    expect(screen.getAllByRole('img', { name: brand.businessName }).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(new RegExp(brand.partnerCredit.name))).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Página de prueba')
  })

  it('renderiza el botón WhatsApp sticky', () => {
    renderWithRoute('/')
    expect(screen.getByRole('link', { name: /whatsapp/i })).toBeInTheDocument()
  })

  it('renderiza el CookieBanner', () => {
    renderWithRoute('/')
    expect(screen.getByText(/utilizamos cookies/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/components/__tests__/Layout.test.tsx`
Expected: FAIL — `Failed to resolve import "../Layout"`.

- [ ] **Step 3: Crear `src/components/Layout.tsx`**

```tsx
import { Outlet } from 'react-router-dom'
import brand from '../../brand.config'
import Header from './Header'
import Footer from './Footer'
import CookieBanner from './CookieBanner'
import { MessageCircle } from 'lucide-react'

export default function Layout() {
  return (
    <>
      <Header />

      <Outlet />

      <Footer />

      {/* Botón WhatsApp sticky — visible en mobile, persiste en todas las rutas */}
      <a
        href={`https://wa.me/${brand.whatsapp}`}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="WhatsApp"
        className="fixed bottom-6 right-6 z-50 bg-green-500 text-white w-14 h-14 rounded-full flex items-center justify-center shadow-lg hover:bg-green-600 transition-colors md:hidden"
      >
        <MessageCircle size={28} />
      </a>

      <CookieBanner />
    </>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/components/__tests__/Layout.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/Layout.tsx src/components/__tests__/Layout.test.tsx
git commit -m "feat: agregar Layout persistente (Header+Outlet+Footer+WhatsApp+CookieBanner)"
```

---

### Task 4: Home.tsx

**Files:**
- Create: `src/pages/Home.tsx`
- Create: `src/pages/__tests__/Home.test.tsx`

**Interfaces:**
- Consumes: `Hero`, `CasoReal`, `Testimonial`, `Pricing`, `FinalCTA`, `Contact` (todos ya existen en `src/components/`, sin cambios).
- Produces: componente `Home` exportado por defecto, sin props. Consumido por `router.tsx` en la Task 8 como la ruta `/`.

- [ ] **Step 1: Escribir el test**

Crear `src/pages/__tests__/Home.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi } from 'vitest'
import Home from '../Home'
import brand from '../../../brand.config'

describe('Home', () => {
  it('renderiza el headline del Hero', () => {
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    )
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(brand.headline)
  })

  it('renderiza las secciones del flujo de conversión y Contact', () => {
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    )
    expect(document.querySelector('#caso-real')).toBeInTheDocument()
    expect(document.querySelector('#precios')).toBeInTheDocument()
    expect(document.querySelector('#contacto')).toBeInTheDocument()
  })

  it('hace scroll al elemento del hash si la URL trae uno', () => {
    window.location.hash = '#contacto'
    const scrollIntoViewMock = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoViewMock
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    )
    expect(scrollIntoViewMock).toHaveBeenCalled()
    window.location.hash = ''
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/pages/__tests__/Home.test.tsx`
Expected: FAIL — `Failed to resolve import "../Home"`.

- [ ] **Step 3: Crear `src/pages/Home.tsx`**

```tsx
import { useEffect } from 'react'
import Hero from '../components/Hero'
import CasoReal from '../components/CasoReal'
import Testimonial from '../components/Testimonial'
import Pricing from '../components/Pricing'
import FinalCTA from '../components/FinalCTA'
import Contact from '../components/Contact'

export default function Home() {
  // Permite que links externos a esta página con un hash (ej. /#contacto,
  // usado por Footer.tsx desde otras rutas) hagan scroll al elemento
  // correcto una vez que Home ya montó.
  useEffect(() => {
    if (window.location.hash) {
      const el = document.querySelector(window.location.hash)
      el?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [])

  return (
    <>
      <Hero />
      <CasoReal />
      <Testimonial />
      <Pricing />
      <FinalCTA />
      <Contact />
    </>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/pages/__tests__/Home.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Home.tsx src/pages/__tests__/Home.test.tsx
git commit -m "feat: agregar pagina Home (Hero->CasoReal->Testimonial->Pricing->FinalCTA->Contact)"
```

---

### Task 5: ServiciosPage, NosotrosPage, ResenasPage

**Files:**
- Create: `src/pages/ServiciosPage.tsx`
- Create: `src/pages/NosotrosPage.tsx`
- Create: `src/pages/ResenasPage.tsx`
- Create: `src/pages/__tests__/ServiciosPage.test.tsx`
- Create: `src/pages/__tests__/NosotrosPage.test.tsx`
- Create: `src/pages/__tests__/ResenasPage.test.tsx`
- Modify: `src/components/Services.tsx` (quitar `id="servicios"`, ya no es destino de ancla)
- Modify: `src/components/About.tsx` (quitar `id="nosotros"`)
- Modify: `src/components/Reviews.tsx` (quitar `id="resenas"`)

**Interfaces:**
- Consumes: `Services`, `About`, `Reviews` (ya existen, sin cambio de contenido, solo pierden su `id`).
- Produces: `ServiciosPage`, `NosotrosPage`, `ResenasPage`, cada uno exportado por defecto sin props. Consumidos por `router.tsx` en la Task 8.

- [ ] **Step 1: Quitar el `id` de ancla de los 3 componentes**

En `src/components/Services.tsx`, cambiar la línea:

```tsx
    <section id="servicios" className="relative py-16 md:py-24 bg-brand-background overflow-hidden">
```

por:

```tsx
    <section className="relative py-16 md:py-24 bg-brand-background overflow-hidden">
```

En `src/components/About.tsx`, cambiar:

```tsx
    <section id="nosotros" className="py-16 md:py-24 bg-brand-background">
```

por:

```tsx
    <section className="py-16 md:py-24 bg-brand-background">
```

En `src/components/Reviews.tsx`, cambiar:

```tsx
    <section id="resenas" className="py-16 md:py-24 bg-brand-background border-t border-white/5">
```

por:

```tsx
    <section className="py-16 md:py-24 bg-brand-background border-t border-white/5">
```

- [ ] **Step 2: Escribir los 3 tests de página**

Crear `src/pages/__tests__/ServiciosPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ServiciosPage from '../ServiciosPage'
import brand from '../../../brand.config'

describe('ServiciosPage', () => {
  it('renderiza el titulo de servicios y al menos un servicio real', () => {
    render(<ServiciosPage />)
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Nuestros servicios')
    expect(screen.getByText(brand.services[0].title)).toBeInTheDocument()
  })
})
```

Crear `src/pages/__tests__/NosotrosPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import NosotrosPage from '../NosotrosPage'
import brand from '../../../brand.config'

describe('NosotrosPage', () => {
  it('renderiza la descripcion real de la marca', () => {
    render(<NosotrosPage />)
    expect(screen.getByText(brand.description)).toBeInTheDocument()
  })
})
```

Crear `src/pages/__tests__/ResenasPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ResenasPage from '../ResenasPage'
import brand from '../../../brand.config'

describe('ResenasPage', () => {
  it('renderiza la segunda reseña de brand.config (la primera esta en Testimonial, seccion de Home)', () => {
    render(<ResenasPage />)
    expect(screen.getByText(brand.reviews[1].author)).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Correr los 3 tests y confirmar que fallan**

Run: `npx vitest run src/pages/__tests__/ServiciosPage.test.tsx src/pages/__tests__/NosotrosPage.test.tsx src/pages/__tests__/ResenasPage.test.tsx`
Expected: FAIL — los 3 archivos de página no existen todavía.

- [ ] **Step 4: Crear los 3 archivos de página**

Crear `src/pages/ServiciosPage.tsx`:

```tsx
import Services from '../components/Services'

export default function ServiciosPage() {
  return <Services />
}
```

Crear `src/pages/NosotrosPage.tsx`:

```tsx
import About from '../components/About'

export default function NosotrosPage() {
  return <About />
}
```

Crear `src/pages/ResenasPage.tsx`:

```tsx
import Reviews from '../components/Reviews'

export default function ResenasPage() {
  return <Reviews />
}
```

- [ ] **Step 5: Correr los 3 tests y confirmar que pasan**

Run: `npx vitest run src/pages/__tests__/ServiciosPage.test.tsx src/pages/__tests__/NosotrosPage.test.tsx src/pages/__tests__/ResenasPage.test.tsx`
Expected: PASS, 3 tests (1 por archivo).

- [ ] **Step 6: Correr la suite completa de componentes para confirmar que quitar los `id` no rompió nada**

Run: `npx vitest run src/components/__tests__/Services.test.tsx src/components/__tests__/About.test.tsx src/components/__tests__/Reviews.test.tsx`
Expected: PASS — ninguno de esos tests verificaba el `id` directamente (verificar leyendo los archivos si el implementador tiene dudas; si alguno SÍ lo verificaba, quitar esa aserción específica del test, no el `id` del componente).

- [ ] **Step 7: Commit**

```bash
git add src/components/Services.tsx src/components/About.tsx src/components/Reviews.tsx \
  src/pages/ServiciosPage.tsx src/pages/NosotrosPage.tsx src/pages/ResenasPage.tsx \
  src/pages/__tests__/ServiciosPage.test.tsx src/pages/__tests__/NosotrosPage.test.tsx src/pages/__tests__/ResenasPage.test.tsx
git commit -m "feat: agregar paginas Servicios/Nosotros/Resenas, quitar ids de ancla ya no usados"
```

---

### Task 6: ComoFuncionaPage.tsx

**Files:**
- Create: `src/pages/ComoFuncionaPage.tsx`
- Create: `src/pages/__tests__/ComoFuncionaPage.test.tsx`

**Interfaces:**
- Consumes: ninguna de las tareas anteriores (contenido nuevo, estático).
- Produces: componente `ComoFuncionaPage` exportado por defecto, sin props. Consumido por `router.tsx` en la Task 8 como la ruta `/comofunciona`.

**Contenido exacto** (explica el pipeline real de Cosmic — 4 pasos, cada uno con un bloque de captura marcado explícitamente como placeholder, mismo patrón que `CasoReal.tsx`):

- [ ] **Step 1: Escribir el test**

Crear `src/pages/__tests__/ComoFuncionaPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ComoFuncionaPage from '../ComoFuncionaPage'

describe('ComoFuncionaPage', () => {
  it('renderiza el titulo y los 4 pasos del proceso', () => {
    render(<ComoFuncionaPage />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Cómo funciona')
    expect(screen.getByText('Cuéntanos tu marca')).toBeInTheDocument()
    expect(screen.getByText('Analizamos tu marca')).toBeInTheDocument()
    expect(screen.getByText('Generamos tu semana de contenido')).toBeInTheDocument()
    expect(screen.getByText('Descarga y publica')).toBeInTheDocument()
  })

  it('marca los 4 bloques de captura como placeholder pendiente', () => {
    render(<ComoFuncionaPage />)
    const placeholders = screen.getAllByText(/\[Placeholder\]/i)
    expect(placeholders).toHaveLength(4)
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/pages/__tests__/ComoFuncionaPage.test.tsx`
Expected: FAIL — `Failed to resolve import "../ComoFuncionaPage"`.

- [ ] **Step 3: Crear `src/pages/ComoFuncionaPage.tsx`**

```tsx
interface Step {
  title: string
  description: string
  placeholder: string
}

const STEPS: Step[] = [
  {
    title: 'Cuéntanos tu marca',
    description: 'Nos das 4 datos: tu marca, qué vendes, a quién le vendes y qué te hace diferente. Nada de formularios largos.',
    placeholder: '[Placeholder] Captura real del formulario de análisis de marca',
  },
  {
    title: 'Analizamos tu marca',
    description: 'Con esos 4 datos construimos los pilares de contenido de tu marca — no una plantilla genérica, algo hecho para lo que tú vendes.',
    placeholder: '[Placeholder] Captura real del resultado del análisis de marca',
  },
  {
    title: 'Generamos tu semana de contenido',
    description: 'Combinamos desarrollo propio con inteligencia artificial para evitar el resultado plano que se nota cuando se deja que la IA improvise sola.',
    placeholder: '[Placeholder] Captura real del calendario de contenido generado',
  },
  {
    title: 'Descarga y publica',
    description: 'Tienes 7 días de contenido listo — imágenes y textos — para descargar y programar en la red social que prefieras, cuando tú quieras.',
    placeholder: '[Placeholder] Captura real de la pantalla de descarga de un post',
  },
]

export default function ComoFuncionaPage() {
  return (
    <section className="py-16 md:py-24 bg-brand-background">
      <div className="container mx-auto px-5 md:px-6">
        <div className="max-w-xl mb-12 md:mb-16">
          <p className="text-brand-secondary font-heading text-xs font-bold uppercase tracking-[0.2em] mb-4">
            El proceso
          </p>
          <h1 className="font-heading text-2xl sm:text-3xl md:text-4xl font-extrabold text-white">
            Cómo funciona
          </h1>
        </div>

        <div className="space-y-14 md:space-y-20">
          {STEPS.map((step, i) => (
            <div key={step.title} className="grid md:grid-cols-2 gap-6 md:gap-10 items-center">
              <div className={i % 2 === 1 ? 'md:order-2' : ''}>
                <span className="font-display text-3xl text-gradient-signature mb-3 block">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <h2 className="font-heading text-xl md:text-2xl font-bold text-white mb-3">
                  {step.title}
                </h2>
                <p className="text-white/70 leading-relaxed">{step.description}</p>
              </div>
              <div
                className={`rounded-2xl bg-brand-surface border border-dashed border-white/15 aspect-video flex items-center justify-center text-white/40 text-sm p-6 text-center ${i % 2 === 1 ? 'md:order-1' : ''}`}
              >
                {step.placeholder}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/pages/__tests__/ComoFuncionaPage.test.tsx`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/pages/ComoFuncionaPage.tsx src/pages/__tests__/ComoFuncionaPage.test.tsx
git commit -m "feat: agregar pagina Como Funciona (4 pasos del pipeline, capturas reales pendientes)"
```

---

### Task 7: DudasPage.tsx (FAQ, borrador)

**Files:**
- Create: `src/pages/DudasPage.tsx`
- Create: `src/pages/__tests__/DudasPage.test.tsx`

**Interfaces:**
- Consumes: ninguna de las tareas anteriores.
- Produces: componente `DudasPage` exportado por defecto, sin props. Consumido por `router.tsx` en la Task 8 como la ruta `/dudas`.

**Contenido exacto** (6 preguntas, redactadas a partir de `MarcaCosmic.md` — objeciones reales del manual de marca, tono "Sabio + Cuidador": resultados concretos, honesto sobre limitaciones, sin jerga de IA. Marcado como borrador para revisión de Anuar):

- [ ] **Step 1: Escribir el test**

Crear `src/pages/__tests__/DudasPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import DudasPage from '../DudasPage'

describe('DudasPage', () => {
  it('renderiza el titulo y el badge de borrador', () => {
    render(<DudasPage />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Dudas frecuentes')
    expect(screen.getByText(/borrador/i)).toBeInTheDocument()
  })

  it('renderiza las 6 preguntas', () => {
    render(<DudasPage />)
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(6)
  })
})
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `npx vitest run src/pages/__tests__/DudasPage.test.tsx`
Expected: FAIL — `Failed to resolve import "../DudasPage"`.

- [ ] **Step 3: Crear `src/pages/DudasPage.tsx`**

```tsx
interface FAQItem {
  question: string
  answer: string
}

const FAQS: FAQItem[] = [
  {
    question: '¿El contenido se ve genérico, como el de otras herramientas de IA?',
    answer: 'No dejamos que la IA improvise sola. Combinamos inteligencia artificial con desarrollo propio — scripts y pilares de contenido definidos para tu marca — que es justo lo que evita el resultado plano que se nota en otras herramientas.',
  },
  {
    question: '¿Qué tan personalizado es el contenido a mi marca específica?',
    answer: 'Mejora mientras más clara y completa sea la información que nos das, y varía según la industria — es algo que seguimos desarrollando activamente. Lo que sí es consistente hoy es la velocidad, la estructura del contenido y la calidad visual del resultado.',
  },
  {
    question: '¿Necesito saber de diseño o marketing para usarlo?',
    answer: 'No. Nos das 4 datos básicos — tu marca, qué vendes, a quién le vendes y qué te hace diferente — y en minutos tienes una semana de contenido listo, sin tener que pensar, diseñar ni editar nada desde cero.',
  },
  {
    question: '¿Cuánto tiempo toma tener contenido listo?',
    answer: 'Minutos, no días. De 4 datos de tu marca a una semana completa de contenido estructurado.',
  },
  {
    question: '¿Cómo es diferente de contratar una agencia de marketing?',
    answer: 'Somos rápidos y accesibles sin sacrificar calidad — pensado para quien no puede pagar una agencia ni esperar semanas de entrega. Además, escalamos con cómputo, no con más manos: más clientes no significa bajar la calidad ni subir los tiempos.',
  },
  {
    question: '¿Qué pasa si mi industria es poco común o muy específica?',
    answer: 'Lo decimos con transparencia: la calidad del resultado varía según la industria y la información que aportas. Preferimos decirte "esto todavía lo estamos mejorando" antes que prometer de más.',
  },
]

export default function DudasPage() {
  return (
    <section className="py-16 md:py-24 bg-brand-background">
      <div className="container mx-auto px-5 md:px-6 max-w-2xl">
        <div className="flex items-center gap-3 mb-4">
          <h1 className="font-heading text-2xl sm:text-3xl md:text-4xl font-extrabold text-white">
            Dudas frecuentes
          </h1>
          <span className="text-[10px] font-heading font-bold uppercase tracking-widest text-brand-accent bg-white/5 px-2.5 py-1 rounded-full">
            Borrador — en revisión
          </span>
        </div>
        <p className="text-white/60 mb-12 md:mb-16">
          Estas respuestas están basadas en nuestro manual de marca y todavía están en revisión.
        </p>

        <div className="space-y-10">
          {FAQS.map((faq) => (
            <div key={faq.question} className="border-t border-white/10 pt-6">
              <h2 className="font-heading text-lg font-bold text-white mb-2">
                {faq.question}
              </h2>
              <p className="text-white/70 leading-relaxed">{faq.answer}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `npx vitest run src/pages/__tests__/DudasPage.test.tsx`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/pages/DudasPage.tsx src/pages/__tests__/DudasPage.test.tsx
git commit -m "feat: agregar pagina Dudas (FAQ borrador desde MarcaCosmic.md, pendiente de revision)"
```

---

### Task 8: router.tsx + main.tsx — integración final

**Files:**
- Create: `src/router.tsx`
- Modify: `src/main.tsx`
- Delete: `src/App.tsx`
- Delete: `src/components/__tests__/App.test.tsx`

**Interfaces:**
- Consumes: `Layout` (Task 3), `Home` (Task 4), `ServiciosPage`/`NosotrosPage`/`ResenasPage` (Task 5), `ComoFuncionaPage` (Task 6), `DudasPage` (Task 7) — todos ya existen.
- Produces: router exportado desde `src/router.tsx`, montado en `main.tsx`. Es la integración final — no hay tareas después que dependan de esto salvo la verificación (Task 9).

- [ ] **Step 1: Crear `src/router.tsx`**

```tsx
import { createBrowserRouter } from 'react-router-dom'
import Layout from './components/Layout'
import Home from './pages/Home'
import ServiciosPage from './pages/ServiciosPage'
import NosotrosPage from './pages/NosotrosPage'
import ResenasPage from './pages/ResenasPage'
import ComoFuncionaPage from './pages/ComoFuncionaPage'
import DudasPage from './pages/DudasPage'

const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Home /> },
      { path: 'nuestroservicio', element: <ServiciosPage /> },
      { path: 'nosotros', element: <NosotrosPage /> },
      { path: 'resenas', element: <ResenasPage /> },
      { path: 'comofunciona', element: <ComoFuncionaPage /> },
      { path: 'dudas', element: <DudasPage /> },
    ],
  },
])

export default router
```

- [ ] **Step 2: Reemplazar `src/main.tsx`**

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import './styles/globals.css'
import router from './router.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
```

- [ ] **Step 3: Eliminar `App.tsx` y `App.test.tsx`**

```bash
git rm src/App.tsx src/components/__tests__/App.test.tsx
```

- [ ] **Step 4: Correr la suite completa de tests**

Run: `npm run test:run`
Expected: todos los test files pasan — 13 archivos previos (menos `App.test.tsx` que se eliminó, más `CookieBanner.test.tsx`, `Layout.test.tsx`, `Home.test.tsx`, `ServiciosPage.test.tsx`, `NosotrosPage.test.tsx`, `ResenasPage.test.tsx`, `ComoFuncionaPage.test.tsx`, `DudasPage.test.tsx` — 8 archivos nuevos, 1 eliminado).

- [ ] **Step 5: Correr el build de producción**

Run: `npm run build`
Expected: `tsc` sin errores de tipos, `vite build` exitoso.

- [ ] **Step 6: Commit**

```bash
git add src/router.tsx src/main.tsx
git commit -m "feat: montar React Router - 6 rutas reales, elimina App.tsx (reemplazado por router.tsx + Layout + paginas)"
```

---

### Task 9: Verificación final (ejecutada por el controlador de la sesión, no delegada)

**Files:** ninguno (solo verificación).

**Interfaces:** N/A — verificación end-to-end de todas las tareas anteriores.

- [ ] **Step 1: Correr la suite completa de tests**

Run: `npm run test:run`
Expected: todos los tests pasan, 0 failures.

- [ ] **Step 2: Correr el build de producción**

Run: `npm run build`
Expected: build limpio.

- [ ] **Step 3: Levantar el preview y navegar las 6 rutas reales con Playwright**

Levantar `npm run preview -- --port 4321 --host` en background. Con Playwright (desktop 1440x900 y mobile 390x844), navegar directo (vía `page.goto`, no solo clicks internos — para confirmar que el fallback SPA de Vite sirve `index.html` en rutas profundas) a cada una de:
`http://localhost:4321/`, `/nuestroservicio`, `/nosotros`, `/resenas`, `/comofunciona`, `/dudas`.

Confirmar en cada una:
- Header y Footer presentes (el logo y el crédito de Tu Web MX se ven).
- El link de "Aviso de privacidad" y "Términos de uso" del Footer apuntan a `https://agentecosmic.com/privacidad/` y `https://agentecosmic.com/terminos/` (inspeccionar el atributo `href`, no hace falta seguir el link).
- El CookieBanner aparece en la primera visita.
- En `/`, el logo grande del Hero se ve, y al hacer scroll el logo chico del Header aparece (mismo mecanismo ya verificado en el rediseño anterior — confirmar que sigue funcionando).
- En las otras 5 rutas, el logo chico del Header se ve fijo desde el inicio (sin el logo grande, porque no hay Hero ahí).

Tomar screenshots reales (mobile + desktop) de `/comofunciona` y `/dudas` — las 2 páginas de contenido completamente nuevo — y guardarlos para revisión visual.

- [ ] **Step 4: Confirmar el comportamiento del ancla cruzada Contacto**

Con Playwright, desde `/nuestroservicio` (o cualquier ruta que no sea Home), hacer click en el link "Contacto" del Footer. Confirmar que navega a `/` y hace scroll hasta la sección con `id="contacto"` (confirmar visualmente en el screenshot que el viewport quedó centrado ahí, no en el top de Home).

- [ ] **Step 5: Detener el preview**

```bash
pkill -f "vite preview"
```

- [ ] **Step 6: Actualizar `.superpowers/sdd/progress.md` con el resumen de las 9 tareas**

No requiere commit (el ledger es scratch, git-ignorado).
