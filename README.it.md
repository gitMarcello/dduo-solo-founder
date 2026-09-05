[EN · English](README.md) · [**IT · Italiano**](README.it.md)

# dDuo Solo Founder

**Dai a Codex e Claude Code una memoria di progetto ispirata alla memoria umana.**

dDuo ritrova le informazioni pertinenti attraverso gli **embedding** e le
consolida durante il **«sonno»**: rielabora le conversazioni per conservare
decisioni, lezioni e ricordi utili nelle chat successive.

In più, mette a disposizione una **dashboard del progetto**: mentre lavori in
chat, l'assistente può creare e aggiornare task, organizzare piani, epic e
sprint e tenere ordinati backlog e attività completate. Nella stessa dashboard
puoi consultare la memoria e capire quanto contesto e quali consumi aggiunge dDuo.

Il sistema gira **sul tuo computer o sul tuo server**, con una memoria separata
per ogni progetto, utilizzabile da solo o con il tuo team. La ricerca semantica
utilizza gli embedding delle API OpenAI.

## Inizia qui

### Installa dDuo per questo progetto

Apri **il progetto su cui vuoi lavorare** in Codex o Claude Code e incolla:

```text
Installa questo plugin nel progetto e guidami nella configurazione:
https://github.com/gitMarcello/dduo-solo-founder/tree/v0.2.0-beta.1
```

