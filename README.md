# WatchDogsMaterialEditor

Tools for editing **Watch Dogs 1** (Disrupt engine) `.material.bin` files.

## material_xml.py — convert `.material.bin` <-> XML

A CLI that converts Disrupt materials to/from XML using the **same `<Root>`
schema as the official `ConvertMaterials.exe`**, so files are interoperable with
the existing community tooling and modded XMLs.

### Usage

```bash
# .material.bin -> .xml
python3 material_xml.py to-xml   input.material.bin [-o output.xml]

# .xml -> .material.bin
python3 material_xml.py from-xml input.xml          [-o output.material.bin]
```

If `-o` is omitted, the output path is derived from the input (`.material.bin`
<-> `.xml`).

### Features

- **Official schema**: `<Root>` with `<magic>/<version>/<unk2..7>`,
  `<name>/<shaderName>`, `<initSettings>`, `<commands><Elem type/name/Value>`,
  `<gradients>`. Enums (type 7) as `_<value>`, type 11 as `<Value1>/<Value2>`.
- **Readable parameter names**: resolved via `materialNames.txt` next to the
  script (same hash->name DB the official tool uses). Unknown params show as
  `?0xHASH`.
- **Lossless-but-clean floats**: values are written as the *shortest* decimal
  string that round-trips to the exact float32 bits. `0.4` stays `0.4`,
  `0.19` stays `0.19`, `0.0423114` keeps its real precision. No more
  `0.399999` artifacts, and no scientific notation (`8190` not `8.19e+03`).
- **Byte-identical round-trip**: an unmodified `.material.bin` converted to
  XML and back is byte-for-byte identical (preserves header, init settings,
  param padding, gradient, EOF, trailing — including v5 big-endian beta files).

### Supported versions

- v7 (WD1 retail) — little-endian
- v5 (2013 beta builds) — big-endian (auto-detected)
- v15 (WDL/leak) — little-endian

### Editing a material

1. `python3 material_xml.py to-xml material.material.bin -o material.xml`
2. Edit `material.xml` in any text editor — change `<Value>` elements
   (comma-separated floats for vec types, `true`/`false` for bools, texture
   paths for strings).
3. `python3 material_xml.py from-xml material.xml -o material.material.bin`
4. Drop the result into the game's `patch`/`installpackage` archive.

### Example

```xml
<Root>
    <magic>5062996</magic>
    <version>7</version>
    <name>NH_MetalGate_01</name>
    <shaderName>DriverGeneric</shaderName>
    <commands>
        <Elem>
            <type>8</type>
            <unk1>0</unk1>
            <name>DiffuseTexture1</name>
            <Value>graphics\_textures\icone\grey.xbt</Value>
        </Elem>
        <Elem>
            <type>1</type>
            <unk1>0</unk1>
            <name>WetDiffuseMultiplier</name>
            <Value>0.87</Value>
        </Elem>
    </commands>
    <gradients/>
</Root>
```

## material_bin.py — format engine

`material_bin.py` is the low-level reader/writer (`MaterialBin` class) that
`material_xml.py` builds on. It can also be used as a Python library:

```python
from material_bin import MaterialBin
mat = MaterialBin.from_file("x.material.bin")
print(mat.name, mat.shader, len(mat.params))
mat.set_param("Opacity", 0.5)
mat.write("out.material.bin")
```

`materialNames.txt` is the hash->name lookup table (from the ConvertMaterials
tool's `res/`) used to resolve readable parameter names.