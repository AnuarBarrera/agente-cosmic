# Integrated Documentation Deployment

This guide shows you how to deploy DIALOGIX with integrated documentation that runs alongside your application.

## 🚀 Two Options Available

### Option 1: GitHub Pages (Automatic)
- **Pros**: Free, automatic deployment, CDN
- **Cons**: Separate URL, requires GitHub account
- **URL**: `https://username.github.io/repository/`

### Option 2: Docker Integration (Recommended)
- **Pros**: Same domain, no external dependencies
- **Cons**: Uses server resources
- **URL**: `http://yourapp.com/docs/`

## 🐳 Docker Integration Setup

### Quick Start
```bash
# Start everything (app + docs)
make dev-docker

# Access documentation at:
# http://localhost:3000/docs/
```

### What happens:
1. **docs** service builds and serves MkDocs
2. **nginx** routes `/docs/` to documentation
3. **Live reload** - changes auto-update

### Architecture
```
┌─────────────────────────────────────┐
│           nginx (port 3000)         │
├─────────────────────────────────────┤
│  /           → frontend:3000        │
│  /api/       → backend:8000         │
│  /docs/      → docs:8001            │ ✨ NEW!
│  /admin/     → backend:8000         │
└─────────────────────────────────────┘
```

## 📝 Production Configuration

### 1. Build Static Docs
For production, build docs as static files:

```bash
# Build documentation
mkdocs build

# Serve with nginx (no separate container)
# nginx serves from ./site/ folder
```

### 2. Production docker-compose.prod.yml
```yaml
version: '3.8'
services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.prod.conf:/etc/nginx/conf.d/default.conf
      - ./site:/var/www/docs  # Pre-built docs
      - ./staticfiles:/var/www/static
    depends_on:
      - backend
```

### 3. nginx.prod.conf
```nginx
server {
    listen 80;
    server_name yourdomain.com;

    # Serve documentation statically
    location /docs/ {
        alias /var/www/docs/;
        try_files $uri $uri/ =404;
        
        # Add headers for better caching
        expires 1h;
        add_header Cache-Control "public, immutable";
    }
    
    # API routes
    location /api/ {
        proxy_pass http://backend:8000;
        # ... proxy headers
    }
    
    # Frontend (SPA)
    location / {
        alias /var/www/frontend/;
        try_files $uri $uri/ /index.html;
    }
}
```

## 🔄 Development Workflow

### Local Development
```bash
# Terminal 1: Start all services
make dev-docker

# Terminal 2: Work on docs (live reload)
# Edit files in docs/ - changes appear instantly
```

### Making Changes
1. Edit `.md` files in `docs/` folder
2. Changes auto-rebuild and appear at `/docs/`
3. Commit changes to trigger GitHub Pages deployment

## ⚙️ Advanced Configuration

### Custom Domain for GitHub Pages
1. Add `CNAME` file to `docs/` folder:
```
docs.yourdomain.com
```

2. Configure DNS:
```
CNAME docs.yourdomain.com username.github.io
```

### Both Options Together
You can have both:
- **GitHub Pages**: Public documentation
- **Integrated**: Internal/admin documentation

Just use different `mkdocs.yml` configurations.

## 🎯 Recommendation

**For most users**: Use **Docker Integration**
- Same domain (`yourapp.com/docs/`)
- No external dependencies
- Better user experience
- Corporate firewall friendly

**For open source**: Use **GitHub Pages**
- Free hosting
- CDN performance
- Community access
- Automatic deployment

## 📊 Comparison Table

| Feature | GitHub Pages | Docker Integration |
|---------|-------------|-------------------|
| Cost | Free | Server resources |
| Setup | One-time | Docker knowledge |
| URL | Separate domain | Same domain |
| Performance | CDN | Your server |
| Updates | Auto on push | Auto on container restart |
| Offline | No | Yes (if app is offline) |
| Corporate | May be blocked | Always works |

---

Choose the option that best fits your deployment strategy! 🎉