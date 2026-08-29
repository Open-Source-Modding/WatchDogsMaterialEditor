#!/usr/bin/env python3
"""material_xml.py — Convert Disrupt engine .material.bin <-> editable XML.

A CLI on top of material_bin.py that emits/consumes the SAME <Root> XML schema
as the official ConvertMaterials.exe tool, so the two are interoperable.

    python3 material_xml.py to-xml   input.material.bin [-o output.xml]
    python3 material_xml.py from-xml input.xml          [-o output.material.bin]

Features:
  * Official <Root> schema: <magic>/<version>/<unk2..7>, <name>/<shaderName>,
    <initSettings>, <commands><Elem type/name/Value>, <gradients>.
  * Readable parameter names (resolved via materialNames.txt next to the
    script, same DB the official tool uses). Unknown params show as ?0xHASH.
  * Comma-separated float values (0.4,0.4) matching the official tool.
  * Enum (type 7) shown as _<value>, type 11 as <Value1>/<Value2>.
  * Lossless-but-clean floats: values are written as the SHORTEST decimal
    string that round-trips to the exact float32 bits (0.4 stays "0.4",
    0.19 stays "0.19", 0.0423114 keeps its real precision). No more
    "0.399999" artifacts.
  * Byte-identical round-trip of unmodified files (via optional pad_hex /
    header_hex attributes — harmless extra attributes the official tool
    ignores).

Numbers are serialized so that float32 -> text -> float32 is bit-exact.
"""

from __future__ import annotations
import argparse
import struct
import sys
import xml.etree.ElementTree as ET

from material_bin import (
    MaterialBin, Param,
    TYPE_U32, TYPE_VEC2, TYPE_VEC3, TYPE_VEC4, TYPE_I32, TYPE_BOOL,
    TYPE_ENUM, TYPE_STR8, TYPE_STR9, TYPE_STR10, TYPE_U32_2,
    TYPE_NAMES, crc32_name,
)


# ── Lossless shortest-float32 formatting ───────────────────────────────────
# The engine stores floats as raw float32 bits (u32). repr() of that value as a
# Python double yields the ugly full expansion (0.4000000059604645). Instead we
# find the FEWEST decimal digits (1..9) that still round-trip to the SAME
# float32 bits, giving clean, exact output (0.4, 0.19, ...).

def _f32_bits(x: float) -> int:
    return struct.unpack('<I', struct.pack('<f', x))[0]


def _fmt_f32(x: float) -> str:
    """Shortest decimal string (no scientific notation) that round-trips to
    the exact float32 bits of x. Tries increasing fixed-point precision."""
    bits = _f32_bits(x)
    for p in range(0, 13):
        s = format(x, '.%df' % p)
        if '.' in s:
            s = s.rstrip('0').rstrip('.')
        if _f32_bits(float(s)) == bits:
            return s
    return repr(x)


# ── Value <-> XML helpers ─────────────────────────────────────────────────

def _bytes_to_hex(b: bytes) -> str:
    return b.hex()


def _hex_to_bytes(s: str | None) -> bytes:
    if not s:
        return b""
    return bytes.fromhex(s)


def _enum_str(v: int) -> str:
    """Official tool renders type-7/type-11 raw u32 as '_' + value."""
    return "_" + str(v)


def _parse_enum(text: str) -> int:
    """Parse '_<value>' (official) — value may be decimal or hex."""
    s = text.strip()
    if s.startswith("_"):
        s = s[1:]
    if not s:
        return 0
    try:
        return int(s, 10)
    except ValueError:
        return int(s, 16)


def _fmt_val(p: Param) -> tuple:
    """Return (value_string, is_two_field) for a param.

    is_two_field True means type 11 (official schema emits Value1+Value2).
    """
    v = p.value[0] if isinstance(p.value, list) else p.value
    if p.type in (TYPE_U32, TYPE_VEC2, TYPE_VEC3, TYPE_VEC4):
        fs = p.as_floats()
        return ", ".join(_fmt_f32(f) for f in fs), False
    if p.type == TYPE_I32:
        return repr(int(v)), False
    if p.type == TYPE_BOOL:
        return "true" if v else "false", False
    if p.type in (TYPE_ENUM, TYPE_U32_2):
        return _enum_str(int(v)), False
    # strings
    return str(v) if v is not None else "", False


def _parse_val(p: Param, text: str):
    text = text.strip()
    if p.type in (TYPE_U32, TYPE_VEC2, TYPE_VEC3, TYPE_VEC4):
        parts = [float(x) for x in text.split(",")] if text else []
        p.value = parts
    elif p.type == TYPE_I32:
        p.value = [int(text)]
    elif p.type == TYPE_BOOL:
        p.value = [text.strip().lower() in ("1", "true", "yes", "on")]
    elif p.type in (TYPE_ENUM, TYPE_U32_2):
        p.value = [_parse_enum(text)]
    elif p.type in (TYPE_STR8, TYPE_STR9, TYPE_STR10):
        p.value = [text]
    else:
        p.value = [text]


# ── bin -> xml ────────────────────────────────────────────────────────────

