"""Find segment highlights that paint track they shouldn't.

`render_map` redraws a whole line's artwork clipped to a rectangle between the two
stations of a claimed segment, so any other part of that same line crossing that
rectangle is painted too. The fix is a hand-drawn group in the base map's "Path
Overrides" layer; this finds the places that need one.

Each segment is rendered on its own, and its paint must form one connected ribbon
joining its own two stations — anything else is off route, and is reported with a
picture. Overlaps between two segments of the same line are reported separately:
those are usually branch geometry (a shared trunk), not a fault.

Only the highlight is rasterised, not the map underneath. That is much faster, and it
keeps station labels out of the mask — drawn over a ribbon they would split it in two
and the far side would look off route.

    python check_segment_bleed.py [Line ...]

Pictures of each finding go to out/segment_bleed/. Exits 1 if anything is off route.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from array import array
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


@contextlib.contextmanager
def quiet():
    """The engine and renderer print on every call, and this runs 249 of each."""
    with open(os.devnull, "w") as null, contextlib.redirect_stdout(null):
        yield


with quiet():
    import render
    from new_game import new_game

OUTPUT_DIR = Path("out/segment_bleed")
# Optional: a helper that rasterises every segment in one process with resvg, for the same
# findings a good deal faster. Built on demand when a Rust toolchain is around; without one
# this falls back to rsvg-convert per segment, so the repo needs no Rust to run.
CRATE_DIR = Path(__file__).resolve().parent / "tools/segment_bleed"
RUST_HELPER = CRATE_DIR / "target/release/segment_bleed"
PROBE_COLOR = "#FF1493"  # deep pink; every other pink on the map is >150 away in RGB
COLOR_TOLERANCE = 70
GROW = 200  # how far a crop is widened when the paint reaches its edge
SCALE = 0.5  # raster pixels per SVG unit; the paint we hunt is tens of units long
MARGIN = 24.0  # slack around the clip shapes, in SVG units; the clip bounds the paint
FORK_EXTRA = 180.0  # hand-drawn overrides aren't parsed, so their reach is allowed for; the
# furthest any of them paints beyond its two stations is 135 units. Too little and the paint is
# cut off at the crop edge, which reads as a broken ribbon, so `painted` checks for that.
GRID = 2  # sample spacing in SVG units (1 / SCALE); crops snap to it so masks line up
CELL = 8.0  # connectivity grid in SVG units: bridges the dot fill and the ribbon's edges
MIN_CELLS = 4  # smaller blobs are anti-aliasing specks
TOUCH_SLACK = 30.0  # a ribbon stops this far short of a marker (its disc is cut out)
OVERLAP_MIN = 60.0  # square units of shared paint worth reporting

Segment = tuple[str, str, str]
Box = tuple[int, int, int, int]

centres = render.load_geometry()["station_centres"]
line_segments = render.load_geometry()["line_segments"]
station_markers = render._get_station_markers()
fork_groups = render._get_svg_fork_geometry(line_segments)
outer_radius = {s: render._marker_outer_radius(s, *centres[s], station_markers) for s in centres}


def decode_png(data: bytes) -> tuple[int, int, int, bytes]:
    """Width, height, bytes per pixel and raw pixels of an 8-bit non-interlaced PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, width, height, colour_type = 8, bytearray(), 0, 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag, chunk = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, colour_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            assert depth == 8 and interlace == 0 and colour_type in (2, 6), "unsupported PNG"
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    bpp = 3 if colour_type == 2 else 4
    stride = width * bpp
    out, prev, pos = bytearray(height * stride), bytearray(stride), 0
    for y in range(height):
        method = raw[pos]
        pos += 1
        row = bytearray(raw[pos : pos + stride])
        pos += stride
        if method == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 255
        elif method == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif method == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif method == 4:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                estimate = left + prev[i] - up_left
                da, db, dc = abs(estimate - left), abs(estimate - prev[i]), abs(estimate - up_left)
                nearest = left if (da <= db and da <= dc) else (prev[i] if db <= dc else up_left)
                row[i] = (row[i] + nearest) & 255
        out[y * stride : (y + 1) * stride] = row
        prev = row
    return width, height, bpp, bytes(out)


def encode_png(width: int, height: int, bpp: int, pixels: bytes) -> bytes:
    stride = width * bpp
    raw = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6 if bpp == 4 else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def rasterise(svg: str, width: int, work: Path) -> tuple[int, int, int, bytes]:
    svg_path = work / "probe.svg"
    svg_path.write_text(svg, encoding="utf-8")
    if render.RSVG_PATH is not None:
        png_path = work / "probe.png"
        subprocess.run(
            [render.RSVG_PATH, "-w", str(width), "-f", "png", "-o", str(png_path), str(svg_path)], check=True
        )
        return decode_png(png_path.read_bytes())
    import resvg_py

    return decode_png(bytes(resvg_py.svg_to_bytes(svg, width=width)))


