#!/usr/bin/env python3
"""Actualiza data.json con las fichas de Teatre Faller publicadas por JCF en fallas.com.

Solo usa la biblioteca estándar. Uso:
  python scripts/actualizar.py            # añade obras nuevas a data.json
  python scripts/actualizar.py --verificar  # relee las obras ya guardadas y escribe verificacion.json
"""
import datetime as dt
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data.json"
LISTADO = "https://fallas.com/va/category/cultura-va/"
UA = "Mozilla/5.0 (compatible; TeatreFallerStats/1.0; +https://miguelmm13.github.io/teatre-faller/)"
MAX_PAGINAS = 4

MESES = {"gener": 1, "febrer": 2, "març": 3, "marc": 3, "abril": 4, "maig": 5, "juny": 6, "juliol": 7,
         "agost": 8, "setembre": 9, "octubre": 10, "novembre": 11, "desembre": 12,
         # por si la ficha viene en castellano
         "enero": 1, "febrero": 2, "marzo": 3, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
         "septiembre": 9, "noviembre": 11, "diciembre": 12}
TEMPORADA = (2026, 2027)  # sep-dic -> 2026, ene-ago -> 2027

# rutas de fallas.com/va/ que nunca son noticias
NO_NOTICIA = re.compile(r"^(category|author|tag|page|wp-|feed|presidencia|directiva|composicio|falleres-|festes-|fallas-municipals|"
                        r"indumentaristes|noticies-|reglament|regim-|historic|qui-som|perfil-|contractes|politica|avis-legal)")


def get(url, intentos=3):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ca,es"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            if i == intentos - 1:
                raise
            time.sleep(4 * (i + 1))


class Texto(HTMLParser):
    """Convierte HTML en líneas de texto, cortando en etiquetas de bloque."""
    BLOQUE = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol"}
    SALTAR = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes, self.saltando = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SALTAR:
            self.saltando += 1
        elif tag in self.BLOQUE:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SALTAR:
            self.saltando = max(0, self.saltando - 1)
        elif tag in self.BLOQUE:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self.saltando:
            self.partes.append(data)

    def lineas(self):
        txt = "".join(self.partes).replace("\xa0", " ")
        return [re.sub(r"[ \t]+", " ", l).strip() for l in txt.split("\n") if l.strip()]


def lineas_de(html_txt):
    p = Texto()
    p.feed(html_txt)
    return p.lineas()


def enlaces_listado(html_txt):
    """Slugs de entradas en el listado, en orden de aparición y sin repetir."""
    vistos, out = set(), []
    for m in re.finditer(r'href="https://fallas\.com/va/([^/"?#]+)/"', html_txt):
        slug = m.group(1)
        if NO_NOTICIA.match(slug) or slug in vistos:
            continue
        vistos.add(slug)
        out.append(slug)
    return out


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()).strip()


ETIQUETAS = {
    "comision": r"comissi[oó]|comisi[oó]n",
    "categoria": r"categor[ií]a",
    "data": r"data|fecha",
    "obra": r"obra(?: in[eè]dita)?",
    "autor": r"autor(?:a|ia|ía)?",
    "direccion": r"direcci[oó]|direcci[oó]n",
    "duracion": r"duraci[oó]n?",
    "asistentes": r"assistents|asistentes",
}


def campo(lineas, clave):
    pat = re.compile(r"^\s*(" + ETIQUETAS[clave] + r")\s*\**\s*:\s*\**\s*(.*)$", re.I)
    for i, l in enumerate(lineas):
        m = pat.match(l)
        if m:
            valor = m.group(2).strip()
            if not valor and i + 1 < len(lineas):  # etiqueta y valor en líneas distintas
                valor = lineas[i + 1].strip()
            return m.group(1), valor
    return None, None


def es_mayus(tok):
    letras = [c for c in tok if c.isalpha()]
    return bool(letras) and all(c.isupper() for c in letras)