def _header_words(mat: MaterialBin) -> list[int]:
    e = mat.endian
    if len(mat.header) >= 32:
        return list(struct.unpack(e + '8I', mat.header[:32]))
    return [0] * 8


def _shader_init_extra(shader: str) -> int:
    """Extra init_settings bytes (0 or 2) dictated by shader-name padding."""
    mod = (4 - len(shader) % 4) % 4
    return 2 if mod >= 2 else 0


def _decode_init_settings(raw: bytes, endian: str, extra: int = 0) -> list[int]:
    """Decode the init_settings block into 8 unk fields (official schema).

    Layout (from material.bin.bt): u16 unk0, [2 extra bytes when extra=2],
    then u8 unk2, u8 unk3, u32 unk4, i32 unk5, i32 unk6, i32 unk7. unk8 spare.
    """
    e = '<' if endian == '<' else '>'
    out = [0] * 8
    need = 20 + extra
    if len(raw) < need:
        return out
    o = 0
    out[0] = struct.unpack_from(e + 'H', raw, o)[0]; o += 2
    o += extra  # skip the mod-pair bytes
    out[1] = raw[o]; o += 1      # unk2
    out[2] = raw[o]; o += 1      # unk3
    out[3] = struct.unpack_from(e + 'I', raw, o)[0]; o += 4
    out[4] = struct.unpack_from(e + 'i', raw, o)[0]; o += 4
    out[5] = struct.unpack_from(e + 'i', raw, o)[0]; o += 4
    out[6] = struct.unpack_from(e + 'i', raw, o)[0]
    return out


def _encode_init_settings(unks: list[int], endian: str, extra: int = 0) -> bytes:
    e = '<' if endian == '<' else '>'
    u = (unks + [0] * 8)[:8]
    raw = bytearray(20 + extra)
    o = 0
    struct.pack_into(e + 'H', raw, o, u[0] & 0xFFFF); o += 2
    o += extra
    raw[o] = u[1] & 0xFF; o += 1
    raw[o] = u[2] & 0xFF; o += 1
    struct.pack_into(e + 'I', raw, o, u[3] & 0xFFFFFFFF); o += 4
    struct.pack_into(e + 'i', raw, o, u[4]); o += 4
    struct.pack_into(e + 'i', raw, o, u[5]); o += 4
    struct.pack_into(e + 'i', raw, o, u[6])
    return bytes(raw)


def material_to_xml(mat: MaterialBin) -> ET.Element:
    root = ET.Element("Root")
    # Preserve endianness (v5 beta = big-endian) + full 68-byte header verbatim
    # (byte-identity); official tool ignores these attributes. The <unk2..7>
    # elements remain human-readable for the official schema.
    root.set("endian", "big" if mat.endian == ">" else "little")
    root.set("header_hex", _bytes_to_hex(mat.header))
    w = _header_words(mat)
    ET.SubElement(root, "magic").text = str(w[0] if w[0] else 5062996)
    ET.SubElement(root, "version").text = str(mat.version)
    ET.SubElement(root, "unk2").text = str(w[2])
    ET.SubElement(root, "unk3").text = str(w[3])
    ET.SubElement(root, "unk4").text = str(w[4])
    ET.SubElement(root, "unk5").text = str(w[5])
    ET.SubElement(root, "unk6").text = str(w[6])
    ET.SubElement(root, "unk7").text = str(w[7])
    ET.SubElement(root, "name").text = mat.name
    ET.SubElement(root, "shaderName").text = mat.shader

    init = ET.SubElement(root, "initSettings")
    # Preserve the raw init_settings block for byte-identical round-trip
    # (as an attribute the official tool ignores), plus unk1..8 decoded to
    # match the official schema.
    if mat.init_settings:
        init.set("hex", _bytes_to_hex(mat.init_settings))
        unks = _decode_init_settings(mat.init_settings, mat.endian,
                                     _shader_init_extra(mat.shader))
    else:
        unks = [0] * 8
    for i in range(1, 9):
        ET.SubElement(init, "unk%d" % i).text = str(unks[i - 1])

    commands = ET.SubElement(root, "commands")
    for p in mat.params:
        e = ET.SubElement(commands, "Elem")
        ET.SubElement(e, "type").text = str(p.type)
        ET.SubElement(e, "unk1").text = "0"
        ET.SubElement(e, "name").text = p.name
        val, two = _fmt_val(p)
        if p.type == TYPE_U32_2:
            # official schema: Value1 (texture/state hash) + Value2
            ET.SubElement(e, "Value1").text = val
            ET.SubElement(e, "Value2").text = "0"
        else:
            ET.SubElement(e, "Value").text = val
        # optional pad_hex preserves byte-identical round-trip (official tool ignores it)
        if p.hdr_raw:
            e.set("pad_hex", _bytes_to_hex(p.hdr_raw))

    gradients = ET.SubElement(root, "gradients")
    if mat.gradient == 1 and mat.gradient_data:
        grad_vec, vecs, grad_id, unk1, unk2 = mat.gradient_data
        ge = ET.SubElement(gradients, "Elem")
        vecs_el = ET.SubElement(ge, "vecs")
        for v in vecs:
            ET.SubElement(vecs_el, "Elem").text = ", ".join(_fmt_f32(struct.unpack('<f', struct.pack('<I', c))[0]) for c in v)
        ET.SubElement(ge, "id").text = str(grad_id)
        ET.SubElement(ge, "unk1").text = str(unk1)
        ET.SubElement(ge, "unk2").text = "true" if unk2 else "false"

    # EOF + trailing preserved for byte-identity (ignored by official tool).
    if mat.has_eof:
        ET.SubElement(root, "eof").text = str(mat.eof)
    if mat.trailing:
        ET.SubElement(root, "trailing").set("hex", _bytes_to_hex(mat.trailing))

    return root


