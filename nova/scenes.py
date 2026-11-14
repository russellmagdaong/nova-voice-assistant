"""Pixel-art backgrounds for NOVA's window, one per pixel mascot. Drawn in code at low resolution
(one array cell per art pixel) and scaled up by whole pixels, like the mascots themselves.

Each scene is laid out around the mascot: `ground` is the row its bottom edge sits on (it peeks
up over a rooftop, a hill, the pond's surface, the sea floor... the way it peeks over the taskbar
in the notch) and `cx` is the column of its centre, kept clear of scenery. Colour bands use
ordered (Bayer) dithering instead of smooth gradients, as pixel art does. Static: nothing moves.

    cat      moonlit rooftops over a city      frog     a pond at night, fireflies
    robot    a space station deck, a planet    ghost    a misty hill under a big moon
    fox      an autumn forest at dusk          axolotl  underwater, sand and seaweed

The orb has no scene (its background stays plain). Drawn by Codex (gpt-6.1-sol) from a brief:
calm and minimal, a small palette per scene in the mascot's colours, quiet where the clock (top
left) and the text box (under the mascot) go.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QImage

BAYER = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16


def rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.uint8)


class Canvas:
    """A w x h pixel canvas (numpy) with a few pixel-art drawing helpers."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.a = np.zeros((h, w, 3), np.uint8)
        self.yy, self.xx = np.mgrid[0:h, 0:w]
        self.bayer = BAYER[self.yy % 4, self.xx % 4]

    def gradient(self, y0: float, y1: float, stops: list[str]):
        """Rows y0..y1 through the colours `stops`, dithered between neighbouring colours."""
        y0, y1 = max(0, int(y0)), min(self.h, int(y1))
        if y1 <= y0:
            return
        cols = np.array([rgb(c) for c in stops])
        n = len(stops) - 1
        t = (np.arange(y0, y1) - y0) / max(1, y1 - y0 - 1) * n
        t = np.repeat(t[:, None], self.w, axis=1)
        i = np.minimum(np.floor(t).astype(int), max(0, n - 1))
        pick = np.clip(i + ((t - i) > self.bayer[y0:y1]), 0, n)
        self.a[y0:y1] = cols[pick]

    def mask(self, m: np.ndarray, color: str, density: float = 1.0):
        """Paint where m is true; density < 1 dithers it (a see-through layer: mist, light)."""
        if density < 1:
            m = m & (self.bayer < density)
        self.a[m] = rgb(color)

    def rect(self, x0, y0, x1, y1, color: str, density: float = 1.0):
        x0, x1 = max(0, int(x0)), min(self.w, max(0, int(x1)))
        y0, y1 = max(0, int(y0)), min(self.h, max(0, int(y1)))
        if x1 <= x0 or y1 <= y0:
            return
        a = self.a[y0:y1, x0:x1]
        if density < 1:
            a[self.bayer[y0:y1, x0:x1] < density] = rgb(color)
        else:
            a[:] = rgb(color)

    def disc(self, cx, cy, r, color: str, density: float = 1.0):
        self.ellipse(cx, cy, r, r, color, density)

    def ellipse(self, cx, cy, rx, ry, color: str, density: float = 1.0):
        x0, x1 = max(0, int(cx-rx-1)), min(self.w, int(cx+rx+2))
        y0, y1 = max(0, int(cy-ry-1)), min(self.h, int(cy+ry+2))
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.ogrid[y0:y1, x0:x1]
        m = ((xx-cx)/max(rx, .5))**2 + ((yy-cy)/max(ry, .5))**2 <= 1
        if density < 1:
            m &= self.bayer[y0:y1, x0:x1] < density
        self.a[y0:y1, x0:x1][m] = rgb(color)

    def cols(self, tops, color: str, bottom: float | None = None, density: float = 1.0):
        """A silhouette: each column x from tops[x] down to `bottom` (or the bottom edge)."""
        m = self.yy >= np.asarray(tops, float)[None, :]
        if bottom is not None:
            m &= self.yy < bottom
        self.mask(m, color, density)

    def px(self, x, y, color: str):
        x, y = int(x), int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            self.a[y, x] = rgb(color)

    def image(self) -> QImage:
        a = np.ascontiguousarray(self.a)
        return QImage(a.data, self.w, self.h, 3 * self.w, QImage.Format_RGB888).copy()


