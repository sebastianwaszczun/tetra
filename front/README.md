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

```
src/
  main.jsx, App.jsx        wejście, układ strony (nagłówek, stopka) i routing
  pages/                   Home (sprawdzanie + kot), Campaigns, Campaign, Operator, Material, shared (wspólne elementy)
  components/              ui (pudełka, plakietki, Chips, useFetch), WhyPanel („dlaczego połączone”), Decision,
                           MaterialInfo (fakty, moduły, miniatury), Result (karta wyniku), sketch (odręczne SVG)
  graph/                   Graph2D (react-force-graph + Rough.js), Graph3D (three.js, ładowany na żądanie),
                           prepare (dane z GET /graph, grupowanie śladów), thumbs (miniatury zrzutów)
  lib/                     api (klient API), route (routing po #)
  styles/                  base.css (strona główna, nagłówek, stopka), kartoteka.css (widoki kartoteki)
  assets/                  klatki kota, fonty Gloria Hallelujah + Patrick Hand (OFL)
```
