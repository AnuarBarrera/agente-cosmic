# GitHub Pages Setup Guide

Follow these steps to enable automatic documentation deployment:

## 1. Enable GitHub Pages

1. Go to your repository on GitHub
2. Click **Settings** tab
3. Scroll to **Pages** section (left sidebar)
4. Under **Source**, select: **GitHub Actions**
5. Save the settings

## 2. Verify Workflow Permissions

1. Go to **Settings** → **Actions** → **General**
2. Under **Workflow permissions**, ensure:
   - ✅ **Read and write permissions** is selected
   - ✅ **Allow GitHub Actions to create and approve pull requests** is checked

## 3. First Deployment

After pushing your changes:
1. Go to **Actions** tab in your repository
2. Watch the "Deploy Documentation" workflow run
3. Once complete, documentation will be available at:
   - `https://YOUR-USERNAME.github.io/YOUR-REPO-NAME/`

## 4. Custom Domain (Optional)

If you have a custom domain:
1. In repository **Settings** → **Pages**
2. Add your custom domain (e.g., `docs.dialogix.com`)
3. Update `site_url` in `mkdocs.yml`

## Troubleshooting

- **404 Error**: Check that workflow completed successfully
- **Permission Error**: Verify workflow permissions are correct
- **Build Failed**: Check Actions tab for error details