# Versione di prova 1.0.10-test.1

Questa versione serve alla valutazione prima del merge. Branch:
`test/tray-installer-1.0.10`. Nessun merge su main e nessuna pubblicazione.

## Installazione e ritorno alla versione abituale

L'installer TEST usa un'identità di disinstallazione separata e propone
`%LOCALAPPDATA%\Programs\VoiceBridge-Test`. Mantieni questa cartella per non
sovrascrivere l'installazione abituale. L'avvio automatico TEST è inizialmente
disattivato e usa un task distinto se lo abiliti.

Chiudi l'app abituale dalla tray prima di avviare la TEST: le due versioni
condividono la protezione contro avvii simultanei. Le impostazioni audio e gli
hook sono condivisi, quindi le modifiche apportate dal pannello si applicano
anche alla configurazione usata dalla versione abituale.

Le caselle Claude Code e Codex sono opzionali e inizialmente selezionate.
Deselezionarle lascia intatti eventuali collegamenti già presenti: non li
disinstalla. Il setup TEST non aggiorna gli interpreti o le copie esterne di
hook già esistenti. Nuovi collegamenti restano configurazioni degli agenti;
prima di disinstallare la TEST, scollega dal suo menu quelli creati verso la
cartella TEST. Per Codex, verifica e autorizza personalmente gli hook in `/hooks`.

Per tornare all'app abituale: chiudi la TEST e riapri il normale collegamento.

## Cosa valutare

1. Nel setup italiano, leggi la spiegazione dell'app e verifica le due caselle
   indipendenti degli hook. L'avvio automatico deve essere spento.
2. Dalla tray apri il pannello: controlla stato, microfono, motore e collegamenti.
   Chiudere il pannello deve lasciare l'app attiva nella tray.
3. In un editor, premi Ctrl+Alt+Spazio per iniziare, parla e premi nuovamente
   per terminare. Controlla il testo incollato e i segnali sonori.
4. Prova due dettature ravvicinate: la seconda non deve essere interrotta dal
   timer della prima. Controlla anche una registrazione fino al limite.
5. Prova trascrizione file e un secondo clic sul comando: deve essere impedito
   l'avvio di un secondo batch. Durante il lavoro il motore non deve essere
   scaricato o cambiato.
6. A riposo l'icona rimane statica; durante il lavoro si anima. Confronta la CPU
   della versione abituale e della TEST nello stesso scenario.
7. Con motore locale, prova "Libera memoria GPU", poi una dettatura. Il primo
   utilizzo successivo deve ricaricare il modello. "Ricarica modello GPU" è
   invece un'azione distinta.
8. Verifica le notifiche reali Claude/Codex dopo aver autorizzato gli hook.

I test automatici e la verifica visiva non sostituiscono la tua prova reale di
microfono, GPU, incolla e notifiche degli agenti.

## Sound Pack Creator: proposta, non implementato

Primo incremento: un editor con nome del pack, importazione di file audio e
assegnazione agli eventi effettivamente supportati dall'app; ascolto in anteprima
ed esportazione in un pacchetto installabile.

Secondo incremento: controllo dei file mancanti o troppo lunghi, regolazione e
normalizzazione facoltativa del volume, importazione di un pack esistente.

Terzo incremento: varianti casuali per evento e anteprima di una sequenza di
notifiche. Eviterei un sintetizzatore o un servizio di generazione audio nel
primo rilascio: l'obiettivo iniziale è creare e condividere pack dai propri suoni.
