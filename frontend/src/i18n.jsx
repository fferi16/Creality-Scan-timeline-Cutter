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
    // SDK path: the mesh is already rebuilt, so there is nothing left to do
    crealityHintSdk:
      'The {name} project is ready — already re-fused without the removed sections. Open it in Creality Scan to check it.',
    sdkBadge: 'Rebuilt automatically',
    sdkBadgeDesc:
      "Creality Scan's own engine does the cut and rebuilds the mesh, so you don't have to re-fuse by hand.",
    // job stages reported by the backend (it sends the key, not the wording)
    stageCopy: 'Copying the project',
    stageOpen: 'Opening the project',
    stageCloud: 'Reading the point cloud',
    stageMatch: 'Matching the time range',
    stageDelete: 'Removing points',
    stageFuse: 'Rebuilding the mesh',
    stageDone: 'Done',
    refuseLabel: 'Rebuild the mesh after cutting',
    refuseDesc:
      'Takes minutes on a full body scan and needs a lot of memory. Off is much faster — the cut is the same either way, you just fuse it in Creality Scan yourself.',
    meshLoading: 'Preparing the surface…',
    errMeshFailed: 'The surface could not be prepared.',
    cutNotFused:
      'The mesh was not rebuilt — the cut itself is done. Reason: {reason}',

    placeholderTitle: 'Choose a scanned project',
    placeholderDesc:
      'Points are coloured by scan time (blue → warm). Motion typically shows up as a time-separated, doubled layer.',

    busyLoad: 'Copying and loading the project…',
    busyCut: 'Removing points and saving to the Creality project…',
    busyFrames: 'Decoding the frames',
    stageFrames: 'Decoding the frames',

    timelineHead: 'Scan time — only the visible section is shown',
    scanStart: 'Scan start',
    scanEnd: 'Scan end',
    pointsShown: '{shown} / {total} points shown',
    framesShown: '{shown} / {total} frames shown',
    frames: 'Frames',
    frameTimeline: 'Frame-exact timeline',
    frameTimelineDesc:
      'The slider selects whole frames, and the cut removes exactly those — what disappears from the view is what disappears from the scan.',
    frameFallback:
      'The frames could not be read ({reason}), so the timeline is the registered cloud: the cut is still by time, only its edges are approximate.',
    frameBlank:
      '{count} frames have nothing to draw (their image would not decode). They stay on the timeline and are cut with their section.',
    cutRowFrames: '{from}–{to}% · {frames} frames',
    keepInfoFrames: '{remaining} / {total} frames kept',

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
    // SDK-út: a mesh már újraépült, nincs teendő
    crealityHintSdk:
      'A {name} projekt kész — a kivágott szakaszok nélkül már újra is fuzionált. Nyisd meg a Creality Scanben, ha meg akarod nézni.',
    sdkBadge: 'Automatikus újraépítés',
    sdkBadgeDesc:
      'A Creality Scan saját motorja végzi a vágást és építi újra a mesh-t, így nem kell kézzel fuzionálnod.',
    // a backend a stádium kulcsát küldi, a szöveg itt van
    stageCopy: 'Projekt másolása',
    stageOpen: 'Projekt megnyitása',
    stageCloud: 'Pontfelhő beolvasása',
    stageMatch: 'Idősáv illesztése',
    stageDelete: 'Pontok törlése',
    stageFuse: 'Mesh újraépítése',
    stageDone: 'Kész',
    refuseLabel: 'Mesh újraépítése vágás után',
    refuseDesc:
      'Teljes alakos szkennél percekbe telik és sok memóriát igényel. Kikapcsolva sokkal gyorsabb — a vágás ugyanaz, csak a fúziót te futtatod a Creality Scanben.',
    meshLoading: 'Felület előkészítése…',
    errMeshFailed: 'A felületet nem sikerült előkészíteni.',
    cutNotFused:
      'A mesh nem épült újra — maga a vágás megvan. Ok: {reason}',

    placeholderTitle: 'Válassz egy szkennelt projektet',
    placeholderDesc:
      'A pontok a szkennelés idejében színeződnek (kék → meleg). A bemozdulás jellemzően időben elkülönülő, kettőződött rétegként tűnik fel.',

    busyLoad: 'Projekt másolása és betöltése…',
    busyCut: 'Pontok törlése és mentés a Creality projektbe…',
    busyFrames: 'Képkockák dekódolása',
    stageFrames: 'Képkockák dekódolása',

    timelineHead: 'Szkennelési idő — csak a látható szakasz jelenik meg',
    scanStart: 'Szken eleje',
    scanEnd: 'Szken vége',
    pointsShown: '{shown} / {total} pont látszik',
    framesShown: '{shown} / {total} képkocka látszik',
    frames: 'Képkockák',
    frameTimeline: 'Képkocka-pontos idővonal',
    frameTimelineDesc:
      'A csúszka egész képkockákat jelöl ki, és pontosan azokat törli a vágás — ami eltűnik a nézetből, az tűnik el a szkenből.',
    frameFallback:
      'A képkockákat nem sikerült beolvasni ({reason}), így az idővonal a regisztrált pontfelhő: a vágás továbbra is idő szerint megy, csak a szélei közelítők.',
    frameBlank:
      '{count} képkockának nincs megjeleníthető pontja (a képük nem dekódolható). Az idővonalon maradnak, és a szakaszukkal együtt kivágódnak.',
    cutRowFrames: '{from}–{to}% · {frames} képkocka',
    keepInfoFrames: '{remaining} / {total} képkocka maradt',

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
