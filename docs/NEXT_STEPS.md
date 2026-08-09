# Next Steps — da "architetturalmente completo" a "verificato in produzione"

Stato a oggi: tutto il codice in questo repo è stato scritto seguendo il
protocollo reale (`repos/AriaCast-Protocol-Spec`) e i moduli "core" (DB, DSP,
ducking, pub/sub) sono stati testati con smoke test reali (vedi PR #1). **Non
è stato testato**: contro Home Assistant Core vero, contro uno speaker
AriaCast vero, con una build Docker reale, o con una build Gradle reale
dell'app. Questo documento è l'elenco ordinato per chiudere quei gap.

## PR aperte

| PR | Repo | Stato |
|---|---|---|
| [Ecosystem-madness #1](https://github.com/AirPlr/AriaCast-Ecosystem-madness/pull/1) | `AriaCast-Ecosystem-madness` | pronta per review, non draft |
| [AriaCast-app #31](https://github.com/AriaCast/AriaCast-app/pull/31) | `AriaCast-app` | **draft** — vedi Priorità 4 |

---

## Priorità 1 — Verificare l'integrazione contro Home Assistant vero

Il rischio più alto: `custom_components/ariacast/` non è mai stato importato
da un vero processo `homeassistant`. Un bug come quello già trovato e
corretto (`hass.helpers.X.Y()` deprecato in `media_player.py`) potrebbe
essercene ancora.

```bash
# Ambiente di sviluppo HA consigliato (devcontainer ufficiale)
git clone https://github.com/home-assistant/core.git
cd core
python3 -m venv venv && source venv/bin/activate
pip install -e .

# Copia l'integrazione nel config di sviluppo
mkdir -p config/custom_components
cp -r /path/to/AriaCast-Ecosystem-madness/custom_components/ariacast config/custom_components/

# Avvia HA e guarda i log per errori di import/config_flow
hass -c config
```

Cosa controllare nei log all'avvio:
- l'integrazione si carica senza `ImportError`/`AttributeError`
- il config flow (`Impostazioni → Integrazioni → Aggiungi → AriaCast Direct`) si apre e completa
- `media_player.ariacast_*` non compare finché non c'è almeno uno speaker
  discovered (aspettato, dato che serve un vero speaker in rete — Priorità 2)

Punti di attenzione noti (mai verificati):
- `MediaType` importato da `homeassistant.components.media_player` in
  `media_player.py` — confermare il path esatto per la versione HA target
- `MediaPlayerEntityFeature` / `MediaPlayerState` — nomi enum possono
  cambiare tra versioni HA
- `ConfigFlow`/`OptionsFlow` — firma `async_get_options_flow` può differire

## Priorità 2 — Testare contro uno speaker AriaCast reale

Serve un'istanza di `Ariacast-server-python` o `AriaCast-Server-GO` in
esecuzione su una macchina in LAN (vedi `repos/Ariacast-server-python/README.md`
o `repos/AriaCast-Server-GO/README.md` per l'avvio).

```bash
cd repos/Ariacast-server-python
pip install -r requirements.txt
python3 main.py
```

Poi, isolatamente (senza HA), verifica che `AriaCastSpeakerClient`
(`custom_components/ariacast/core/protocol_client.py`) si connetta davvero:

```python
import asyncio
from custom_components.ariacast.core.protocol_client import discover_udp, AriaCastSpeakerClient

async def main():
    found = await discover_udp(timeout=2.0)
    print(found)  # deve trovare il server appena avviato
    if found:
        client = AriaCastSpeakerClient(found[0].ip, found[0].port)
        await client.start()
        await asyncio.sleep(3)
        print("connected:", client.connected)
        await client.play()
        await client.stop()

asyncio.run(main())
```

Se questo funziona, il resto (heartbeat, node_manager, DSP) ha già smoke
test propri e dovrebbe comportarsi come previsto — ma vale la pena rifarli
con un nodo reale che si disconnette (chiudi il server con Ctrl+C e verifica
che lo stato passi a `offline` entro ~6s, per `OFFLINE_GRACE_S`).

## Priorità 3 — Build reale dell'add-on Docker

```bash
cd AriaCast-Ecosystem-madness
bash scripts/build_addon.sh --build
docker run --rm -p 8099:8099 -e SUPERVISOR_TOKEN= ariacast_core:local
# apri http://localhost:8099 — deve servire la Web UI Canvas
```

Nota: `SUPERVISOR_TOKEN` non è disponibile fuori da un vero Supervisor HA,
quindi la sincronizzazione luci (`_call_light_service` in `app/main.py`)
resterà no-op in locale — è previsto, verrà loggato un debug e nient'altro.

Da lì, per il deploy reale come add-on:
1. installa Supervisor / HA OS (o usa un dev target Supervisor)
2. aggiungi questo repo come "add-on repository" locale
3. installa "AriaCast Core" dallo store add-on locale

## Priorità 4 — Completare l'app companion (sblocca la draft PR #31)

`HaEcosystemBridge.kt` esiste ma non è agganciato a nulla. Ordine di lavoro
consigliato dentro `AriaCast-app`:

1. **Verifica compilazione**: `./gradlew assembleDebug` — prima cosa da
   fare, a costo zero, prima di qualunque altro lavoro sull'app.
2. **Toggle "HA Mode" in Settings**: in `SettingsActivity.kt`, aggiungi uno
   switch che legge/scrive `HAModeManager.enabled` e un campo per
   `addonBaseUrl`.
3. **Selettore stanze da HA Areas**: dove oggi l'app mostra la lista
   server/stanze manuali, quando `HAModeManager.enabled == true` sostituisci
   la fonte dati con `HaEcosystemClient.fetchRooms()`.
4. **Room Canvas 2D mobile**: riusa la stessa logica di
   `addon/ariacast_core/app/www/index.html` (canvas 2D, drag speaker/listener,
   `PX_PER_METER`) ma in Compose/Canvas nativo Android, chiamando
   `HaEcosystemClient.updateSpeakerPosition` / `pushListenerPosition`.
5. Solo dopo 1–4: togliere lo stato **draft** dalla PR #31.

Le voci "Quick Light Overlay" e "Adaptive Glassmorphic Theme" dal prompt
originale non sono nemmeno abbozzate — sono lavoro nuovo da pianificare
separatamente, non dipendono dal bridge già scritto.

## Priorità 5 — Merge

Ordine consigliato: **Priorità 1 e 2 prima del merge di PR #1** (è la base
di tutto — se l'integrazione HA ha bug di import, meglio scoprirlo prima del
merge). PR #31 resta in draft finché non è completa la Priorità 4.

## Riferimento rapido: dove intervenire per ciascun sintomo

| Sintomo | File da guardare |
|---|---|
| L'integrazione non si carica in HA | `custom_components/ariacast/__init__.py`, `manifest.json` |
| Config flow non si apre / crasha | `custom_components/ariacast/config_flow.py` |
| Entità non compare per uno speaker noto | `custom_components/ariacast/core/node_manager.py` (`_discovery_loop`, `_adopt`) |
| Stato entità sbagliato (`unavailable` quando dovrebbe essere `off`) | `custom_components/ariacast/core/node_manager.py` (`_heartbeat_loop`, `OFFLINE_GRACE_S`) |
| Comandi play/pause non arrivano allo speaker | `custom_components/ariacast/core/protocol_client.py` (`_send_control`) |
| DSP non ricalcola quando un nodo cade | `custom_components/ariacast/coordinator.py` (`_on_transition`) |
| Web UI Canvas non si aggiorna live | `custom_components/ariacast/core/pubsub.py`, `addon/ariacast_core/app/www/index.html` (`connectWS`) |
| Notifiche TTS non abbassano la musica | `addon/ariacast_core/app/socket_server.py` (`_handle_notify`), `custom_components/ariacast/core/ducking.py` |