def partir_linea_reparto(l):
    """'JOVE – ÀNGEL Pablo Sendra Vidal(*)' -> ('JOVE – ÀNGEL', 'Pablo Sendra Vidal', True)"""
    novell = "*" in l
    l = re.sub(r"\(\s*\*\s*\)|\*", "", l).strip()
    toks = l.split()
    i = 0
    while i < len(toks) and (es_mayus(toks[i]) or not any(c.isalpha() for c in toks[i])):
        i += 1
    personaje, actor = " ".join(toks[:i]).strip(" -–:"), " ".join(toks[i:]).strip()
    return personaje, actor, novell


def reparto(lineas):
    try:
        ini = next(i for i, l in enumerate(lineas) if re.match(r"^\**\s*REPART[A-ZÀ-Ü]*\s*:?\s*\**$", l, re.I))
    except StopIteration:
        return None
    out, pendiente = [], None
    for l in lineas[ini + 1:]:
        if (re.match(r"^\(\s*\*\s*\)", l) or re.match(r"^\**\s*(SINOPSI|SINOPSIS|DURACI)", l, re.I)
                or re.match(r"^\**\s*[A-Za-zÀ-ÿ·]+(?: [A-Za-zÀ-ÿ·]+)?\s*\**\s*:", l)):
            break
        p, a, n = partir_linea_reparto(l)
        if p and not a:  # personaje y actor en celdas/líneas separadas
            pendiente = p
            continue
        if pendiente and not p:
            p, pendiente = pendiente, None
        if a:
            out.append({"p": p.title() if p else "", "a": a, "n": n})
    return out


def fecha_funcion(txt):
    if not txt:
        return None, None
    m = re.search(r"(\d{1,2})\s*(?:de\s+|d\s+)?([a-z]+)", norm(re.sub(r"[’'`]", " ", txt)))
    hora = re.search(r"(\d{1,2})[:.h](\d{2})", txt)
    hh = f"{int(hora.group(1)):02d}:{hora.group(2)}" if hora else None
    if not m:
        return None, hh
    mes = MESES.get(m.group(2)) or MESES.get(m.group(2).rstrip("."))
    if not mes:
        return None, hh
    anyo = TEMPORADA[0] if mes >= 9 else TEMPORADA[1]
    return f"{anyo}-{mes:02d}-{int(m.group(1)):02d}", hh


def fecha_publicacion(html_txt, lineas):
    m = re.search(r'article:published_time"\s+content="(\d{4}-\d{2}-\d{2})', html_txt) or \
        re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', html_txt)
    if m:
        return m.group(1)
    for l in lineas:
        m = re.search(r"(\d{2})/(\d{2})/(20\d{2})", l)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def numero(txt, lo, hi):
    if not txt:
        return None
    m = re.search(r"\d[\d.]*", txt)
    if not m:
        return None
    n = int(m.group(0).replace(".", ""))
    return n if lo <= n <= hi else None


def titulo(obra):
    t = re.sub(r"^[‘'\"“«\s*]+|[’'\"”»\s*]+$", "", obra or "").strip()
    if t and t == t.upper():
        t = t[:1] + t[1:].lower()
    return t or None


