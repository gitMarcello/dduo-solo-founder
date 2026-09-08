[EN · English](uninstall.md) · [**IT · Italiano**](uninstall.it.md)

# Disinstallazione

Da un checkout della stessa release dDuo o di una più recente:

```bash
node bin/install.mjs --uninstall --yes
```

L'installer esegue prima i backup dei progetti con destinazione configurata.
Un backup configurato fallito blocca la disinstallazione, salvo `--force`
esplicito. I progetti senza backup vengono indicati come non protetti; i loro
volumi sono comunque conservati.

Fermare un progetto conserva i dati. Rimuoverne i volumi Compose elimina
definitivamente i dati PostgreSQL e Qdrant: l'agente
deve indicare il progetto e ottenere conferma esplicita per questa eliminazione.

Un checkout remoto non possiede uno stack locale. Disinstallarne il plugin non
ferma o elimina la memoria VPS e non revoca implicitamente la credenziale del
dispositivo. Per interrompere l'accesso il gestore revoca membro o dispositivo
dalla scheda Team.

Se era installata la telemetria Claude, dDuo ripristina prima l'esatta status
line conservata, poi rimuove l'adapter. La disinstallazione elimina pacchetto
Agent Plugins e registrazioni Codex/Claude selezionate. Il client può rimuovere
anche `PLUGIN_DATA`, che non contiene memoria autorevole. PostgreSQL, Qdrant,
segreti privati, archivi backup e recovery key restano fuori dal pacchetto.

Eliminare `.dduo-solo-founder/project.toml` solo se il repository non deve più
puntare a quell'identità. Archivi backup e recovery key non vengono mai
eliminati dalla disinstallazione.
