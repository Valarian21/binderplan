"""Binderplan – einen Binder per Link teilen: Schalter, Pause, Ablauf, QR-Code.

Ein Abschnitt der main.py in eigener Datei (10.09.2026), ausgeführt nach `pdf`.

**Warum es das braucht.** „Binder-Link teilen" kopierte bisher `/b/<id>` — und dieser
Link funktionierte für Fremde nur, wenn der Binder entweder gar keinem Konto gehörte
oder in der Vitrine stand. Wer angemeldet war und einen privaten Binder teilte,
verschickte einen Link, der beim Empfänger auf „Dieser Binder gehört jemand anderem"
lief. Der Besitzer sah den Fehler nie, weil er selbst lesen darf.

Teilen und Vitrine sind ab jetzt zwei Dinge: `sichtbar` heißt „steht öffentlich in der
Vitrine und taucht in Listen auf", `geteilt` heißt „wer den Link hat, darf zusehen".
Das zweite ist die leisere und häufigere Absicht — der Tauschpartner am Tisch, nicht
das Publikum.

**Pausieren statt Löschen.** Der Schalter aus lässt den Link tot, behält aber die
Adresse. Wer sie in seine Discord-Bio gestellt hat, muss sie nach einer Pause nicht neu
verschicken. Ein Ablaufdatum ist die Selbstaufräum-Variante davon („nur für dieses
Wochenende"), und es ändert die Adresse ebenfalls nicht.
"""

import io as _io


def _teilen_migration():
    con = get_db()
    for alter in ("ALTER TABLE binders ADD COLUMN geteilt INTEGER DEFAULT 0",
                  "ALTER TABLE binders ADD COLUMN geteilt_bis TEXT"):
        try:
            con.execute(alter)
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


_teilen_migration()

# Auswahl im Dialog. `None` heißt „läuft nicht ab" und ist die Vorgabe.
TEILEN_FRISTEN = {"1h": 1 / 24, "1t": 1, "7t": 7, "30t": 30}


def _teilen_abgelaufen(bis) -> bool:
    """Ist eine Frist gesetzt und vorbei? Ohne Frist nie."""
    if not bis:
        return False
    try:
        return datetime.datetime.fromisoformat(str(bis)) < datetime.datetime.now(datetime.timezone.utc)
    except ValueError:
        return False


def teilen_offen(row) -> bool:
    """Darf ein Fremder mit dem Link zusehen? Die eine Stelle, die das entscheidet."""
    if not row:
        return False
    schluessel = row.keys() if hasattr(row, "keys") else row
    if "geteilt" not in schluessel:
        return False
    return bool(row["geteilt"]) and not _teilen_abgelaufen(
        row["geteilt_bis"] if "geteilt_bis" in schluessel else None)


def _teilen_status(binder_id: str) -> dict:
    con = get_db()
    row = con.execute("SELECT COALESCE(geteilt,0) geteilt, geteilt_bis,"
                      " COALESCE(sichtbar,0) sichtbar FROM binders WHERE id = ?",
                      (binder_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "Binder nicht gefunden")
    return {
        "geteilt": bool(row["geteilt"]),
        "bis": row["geteilt_bis"],
        "abgelaufen": _teilen_abgelaufen(row["geteilt_bis"]),
        "in_vitrine": bool(row["sichtbar"]),
        "url": f"{(_env().get('APP_URL') or 'https://binderplan.app').rstrip('/')}/b/{binder_id}",
    }


@app.get("/api/binders/{binder_id}/teilen")
def binder_teilen_status(binder_id: str, request: Request):
    _binder_schreibrecht(binder_id, request)
    return _teilen_status(binder_id)


@app.post("/api/binders/{binder_id}/teilen")
async def binder_teilen_setzen(binder_id: str, request: Request):
    """Link an- oder ausschalten, mit optionaler Frist.

    Die Adresse bleibt in jedem Fall dieselbe — sie ist die Binder-ID, und die ändert
    sich nie. Wer den Link wirklich unbrauchbar machen will, löscht den Binder oder
    kopiert ihn (neue ID)."""
    _binder_schreibrecht(binder_id, request)
    data = await request.json()
    an = bool(data.get("an"))
    frist = str(data.get("frist") or "").strip()
    bis = None
    if an and frist in TEILEN_FRISTEN:
        bis = (datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(days=TEILEN_FRISTEN[frist])).isoformat()
    con = get_db()
    con.execute("UPDATE binders SET geteilt = ?, geteilt_bis = ? WHERE id = ?",
                (1 if an else 0, bis, binder_id))
    con.commit()
    con.close()
    return _teilen_status(binder_id)


@app.get("/api/binders/{binder_id}/qr.png")
def binder_qr(binder_id: str, request: Request, groesse: int = 8):
    """Der Link als QR-Code.

    Gedacht für den Tauschabend: das Handy des Gegenübers scannt den Code und blättert
    im Binder, statt dass jemand eine Adresse abtippt. Deshalb serverseitig gerendert —
    ein Bild, das man auch ausdrucken und an die Vitrine kleben kann.

    Absichtlich ohne Rechteprüfung auf *Lesen*: der QR-Code enthält nur die Adresse, die
    der Besitzer ohnehin verschickt. Geprüft wird beim Öffnen des Binders."""
    import segno
    _load_binder(binder_id)                      # 404, wenn es ihn nicht gibt
    basis = (_env().get("APP_URL") or "https://binderplan.app").rstrip("/")
    puffer = _io.BytesIO()
    segno.make(f"{basis}/b/{binder_id}", error="m").save(
        puffer, kind="png", scale=max(2, min(20, groesse)), border=2,
        dark="#14161c", light="#ffffff")
    return Response(puffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