def parsear(slug, html_txt):
    """Devuelve el dict de la obra, o None si la página no es una ficha de teatro."""
    L = lineas_de(html_txt)
    _, comision = campo(L, "comision")
    _, categoria = campo(L, "categoria")
    rep = reparto(L)
    if not comision or not categoria or rep is None:
        return None
    etq_obra, obra = campo(L, "obra")
    _, data_txt = campo(L, "data")
    fecha, hora = fecha_funcion(data_txt)
    _, autor = campo(L, "autor")
    _, direccion = campo(L, "direccion")
    _, dur = campo(L, "duracion")
    _, asis = campo(L, "asistentes")
    nombres = {norm(x["a"]) for x in rep if x["a"]}
    return {
        "id": slug,
        "comision": comision.strip(" *"),
        "categoria": categoria.strip(" *").capitalize(),
        "fecha": fecha,
        "hora": hora,
        "obra": titulo(obra),
        "inedita": bool(etq_obra and re.search(r"in[eè]dita", etq_obra, re.I)),
        "autor": (autor or "").strip(" *") or None,
        "direccion": (direccion or "").strip(" *") or None,
        "reparto": rep,
        "actores": len(nombres) or None,
        "novells": sum(1 for x in rep if x["n"]),
        "duracion": numero(dur, 10, 240),
        "asistentes": numero(asis, 1, 3000),
        "publicado": fecha_publicacion(html_txt, L),
        "url": f"https://fallas.com/va/{slug}/",
    }


def cargar():
    return json.loads(DATA.read_text("utf-8"))


def guardar(d):
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", "utf-8")
    json.loads(DATA.read_text("utf-8"))  # validación


def actualizar():
    d = cargar()
    meta = d.setdefault("meta", {})
    ignoradas = list(dict.fromkeys(meta.get("ignoradas", [])))
    conocidas = {o["id"] for o in d["obras"]} | set(ignoradas)
    nuevas, revisadas, errores = [], 0, []
    for pag in range(1, MAX_PAGINAS + 1):
        url = LISTADO if pag == 1 else f"{LISTADO}page/{pag}/"
        slugs = enlaces_listado(get(url))
        candidatas = [s for s in slugs if s not in conocidas]
        for slug in candidatas:
            try:
                obra = parsear(slug, get(f"https://fallas.com/va/{slug}/"))
            except Exception as e:  # noqa: BLE001
                errores.append(f"{slug}: {e}")
                continue
            revisadas += 1
            if obra:
                nuevas.append(obra)
            else:
                ignoradas.append(slug)
            conocidas.add(slug)
            time.sleep(1.5)
        # si en esta página ya había entradas conocidas, no hace falta seguir hacia atrás
        if len(candidatas) < len(slugs):
            break
    d["obras"].extend(nuevas)
    meta["ignoradas"] = ignoradas
    meta["ultimaRevision"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if errores:
        meta["errores"] = errores[-10:]
    else:
        meta.pop("errores", None)
    guardar(d)
    print(f"Revisadas {revisadas} entradas · {len(nuevas)} obras nuevas: " + ", ".join(o["comision"] for o in nuevas))
    for e in errores:
        print("ERROR", e)


def verificar():
    """Relee las obras guardadas y compara el resultado del lector con los datos verificados."""
    d = cargar()
    informe = []
    for o in d["obras"]:
        try:
            p = parsear(o["id"], get(o["url"]))
        except Exception as e:  # noqa: BLE001
            informe.append({"id": o["id"], "error": str(e)})
            continue
        if not p:
            informe.append({"id": o["id"], "error": "no reconocida como ficha"})
            continue
        dif = {}
        for k in ("comision", "categoria", "fecha", "hora", "obra", "inedita", "duracion", "asistentes", "actores", "novells", "publicado"):
            if norm(str(p.get(k))) != norm(str(o.get(k))):
                dif[k] = {"lector": p.get(k), "guardado": o.get(k)}
        if [norm(x["a"]) for x in p["reparto"]] != [norm(x["a"]) for x in o["reparto"]]:
            dif["reparto_actores"] = {"lector": [x["a"] for x in p["reparto"]], "guardado": [x["a"] for x in o["reparto"]]}
        informe.append({"id": o["id"], "ok": not dif, "diferencias": dif})
        time.sleep(1)
    (RAIZ / "verificacion.json").write_text(json.dumps(informe, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(sum(1 for x in informe if x.get("ok")), "de", len(informe), "obras coinciden")


if __name__ == "__main__":
    verificar() if "--verificar" in sys.argv else actualizar()
