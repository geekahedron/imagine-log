# Imagine log

Local catalog for Grok Imagine stills and videos.

Double-click `start.bat`. It opens http://127.0.0.1:8765/ and the page reads `media/` itself.

## Layout

```
imagine-log/
  start.bat
  server.py
  index.html
  styles.ini
  imagine-log-grabber.user.js
  media/          not in git
  imports/        catalog-*.txt, not in git
  catalog.js      local data, not in git
```

Install the userscript in Tampermonkey. Downloads land in the browser download folder. Move pictures to `media/` and catalog text files to `imports/`.
