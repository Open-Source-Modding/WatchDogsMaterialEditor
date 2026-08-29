import re, zlib, pprint
import xml.etree.ElementTree as ET
from tkinter import *; from tkinter import filedialog

nonascii = bytearray(range(0x80, 0x100))

def byteswap(s):
    s = re.findall('..', s)
    return ''.join(list(reversed(s)))

def crc32b(s):
    h = format(zlib.crc32(bytes(s, 'utf-8')), 'x')
    return h

def readxml(f):
    tree = ET.parse(f)
    root = tree.getroot()
    for parameter in root.findall('parameter'):
        name = parameter.get('name')
        type = parameter.get('type')
        print(name,type)

def readmat(filename):
    with open(filename, mode='rb') as file: # b is important -> binary
        file.seek(0x48)
        name = file.read(0x17).decode('ascii', errors='ignore')
        return name


window = Tk()
#xmlfile = filedialog.askopenfilename(initialdir = ".", title = "Select file", filetypes = (("XML File", "*.xml"),))
binfile = filedialog.askopenfilename(initialdir = ".", title = "Select file", filetypes = (("Watch Dogs Material File", ".material.bin"),))
#readxml(rootw.filename)
print(readmat(binfile))
