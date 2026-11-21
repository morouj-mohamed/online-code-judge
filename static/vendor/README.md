# Vendored front-end libraries

These files are committed so the site works offline (lab networks, CDN outages)
and needs no build step to run.

| File | What | License |
|------|------|---------|
| `codemirror.js` | CodeMirror 6 (state, view, commands, language, autocomplete, search, lang-cpp, lang-python) bundled into one ES module | MIT (license texts at the end of the file) |
| `../fonts/Inter-Variable.woff2` | Inter variable font, Latin subset | SIL OFL 1.1 (`../fonts/Inter-LICENSE.txt`) |
| `../fonts/JetBrainsMono-Variable.woff2` | JetBrains Mono variable font, Latin subset | SIL OFL 1.1 (`../fonts/JetBrainsMono-LICENSE.txt`) |

To rebuild `codemirror.js` (only needed to upgrade CodeMirror):

    cd tools/vendor
    npm install
    npm run build
