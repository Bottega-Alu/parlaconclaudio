# Versione di prova 1.0.11-test.2

Integra `fix/idle-cpu` (05e66f4) sopra `test/tray-installer-1.0.10`
(0c60e10), sul branch `test/tray-installer-1.0.11` in un worktree separato.
Nessun merge in main, push, tag o rilascio pubblico.

## Hotfix test.2: console delle notifiche Codex

Il worker audio ora preferisce il Python GUI di base, mantenendo il virtualenv,
e viene avviato con `CREATE_NO_WINDOW` senza `DETACHED_PROCESS`. Rimangono il
passaggio JSON su stdin e il tentativo di uscire dal job del processo chiamante,
così la voce può terminare dopo il completamento dell'hook.

La regressione Windows avvia un worker reale e verifica console assente,
virtualenv corretto e payload UTF-8 integro. La suite completa conta 400 test.
La prova non genera audio e non sostituisce l'osservazione del successivo evento
Codex reale.

Sul PC interessato è stato aggiornato con backup soltanto lo script
`VoiceBridge103/scripts/notify-tts.py` effettivamente richiamato dagli hook.
Il file `hooks.json` e i comandi approvati sono rimasti invariati: non serve
una nuova approvazione per questa correzione dello script. Il nuovo installer
include la correzione per gli hook che usano la propria copia installata;
mantiene la politica TEST di non sovrascrivere altre copie esterne preesistenti.

## Cosa contiene

- Conserva il pannello nativo, i fix runtime e l'installer TEST della 1.0.10.
- Integra il renderer NumPy con geometria originale a 192 px: calcolo a 128 px,
  antialiasing e icona finale a 64 px.
- Conserva l'icona statica a riposo: nessun aggiornamento periodico della shell
  e nessuna generazione periodica di file ICO a riposo.
- Durante il lavoro riutilizza fino a 12 frame per stato, senza ricalcolarli
  continuamente. Anche questi frame passano dal nuovo renderer.
- Conserva la compilazione da worktree con `build_exe.ps1 -Python <interprete>`.

## Installazione e prova

L'installer aggiorna la precedente versione TEST nella cartella
`%LOCALAPPDATA%\Programs\VoiceBridge-Test`; l'identità TEST e il suo task di
avvio rimangono separati dalla versione abituale. Mantieni la cartella proposta.
Chiudi l'app abituale o la TEST precedente prima di avviare questa versione.
Le due versioni non possono dettare contemporaneamente.

Impostazioni audio e configurazioni degli hook rimangono condivise. Le caselle
Claude Code/Codex sono opzionali e inizialmente selezionate in un setup nuovo;
su aggiornamento vengono rispettate le scelte precedenti. Deselezionarle non
elimina collegamenti già presenti. L'avvio automatico TEST parte disattivato.
Il setup TEST non aggiorna copie esterne di script o interpreti degli hook.
Per nuovi hook Codex resta necessaria la tua verifica/autorizzazione in `/hooks`.

1. Controlla versione `1.0.11-test.2`, pannello e opzioni del setup.
2. Prova dettatura e incolla in un editor, due registrazioni ravvicinate e
   trascrizione di file. I comportamenti della 1.0.10 devono restare disponibili.
3. A riposo controlla che l'icona sia statica; durante registrazione e
   trascrizione deve animarsi senza perdita di nitidezza.
4. Dopo l'installazione misura per 60 secondi il processo bridge con `--child`
   tramite CPU e byte scritti: questa è la verifica reale dell'app completa.

I test del renderer, della cache e un'eventuale misura isolata dell'animatore
non sostituiscono la misura del bridge completo dopo installazione. Le stime
del branch `fix/idle-cpu` riguardavano l'animazione continua: non sono il risultato
atteso a riposo di questa integrazione, che mantiene l'icona statica.

Per ulteriori scenari e le sole proposte Sound Pack Creator:
[guida 1.0.10](TEST-1.0.10.md). Il creator non è implementato.