# ── xml -> bin ────────────────────────────────────────────────────────────

def xml_to_material(root: ET.Element) -> MaterialBin:
    mat = MaterialBin()
    mat.version = int(root.findtext("version", "15"))
    mat.name = root.findtext("name", "")
    mat.shader = root.findtext("shaderName", "")
    mat.endian = ">" if root.get("endian") == "big" else "<"

    # Header words: prefer preserved header_hex (byte-identity), else build
    # from magic/version/unk2..7 (official-tool-compatible). The engine
    # recomputes size words (8,9,12,14) in to_bytes().
    hh = root.get("header_hex")
    if hh:
        mat.header = _hex_to_bytes(hh)
    else:
        hdr = bytearray(68)
        magic = int(root.findtext("magic", "5062996"))
        w = [magic, mat.version,
             int(root.findtext("unk2", "0") or "0"),
             int(root.findtext("unk3", "0") or "0"),
             int(root.findtext("unk4", "0") or "0"),
             int(root.findtext("unk5", "0") or "0"),
             int(root.findtext("unk6", "0") or "0"),
             int(root.findtext("unk7", "0") or "0")]
        struct.pack_into('<8I', hdr, 0, *w)
        mat.header = bytes(hdr)

    # initSettings: restore raw block for byte-identity; else decode from unk1..8.
    init = root.find("initSettings")
    hx = init.get("hex") if init is not None else None
    if hx:
        mat.init_settings = _hex_to_bytes(hx)
    elif init is not None:
        unks = [int((init.findtext("unk%d" % i) or "0"), 0) for i in range(1, 9)]
        mat.init_settings = _encode_init_settings(unks, mat.endian,
                                                  _shader_init_extra(mat.shader))
    else:
        mat.init_settings = b""

    commands = root.find("commands")
    if commands is not None:
        for e in commands.findall("Elem"):
            typ = int(e.findtext("type", "1"))
            name = e.findtext("name", "")
            hdr_raw = _hex_to_bytes(e.get("pad_hex"))
            if name.startswith("?0x"):
                name_hash = int(name[3:], 16)
            else:
                name_hash = crc32_name(name) if name else 0
            p = Param(type=typ, name=name, name_hash=name_hash, hdr_raw=hdr_raw)
            if typ == TYPE_U32_2:
                val1 = e.findtext("Value1", "")
                _parse_val(p, val1)
            else:
                _parse_val(p, e.findtext("Value", ""))
            mat.params.append(p)

    gradients = root.find("gradients")
    if gradients is not None:
        ge = gradients.find("Elem")
        if ge is not None:
            mat.gradient = 1
            vecs_el = ge.find("vecs")
            vecs = []
            if vecs_el is not None:
                for ve in vecs_el.findall("Elem"):
                    floats = [float(x) for x in (ve.text or "").split(",") if x.strip()]
                    vecs.append([struct.unpack('<I', struct.pack('<f', f))[0] for f in floats])
            mat.gradient_data = (
                len(vecs), vecs,
                int(ge.findtext("id", "0") or "0"),
                int(ge.findtext("unk1", "0") or "0"),
                1 if ge.findtext("unk2", "").strip().lower() in ("true", "1") else 0,
            )

    eof = root.find("eof")
    if eof is not None:
        mat.has_eof = True
        mat.eof = int(eof.text or "0")
    tr = root.find("trailing")
    if tr is not None:
        mat.trailing = _hex_to_bytes(tr.get("hex"))
    return mat


# ── CLI ───────────────────────────────────────────────────────────────────

def _indent(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def _do_to_xml(args):
    mat = MaterialBin.from_file(args.input)
    root = material_to_xml(mat)
    _indent(root)
    out = args.output or (args.input.rsplit(".", 1)[0] + ".xml")
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
    print(f"to-xml: {args.input} -> {out} ({len(mat.params)} params)")


def _do_from_xml(args):
    root = ET.parse(args.input).getroot()
    mat = xml_to_material(root)
    out = args.output or (args.input.rsplit(".", 1)[0] + ".material.bin")
    mat.write(out)
    print(f"from-xml: {args.input} -> {out} ({len(mat.params)} params)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="material_xml.py",
                                 description="Convert Disrupt .material.bin <-> XML (official ConvertMaterials.exe schema)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("to-xml", help=".material.bin -> .xml")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.set_defaults(func=_do_to_xml)

    p = sub.add_parser("from-xml", help=".xml -> .material.bin")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.set_defaults(func=_do_from_xml)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()