def clip_box(line: str, a: str, b: str) -> Box:
    """Integer bbox covering everything this segment could paint."""
    shapes = render._clip_shapes_for_segment(line, a, b, centres, fork_groups, station_markers)
    xs, ys = [centres[a][0], centres[b][0]], [centres[a][1], centres[b][1]]
    extra = 0.0
    for shape in shapes:
        if isinstance(shape, str):
            extra = FORK_EXTRA
            continue
        for x, y in shape:
            xs.append(x)
            ys.append(y)
    # Snap to the sample lattice: two segments painting the same track must produce the
    # same coordinates, or comparing their masks finds nothing in common.
    x0 = int(math.floor((min(xs) - MARGIN - extra) / GRID) * GRID)
    y0 = int(math.floor((min(ys) - MARGIN - extra) / GRID) * GRID)
    x1 = int(math.ceil((max(xs) + MARGIN + extra) / GRID) * GRID)
    y1 = int(math.ceil((max(ys) + MARGIN + extra) / GRID) * GRID)
    return x0, y0, x1 - x0, y1 - y0


def claim(line: str, a: str, b: str):
    with quiet():
        game = new_game(start_positions={"probe": a}, team_colors={"probe": PROBE_COLOR}, bonus_interchanges=set())
        team = game.teams[0]
        game.complete_challenge(team.role_id, line)
        game.request_challenge(team.role_id, b)
        game.complete_challenge(team.role_id, line)
    return game


# Any pixel within COLOR_TOLERANCE of the probe colour has a green byte this low, so this
# picks candidates in one C-level pass; the full test then runs on those few only.
_GREENISH = bytes(1 if i <= 20 + COLOR_TOLERANCE else 0 for i in range(256))


def rust_helper(build: bool = False) -> Path | None:
    """The resvg helper, or None to fall back to rsvg-convert.

    A binary older than its source is ignored rather than trusted. Building is left to the
    command line: a test run shouldn't stop to compile anything.
    """
    source = CRATE_DIR / "src/main.rs"
    if RUST_HELPER.exists() and RUST_HELPER.stat().st_mtime >= source.stat().st_mtime:
        return RUST_HELPER
    cargo = shutil.which("cargo")
    if not build or cargo is None:
        return None
    print("Building the Rust rasteriser (one off; without it rsvg-convert is used)...", flush=True)
    built = subprocess.run([cargo, "build", "--release"], cwd=CRATE_DIR, capture_output=True, text=True, check=False)
    if built.returncode != 0:
        print(f"  cargo build failed, using rsvg-convert instead:\n{built.stderr.strip()[-400:]}")
        return None
    return RUST_HELPER if RUST_HELPER.exists() else None


def overlay_svg(segment: Segment, box: Box) -> str:
    """Just this segment's highlight, on white, cropped to `box`."""
    x0, y0, w, h = box
    overlay = render._build_segment_overlays(claim(*segment))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {w} {h}" width="{w}" height="{h}">'
        f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" fill="#ffffff"/>{overlay}</svg>'
    )


def marker_cuts(segment: Segment) -> list[list[float]]:
    """Discs to drop from a mask: the two claimed stations, which render in the probe colour."""
    return [[*centres[station], outer_radius[station] + 10] for station in segment[1:]]


def touches_edge(mask: set, box: Box) -> bool:
    """Paint against the crop edge means it was cut off, which reads as a broken ribbon."""
    x0, y0, w, h = box
    edge = max(round(1 / SCALE), 2)
    return bool(mask) and (
        min(p[0] for p in mask) <= x0 + edge
        or max(p[0] for p in mask) >= x0 + w - edge
        or min(p[1] for p in mask) <= y0 + edge
        or max(p[1] for p in mask) >= y0 + h - edge
    )


def grow(box: Box) -> Box:
    """Widen a crop that cut the paint off, so the retry can see the whole ribbon."""
    x0, y0, w, h = box
    return (x0 - GROW, y0 - GROW, w + 2 * GROW, h + 2 * GROW)