L'assistente segue le
[istruzioni d'installazione](docs/installation.it.md#instructions-for-the-installing-agent)
e la [guida di sicurezza](SECURITY.it.md) della release: non serve un prompt lungo.

<details>
<summary>Preferisci il terminale? Comando di installazione</summary>

Eseguilo dalla root del tuo progetto. Servono Node.js 18+ e Git:

```bash
dduo_install_dir="$(mktemp -d)"
git clone --depth 1 --branch v0.2.0-beta.1 https://github.com/gitMarcello/dduo-solo-founder.git "$dduo_install_dir"
"$dduo_install_dir/install.sh" --only codex --project-root "$PWD" --yes
# Usa invece --only claude per installare Claude Code.
```

Su Windows nativo, usa PowerShell:

```powershell
$dduoInstallDir = Join-Path ([IO.Path]::GetTempPath()) ("dduo-" + [guid]::NewGuid())
git clone --depth 1 --branch v0.2.0-beta.1 https://github.com/gitMarcello/dduo-solo-founder.git $dduoInstallDir
node (Join-Path $dduoInstallDir 'bin/install.mjs') --only codex --project-root (Get-Location).Path --yes
```

</details>

L'assistente esegue l'installazione e apre Setup. Completa le azioni mostrate,
poi segui le indicazioni per il riavvio. Vedi i [requisiti](#requisiti).

### Crea la prima memoria direttamente su una VPS

La prima memoria può nascere sulla VPS, senza Docker locale. L'assistente
segue la [procedura VPS senza browser](docs/platform-support.it.md).

```text
Crea la prima memoria dDuo di questo progetto direttamente su VPS e collega
questo computer da remoto. Preserva eventuali memorie esistenti, verifica i
requisiti del server e segui questa procedura. Chiedimi solo dati e decisioni mancanti:
https://github.com/gitMarcello/dduo-solo-founder/blob/v0.2.0-beta.1/docs/platform-support.it.md
```

### Sposta un progetto dDuo esistente su una VPS

Incollalo nella chat del progetto esistente. Gli altri progetti mantengono
la propria configurazione.

```text
Sposta su una VPS condivisa la memoria dDuo esistente di questo progetto.
Prima di trattare credenziali infrastrutturali, attiva e verifica la modalità off-record.
Verifica un backup di recupero, tieni private le credenziali e mantieni
una sola memoria scrivibile. Segui le procedure di trasferimento e recupero
della release; chiedimi solo dati mancanti e decisioni necessarie:
https://github.com/gitMarcello/dduo-solo-founder/blob/v0.2.0-beta.1/docs/remote-teams.it.md
https://github.com/gitMarcello/dduo-solo-founder/blob/v0.2.0-beta.1/docs/backup-and-recovery.it.md
```

### Entra in un progetto già condiviso

Nel checkout Git autorizzato usa l'invito al posto del prompt di installazione locale.
Il codice monouso del gestore ti collega alla memoria esistente senza Docker locale o
credenziali VPS. L'accesso alla memoria non concede accesso Git.

## Cosa ottieni

- **Continuità del progetto:** ritrovi decisioni, vincoli e lezioni pertinenti
  tra una chat e l'altra, con fonti e cronologia delle revisioni.
- **Lavoro organizzato:** discuti l'obiettivo; l'assistente può proporre un Plan,
  raggruppare attività in Epic, pianificare Sprint e tenere separati Backlog
  e archivio, con Task e link leggibili.
- **Procedure condivise:** raccogli le regole di test, rilascio e lavoro in un
  Manuale operativo versionato, utile sia da solo sia in team.
- **Una dashboard consultabile:** leggi i ricordi, segui il lavoro, controlli
  il contesto fornito e le misurazioni d'uso, e gestisci i backup cifrati di recupero.

Per esempio: «Riprendiamo da dove eravamo e proponimi il prossimo passo»,
«Trasforma questa idea in un piano» oppure «Cosa abbiamo già deciso sull'accesso
degli utenti?».

## Requisiti

| Scenario | Minimo |
| --- | --- |
| VPS condivisa | Linux con systemd, 1 vCPU, 1 GiB RAM, 2 GiB di swap persistente su disco, 5 GiB liberi nello storage Docker dopo lo swap |

Su macOS o Windows nativo servono Node.js 18+, Git e il client supportato;
la memoria locale usa anche Docker Desktop. Non imponiamo requisiti hardware locali.
L'installer prepara `uv`. Chi ospita la memoria fornisce una chiave API OpenAI
per gli embeddings (ricerca semantica) e un login Codex in abbonamento per
consolidare i ricordi, anche quando si lavora in chat con Claude. I collaboratori
usano il proprio accesso al client di chat e i servizi di memoria del gestore.

Il minimo VPS vale per un solo progetto leggero. Ogni progetto aggiuntivo
mantiene uno stack completo e richiede quindi RAM e spazio ulteriori.
`remote-host` verifica l'host reale prima di cambiare l'autorità del progetto.

## Documentazione

- [Installazione e onboarding](docs/installation.it.md)
- [Piattaforme e avvio diretto su VPS](docs/platform-support.it.md)
- [Progetti remoti e team](docs/remote-teams.it.md)
- [Lavoro: Sprint, Backlog e storico](docs/work.it.md)
- [Architettura](docs/architecture.it.md)
- [Motore della memoria](docs/memory-engine.it.md)
- [Backup e recupero](docs/backup-and-recovery.it.md)
- [Binding dei client e aggiornamenti](docs/client-binding-and-updates.it.md)
- [Risoluzione dei problemi](docs/troubleshooting.it.md)
- [Sicurezza](SECURITY.it.md) e [privacy](docs/privacy.it.md)
- [Guida completa in italiano](docs/guida-completa-dduo-solo-founder.md)
- [Contributi e gate di rilascio](CONTRIBUTING.it.md)

## Stato del progetto

[v0.2.0-beta.1](https://github.com/gitMarcello/dduo-solo-founder/releases/tag/v0.2.0-beta.1)
è una Beta per sviluppatori invitati. Usa il formato Agent Plugins 1.0 con
adapter nativi per Codex e Claude Code su macOS e Windows, o un host Linux per la
memoria. Codex IDE/VS Code usa lo stesso adapter e deve superare la verifica
degli hook nativi: il solo manifest standard non prova il lifecycle completo.
Gli altri client restano disabilitati. Vedi i [confini di verifica](docs/platform-support.it.md).

Distribuito con [licenza Apache 2.0](LICENSE).
