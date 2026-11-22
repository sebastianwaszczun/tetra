# front – TETRA (kot detektyw + kartoteka kampanii)

React + Vite. Strona główna sprawdza materiał (`POST /analyze`), kartoteka pokazuje kampanie, operatorów i graf powiązań.

```bash
cd front
bun install        # albo npm install
bun run dev        # http://localhost:5173 (adres API: VITE_API_URL, domyślnie http://localhost:8000)
bun run build      # -> dist/
```

Widoki (routing po `#`): `#/` sprawdzanie, `#/kampanie` przegląd (lista, mapa 2D/3D, feed skanera),
`#/kampanie/<id>` kampania, `#/operator/<id>` operator, `#/material/<id>` materiał.

- `src/App.jsx` – strona główna, animacja kota (idle → jump → pull → open → closing), wynik sprawdzenia, routing
- `src/pages.jsx` – przegląd kampanii, kampania, operator, materiał
- `src/parts.jsx` – panel „dlaczego połączone”, decyzja analityka, fakty i miniatury materiału
- `src/Graph.jsx` – graf 2D (react-force-graph + Rough.js, styl szkicu), `src/Graph3D.jsx` – mapa 3D (three.js, ładowana na żądanie)
- `src/graphData.js` – przygotowanie grafu z `GET /graph` (grupowanie śladów tego samego rodzaju)
- `src/Result.jsx` – karta wyniku po sprawdzeniu materiału
- `src/api.js`, `src/route.js`, `src/ui.jsx` – klient API, routing, wspólne elementy
- `src/index.css`, `src/views.css` – style (papier, obrys „tuszem”, kreskowanie)
- `src/assets/cat/` – klatki kota, `src/assets/fonts/` – Gloria Hallelujah + Patrick Hand (OFL)