# ---- scene composition -------------------------------------------------------
# All scenery is drawn at art resolution. Wide quiet shapes carry the composition;
# ordered dithering is restricted to the short joins between palette bands.
def bands(c, y0, y1, colors):
    edges = np.linspace(y0, y1, len(colors) + 1).astype(int)
    for i, col in enumerate(colors):
        c.rect(0, edges[i], c.w, edges[i + 1], col)
        if i:
            d = max(2, int((y1-y0) * .055))
            c.gradient(edges[i]-d, edges[i]+d, [colors[i-1], col])


def safe(c, cx, half, x, radius=0):
    return abs(x-cx) > half + 4 + radius


def focal_x(c, cx, half, radius, preferred=.79):
    """Keep the complete moon/planet off the mascot's reserved columns."""
    candidates = (c.w*preferred, c.w*.83, c.w*.17)
    for x in candidates:
        if safe(c, cx, half, x, radius+2):
            return int(x)
    return None


def pinlights(c, g, cx, half, rng, color, count=8):
    # Deliberately sparse, with the clock and mascot columns left quiet.
    for _ in range(count):
        x = int(rng.integers(3, max(4, c.w-3)))
        y = int(rng.integers(max(2, int(g*.14)), max(3, int(g*.77))))
        if safe(c, cx, half, x) and not (x < c.w*.31 and y < c.h*.19):
            c.px(x, y, color)


def ribbon(c, top, width, color, density=1):
    top = np.round(top)[None, :]
    c.mask((c.yy >= top) & (c.yy < top+width), color, density)


def pine(c, x, base, height, color):
    """Three overlapping stepped boughs, a single solid silhouette."""
    x0, x1 = max(0, int(x-height*.31-1)), min(c.w, int(x+height*.31+2))
    y0, y1 = max(0, int(base-height-1)), min(c.h, int(base+1))
    if x1 <= x0 or y1 <= y0:
        return
    xs = np.arange(x0, x1)
    top = np.full(x1-x0, c.h+1.)
    for level, scale in ((0, .64), (.23, .83), (.46, 1.)):
        tip = base-height+height*level
        radius = max(2, height*.29*scale)
        t = tip + np.floor(abs(xs-x)/radius*height*.43)
        t[abs(xs-x) > radius] = c.h+1
        top = np.minimum(top, t)
    yy = np.arange(y0, y1)[:, None]
    c.a[y0:y1, x0:x1][(yy >= top[None, :]) & (yy < base)] = rgb(color)
    c.rect(x-1, base-height*.3, x+1, base+1, color)


def moonlight(c, x, y, r, face, shade, halo):
    if x is None:
        return
    c.disc(x, y, r+3, halo)
    c.disc(x, y, r, face)
    # A few irregular, softly coloured craters; never horizontal face-like stripes.
    c.ellipse(x-r*.39, y-r*.23, max(1, r*.21), max(1, r*.27), shade)
    c.disc(x+r*.28, y+r*.38, max(1, r*.14), shade)


def cat(c: Canvas, g: int, cx: float, half: float, rng):
    W, H = c.w, c.h
    sky, mid, haze = '#101425', '#191e34', '#292b43'
    distant, brick, seam, edge = '#20253b', '#171b2b', '#1c2132', '#414356'
    bands(c, 0, g, [sky, mid, haze])
    r = max(4, int(H*.064))
    mx = focal_x(c, cx, half, r)
    moonlight(c, mx, int(g*.29), r, '#ead8a0', '#c5b78e', haze)
    pinlights(c, g, cx, half, rng, '#7a8095', 9)
    # An asymmetric, edited skyline: paired windows rather than window noise.
    for fx, fw, fh in ((.00,.09,.15),(.085,.065,.23),(.18,.085,.12),
                       (.28,.055,.18),(.70,.10,.11),(.82,.065,.20),(.91,.10,.15)):
        x, bw, bh = int(W*fx), max(3,int(W*fw)), int(H*fh)
        if not safe(c, cx, half, x+bw/2, bw/2):
            continue
        c.rect(x, g-bh, x+bw, g, distant)
        c.rect(x, g-bh, x+bw, g-bh+1, haze)
        if fx in (.085, .82):
            c.rect(x+bw*.5, g-bh-3, x+bw*.5+1, g-bh, distant)
        if fx in (.085,.28,.82,.91):
            for dx, dy in ((2,4),(5,4),(2,9)):
                if dx < bw-1:
                    c.rect(x+dx, g-bh+dy, x+dx+1, g-bh+dy+2, '#c5a46b')
    # Broad rooftop coping and quiet masonry below the text box.
    c.rect(0, g, W, H, brick)
    c.rect(0, g, W, g+2, edge)
    c.rect(0, g+2, W, g+5, distant)
    c.rect(0, g+5, W, g+6, sky)
    depth = H-g
    for i, fy in enumerate((.39,.78)):
        y = g+int(depth*fy)
        c.rect(0, y, W, y+1, seam)
        for fx in ((.15,.76) if i == 0 else (.31,.88)):
            x = int(W*fx)
            c.rect(x, y-int(depth*.32), x+1, y, seam)
    # A small chimney anchors one edge, away from the clock and mascot.
    x = int(W*.075)
    if safe(c,cx,half,x,5):
        c.rect(x, g-10, x+7, g, brick)
        c.rect(x-1, g-11, x+8, g-9, edge)
    if mx is not None:
        c.rect(mx-r, g+2, mx+r, g+3, '#666279', .5)


