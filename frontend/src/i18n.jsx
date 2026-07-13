import React, { createContext, useContext, useState } from 'react';

// Two languages, one flat dictionary each. Use {name} placeholders and pass
// values via t('key', { name: ... }).
const translations = {
  en: {
    appName: 'Creality Scan Timeline Cutter',
    statusReady: 'Local agent available',

    toolTitle: 'Timeline Cutter',
    intro:
      'Pick a scanned project. Its points appear in the real scan order — use the bottom slider to scrub through time, isolate the section where the subject moved, and cut it. The cut removes those points from the scan cloud, so re-fusing in Creality Scan rebuilds the model without them.',

    projects: 'Projects',
    loading: 'Loading…',
    noProjects: 'No Creality Scan project found on this computer.',

    cut: 'Cut',
    points: 'Points',
    project: 'Project',
    modelSurface: 'Model surface',
    modelSurfaceDesc: 'Show the fused surface under the points',
    intervalStart: 'Start',
    intervalEnd: 'End',
    cutButton: '✂️ Cut this section',
    inProgress: 'In progress…',
    anotherProject: '← Another project',

    cutsHeader: '✅ Cuts made',
    startOver: '↺ Start over',
    cutRow: '{from}–{to}% · {points} points',
    keepInfo: '{remaining} / {total} points kept',
    crealityHint:
      'Open the {name} project in Creality Scan and run the fusion — it rebuilds without the removed sections.',

    placeholderTitle: 'Choose a scanned project',
    placeholderDesc:
      'Points are coloured by scan time (blue → warm). Motion typically shows up as a time-separated, doubled layer.',

    busyLoad: 'Copying and loading the project…',
    busyCut: 'Removing points and saving to the Creality project…',

    timelineHead: 'Scan time — only the visible section is shown',
    scanStart: 'Scan start',
    scanEnd: 'Scan end',
    pointsShown: '{shown} / {total} points shown',

    errWholeScan:
      'Narrow the slider to the section to remove — right now the whole scan would be cut.',
    errUnknown: 'Unknown error',
  },
  hu: {
    appName: 'Creality Scan Timeline Cutter',
    statusReady: 'Helyi ágens elérhető',

    toolTitle: 'Idő-alapú vágó',
    intro:
      'Válaszd ki a szkennelt projektet. A pontok a valódi szkennelési sorrendben jelennek meg — az alsó csúszkával végignézed az időt, kijelölöd a bemozdult szakaszt, és kivágod. A vágás ezeket a pontokat törli a szken felhőjéből, így a Creality Scanben újrafuzionálva a hibás szakasz nélkül áll össze a modell.',

    projects: 'Projektek',
    loading: 'Betöltés…',
    noProjects: 'Nem található Creality Scan projekt ezen a gépen.',

    cut: 'Vágás',
    points: 'Pontok',
    project: 'Projekt',
    modelSurface: 'Modell felület',
    modelSurfaceDesc: 'A fúzionált felület mutatása a pontok alatt',
    intervalStart: 'Kezdet',
    intervalEnd: 'Vég',
    cutButton: '✂️ Szakasz kivágása',
    inProgress: 'Folyamatban…',
    anotherProject: '← Másik projekt',

    cutsHeader: '✅ Vágások',
    startOver: '↺ Újrakezdés',
    cutRow: '{from}–{to}% · {points} pont',
    keepInfo: '{remaining} / {total} pont maradt',
    crealityHint:
      'Nyisd meg a {name} projektet a Creality Scanben, és futtasd le a fúziót — a kivágott szakaszok nélkül áll össze.',

    placeholderTitle: 'Válassz egy szkennelt projektet',
    placeholderDesc:
      'A pontok a szkennelés idejében színeződnek (kék → meleg). A bemozdulás jellemzően időben elkülönülő, kettőződött rétegként tűnik fel.',

    busyLoad: 'Projekt másolása és betöltése…',
    busyCut: 'Pontok törlése és mentés a Creality projektbe…',

    timelineHead: 'Szkennelési idő — csak a látható szakasz jelenik meg',
    scanStart: 'Szken eleje',
    scanEnd: 'Szken vége',
    pointsShown: '{shown} / {total} pont látszik',

    errWholeScan:
      'Szűkítsd a csúszkát a kivágandó szakaszra — most az egész szken ki lenne vágva.',
    errUnknown: 'Ismeretlen hiba',
  },
};

const LangContext = createContext(null);

export function LangProvider({ children }) {
  const [lang, setLang] = useState(() => localStorage.getItem('lang') || 'hu');
  const change = (l) => { localStorage.setItem('lang', l); setLang(l); };
  return <LangContext.Provider value={{ lang, setLang: change }}>{children}</LangContext.Provider>;
}

export function useT() {
  const { lang, setLang } = useContext(LangContext);
  const t = (key, vars) => {
    let s = (translations[lang] && translations[lang][key]) || key;
    if (vars) for (const k in vars) s = s.split(`{${k}}`).join(vars[k]);
    return s;
  };
  return { t, lang, setLang };
}
