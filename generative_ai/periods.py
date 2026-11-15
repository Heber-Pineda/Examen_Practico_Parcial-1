"""Períodos explícitos en español, independientes del proveedor generativo.

Una expresión ambigua se rechaza; nunca se completa usando fechas del modelo.
Los límites finales se representan como date_to EXCLUSIVA.
"""

from dataclasses import dataclass
from datetime import date, timedelta
import re
import unicodedata

MONTHS = {"enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
          "abril": 4, "abr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
          "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9, "setiembre": 9,
          "sept": 9, "sep": 9, "set": 9, "octubre": 10, "oct": 10, "noviembre": 11,
          "nov": 11, "diciembre": 12, "dic": 12}
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
YEAR = re.compile(r"(?<!\w)\d{4}(?!\w)")
COMPARISON = re.compile(r"\b(?:compar\w*|versus|vs|frente\s+a|respecto\s+a|contra|diferencia\w*|variacion\w*|cambi\w*)\b")
RANGE = re.compile(r"\b(?:a|al|hasta)\b|\bentre\b.*\by\b|\s[-–—]\s")
UNSUPPORTED = re.compile(r"\b(?:hoy|ayer|anteayer|actualidad|presente|adelante|bimestre\w*|trimestre\w*|semestre\w*|quincena\w*|ultimo\w*|proximo\w*|"
                         r"anterior\w*|posterior\w*|excepto|excluy\w*)\b|\ba\s+la\s+fecha\b|\b(?:este|esta|ese|esa|pasad[oa])\s+(?:mes|ano|semana)\b|\b(?:mes|ano|semana)\s+pasad[oa]\b")
HISTORY = re.compile(r"\b(?:todo\s+(?:el\s+)?(?:historico|historial)|(?:historico|historial)\s+completo)\b")


class PeriodError(ValueError):
    pass


@dataclass(frozen=True)
class PeriodWindow:
    start: date
    until: date
    label: str

    def as_dict(self):
        return {"date_from": self.start.isoformat(), "date_to": self.until.isoformat(), "label": self.label}


@dataclass(frozen=True)
class RequestedPeriod:
    mode: str
    windows: tuple[PeriodWindow, ...]


def text_normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _window(year, month=None, day=None):
    start = date(year, month or 1, day or 1)
    if day is not None:
        until = start + timedelta(days=1)
    elif month is not None:
        until = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    else:
        until = date(year + 1, 1, 1)
    return PeriodWindow(start, until, f"{start.isoformat()} a {(until - timedelta(days=1)).isoformat()}")