def robot(c: Canvas, g: int, cx: float, half: float, rng):
    W,H = c.w,c.h
    sky, space, rim = '#0c1422', '#142237', '#31465c'
    floor, panel = '#172333', '#1d2c3d'
    bands(c, 0, g, [sky, sky, space])
    pinlights(c,g,cx,half,rng,'#72899c',12)
    r = max(6,int(H*.095))
    px = focal_x(c,cx,half,r*1.65)
    py = int(g*.33)
    if px is not None:
        # Tilted ring, shaded sphere and the near half of the ring.
        dx,dy = c.xx-px,c.yy-py
        ring = (dx/(r*1.7))**2+((dy+dx*.22)/(r*.36))**2
        band = (ring < 1) & (ring > .64)
        inside = dx**2+dy**2 <= r*r
        c.mask(band & ~inside, '#5f8db4')
        c.mask(inside, rim)
        c.mask(inside & ((dx+r*.4)**2+(dy+r*.25)**2 < (r*.99)**2), '#5f8db4')
        c.mask(inside & ((dx+r*.65)**2+(dy+r*.45)**2 < (r*.78)**2), '#9cc4de')
        c.mask(band & (dy+dx*.22 >= 0), '#9cc4de')
    # Window sill, inset deck panels and a few edge status lights.
    c.rect(0,g,W,H,floor)
    c.rect(0,g,W,g+1,'#5f8db4')
    c.rect(0,g+1,W,g+4,rim)
    c.rect(0,g+4,W,g+6,sky)
    d=H-g
    for x in (-W*.23,W*.08,W*.92,W*1.23):
        yy=np.arange(g+6,H)
        xx=np.round(W/2+(x-W/2)*(1+(yy-g)/max(1,d)*.55)).astype(int)
        valid=(xx>=0)&(xx<W)
        c.a[yy[valid],xx[valid]]=rgb(panel)
    for fy in (.46,.87):
        y=g+int(d*fy)
        c.rect(0,y,W,y+1,panel)
    for fx in (.045,.91):
        x=int(W*fx)
        c.rect(x,g+11,x+int(W*.045),g+12,'#5f8db4')
        c.rect(x,g+14,x+int(W*.025),g+15,rim)
    c.rect(W*.95,g+2,W*.95+2,g+3,'#e84a4a')
    c.rect(W*.05,g+2,W*.05+2,g+3,'#9cc4de')


def fox(c: Canvas, g: int, cx: float, half: float, rng):
    W,H=c.w,c.h
    sky, plum, dusk, amber = '#241e35','#493047','#81505a','#bd795f'
    far, trees, earth, contour = '#624052','#342c3d','#292332','#322a37'
    bands(c,0,g,[sky,plum,dusk,amber])
    r=max(5,int(H*.082)); sx=focal_x(c,cx,half,r, .76)
    if sx is not None:
        c.disc(sx,g-H*.115,r,'#edb679')
    xs=np.arange(W)
    hill=g-H*.045-H*.055*(.5+.5*np.cos(xs/W*math.tau+1))
    hill[np.abs(xs-cx)<half+4]=g-2
    c.cols(np.round(hill),far)
    for fx, fh in ((.015,.25),(.105,.19),(.20,.27),(.29,.13),(.77,.14),(.88,.24),(.99,.31)):
        x=W*fx; ht=H*fh
        if safe(c,cx,half,x,ht*.30):
            pine(c,x,g,ht,trees)
    c.rect(0,g,W,H,earth)
    # The forest floor has broad banks of leaves, with only two little leaf clusters.
    bank=g+(H-g)*(.47+.11*np.sin(xs/W*math.tau+.3))
    c.cols(np.round(bank),contour)
    c.cols(g+(H-g)*(.84+.09*np.sin(xs/W*math.tau-1)),earth)
    for fx,fy in ((.09,.21),(.86,.69)):
        x,y=int(W*fx),int(g+(H-g)*fy)
        c.rect(x,y,x+3,y+1,'#98644f')
        c.rect(x+4,y+2,x+6,y+3,'#624052')
    # One copper bough on either side, safely below the clock.
    for fx in (.055,.945):
        x=W*fx
        if safe(c,cx,half,x,5):
            c.ellipse(x,g-H*.065,W*.035,H*.025,far)


