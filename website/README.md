# Sugar Activity Studio documentation

This is the Fumadocs documentation site for Sugar Activity Studio. Its source
of truth is `content/docs/`; do not edit generated `.next/` or `.source/`
content.

## Local development

Fumadocs currently requires Node.js 22 or newer.

```sh
npm install
npm run dev
```

Open <http://localhost:3000/docs>.

## Validate a documentation change

```sh
npm run lint
npm run typecheck
npm run build
```

The production server is started with `npm start` after a successful build.
The site also exposes `/llms.txt`, `/llms-full.txt`, and a Markdown
representation of every page at `/docs/<slug>.md`.

Set `NEXT_PUBLIC_SITE_URL` to the deployed origin (for example,
`https://docs.example.org`) so generated social metadata uses the canonical
absolute URL. Local builds fall back to `http://localhost:3000`.

See the rendered **Contributing → Documentation** page for authoring and
maintenance conventions.
