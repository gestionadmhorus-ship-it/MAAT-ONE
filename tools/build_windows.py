"""Construye la aplicación autónoma y el ZIP instalable para Windows x64."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

def icon():
    target = ROOT / 'assets' / 'maat-one.ico'
    target.parent.mkdir(exist_ok=True)
    im = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((8,8,504,504),radius=105,fill='#10243B')
    d.rounded_rectangle((30,30,482,482),radius=87,outline='#1B4963',width=6)
    d.line([(104,345),(104,159),(256,297),(408,159),(408,345)],fill='#36DDD0',width=35,joint='curve')
    d.line([(184,382),(328,382)],fill='#FFFFFF',width=14)
    d.ellipse((365,65,423,123),fill='#F6BC54')
    im.save(target,sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    return target

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--iscc',required=True)
    args=parser.parse_args()
    version=json.loads((ROOT/'version.json').read_text())['version']
    image=icon()
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
        '--name','MAAT One','--icon',str(image),'--add-data',f'{image};assets',
        '--collect-all','python_calamine','--hidden-import','PIL._tkinter_finder',
        '--distpath',str(ROOT/'dist'), '--workpath',str(ROOT/'build'),
        '--specpath',str(ROOT/'build'),str(ROOT/'app.py')],cwd=ROOT,check=True)
    subprocess.run([args.iscc,f'/DAppVersion={version}',str(ROOT/'tools'/'installer.iss')],cwd=ROOT,check=True)
    output=ROOT/'release_dist'
    exe=output/f'MAAT-One-{version}-Setup.exe'
    archive=output/f'MAAT-One-{version}-Windows.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(exe,exe.name)
        z.writestr('LEEME.txt',f'MAAT One {version}\r\n\r\n1. Extraiga este ZIP.\r\n2. Abra {exe.name}.\r\n3. Complete el asistente.\r\n\r\nIncluye Python y dependencias. No necesita instalarlos aparte.\r\nCrea accesos directos en el escritorio y el menu Inicio.\r\nWindows 10/11 de 64 bits. Los proyectos y fotos no se modifican al instalar.\r\n')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n',encoding='ascii')
    print(archive)

if __name__=='__main__': main()