def parse_period(question):
    """Mes/año, año, fechas y dos extremos de rango o comparación; sin inferir años."""
    text = text_normalized(question)
    if UNSUPPORTED.search(text):
        raise PeriodError("Precisa fechas absolutas: mes y año, año completo o un rango con sus extremos.")
    windows, occupied, month_matches = [], [], []
    def free(start, end):
        return not any(start < old_end and end > old_start for old_start, old_end in occupied)
    def add(match, window):
        windows.append((match.start(), window))
        occupied.append(match.span())
    try:
        # Los días completos se reconocen antes que sus fragmentos mes/año.
        for pattern, order in ((r"(?<!\d)(\d{4})[/-](\d{2})[/-](\d{2})(?!\d)", "ymd"),
                               (r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)", "dmy")):
            for match in re.finditer(pattern, text):
                if free(*match.span()):
                    a, b, c = map(int, match.groups())
                    add(match, _window(a, b, c) if order == "ymd" else _window(c, b, a))
        for pattern, order in ((r"(?<![\w/-])(\d{4})[-/](\d{1,2})(?![\w/-])", "ym"),
                               (r"(?<![\w/-])(\d{1,2})/(\d{4})(?![\w/-])", "my")):
            for match in re.finditer(pattern, text):
                if free(*match.span()):
                    a, b = map(int, match.groups())
                    add(match, _window(a, b) if order == "ym" else _window(b, a))
        month_re = re.compile(r"(?<!\w)(?:(?P<day>\d{1,2})\s+(?:de\s+)?)?(?P<month>" + MONTH_PATTERN +
                              r")(?:\.)?(?:\s*(?:de(?:l)?\s+)?(?P<year>\d{4})(?!\d))?(?!\w)")
        for match in month_re.finditer(text):
            if free(*match.span()):
                month_matches.append(match)
                occupied.append(match.span())
        # Años que no forman parte de un mes/fecha ni de un ID explícito.
        loose_years = []
        for match in YEAR.finditer(text):
            if free(*match.span()) and not re.search(r"(?:cliente\w*|producto\w*|factura\w*|codigo\w*|sku|id|#)\s*[:#]?\s*$", text[:match.start()]):
                loose_years.append(match)
        explicit_years = {int(m["year"]) for m in month_matches if m["year"]}
        explicit_years.update(int(m.group()) for m in loose_years)
        for match in month_matches:
            year = int(match["year"]) if match["year"] else next(iter(explicit_years)) if len(explicit_years) == 1 else None
            if year is None:
                raise PeriodError("Indica el año de cada mes; no se deduce del dashboard ni del modelo.")
            windows.append((match.start(), _window(year, MONTHS[match["month"]], int(match["day"]) if match["day"] else None)))
        # Un año suelto puede calificar «en 2016, abril y mayo».
        if not month_matches:
            for match in loose_years:
                add(match, _window(int(match.group())))
        elif len(explicit_years) > 1 and loose_years:
            raise PeriodError("Especifica el año junto a cada mes o cada extremo del rango.")
        else:
            occupied.extend(match.span() for match in loose_years)
        unresolved = any(free(*m.span()) and not re.search(r"(?:cliente\w*|producto\w*|factura\w*|codigo\w*|sku|id|#)\s*[:#]?\s*$", text[:m.start()])
                         for m in re.finditer(r"(?<!\d)\d{4}(?!\d)", text))
        if unresolved:
            raise PeriodError("No se pudo interpretar todo el período. Escribe los años y fechas completos, separados de las palabras.")
        if HISTORY.search(text):
            if windows:
                raise PeriodError("Aclara si deseas todo el histórico o los períodos indicados.")
            return RequestedPeriod("history", ())
        if not windows:
            temporal = re.search(r"\b(?:mes|ano|fecha|periodo|desde|hasta|entre)\b|\d[/-]\d", text)
            coverage_question = re.search(r"\b(?:cobertura|disponibles?|cubr\w*|abarc\w*)\b", text)
            if temporal and not coverage_question:
                raise PeriodError("No se identifica un período seguro. Usa, por ejemplo, mayo de 2016 o del 2016-04-01 al 2016-05-31.")
            return None
        windows.sort(key=lambda item: item[0])
        # Evita interpretar «del 1 al 31 de mayo 2016» como solo el día 31.
        remaining = text
        for start, end in sorted(occupied, reverse=True):
            remaining = remaining[:start] + " " * (end - start) + remaining[end:]
        if re.search(r"\b(?:del?|desde|hasta|al|entre)\s*\d{1,2}(?!\d)", remaining):
            raise PeriodError("Escribe ambos extremos con fecha completa; por ejemplo, del 2016-05-01 al 2016-05-31.")
        if len(windows) == 1:
            if COMPARISON.search(text) or re.search(r"\b(?:desde|hasta|entre)\b", text):
                raise PeriodError("Indica ambos períodos o extremos completos para comparar o delimitar el rango.")
            return RequestedPeriod("single", (windows[0][1],))
        if len(windows) != 2:
            raise PeriodError("Esta consulta admite un período, un rango o la comparación de dos períodos; precisa la selección.")
        first, second = windows[0][1], windows[1][1]
        if COMPARISON.search(text):
            if first == second:
                raise PeriodError("La comparación debe identificar dos períodos distintos.")
            return RequestedPeriod("comparison", (first, second))
        if RANGE.search(remaining):
            if first.start > second.start or first.until > second.until:
                raise PeriodError("El rango está invertido; indica también el año al cruzar de diciembre a enero.")
            whole = PeriodWindow(first.start, second.until, f"{first.start} a {second.until - timedelta(days=1)}")
            return RequestedPeriod("range", (whole,))
        raise PeriodError("Aclara si quieres sumar un rango o comparar por separado los dos períodos.")
    except (OverflowError, ValueError) as exc:
        if isinstance(exc, PeriodError):
            raise
        raise PeriodError("El período contiene una fecha inválida. Precisa mes/año o fechas completas válidas.") from None


def months_in(window):
    current = window.start.replace(day=1)
    last = (window.until - timedelta(days=1)).replace(day=1)
    while current <= last:
        yield current.isoformat()[:7]
        if current == last:
            break
        current = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
