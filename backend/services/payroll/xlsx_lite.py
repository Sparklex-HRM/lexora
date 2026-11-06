"""Tiny dependency-free .xlsx reader/writer (stdlib zipfile + xml only).

The service images do not ship openpyxl, so templates and uploads are handled
with the raw Office-Open-XML format: a workbook is a zip of a few XML parts.
Writer: inline strings, bold header row, column widths, several sheets.
Reader: shared strings, inline strings, numbers, booleans, and dates (numeric
cells whose style uses a date number-format are returned as `datetime.date`).
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_EPOCH = date(1899, 12, 30)
_BUILTIN_DATE_FMTS = set(range(14, 23)) | set(range(45, 48))


# ───────────────────────────── writer ─────────────────────────────

def _col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _cell_xml(ref: str, v, header: bool) -> str:
    style = ' s="1"' if header else ""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"{style}><v>{1 if v else 0}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"{style}><v>{v}</v></c>'
    if isinstance(v, (date, datetime)):
        v = v.isoformat()[:10]
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'


def _sheet_xml(rows: list[list], widths: list[float] | None) -> str:
    out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<worksheet xmlns="{_NS}">']
    if widths:
        out.append("<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
            + "</cols>")
    out.append("<sheetData>")
    for r_i, row in enumerate(rows):
        cells = "".join(_cell_xml(f"{_col_letter(c_i)}{r_i + 1}", v, r_i == 0) for c_i, v in enumerate(row))
        out.append(f'<row r="{r_i + 1}">{cells}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_workbook(sheets: list[tuple[str, list[list], list[float] | None]]) -> bytes:
    """sheets = [(name, rows, column_widths|None), ...] → .xlsx bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                             f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(sheets)))
                   + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<Relationships xmlns="{_NS_PKG}">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<workbook xmlns="{_NS}" xmlns:r="{_NS_R}"><sheets>'
                   + "".join(f'<sheet name="{escape(n[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                             for i, (n, _, _) in enumerate(sheets))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<Relationships xmlns="{_NS_PKG}">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(sheets)))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<styleSheet xmlns="{_NS}">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
                   '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FF4F46E5"/></patternFill></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
                   "</styleSheet>")
        for i, (_, rows, widths) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(rows, widths))
    return buf.getvalue()


# ───────────────────────────── reader ─────────────────────────────

def _ref_col(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _serial_to_date(v: float):
    try:
        return _EPOCH + timedelta(days=int(v))
    except (OverflowError, ValueError):
        return None


def read_workbook(data: bytes, max_rows: int = 20000) -> dict[str, list[list]]:
    """→ {sheet_name: [[cell, ...], ...]} with python values (str/float/bool/date/None)."""
    out: dict[str, list[list]] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{{{_NS}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS}}}t")))
        date_styles: set[int] = set()
        if "xl/styles.xml" in names:
            sroot = ET.fromstring(z.read("xl/styles.xml"))
            custom_date = set()
            for nf in sroot.iter(f"{{{_NS}}}numFmt"):
                code = (nf.get("formatCode") or "").lower()
                code = re.sub(r"\[[^\]]*\]", "", code)
                if re.search(r"[dmy]", code) and not re.search(r"[#0]", code):
                    custom_date.add(int(nf.get("numFmtId")))
            xfs = sroot.find(f"{{{_NS}}}cellXfs")
            if xfs is not None:
                for i, xf in enumerate(xfs.findall(f"{{{_NS}}}xf")):
                    fid = int(xf.get("numFmtId") or 0)
                    if fid in _BUILTIN_DATE_FMTS or fid in custom_date:
                        date_styles.add(i)
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[rel.get("Id")] = rel.get("Target")
        for sh in wb.iter(f"{{{_NS}}}sheet"):
            rid = sh.get(f"{{{_NS_R}}}id")
            target = rels.get(rid, "")
            path = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
            if path not in names:
                continue
            rows: list[list] = []
            root = ET.fromstring(z.read(path))
            for row in root.iter(f"{{{_NS}}}row"):
                vals: list = []
                for c in row.findall(f"{{{_NS}}}c"):
                    col = _ref_col(c.get("r") or f"{_col_letter(len(vals))}1")
                    while len(vals) < col:
                        vals.append(None)
                    t = c.get("t")
                    v_el = c.find(f"{{{_NS}}}v")
                    val = None
                    if t == "s" and v_el is not None:
                        val = shared[int(v_el.text)] if v_el.text else ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{{{_NS}}}t"))
                    elif t == "b" and v_el is not None:
                        val = v_el.text == "1"
                    elif t in ("str", "e") and v_el is not None:
                        val = v_el.text
                    elif t == "d" and v_el is not None:
                        val = v_el.text
                    elif v_el is not None and v_el.text not in (None, ""):
                        try:
                            num = float(v_el.text)
                        except ValueError:
                            num = None
                        if num is not None and int(c.get("s") or 0) in date_styles:
                            val = _serial_to_date(num)
                        else:
                            val = num
                    vals.append(val)
                rows.append(vals)
                if len(rows) >= max_rows:
                    break
            out[sh.get("name")] = rows
    return out
