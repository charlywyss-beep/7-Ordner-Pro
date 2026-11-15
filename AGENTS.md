# 7-Ordner-System Organizer Pro – Projektregeln

1. **Versionierung:** Bei jeder Änderung an `app.pyw` immer `APP_VERSION` hochzählen (z. B. `v1.0.1` → `v1.0.2`).
2. **EXE-Deployment (vollautomatisch):** Nach Änderungen an `app.pyw` immer `pyinstaller --noconfirm 7-Ordner-Pro.spec` ausführen, eine noch laufende `7-Ordner-Pro.exe` zuerst sauber schließen (`taskkill /IM 7-Ordner-Pro.exe`, nur bei Bedarf `/F`), die neue `dist\7-Ordner-Pro.exe` sowohl nach `7-Ordner-Pro.exe` im Projektordner als auch nach `C:\Users\01\Desktop\7-Ordner-Pro.exe` kopieren und die Desktop-EXE anschließend direkt wieder starten.
3. **Design:** Kompakte Labels/Badges ohne runde Klammern schreiben; nebeneinanderliegende Buttons in derselben Leiste gleich hoch halten.
