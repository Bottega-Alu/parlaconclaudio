# 1.0.14-test.2 — Comandi rapidi e finestra ridisegnati

Anteprima basata sulla stabile 1.0.13.

## Comandi rapidi (clic sull'icona nella tray)

- Sei tile come le Impostazioni rapide di Windows 11: Dettatura, Audio, Suoni,
  Avvisi vocali, Microfono, Riconoscimento. Sotto ogni tile, il valore in uso.
- Suoni, Avvisi vocali, Microfono e Riconoscimento aprono una lista. Per i
  pacchetti suoni la lista funziona come le reti Wi-Fi: la riga in uso ha la
  barra colorata e "In uso"; clic su un pacchetto per espanderlo con
  "Ascolta" e "Usa". Esc o la freccia tornano indietro.
- Indicatore colorato dello stato, slider del volume con icona mute, barra in
  basso con scorciatoia e icone per file audio, impostazioni e menu completo.
- Colore accent e tema chiaro/scuro letti da Windows; icone di sistema
  (Segoe Fluent Icons).
- Con la taskbar in auto-hide il pannello non finisce più sotto la taskbar e il
  fondo non viene più tagliato dal bordo nativo.

## Novità della test.2

- **Incolla automaticamente** si può spegnere (pannello > Dettatura, o menu
  completo): il testo dettato resta solo negli appunti e lo incolli tu con Ctrl+V.
- **Volume voci** separato dal volume dei suoni, nei comandi rapidi, nel
  pannello e nel menu completo. Finché non lo cambi segue il volume dei suoni.

## Finestra dell'app (Impostazioni)

- Layout come le Impostazioni di Windows 11: navigazione a sinistra (Home,
  Dettatura, Avvisi vocali, Collegamenti, Informazioni) e schede a destra.
- Nuove opzioni senza passare dal menu: lingua di dettatura, volume, modalità
  degli annunci, voce, pacchetto suoni con anteprima, avvio con Windows,
  chiavi API cloud e verifica connessione.
- Tema scuro anche sulla barra del titolo; testo nitido ad alto DPI.

L'installer usa identità e cartella TEST separate dalla stabile 1.0.13.
Chiudere la versione stabile prima di avviare la TEST, per evitare hotkey e
audio duplicati. La build non è pubblicata e non è integrata in main.
