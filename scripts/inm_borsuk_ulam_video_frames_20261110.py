import os,json,hashlib,cv2
import numpy as np
from PIL import Image,ImageOps
from pathlib import Path
import sys
v=sys.argv[1] if len(sys.argv)>1 else '/mnt/data/Borsuk-Ulam定理内测V0.5772版.mp4'
out=Path(__file__).resolve().parents[1]/'figures'/'N-20261110';out.mkdir(parents=True,exist_ok=True)
frames=[
('01-antipodal',130,'antipodal points and a single odd scalar'),
('02-paired-functions',203,'two scalar odd functions as planar trace'),
('03-rotating-meridian',272,'antipodal meridians and centrally symmetric planar tracks'),
('04-self-intersections',361,'general self-intersecting curves'),
('05-total-angle',466,'endpoint opposite and winding lift'),
('06-deformation',531,'deformation of path avoiding origin'),
('07-reversed-path',639,'orientation reversal of antipodal curves'),
('08-forced-zero',666,'final trace through origin'),
]
bg=np.array([22,31,42],dtype=np.float32)
cap=cv2.VideoCapture(v)
receipts=[]
for name,t,subject in frames:
 cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,raw=cap.read()
 assert ok,(t,cap.get(cv2.CAP_PROP_POS_MSEC))
 arr=cv2.cvtColor(raw,cv2.COLOR_BGR2RGB)
 # Clip the video title (top) and hard-coded Chinese subtitles (bottom); preserve graph + in-figure math labels.
 x0,y0,x1,y1=160,100,1780,755
 arr=arr[y0:y1,x0:x1]
 arr=cv2.resize(arr,(1260,510),interpolation=cv2.INTER_AREA)
 diff=arr.astype(np.float32)-bg
 d=np.max(diff,axis=2)
 # White-background remapping with source-color distinctions retained.
 # Each class is derived solely from actual pixel RGB values, without image synthesis.
 lum=arr.mean(2)
 gold=((arr[:,:,0].astype(float)-arr[:,:,2])>20) & ((arr[:,:,0].astype(float)-arr[:,:,1])>5)
 teal=((arr[:,:,2].astype(float)-arr[:,:,0])>23)&((arr[:,:,1].astype(float)-arr[:,:,0])>14)
 # 'Ink' = nearly white labels, 'sphere' = low contrast blue translucent shading.
 alpha=np.clip((d-8)/176,0,1)**0.75
 neutral=np.array([25,38,52.],dtype=np.float32)
 cyan=np.array([26,104,130.],dtype=np.float32)
 ochre=np.array([148,99,30.],dtype=np.float32)
 sph=np.array([80,130,144.],dtype=np.float32)
 target=np.zeros((*d.shape,3),np.float32)+neutral
 target[teal]=cyan;target[gold]=ochre
 # sphere region keeps faint gradient; avoid recoloring bright original curve strokes.
 yy,xx=np.mgrid[0:d.shape[0],0:d.shape[1]]
 # Map coordinates of left sphere circle x~480,y~395 to crop/resized coords
 cx=(480-x0)*1260/(x1-x0);cy=(395-y0)*510/(y1-y0);r=230*1260/(x1-x0)
 sphere=((xx-cx)**2+(yy-cy)**2<r*r)&(d<86)&(d>11)
 target[sphere]=np.array([145.,190.,202.],dtype=np.float32)
 alpha[sphere]=np.maximum(alpha[sphere],np.minimum(.49,(d[sphere]-8)/130))
 white=np.full_like(target,255.)
 rgb=np.uint8(np.clip(white-(255-target)*alpha[:,:,None],0,255))
 # Compound only a tiny bit of denoising in background; preserve equations & line ends.
 rgb[d<12]=255
 # Erase the remaining Chinese heading above the winding-angle scale only; leave mathematical tick labels untouched.
 if t>=500:
  # A partly cropped angle-scale panel remains at the far right for these frames.
  # Remove only that cut-off strip, preserving the full source sphere and planar trace.
  rgb[:,1175:]=255
 # JPEG quality for readable plots in print, PNG copy is exact post-conversion raster.
 pic=Image.fromarray(rgb)
 pic.save(out/f'{name}.png',optimize=True)
 pic.save(out/f'{name}.jpg',quality=84,optimize=True,subsampling=0)
 # Compact swatch image for vectorization: 4 categories + subtle sphere shading
 # Classification based on white remapping, not manual recreation.
 pal=np.zeros(d.shape,np.uint8)
 pal[(d>17)&(~gold)&(~teal)]=1
 pal[(d>18)&teal]=2
 pal[(d>18)&gold]=3
 pal[sphere]=4
 # Text over sphere: high original brightness is text or marked meridian, not background fill
 pal[sphere & (d>92) & teal]=2
 pal[sphere & (d>100) & ~teal & ~gold]=1
 # Remove small blocky background noise (but not curves)
 # Vectorize at a readable resolution via contours of palette masks
 layers=[(4,'#e0edf0'),(1,'#364351'),(2,'#247d94'),(3,'#aa7b34')]
 h,w=pal.shape
 paths=[]
 for idx,color in layers:
  mask=np.uint8(pal==idx)*255
  cnts,_=cv2.findContours(mask,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
  dstr=[]
  for c in cnts:
   area=abs(cv2.contourArea(c))
   if area<.22 and len(c)<3:continue
   pts=cv2.approxPolyDP(c,.62,True).reshape(-1,2)
   if len(pts)<3:continue
   cmd='M'+' '.join(f'{int(x)} {int(y)}' if j==0 else f'L{int(x)} {int(y)}' for j,(x,y) in enumerate(pts))+'Z'
   dstr.append(cmd)
  paths.append(f'<path fill="{color}" fill-rule="evenodd" d="'+''.join(dstr)+'"/>')
 svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="white"/>'+'\n'.join(paths)+'</svg>'
 (out/f'{name}.svg').write_text(svg)
 receipts.append({'asset':name,'timestamp_seconds':t,'subject':subject,'source':os.path.basename(v),'crop':[x0,y0,x1,y1],'size':[w,h],'png_bytes':(out/f'{name}.png').stat().st_size,'jpeg_bytes':(out/f'{name}.jpg').stat().st_size,'svg_bytes':(out/f'{name}.svg').stat().st_size})
 print(name,'png',receipts[-1]['png_bytes'],'jpg',receipts[-1]['jpeg_bytes'],'svg',receipts[-1]['svg_bytes'])
cap.release()
(out/'provenance.json').write_text(json.dumps({'source_sha256':hashlib.sha256(open(v,'rb').read()).hexdigest(),'video_duration_sec':694.767,'frames':receipts,'processing':'frame extraction + fixed crop title/subtitle + RGB background transfer to white; no image generator'},ensure_ascii=False,indent=2))
# make quality contact sheets
from PIL import ImageDraw
imgs=[]
for name,_,_ in frames:
 i=Image.open(out/f'{name}.png');i.thumbnail((620,275));imgs.append((name,i.copy()))
board=Image.new('RGB',(1280,4*320),'#ededed');draw=ImageDraw.Draw(board)
for k,(name,i) in enumerate(imgs):
 x=(k%2)*640+(640-i.width)//2;y=(k//2)*320+20
 board.paste(i,(x,y));draw.text(((k%2)*640+8,(k//2)*320+4),name,fill='black')
board.save(out/'contact.png')
# SVG rendered by CairoSVG/PyMuPDF to inspect
try:
 import cairosvg
 b=Image.new('RGB',(1280,4*320),'#eeeeee');dr=ImageDraw.Draw(b)
 for k,(name,_) in enumerate(imgs):
  buf=cairosvg.svg2png(url=str(out/f'{name}.svg'),output_width=620,output_height=251)
  from io import BytesIO
  im=Image.open(BytesIO(buf)).convert('RGB')
  b.paste(im,((k%2)*640+10,(k//2)*320+20));dr.text(((k%2)*640+8,(k//2)*320+4),name,fill='black')
 b.save(out/'svg_contact.png')
except Exception as e:print('SVG preview error',e)