def painted_all(segments: list[Segment], boxes: dict[Segment, Box], helper: Path) -> list[set[tuple[int, int]]]:
    """Every segment's paint, rasterised by the Rust helper in one pass."""
    todo = list(segments)
    masks: dict[Segment, set] = {}
    for _attempt in range(3):
        jobs = []
        for segment in todo:
            x0, y0, w, h = boxes[segment]
            jobs.append(
                {"svg": overlay_svg(segment, boxes[segment]), "x0": x0, "y0": y0, "w": w, "h": h}
                | {"cuts": marker_cuts(segment)}
            )
        payload = json.dumps({"scale": SCALE, "tolerance": COLOR_TOLERANCE, "probe": [255, 20, 147], "jobs": jobs})
        result = subprocess.run([str(helper)], input=payload.encode(), capture_output=True, check=True)

        data, pos, grew = result.stdout, 0, []
        for segment in todo:
            (count,) = struct.unpack_from("<I", data, pos)
            pos += 4
            points = array("i")
            points.frombytes(data[pos : pos + count * 8])
            pos += count * 8
            mask = set(zip(points[0::2], points[1::2]))
            if touches_edge(mask, boxes[segment]):
                boxes[segment] = grow(boxes[segment])
                grew.append(segment)
            else:
                masks[segment] = mask
        if not grew:
            break
        todo = grew
    for segment in todo:  # gave up growing; take what it painted
        masks.setdefault(segment, set())
    return [masks[segment] for segment in segments]


