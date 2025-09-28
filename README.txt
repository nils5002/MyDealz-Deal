# MyDealz Kommentar-Bild-Monitor

Dieses Tool ueberwacht einen MyDealz-Deal auf neue Kommentare und schickt dir jeden neuen Beitrag (Text und Bilder) per Telegram.

## Voraussetzungen
- Python 3.10+
- oder Docker/Portainer
- Telegram-Bot und Chat-ID

## Einrichtung ohne Docker
1. Abhaengigkeiten installieren:
   ```bash
   pip install -r requirements.txt
   ```
2. `.env.example` nach `.env` kopieren und Werte setzen:
   - `DEAL_URL`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - optional `POLL_SECONDS`, `STARTUP_MESSAGE`, `STARTUP_IMAGE_URL`, `SEEN_LIMIT`
3. Skript starten:
   ```bash
   python mydealz_monitor.py
   ```

Die Datei `state.json` speichert gesehene Kommentare. Beim Start schickt der Bot dir vorsorglich den zuletzt gefundenen Kommentar als Funktionstest. Loeschen, falls du alles neu einsammeln willst.

## Betrieb mit Docker oder Portainer
1. `.env.example` nach `.env` kopieren und anpassen (nie ins Repo pushen!).
2. Optional `data/` Ordner anlegen, um den State vorab zu setzen.
3. Lokal testen:
   ```bash
   docker compose up -d
   ```
   Der Container baut das Image mit dem beiliegenden `Dockerfile`, liest Variablen aus `.env` und legt den State unter `./data/state.json` ab.
4. Portainer-Stack:
   - In Portainer auf *Stacks* -> *Add stack*.
   - Git-Repository `https://github.com/nils5002/MyDealz-Deal.git` eintragen.
   - Unter *Env vars* oder per Portainer-Konfiguration die `.env` Werte setzen.
   - Deploy starten; der Stack nutzt `docker-compose.yml` und baut das Image auf deinem Server.

### Wichtige Variablen
- `DEAL_URL`: Voller Link zum Deal inkl. `#comments`.
- `TELEGRAM_BOT_TOKEN`: Token vom BotFather.
- `TELEGRAM_CHAT_ID`: Empfaenger-Chat oder Kanal.
- `STATE_PATH`: Wird im Compose-File auf `/data/state.json` gesetzt, damit der State in `./data` persistent bleibt.

## Tipps
- Waehl eine realistische Abruffrequenz (`POLL_SECONDS`), um MyDealz nicht zu stark zu belasten.
- Logging siehst du mit `docker compose logs -f` oder ueber Portainer.
- Fuer mehrere Deals kannst du mit eigenen `.env` Dateien und separaten Services arbeiten.
