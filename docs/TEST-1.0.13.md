# 1.0.13-test.1 — Comandi rapidi della tray

Anteprima del pannello ispirato ai controlli rapidi di Windows 11.

- Clic sinistro o destro sulla tray: pannello con dettatura, audio, volume,
  microfono e riconoscimento. Il clic sinistro attende il doppio clic.
- Tema chiaro/scuro letto da Windows, icone ridisegnate ad alta risoluzione,
  angoli arrotondati su Windows 11. Non è un componente delle Impostazioni di Windows.
- Impostazioni apre il pannello esistente; Menu completo conserva tutte le funzioni.
- Esc o clic fuori chiudono il pannello; Tab e frecce permettono la navigazione.
- Il pannello viene creato al primo utilizzo; nascosto non legge configurazioni,
  non cerca microfoni e non ridisegna. Il timer controlla solo la coda una volta al secondo.

L'installer usa identità e cartella TEST separate dalla versione stabile 1.0.12.
Chiudere la versione stabile prima di avviare la TEST, per evitare hotkey e audio duplicati.
Gli hook Claude/Codex sono opzionali e preselezionati: deselezionarli per provare solo
l'interfaccia, oppure selezionarli consapevolmente per collegare la versione TEST.
La build non è pubblicata e non è integrata in main.

Da valutare sul proprio computer: apertura vicino alla tray, cambio tema e monitor,
volume e mute, scelta microfono, dettatura con ritorno del testo nell'editor,
impostazioni e menu completo. La verifica grafica locale usa il renderer reale
con azioni di anteprima innocue; non sostituisce la prova audio dopo installazione.