def painted(segment: Segment, box: Box, work: Path, _retries: int = 2) -> set[tuple[int, int]]:
    """Where this segment's highlight lands, in SVG units.

    The two claimed stations' marker discs are cut out. Nothing but the highlight is
    drawn here, so without that the paint would run straight through a station and a
    bleed on the far side would read as part of the route.
    """
    x0, y0, w, _h = box
    width, _height, bpp, px = rasterise(overlay_svg(segment, box), max(1, int(w * SCALE)), work)
    step = round(1 / SCALE)

    # Scanning every pixel in Python costs more than rendering it does.
    candidates = px[1::bpp].translate(_GREENISH)
    mask, idx = set(), candidates.find(1)
    while idx != -1:
        i = idx * bpp
        dr, dg, db = px[i] - 255, px[i + 1] - 20, px[i + 2] - 147
        if dr * dr + dg * dg + db * db < COLOR_TOLERANCE * COLOR_TOLERANCE:
            mask.add((x0 + (idx % width) * step, y0 + (idx // width) * step))
        idx = candidates.find(1, idx + 1)

    for cx, cy, reach in marker_cuts(segment):
        mask -= {p for p in mask if math.hypot(p[0] - cx, p[1] - cy) <= reach}

    if _retries and touches_edge(mask, box):
        return painted(segment, grow(box), work, _retries - 1)
    return mask


def blobs(mask: set) -> list[set]:
    cells = {(int(x // CELL), int(y // CELL)) for x, y in mask}
    found, seen = [], set()
    for start in cells:
        if start in seen:
            continue
        blob, queue = {start}, deque([start])
        seen.add(start)
        while queue:
            cx, cy = queue.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbour = (cx + dx, cy + dy)
                    if neighbour in cells and neighbour not in seen:
                        seen.add(neighbour)
                        blob.add(neighbour)
                        queue.append(neighbour)
        found.append(blob)
    return sorted(found, key=len, reverse=True)


def touches(blob: set, station: str) -> bool:
    cx, cy = centres[station]
    reach = outer_radius[station] + TOUCH_SLACK + CELL
    return any(math.hypot(gx * CELL + CELL / 2 - cx, gy * CELL + CELL / 2 - cy) <= reach for gx, gy in blob)


def assess(segment: Segment, mask: set) -> tuple[bool, set]:
    """Whether the paint joins both stations, and any paint that isn't on the route."""
    _line, a, b = segment
    found = blobs(mask)
    route = [blob for blob in found if touches(blob, a) and touches(blob, b)]
    stray = [blob for blob in found if blob not in route and len(blob) >= MIN_CELLS]
    cells = {cell for blob in stray for cell in blob}
    return bool(route), {p for p in mask if (int(p[0] // CELL), int(p[1] // CELL)) in cells}


def scan(segment: Segment) -> tuple[Segment, bool, set, set]:
    """The fallback path: one rsvg-convert per segment, run across a process pool."""
    with tempfile.TemporaryDirectory() as tmp:
        mask = painted(segment, clip_box(*segment), Path(tmp))
    has_route, off = assess(segment, mask)
    return segment, has_route, off, mask


def picture(segment: Segment, off: set, work: Path) -> None:
    """The segment over the real map, with the off-route paint blacked in."""
    line, a, b = segment
    pad = 260
    xs, ys = [x for x, _ in off], [y for _, y in off]
    box = (int(min(xs) - pad), int(min(ys) - pad), int(max(xs) - min(xs) + 2 * pad), int(max(ys) - min(ys) + 2 * pad))
    svg_path = work / "map.svg"
    with quiet():
        render.render_map(claim(*segment), svg_path)
    svg = svg_path.read_text(encoding="utf-8")
    svg = svg[: svg.index('<g id="Legend">')] + "</svg>"
    tag = re.search(r"<svg\b[^>]*>", svg)
    head = re.sub(r'\sviewBox="[^"]*"', f' viewBox="{box[0]} {box[1]} {box[2]} {box[3]}"', tag.group(0))
    head = re.sub(r'\swidth="[^"]*"', f' width="{box[2]}"', head)
    head = re.sub(r'\sheight="[^"]*"', f' height="{box[3]}"', head)
    width, height, bpp, px = rasterise(svg[: tag.start()] + head + svg[tag.end() :], box[2], work)

    buffer = bytearray(px)
    for ux, uy in off:
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                x, y = int(ux - box[0]) + dx, int(uy - box[1]) + dy
                if 0 <= x < width and 0 <= y < height:
                    i = (y * width + x) * bpp
                    buffer[i : i + 3] = b"\x00\x00\x00"
    name = f"{line}_{a}-{b}".replace(" ", "_").replace("/", "-")
    (OUTPUT_DIR / f"{name}.png").write_bytes(encode_png(width, height, bpp, bytes(buffer)))


def sweep(lines: set[str] | None = None) -> tuple[list, list, list]:
    """Returns (off-route, no-route, shared-track) findings for the whole map."""
    segments = [
        (line, seg[0], seg[1])
        for line, segs in sorted(line_segments.items())
        for seg in segs
        if not lines or line in lines
    ]
    masks: dict[Segment, set] = {}
    off_route, no_route = [], []
    helper = rust_helper()
    if helper is not None:
        boxes = {segment: clip_box(*segment) for segment in segments}
        for segment, mask in zip(segments, painted_all(segments, boxes, helper)):
            masks[segment] = mask
            has_route, off = assess(segment, mask)
            if not has_route:
                no_route.append(segment)
            if off:
                off_route.append((len(off) / (SCALE * SCALE), segment, off))
    else:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
            for segment, has_route, off, mask in pool.map(scan, segments, chunksize=4):
                masks[segment] = mask
                if not has_route:
                    no_route.append(segment)
                if off:
                    off_route.append((len(off) / (SCALE * SCALE), segment, off))

    shared = []
    strays = {segment: off for _, segment, off in off_route}
    by_line: dict[str, list] = {}
    for segment in masks:
        by_line.setdefault(segment[0], []).append(segment)
    for keys in by_line.values():
        for i, s in enumerate(keys):
            for t in keys[i + 1 :]:
                # Paint already reported as off route is a bleed, not shared track: without
                # this every bleed would also be listed here, against the segment it landed on.
                overlap = (masks[s] - strays.get(s, set())) & (masks[t] - strays.get(t, set()))
                if len(overlap) / (SCALE * SCALE) >= OVERLAP_MIN:
                    shared.append((len(overlap) / (SCALE * SCALE), s, t))

    off_route.sort(reverse=True, key=lambda f: f[0])
    shared.sort(reverse=True, key=lambda f: f[0])
    return off_route, no_route, shared


def main() -> int:
    wanted = set(sys.argv[1:])
    unknown = wanted - set(line_segments)
    if unknown:
        print(f"Unknown line(s): {sorted(unknown)}\nAvailable: {sorted(line_segments)}")
        return 2

    rust_helper(build=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUTPUT_DIR.glob("*.png"):
        stale.unlink()  # otherwise a fixed segment keeps its picture and looks unfixed
    off_route, no_route, shared = sweep(wanted)

    with tempfile.TemporaryDirectory() as tmp:
        for _, segment, off in off_route:
            picture(segment, off, Path(tmp))

    print(f"OFF ROUTE — paint that isn't on the segment's own track ({len(off_route)}):\n")
    if off_route:
        print(f"  {'units':>6}  {'line':12} {'segment':44} where")
    for area, (line, a, b), off in off_route:
        cx = sum(x for x, _ in off) / len(off)
        cy = sum(y for _, y in off) / len(off)
        print(f"  {area:6.0f}  {line:12} {a + ' - ' + b:44} ({cx:.0f},{cy:.0f})")
    print(f"\n  Pictures in {OUTPUT_DIR}/ (the offending paint is blacked in).")
    print("  Fix by hand-drawing the segment into the base map's Path Overrides layer,")
    print('  as <g id="{line}:{station_a}:{station_b}">, then re-run this.\n')

    if no_route:
        print(f"NO ROUTE — nothing painted joins the two stations, check for a gap ({len(no_route)}):\n")
        for line, a, b in no_route:
            print(f"          {line:12} {a} - {b}")
        print()

    print(f"SHARED TRACK — both segments draw it, usually a branch merging ({len(shared)}):\n")
    for area, s, t in shared:
        print(f"  {area:6.0f}  {s[0]:12} {s[1] + ' - ' + s[2]:40} & {t[1] + ' - ' + t[2]}")
    return 1 if off_route or no_route else 0


if __name__ == "__main__":
    raise SystemExit(main())