def frog(c: Canvas, g: int, cx: float, half: float, rng):
    W,H=c.w,c.h
    sky, haze, shore = '#101e2b','#1a3341','#152e30'
    water, deep, ripple = '#18333b','#132a33','#24424a'
    bands(c,0,g,[sky,sky,haze])
    r=max(3,int(H*.043)); mx=focal_x(c,cx,half,r,.77)
    moonlight(c,mx,int(g*.28),r,'#c4d3b0','#8baba0',haze)
    # Low banks fade towards the open water at the mascot's chin.
    xs=np.arange(W)
    bank=g-H*.07*(np.exp(-((xs-W*.1)/(W*.17))**2)+np.exp(-((xs-W*.96)/(W*.14))**2))
    bank[np.abs(xs-cx)<half+4]=g
    c.cols(np.round(bank),shore)
    bands(c,g,H,[water,water,deep])
    c.rect(0,g,W,g+1,ripple)
    if mx is not None:
        for i in range(6):
            y=int(g+3+(H-g)*i*.135)
            width=max(2,int(r*(1-i*.10)))
            offset=(-1,1,0,2,-1,1)[i]
            c.rect(mx-width+offset,y,mx+width+offset,y+1,'#58746c' if i<2 else ripple)
    for fx,fy,sz in ((.13,.30,.032),(.84,.52,.041),(.22,.83,.022)):
        x,y=W*fx,g+(H-g)*fy; rx=max(3,W*sz)
        c.ellipse(x,y+1,rx+2,rx*.32,ripple)
        c.ellipse(x,y,rx,rx*.30,'#42674c')
        c.rect(x-rx*.7,y-1,x+rx*.45,y,'#70915c')
        c.rect(x,y,x+rx+1,y+1,water if fy<.65 else deep)
        if fx==.84:
            c.ellipse(x-2,y-2,2,1,'#b48e9e')
            c.px(x-2,y-3,'#d7c6b1')
    for fx,ht in ((.025,.16),(.05,.12),(.08,.19),(.94,.13),(.97,.21)):
        x=int(W*fx); height=int(H*ht)
        if safe(c,cx,half,x,3):
            c.rect(x,g-height,x+1,g+5,'#42674c')
            c.rect(x-1,g-height,x+1,g-height+4,'#70915c')
            for j in range(max(2,height//2)):
                c.px(x+1+j//4,g-j,'#42674c')
    for fx,fy in ((.16,.70),(.29,.83),(.88,.62)):
        x,y=int(W*fx),int(g*fy)
        if safe(c,cx,half,x,2):
            c.disc(x,y,1.5,ripple)
            c.px(x,y,'#c4d3b0')


def ghost(c: Canvas, g: int, cx: float, half: float, rng):
    W,H=c.w,c.h
    sky, haze, halo = '#191a30','#282942','#3a3b57'
    far, earth, mist, crown = '#303149','#202338','#35394f','#292d43'
    bands(c,0,g,[sky,sky,haze])
    r=max(5,int(H*.102)); mx=focal_x(c,cx,half,r,.77)
    moonlight(c,mx,int(g*.34),r,'#e5dfdf','#c4c1cf',halo)
    pinlights(c,g,cx,half,rng,'#85879c',5)
    xs=np.arange(W)
    hill=g-H*.13*np.exp(-((xs-W*.14)/(W*.19))**2)
    hill[np.abs(xs-cx)<half+4]=g
    c.cols(np.round(hill),far)
    near = g+H*.014-H*.045*np.sin(xs/W*math.tau+.5)
    near[np.abs(xs-cx)<half+4] = g
    c.cols(np.round(near),earth)
    # A rounded little grove, with a bent trunk instead of a bare stick.
    tx=int(W*.13); base=int(hill[tx]); ht=max(8,int(H*.20))
    if safe(c,cx,half,tx,ht*.6):
        for j in range(ht):
            x=tx+int(3*math.sin(j/ht*2.3))
            c.rect(x,base-j,x+2,base-j+1,earth)
        for dx,dy,rx,ry in ((-.055,.0,.055,.05),(.015,-.025,.068,.065),(.065,.005,.05,.045)):
            c.ellipse(tx+W*dx,base-ht+H*dy,W*rx,H*ry,crown)
    # Overlapping, continuous fog banks. Their soft curves replace dotted mist rows.
    depth=H-g
    fog=g+depth*(.20+.09*np.sin(xs/W*math.tau+.4))
    c.cols(np.round(fog),crown)
    fog2=g+depth*(.41+.09*np.cos(xs/W*math.tau-.5))
    ribbon(c,fog2,max(2,int(depth*.075)),mist)
    c.cols(np.round(g+depth*(.71+.12*np.sin(xs/W*math.tau-1))),earth)
    # Small moonlit grasses at the far edge of the hill.
    for fx in (.87,.90,.94):
        x=int(W*fx); y=int(g+depth*.75)
        c.rect(x,y-2,x+1,y+1,halo)
        c.px(x-1,y-1,halo)


def axolotl(c: Canvas, g: int, cx: float, half: float, rng):
    W,H=c.w,c.h
    sky, water, deep = '#183343','#204657','#285a68'
    ray, sand, sandlight, trough = '#306574','#52676c','#5d7477','#465e66'
    weed, coral, tip = '#326366','#a56885','#d28fa8'
    bands(c,0,g,[water,water,sky])
    # Only two broad shafts, both clear of the clock and the mascot.
    beam = np.zeros((H, W), bool)
    # A pair of broadcast shafts, combined before painting the canvas.
    centres = W*np.array([.77, .94])
    widths = W*np.array([.055, .028])
    enabled = np.abs(centres-cx) > half+4+widths+g*.12
    shafts = np.abs(c.xx[...,None]-(centres-c.yy[...,None]*.12)) < widths
    beam = np.any(shafts & enabled, axis=2) & (c.yy<g*.82)
    beam &= np.abs(c.xx-cx) > half+4
    c.mask(beam,deep)
    c.mask(beam & (c.yy<g*.28),ray)
    xs=np.arange(W)
    # Distant submerged banks stop at the reserved mascot columns.
    ridge=g-H*.055*(.5+.5*np.cos(xs/W*math.tau))
    ridge[np.abs(xs-cx)<half+4]=g
    c.cols(np.round(ridge),deep)
    c.rect(0,g,W,H,sand)
    c.rect(0,g,W,g+1,sandlight)
    depth=H-g
    for fy,amp,col in ((.25,.07,sandlight),(.45,.08,sand),(.73,.10,trough),(.86,.09,sand)):
        curve=g+depth*(fy+amp*np.sin(xs/W*math.tau+.8))
        c.cols(np.round(curve),col)
    # A pair of edited edge gardens, rather than a curtain of seaweed.
    for fx,fh in ((.035,.22),(.07,.16),(.93,.20),(.97,.29)):
        x0=W*fx; ht=int(H*fh)
        if not safe(c,cx,half,x0,5):
            continue
        for j in range(ht):
            x=x0+2*math.sin(j/max(1,ht)*5)
            c.rect(x,g-j,x+2,g-j+1,weed)
    for fx in (.12,.87):
        x=int(W*fx)
        if not safe(c,cx,half,x,7):
            continue
        base=g+int(depth*.12)
        for dx,ht in ((0,10),(-4,6),(4,8)):
            c.rect(x+dx,base-ht,x+dx+2,base,coral)
            c.rect(x+dx,base-ht,x+dx+2,base-ht+2,tip)
        c.rect(x-3,base-4,x+5,base-2,coral)
    # Three small locally drawn bubble rings, never full-canvas masks in a loop.
    for fx,fy in ((.17,.46),(.19,.30),(.90,.38)):
        x,y=int(W*fx),int(g*fy)
        if safe(c,cx,half,x,2) and not (x<W*.3 and y<H*.19):
            c.disc(x,y,2,ray)
            c.disc(x,y,1,water)
            c.px(x-1,y-1,'#88a5ac')
    for fx,fy in ((.07,.72),(.91,.66)):
        x,y=W*fx,g+depth*fy
        c.ellipse(x,y,3,1,trough)
        c.rect(x-1,y-1,x+2,y,sandlight)


SCENES = {'cat': cat, 'robot': robot, 'fox': fox, 'frog': frog, 'ghost': ghost, 'axolotl': axolotl}


def render(name: str, w: int, h: int, ground: int, cx: float, half: float, seed: int = 7) -> QImage | None:
    """Render art pixels around a peeking mascot; unrecognised names have no scene."""
    fn = SCENES.get(name)
    if fn is None or w < 8 or h < 8:
        return None
    c = Canvas(w, h)
    fn(c, int(max(4, min(h - 2, ground))), cx, half, np.random.default_rng(seed))
    return c.image()

