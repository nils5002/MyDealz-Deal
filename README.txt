
# MyDealz Kommentar-Bild-Monitor

Dieses kleine Tool überwacht einen bestimmten MyDealz-Deal (Thread) auf neue Kommentare
und schickt Bilder, die in neuen Kommentaren auftauchen, automatisch per Telegram.

## Schnellstart

1. Python 3.10+ installieren
2. In diesem Ordner:
   ```bash
   pip install -r requirements.txt
   ```
3. `.env.example` nach `.env` kopieren und Felder ausfüllen:
   - `DEAL_URL`: Link zum Deal-Thread
   - `TELEGRAM_BOT_TOKEN`: Bot-Token von @BotFather
   - `TELEGRAM_CHAT_ID`: Deine Chat-ID (z. B. via @userinfobot herausfinden oder indem du dem Bot schreibst und die `getUpdates`-API nutzt)
   - optional `POLL_SECONDS` (Standard: 60 Sekunden)
4. Starten:
   ```bash
   python mydealz_monitor.py
   ```

**Hinweis:** Beim ersten Start werden vorhandene Kommentare als "gesehen" markiert,
sodass du nur Benachrichtigungen für neue Kommentare bekommst. Wenn du auch bestehende
Bilder direkt erhalten willst, lösche die `state.json` vor dem Start.

## Was wird erkannt?
- Bilder, die als `<img>` im Kommentar eingebunden sind (auch `data-src`, `srcset`)
- Links, die direkt auf Bilddateien enden (.jpg/.jpeg/.png/.gif/.webp)

## Tipps
- Achte auf eine realistische Abruffrequenz (`POLL_SECONDS`), um MyDealz nicht zu
  überlasten. 30–120 Sekunden ist meist ausreichend.
- Manche Seiten laden Kommentare via JavaScript nach. Dieses Script parst den
  ausgelieferten HTML-Quelltext. Falls auf deinem Deal keine Kommentare erkannt
  werden, schick mir den Link – ich passe die Selektoren an.

## Alternative Benachrichtigungen
- Statt Telegram könntest du z. B. Discord Webhooks, Pushover oder E-Mail einbauen.
  Melde dich, wenn du das wünschst – ich gebe dir die Variante deiner Wahl.
