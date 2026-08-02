# -*- coding: utf-8 -*-
"""
BattleChess -- procedural chess piece generator (Blender 4.1+ / 5.x).

Builds two full chess sets and exports them as GLB:

    assets/models/federation.glb   Starfleet: polished duranium, swept curves
    assets/models/imperium.glb     Warhammer 40k gothic: black iron + brass

Run inside Blender:

    exec(compile(open(r"<repo>/BattleChess/blender/build_pieces.py",
         encoding="utf-8").read(), "build_pieces.py", "exec"))

The script is idempotent: it wipes the scene, rebuilds every mesh from scratch
and re-exports both GLB files.

CONTRACT (see BattleChess/CONTRACT.md section 4)
  * exactly 6 root meshes per file: pawn knight bishop rook queen king
  * origin at base centre, geometry grows +Y after the GLTF axis conversion
    (we model +Z up with the base on Z=0; the exporter swaps the axes)
  * piece "front" must be -Z in three.js.  The glTF exporter maps Blender
    (x, y, z) -> glTF (x, z, -y), therefore Blender +Y becomes glTF -Z and
    the fronts are modelled toward Blender +Y.  `verify_export()` re-imports
    the GLB and asserts this empirically.
  * footprint radius <= 0.40, heights per the contract table
  * exactly two material slots, index 0 = 'body', index 1 = 'glow'
"""

import bpy
import bmesh
import math
import os

from mathutils import Vector, Matrix, Euler

TAU = math.pi * 2.0

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------

ROOT = r"B:/Grok/Grok-Party-Pack/BattleChess"
OUT_DIR = os.path.join(ROOT, "assets", "models").replace("\\", "/")

MAT_BODY = 0
MAT_GLOW = 1

# The Blender axis that becomes glTF -Z (the piece "front").
FRONT_Y = 1.0

SHARP_ANGLE = 28.0          # degrees; above this an edge is marked sharp
FOOTPRINT_LIMIT = 0.40

HEIGHTS = {
    "pawn":   0.85,
    "knight": 1.05,
    "rook":   1.00,
    "bishop": 1.15,
    "queen":  1.35,
    "king":   1.50,
}

ORDER = ["pawn", "knight", "bishop", "rook", "queen", "king"]


# --------------------------------------------------------------------------
# scene / material plumbing
# --------------------------------------------------------------------------

def reset_scene():
    """Delete every object + orphan datablock so the build is reproducible."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
                  bpy.data.cameras, bpy.data.lights, bpy.data.images):
        for item in list(block):
            try:
                block.remove(item, do_unlink=True)
            except Exception:
                pass


def _principled(mat):
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _set_input(node, name, value):
    if node is None:
        return
    sock = node.inputs.get(name)
    if sock is not None:
        sock.default_value = value


def make_materials(faction):
    """Create the two shared material datablocks, named exactly body / glow."""
    if faction == "federation":
        base = (0.86, 0.90, 0.95, 1.0)
        metallic, rough = 0.88, 0.22
        emit = (0.22, 0.78, 1.00, 1.0)
        view_body = (0.80, 0.85, 0.90, 1.0)
        view_glow = (0.15, 0.80, 1.00, 1.0)
    else:
        base = (0.055, 0.055, 0.065, 1.0)
        metallic, rough = 0.95, 0.38
        emit = (1.00, 0.20, 0.10, 1.0)
        view_body = (0.10, 0.10, 0.12, 1.0)
        view_glow = (1.00, 0.25, 0.10, 1.0)

    body = bpy.data.materials.new("body")
    body.use_nodes = True
    n = _principled(body)
    _set_input(n, "Base Color", base)
    _set_input(n, "Metallic", metallic)
    _set_input(n, "Roughness", rough)
    _set_input(n, "Emission Strength", 0.0)
    body.diffuse_color = view_body
    body.metallic = metallic
    body.roughness = rough

    glow = bpy.data.materials.new("glow")
    glow.use_nodes = True
    n = _principled(glow)
    _set_input(n, "Base Color", (emit[0] * 0.3, emit[1] * 0.3, emit[2] * 0.3, 1.0))
    _set_input(n, "Metallic", 0.0)
    _set_input(n, "Roughness", 0.35)
    _set_input(n, "Emission Color", emit)
    _set_input(n, "Emission Strength", 3.0)
    glow.diffuse_color = view_glow
    glow.metallic = 0.0
    glow.roughness = 0.3

    return body, glow


# --------------------------------------------------------------------------
# low level bmesh helpers
# --------------------------------------------------------------------------

def _face(bm, verts):
    """bm.faces.new that tolerates duplicate / degenerate input."""
    if len(set(verts)) != len(verts):
        return None
    try:
        return bm.faces.new(verts)
    except ValueError:
        return None


def emit(bm, builder, matrix=None, mat=MAT_BODY):
    """Run `builder(bm)`, then transform + tag exactly the geometry it added."""
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    v0 = len(bm.verts)
    f0 = len(bm.faces)

    builder(bm)

    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    new_v = [bm.verts[i] for i in range(v0, len(bm.verts))]
    new_f = [bm.faces[i] for i in range(f0, len(bm.faces))]

    if matrix is not None and new_v:
        bmesh.ops.transform(bm, matrix=matrix, verts=new_v)
    if new_f:
        bmesh.ops.recalc_face_normals(bm, faces=new_f)
        for f in new_f:
            f.material_index = mat
            f.smooth = True
    return new_v, new_f


def stitch(bm, rings, cap_start=True, cap_end=True, closed=True):
    """Core primitive: build a tube through a list of vertex rings.

    `rings` is a list of point-lists.  Every ring must have the same length,
    except poles which are a single point.  Returns the created vertex rings.
    """
    vrings = []
    for ring in rings:
        vrings.append([bm.verts.new((float(p[0]), float(p[1]), float(p[2])))
                       for p in ring])

    for i in range(len(vrings) - 1):
        lo, hi = vrings[i], vrings[i + 1]
        if len(lo) == 1 and len(hi) == 1:
            continue
        if len(lo) == 1:
            p = lo[0]
            n = len(hi)
            rng = range(n) if closed else range(n - 1)
            for j in rng:
                k = (j + 1) % n
                _face(bm, (p, hi[k], hi[j]))
        elif len(hi) == 1:
            p = hi[0]
            n = len(lo)
            rng = range(n) if closed else range(n - 1)
            for j in rng:
                k = (j + 1) % n
                _face(bm, (lo[j], lo[k], p))
        else:
            n = min(len(lo), len(hi))
            rng = range(n) if closed else range(n - 1)
            for j in rng:
                k = (j + 1) % n
                _face(bm, (lo[j], lo[k], hi[k], hi[j]))

    if cap_start and len(vrings[0]) > 2:
        _face(bm, tuple(reversed(vrings[0])))
    if cap_end and len(vrings[-1]) > 2:
        _face(bm, tuple(vrings[-1]))
    return vrings


def _dedupe_profile(profile):
    out = []
    for r, z in profile:
        r = max(0.0, float(r))
        z = float(z)
        if out and abs(out[-1][0] - r) < 1e-7 and abs(out[-1][1] - z) < 1e-7:
            continue
        out.append((r, z))
    return out


def lathe(bm, profile, segments=24, mat=MAT_BODY, matrix=None,
          cap_bottom=True, cap_top=True, bands=None, rmod=None,
          arc=TAU, sx=1.0, sy=1.0):
    """Surface of revolution about +Z.

    profile : [(radius, z), ...] in order; may double back (shells, dishes)
    bands   : optional {band_index: material_index}.  Band i is the strip
              between the i-th and (i+1)-th point of the *final* point list.
              Pass explicit (0.0, z) end points plus cap_bottom/cap_top=False
              when you use it, so the indices line up with what you wrote.
    rmod    : optional callable f(j, segments) -> radius multiplier (flutes)
    """
    pts = _dedupe_profile(profile)
    if cap_bottom and pts[0][0] > 1e-6:
        pts.insert(0, (0.0, pts[0][1]))
    if cap_top and pts[-1][0] > 1e-6:
        pts.append((0.0, pts[-1][1]))

    closed = abs(arc - TAU) < 1e-6
    n = segments if closed else segments + 1

    def build(b):
        rings = []
        for (r, z) in pts:
            if r <= 1e-6:
                rings.append([(0.0, 0.0, z)])
            else:
                ring = []
                for j in range(n):
                    a = arc * j / segments
                    rr = r * (rmod(j, segments) if rmod else 1.0)
                    ring.append((rr * math.cos(a) * sx,
                                 rr * math.sin(a) * sy, z))
                rings.append(ring)
        stitch(b, rings, cap_start=True, cap_end=True, closed=closed)

    verts, faces = emit(bm, build, matrix, mat)

    if bands:
        # `faces` is in creation order: every band in profile order first,
        # then (at most) the two flat end caps.
        idx = 0
        per_band = n if closed else n - 1
        for i in range(len(pts) - 1):
            lo_pole = pts[i][0] <= 1e-6
            hi_pole = pts[i + 1][0] <= 1e-6
            if lo_pole and hi_pole:
                continue
            m = bands.get(i, mat)
            for _ in range(per_band):
                if idx < len(faces):
                    faces[idx].material_index = m
                idx += 1
    return verts, faces


def torus(bm, major_r, minor_r, major_seg=24, minor_seg=8, mat=MAT_BODY,
          matrix=None, sx=1.0, sy=1.0):
    def build(b):
        rings = []
        for i in range(major_seg):
            a = TAU * i / major_seg
            ca, sa = math.cos(a), math.sin(a)
            ring = []
            for j in range(minor_seg):
                t = TAU * j / minor_seg
                rr = major_r + minor_r * math.cos(t)
                ring.append((rr * ca * sx, rr * sa * sy,
                             minor_r * math.sin(t)))
            rings.append(ring)
        vr = [[b.verts.new(p) for p in ring] for ring in rings]
        m = len(vr)
        for i in range(m):
            lo = vr[i]
            hi = vr[(i + 1) % m]
            for j in range(minor_seg):
                k = (j + 1) % minor_seg
                _face(b, (lo[j], lo[k], hi[k], hi[j]))
    return emit(bm, build, matrix, mat)


def _rect(cx, cy, z, hx, hy, rot=0.0):
    pts = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
    c, s = math.cos(rot), math.sin(rot)
    out = []
    for x, y in pts:
        out.append((cx + x * c - y * s, cy + x * s + y * c, z))
    return out


def taper_box(bm, p0, hx0, hy0, p1, hx1, hy1, rot=0.0, mat=MAT_BODY,
              matrix=None):
    """Loft between two axis-aligned rectangles (rotated by `rot` about Z)."""
    def build(b):
        r0 = _rect(p0[0], p0[1], p0[2], hx0, hy0, rot)
        r1 = _rect(p1[0], p1[1], p1[2], hx1, hy1, rot)
        stitch(b, [r0, r1], cap_start=True, cap_end=True, closed=True)
    return emit(bm, build, matrix, mat)


def loft(bm, rings, mat=MAT_BODY, matrix=None, cap_start=True, cap_end=True,
         closed=True):
    def build(b):
        stitch(b, rings, cap_start=cap_start, cap_end=cap_end, closed=closed)
    return emit(bm, build, matrix, mat)


def prism(bm, poly, thickness, plane="XZ", offset=0.0, mat=MAT_BODY,
          matrix=None):
    """Extrude a 2D polygon into a solid slab.

    plane 'XZ' -> (a, b) becomes (a, offset +- t/2, b)
    plane 'XY' -> (a, b) becomes (a, b, offset +- t/2)
    plane 'YZ' -> (a, b) becomes (offset +- t/2, a, b)
    """
    h = thickness * 0.5

    def to3(a, b, s):
        if plane == "XZ":
            return (a, offset + s * h, b)
        if plane == "XY":
            return (a, b, offset + s * h)
        return (offset + s * h, a, b)

    def build(b):
        r0 = [to3(a, c, -1.0) for a, c in poly]
        r1 = [to3(a, c, 1.0) for a, c in poly]
        stitch(b, [r0, r1], cap_start=True, cap_end=True, closed=True)
    return emit(bm, build, matrix, mat)


def sweep(bm, path, radii, sections=10, mat=MAT_BODY, matrix=None,
          ref_up=(0.0, 0.0, 1.0), cap=True, square=False):
    """Sweep an elliptical (or rectangular) section along a poly-path."""
    pts = [Vector(p) for p in path]
    n = len(pts)
    tangents = []
    for i in range(n):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        if t.length < 1e-9:
            t = Vector((0.0, 0.0, 1.0))
        tangents.append(t.normalized())

    def build(b):
        rings = []
        for i in range(n):
            t = tangents[i]
            up = Vector(ref_up)
            if abs(t.dot(up)) > 0.985:
                up = Vector((0.0, 1.0, 0.0))
            xax = up.cross(t)
            if xax.length < 1e-8:
                xax = Vector((1.0, 0.0, 0.0))
            xax.normalize()
            yax = t.cross(xax).normalized()
            rx, ry = radii[i]
            ring = []
            if square:
                corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
                for cx, cy in corners:
                    p = pts[i] + xax * (rx * cx) + yax * (ry * cy)
                    ring.append((p.x, p.y, p.z))
            else:
                for j in range(sections):
                    a = TAU * j / sections
                    p = (pts[i] + xax * (rx * math.cos(a))
                         + yax * (ry * math.sin(a)))
                    ring.append((p.x, p.y, p.z))
            rings.append(ring)
        stitch(b, rings, cap_start=cap, cap_end=cap, closed=True)
    return emit(bm, build, matrix, mat)


def uvsphere(bm, radius, u=12, v=8, mat=MAT_BODY, matrix=None,
             scale=(1.0, 1.0, 1.0)):
    def build(b):
        rings = []
        rings.append([(0.0, 0.0, -radius * scale[2])])
        for i in range(1, v):
            phi = math.pi * i / v
            z = -math.cos(phi) * radius
            r = math.sin(phi) * radius
            ring = []
            for j in range(u):
                a = TAU * j / u
                ring.append((r * math.cos(a) * scale[0],
                             r * math.sin(a) * scale[1],
                             z * scale[2]))
            rings.append(ring)
        rings.append([(0.0, 0.0, radius * scale[2])])
        stitch(b, rings, cap_start=False, cap_end=False, closed=True)
    return emit(bm, build, matrix, mat)


def spike(bm, base_z, tip_z, r0, r1=0.0, sides=6, mat=MAT_BODY, matrix=None,
          offset=(0.0, 0.0)):
    prof = [(r0, base_z), (r1, tip_z)]
    return lathe(bm, prof, segments=sides, mat=mat,
                 matrix=(matrix if matrix else Matrix.Translation(
                     (offset[0], offset[1], 0.0))))


def bez2(p0, p1, p2, t):
    u = 1.0 - t
    return (Vector(p0) * (u * u) + Vector(p1) * (2 * u * t)
            + Vector(p2) * (t * t))


def bez3(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (Vector(p0) * (u ** 3) + Vector(p1) * (3 * u * u * t)
            + Vector(p2) * (3 * u * t * t) + Vector(p3) * (t ** 3))


def M(loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    """Convenience TRS matrix; rot in degrees, XYZ euler."""
    t = Matrix.Translation(Vector(loc))
    r = Euler((math.radians(rot[0]), math.radians(rot[1]),
               math.radians(rot[2])), "XYZ").to_matrix().to_4x4()
    s = Matrix.Diagonal(Vector((scale[0], scale[1], scale[2], 1.0)))
    return t @ r @ s


# --------------------------------------------------------------------------
# composite shape library
# --------------------------------------------------------------------------

def skull(bm, size=0.10, matrix=None, mat=MAT_BODY, glow=MAT_GLOW,
          detail=1, teeth=6):
    """Stylised skull of unit height 1.0, facing +Y, centred on its origin."""
    mm = matrix if matrix is not None else Matrix.Identity(4)
    S = mm @ Matrix.Diagonal(Vector((size, size, size, 1.0)))
    useg, vseg = (12, 8) if detail else (9, 6)

    uvsphere(bm, 0.36, u=useg, v=vseg, mat=mat,
             matrix=S @ M(loc=(0, -0.04, 0.13), scale=(1.0, 1.10, 1.02)))
    taper_box(bm, (0, 0.16, 0.30), 0.29, 0.16,
              (0, 0.225, 0.15), 0.315, 0.15, mat=mat, matrix=S)
    for sgn in (-1, 1):
        taper_box(bm, (sgn * 0.30, 0.00, 0.10), 0.055, 0.13,
                  (sgn * 0.255, 0.17, -0.03), 0.045, 0.075, mat=mat, matrix=S)
    taper_box(bm, (0, 0.13, 0.02), 0.245, 0.16,
              (0, 0.235, -0.20), 0.185, 0.115, mat=mat, matrix=S)
    prism(bm, [(0.0, 0.06), (0.05, -0.07), (-0.05, -0.07)], 0.09,
          plane="XZ", offset=0.29, mat=glow, matrix=S)
    taper_box(bm, (0, 0.11, -0.235), 0.235, 0.175,
              (0, 0.205, -0.40), 0.175, 0.115, mat=mat, matrix=S)
    for i in range(teeth):
        x = ((i + 0.5) / teeth - 0.5) * 0.33
        taper_box(bm, (x, 0.19, -0.185), 0.019, 0.035,
                  (x, 0.20, -0.245), 0.014, 0.028, mat=mat, matrix=S)
        taper_box(bm, (x, 0.18, -0.240), 0.019, 0.035,
                  (x, 0.19, -0.180), 0.014, 0.028, mat=mat, matrix=S)
    for sgn in (-1, 1):
        uvsphere(bm, 0.125, u=8, v=6, mat=glow,
                 matrix=S @ M(loc=(sgn * 0.152, 0.205, 0.10),
                              scale=(1.0, 0.8, 0.92)))
        torus(bm, 0.138, 0.026, major_seg=10, minor_seg=4, mat=mat,
              matrix=S @ M(loc=(sgn * 0.152, 0.232, 0.10), rot=(90, 0, 0)))


def crenellations(bm, half, z0, z1, per_side=3, w=0.048, d=0.042,
                  mat=MAT_BODY, matrix=None):
    """Merlons around a square parapet of half-width `half`."""
    for side in range(4):
        rot = math.radians(side * 90.0)
        for i in range(per_side):
            s = ((i + 0.5) / per_side - 0.5) * (half * 2.0 - w * 1.6)
            lx, ly = s, half - d * 0.5
            x = lx * math.cos(rot) - ly * math.sin(rot)
            y = lx * math.sin(rot) + ly * math.cos(rot)
            taper_box(bm, (x, y, z0), w, d, (x, y, z1), w * 0.9, d * 0.9,
                      rot=rot, mat=mat, matrix=matrix)


def rivet_ring(bm, radius, z, count=12, r=0.016, mat=MAT_BODY, matrix=None):
    for i in range(count):
        a = TAU * i / count
        uvsphere(bm, r, u=8, v=5, mat=mat,
                 matrix=(matrix if matrix is not None else Matrix.Identity(4))
                 @ M(loc=(radius * math.cos(a), radius * math.sin(a), z),
                     scale=(1.0, 1.0, 0.7)))


def rivet_square(bm, half, z, per_side=3, r=0.016, mat=MAT_BODY, matrix=None):
    base = matrix if matrix is not None else Matrix.Identity(4)
    for side in range(4):
        rot = math.radians(side * 90.0)
        for i in range(per_side):
            s = ((i + 0.5) / per_side - 0.5) * half * 1.75
            x = s * math.cos(rot) - half * math.sin(rot)
            y = s * math.sin(rot) + half * math.cos(rot)
            uvsphere(bm, r, u=8, v=5, mat=mat,
                     matrix=base @ M(loc=(x, y, z), scale=(1.0, 1.0, 0.7)))


def gothic_arch_poly(half_w, z_spring, z_top, z_apex, steps=6):
    """Polygon filling a bay while leaving a pointed-arch void."""
    poly = [(-half_w, z_spring), (-half_w, z_top),
            (half_w, z_top), (half_w, z_spring)]
    rise = z_apex - z_spring
    for i in range(steps + 1):
        p = bez2((half_w, z_spring, 0.0),
                 (half_w * 0.94, z_spring + rise * 0.70, 0.0),
                 (0.0, z_apex, 0.0), i / steps)
        poly.append((p.x, p.y))
    for i in range(1, steps + 1):
        p = bez2((0.0, z_apex, 0.0),
                 (-half_w * 0.94, z_spring + rise * 0.70, 0.0),
                 (-half_w, z_spring, 0.0), i / steps)
        poly.append((p.x, p.y))
    return poly


def gothic_arcade(bm, radius, z0, z1, count=8, col_hw=0.030, col_hd=0.038,
                  mat=MAT_BODY, glow=MAT_GLOW, matrix=None, glow_r=None,
                  arch_frac=0.44, core=True):
    """Ring of pointed arches with a glowing interior seen through them."""
    mm = matrix if matrix is not None else Matrix.Identity(4)
    gr = glow_r if glow_r is not None else radius - col_hd * 1.25
    if core:
        lathe(bm, [(gr, z0 - 0.015), (gr, z1 + 0.005)],
              segments=max(16, count * 2), mat=glow, matrix=mm)

    z_spring = z0 + (z1 - z0) * (1.0 - arch_frac)
    bay = TAU / count
    half_arc = radius * math.sin(bay * 0.5)

    for i in range(count):
        rot = M(rot=(0, 0, math.degrees(bay * i)))
        taper_box(bm, (0.0, radius, z0), col_hw, col_hd,
                  (0.0, radius - 0.004, z_spring), col_hw * 0.92, col_hd,
                  mat=mat, matrix=mm @ rot)
        taper_box(bm, (0.0, radius - 0.004, z_spring - 0.014),
                  col_hw * 1.5, col_hd * 1.2,
                  (0.0, radius - 0.004, z_spring + 0.014),
                  col_hw * 1.15, col_hd * 1.0, mat=mat, matrix=mm @ rot)
        rot2 = M(rot=(0, 0, math.degrees(bay * (i + 0.5))))
        poly = gothic_arch_poly(half_arc, z_spring + 0.004, z1,
                                z_spring + (z1 - z_spring) * 0.88, steps=6)
        prism(bm, poly, col_hd * 1.85, plane="XZ", offset=radius - 0.006,
              mat=mat, matrix=mm @ rot2)


def buttress(bm, ang_deg, pier_r, pier_z0, pier_z1, tower_r, tower_z,
             pier_hw=0.052, pier_hd=0.046, arc_r=(0.028, 0.044),
             mat=MAT_BODY, matrix=None, finial=True):
    """Outer pier plus a flying arc reaching back to the tower."""
    mm = matrix if matrix is not None else Matrix.Identity(4)
    frame = mm @ M(rot=(0, 0, ang_deg))

    taper_box(bm, (0.0, pier_r, pier_z0), pier_hw, pier_hd,
              (0.0, pier_r * 0.95, pier_z1), pier_hw * 0.74, pier_hd * 0.80,
              mat=mat, matrix=frame)
    taper_box(bm, (0.0, pier_r * 0.95, pier_z1), pier_hw * 0.74, pier_hd * 0.80,
              (0.0, pier_r * 0.88, pier_z1 + 0.05), pier_hw * 0.32,
              pier_hd * 0.34, mat=mat, matrix=frame)
    if finial:
        lathe(bm, [(pier_hw * 0.40, pier_z1 + 0.045),
                   (pier_hw * 0.30, pier_z1 + 0.082),
                   (0.0, pier_z1 + 0.145)], segments=6, mat=mat,
              matrix=frame @ Matrix.Translation((0.0, pier_r * 0.88, 0.0)))

    p0 = Vector((0.0, pier_r * 0.92, pier_z1 - 0.03))
    p3 = Vector((0.0, tower_r, tower_z))
    path = [bez3(p0, Vector((0.0, pier_r * 0.92, pier_z1 + 0.12)),
                 Vector((0.0, tower_r + 0.06, tower_z + 0.10)), p3, i / 6.0)
            for i in range(7)]
    radii = [(arc_r[0] * (1.0 - 0.22 * i / 6.0),
              arc_r[1] * (1.0 - 0.12 * i / 6.0)) for i in range(7)]
    sweep(bm, path, radii, sections=6, mat=mat, matrix=frame, square=True)


def feathered_wing(bm, root, ctrl, tip, feathers=5, spar_r=(0.030, 0.044),
                   feather_len=0.26, feather_w=0.032, thickness=0.016,
                   sweep_back=0.55, mirror=False, mat=MAT_BODY, matrix=None,
                   taper=0.5):
    """Wing = a bowed spar plus a fan of tapered feather blades."""
    sx = -1.0 if mirror else 1.0
    mm = (matrix if matrix is not None else Matrix.Identity(4)) \
        @ Matrix.Diagonal(Vector((sx, 1.0, 1.0, 1.0)))

    steps = 6
    path = [bez2(root, ctrl, tip, i / steps) for i in range(steps + 1)]
    radii = [(spar_r[0] * (1.0 - 0.62 * i / steps),
              spar_r[1] * (1.0 - 0.58 * i / steps)) for i in range(steps + 1)]
    sweep(bm, path, radii, sections=8, mat=mat, matrix=mm)

    for i in range(feathers):
        t = (i + 0.65) / (feathers + 0.25)
        p = bez2(root, ctrl, tip, t)
        tangent = (bez2(root, ctrl, tip, min(1.0, t + 0.05))
                   - bez2(root, ctrl, tip, max(0.0, t - 0.05)))
        if tangent.length < 1e-8:
            tangent = Vector((1.0, 0.0, 0.0))
        tangent.normalize()
        d = Vector((0.0, 0.0, -1.0)) * (1.0 - sweep_back) - tangent * sweep_back
        d.normalize()
        L = feather_len * (1.0 - taper * (i / max(1, feathers - 1)))
        w0, w1 = feather_w, feather_w * 0.40
        a0 = p + tangent * (w0 * 0.5)
        b0 = p - tangent * (w0 * 0.5)
        e = p + d * L
        a1 = e + tangent * (w1 * 0.5)
        b1 = e - tangent * (w1 * 0.5)
        n = Vector((0.0, 1.0, 0.0)) * (thickness * 0.5)
        r0 = [tuple(a0 - n), tuple(b0 - n), tuple(b0 + n), tuple(a0 + n)]
        r1 = [tuple(a1 - n * 0.55), tuple(b1 - n * 0.55),
              tuple(b1 + n * 0.55), tuple(a1 + n * 0.55)]
        loft(bm, [r0, r1], mat=mat, matrix=mm)


def saucer(bm, rx, ry, top_h, bot_h, segments=32, mat=MAT_BODY,
           glow=MAT_GLOW, matrix=None):
    """Elliptical starship saucer with an emissive rim band."""
    prof = [
        (0.000, top_h * 1.00),
        (0.200, top_h * 0.96),
        (0.400, top_h * 0.86),
        (0.600, top_h * 0.68),
        (0.780, top_h * 0.45),
        (0.900, top_h * 0.24),
        (0.972, top_h * 0.08),
        (1.000, 0.0),
        (0.985, -bot_h * 0.22),
        (0.920, -bot_h * 0.48),
        (0.780, -bot_h * 0.72),
        (0.580, -bot_h * 0.89),
        (0.340, -bot_h * 0.98),
        (0.000, -bot_h * 1.00),
    ]
    return lathe(bm, prof, segments=segments, mat=mat, matrix=matrix,
                 cap_bottom=False, cap_top=False,
                 bands={6: glow, 7: glow}, sx=rx, sy=ry)


def nacelle(bm, length, radius, matrix=None, mat=MAT_BODY, glow=MAT_GLOW,
            segments=16):
    """Warp nacelle in a local frame: +Z is forward, origin at the rear."""
    L, r = length, radius
    prof = [
        (0.000, -0.02 * L),
        (r * 0.55, 0.00 * L),
        (r * 0.86, 0.04 * L),
        (r * 0.98, 0.10 * L),
        (r * 1.00, 0.62 * L),
        (r * 0.97, 0.78 * L),
        (r * 0.84, 0.90 * L),
        (r * 0.60, 0.965 * L),
        (0.000, 1.00 * L),
    ]
    lathe(bm, prof, segments=segments, mat=mat, matrix=matrix,
          cap_bottom=False, cap_top=False, bands={6: glow, 7: glow})
    lathe(bm, [(0.0, 0.010 * L), (r * 0.52, 0.025 * L),
               (r * 0.52, 0.060 * L), (0.0, 0.050 * L)],
          segments=segments, mat=glow, matrix=matrix,
          cap_bottom=False, cap_top=False)
    for sgn in (-1, 1):
        taper_box(bm, (sgn * r * 0.86, 0.0, 0.16 * L), r * 0.34, r * 0.24,
                  (sgn * r * 0.90, 0.0, 0.50 * L), r * 0.38, r * 0.34,
                  mat=glow, matrix=matrix)
        taper_box(bm, (sgn * r * 0.90, 0.0, 0.50 * L), r * 0.38, r * 0.34,
                  (sgn * r * 0.86, 0.0, 0.84 * L), r * 0.32, r * 0.22,
                  mat=glow, matrix=matrix)


# --------------------------------------------------------------------------
# finishing: normalise, tidy topology, build the object
# --------------------------------------------------------------------------

def finish_piece(bm, name, target_h, mat_body, mat_glow,
                 sharp=SHARP_ANGLE, limit=FOOTPRINT_LIMIT):
    """Normalise height, tidy topology, and turn the bmesh into an object."""
    # 1. n-gons above 4 sides -> triangles (contract: no n-gon > 6 sides)
    ngons = [f for f in bm.faces if len(f.verts) > 4]
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY",
                              ngon_method="BEAUTY")

    # 2. drop zero-area faces the compositing may have produced
    dead = [f for f in bm.faces if f.calc_area() < 1e-10]
    if dead:
        bmesh.ops.delete(bm, geom=dead, context="FACES_ONLY")

    # 3. normalise: base on Z=0, tip at target height
    zs = [v.co.z for v in bm.verts]
    zmin, zmax = min(zs), max(zs)
    bmesh.ops.translate(bm, verts=bm.verts, vec=Vector((0.0, 0.0, -zmin)))
    height = zmax - zmin
    s = target_h / height if height > 1e-9 else 1.0
    bmesh.ops.scale(bm, verts=bm.verts, vec=Vector((s, s, s)))

    # 4. footprint guard (should never fire; design keeps margin)
    rad = max(math.hypot(v.co.x, v.co.y) for v in bm.verts)
    squash = 1.0
    if rad > limit:
        squash = (limit - 0.002) / rad
        bmesh.ops.scale(bm, verts=bm.verts,
                        vec=Vector((squash, squash, 1.0)))
        print("  ! %-7s footprint %.3f > %.2f, squashed XY by %.3f"
              % (name, rad, limit, squash))
        rad = max(math.hypot(v.co.x, v.co.y) for v in bm.verts)

    # 5. shading: everything smooth, edges sharp above the crease angle
    thresh = math.radians(sharp)
    for f in bm.faces:
        f.smooth = True
    for e in bm.edges:
        if len(e.link_faces) == 2:
            e.smooth = e.calc_face_angle(math.pi) < thresh
        else:
            e.smooth = False

    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()

    me.materials.append(mat_body)
    me.materials.append(mat_glow)

    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.scale = (1.0, 1.0, 1.0)

    me.calc_loop_triangles()
    tris = len(me.loop_triangles)
    used = set(p.material_index for p in me.polygons)
    zs = [v.co.z for v in me.vertices]
    print("  %-7s h=%.3f  r=%.3f  tris=%5d  verts=%5d  slots_used=%s"
          % (name, max(zs), rad, tris, len(me.vertices), sorted(used)))
    if used != {0, 1}:
        print("  ! %s does not use both material slots (%s)" % (name, used))
    if tris > 8000:
        print("  ! %s exceeds the 8k triangle budget (%d)" % (name, tris))
    return obj


# ==========================================================================
#  FEDERATION  --  Starfleet: polished duranium, swept curves, cyan glow
# ==========================================================================

def fed_plinth(bm, r0, r1, r2, top_z, glow_band=True):
    """Shared Starfleet pedestal: two soft steps with an inlaid light ring."""
    lathe(bm, [(r0, 0.0), (r0, top_z * 0.26), (r0 * 0.955, top_z * 0.46),
               (r1, top_z * 0.60), (r1, top_z * 0.80),
               (r2, top_z)], segments=32)
    if glow_band:
        torus(bm, r1 * 1.015, top_z * 0.085, major_seg=32, minor_seg=6,
              mat=MAT_GLOW,
              matrix=Matrix.Translation((0.0, 0.0, top_z * 0.70)))


def fed_pawn(mat_body, mat_glow):
    bm = bmesh.new()
    fed_plinth(bm, 0.300, 0.205, 0.150, 0.095)

    # slim pedestal column
    lathe(bm, [(0.108, 0.092), (0.082, 0.150), (0.078, 0.230),
               (0.112, 0.278)], segments=20)

    # rounded teardrop hull with a recessed impulse channel at the waist
    lathe(bm, [(0.072, 0.258), (0.145, 0.292), (0.196, 0.338),
               (0.224, 0.394), (0.234, 0.436),
               (0.206, 0.452), (0.206, 0.490), (0.234, 0.506),
               (0.228, 0.556), (0.208, 0.614), (0.180, 0.668),
               (0.144, 0.718), (0.102, 0.766), (0.058, 0.808),
               (0.024, 0.840), (0.0, 0.856)], segments=28)

    # impulse ring seated in the channel
    torus(bm, 0.213, 0.026, 28, 8, mat=MAT_GLOW,
          matrix=Matrix.Translation((0, 0, 0.471)))
    torus(bm, 0.238, 0.010, 28, 5, mat=MAT_BODY,
          matrix=Matrix.Translation((0, 0, 0.436)))
    torus(bm, 0.238, 0.010, 28, 5, mat=MAT_BODY,
          matrix=Matrix.Translation((0, 0, 0.506)))

    # forward sensor eye
    uvsphere(bm, 0.034, 10, 7, mat=MAT_GLOW,
             matrix=M(loc=(0.0, 0.176 * FRONT_Y, 0.610),
                      scale=(1.0, 0.7, 0.9)))
    torus(bm, 0.046, 0.013, 12, 5, mat=MAT_BODY,
          matrix=M(loc=(0.0, 0.180 * FRONT_Y, 0.610), rot=(90, 0, 0)))

    # swept dorsal fin
    fin = [(0.070 * FRONT_Y, 0.600), (-0.070 * FRONT_Y, 0.572),
           (-0.300 * FRONT_Y, 0.628), (-0.240 * FRONT_Y, 0.760),
           (-0.090 * FRONT_Y, 0.828)]
    prism(bm, fin, 0.030, plane="XZ", mat=MAT_BODY,
          matrix=M(rot=(0, 0, 90)))
    prism(bm, [(0.030 * FRONT_Y, 0.628), (-0.060 * FRONT_Y, 0.614),
               (-0.226 * FRONT_Y, 0.662), (-0.186 * FRONT_Y, 0.740),
               (-0.086 * FRONT_Y, 0.784)],
          0.036, plane="XZ", mat=MAT_GLOW, matrix=M(rot=(0, 0, 90)))
    # ventral manoeuvring thruster pods
    for i in range(4):
        a = 45.0 + 90.0 * i
        taper_box(bm, (0.0, 0.196, 0.330), 0.030, 0.026,
                  (0.0, 0.214, 0.386), 0.024, 0.022,
                  mat=MAT_BODY, matrix=M(rot=(0, 0, a)))
        uvsphere(bm, 0.016, 8, 6, mat=MAT_GLOW,
                 matrix=M(rot=(0, 0, a))
                 @ M(loc=(0.0, 0.222, 0.386), scale=(1.0, 0.6, 0.7)))
    return finish_piece(bm, "pawn", HEIGHTS["pawn"], mat_body, mat_glow)


def fed_knight(mat_body, mat_glow):
    bm = bmesh.new()
    fed_plinth(bm, 0.300, 0.210, 0.150, 0.092)

    # fuselage frame: tail at the rear, nose pitched up and forward
    tail = Vector((0.0, -0.265 * FRONT_Y, 0.490))
    pitch = 44.2
    FM = Matrix.Translation(tail) @ M(rot=(-pitch * FRONT_Y, 0, 0))
    L = 0.781

    # asymmetric launch pylon, a solid swept blade
    sweep(bm, [(0.0, 0.010 * FRONT_Y, 0.075),
               (0.0, -0.070 * FRONT_Y, 0.200),
               (0.0, -0.150 * FRONT_Y, 0.330),
               (0.0, -0.215 * FRONT_Y, 0.455)],
          [(0.098, 0.135), (0.076, 0.115), (0.060, 0.100), (0.050, 0.092)],
          sections=12, mat=MAT_BODY)
    prism(bm, [(0.130 * FRONT_Y, 0.086), (-0.150 * FRONT_Y, 0.086),
               (-0.210 * FRONT_Y, 0.330), (-0.140 * FRONT_Y, 0.330),
               (-0.090 * FRONT_Y, 0.150), (0.110 * FRONT_Y, 0.140)],
          0.036, plane="XZ", mat=MAT_BODY, matrix=M(rot=(0, 0, 90)))
    taper_box(bm, (0.0, -0.062 * FRONT_Y, 0.170), 0.100, 0.016,
              (0.0, -0.108 * FRONT_Y, 0.262), 0.082, 0.016, mat=MAT_GLOW)

    # fuselage
    lathe(bm, [(0.052, 0.000), (0.086, 0.060), (0.104, 0.160),
               (0.108, 0.300), (0.100, 0.450), (0.082, 0.580),
               (0.058, 0.680), (0.028, 0.752), (0.0, L)],
          segments=20, mat=MAT_BODY,
          matrix=FM @ Matrix.Diagonal(Vector((0.92, 1.30, 1.0, 1.0))))
    # armoured dorsal spine
    lathe(bm, [(0.040, 0.180), (0.052, 0.300), (0.044, 0.470),
               (0.024, 0.600)], segments=10, mat=MAT_BODY,
          matrix=FM @ Matrix.Translation((0.0, -0.104, 0.0)))

    # raked delta wings (built in fuselage-local space, then mirrored)
    wing = [(0.062, 0.470), (0.150, 0.400), (0.290, 0.235),
            (0.300, 0.120), (0.180, 0.108), (0.062, 0.150)]
    for sgn in (1.0, -1.0):
        poly = [(x * sgn, z) for (x, z) in wing]
        prism(bm, poly, 0.040, plane="XZ", offset=0.006, mat=MAT_BODY,
              matrix=FM)
        # thickened root fairing
        taper_box(bm, (sgn * 0.075, 0.006, 0.300), 0.038, 0.048,
                  (sgn * 0.190, 0.006, 0.220), 0.024, 0.030,
                  mat=MAT_BODY, matrix=FM)
        # leading-edge light strip
        edge = [(x * sgn, z) for (x, z) in
                [(0.086, 0.452), (0.276, 0.238), (0.282, 0.196),
                 (0.086, 0.408)]]
        prism(bm, edge, 0.048, plane="XZ", offset=0.006, mat=MAT_GLOW,
              matrix=FM)
        # wingtip pod
        lathe(bm, [(0.0, 0.088), (0.020, 0.108), (0.024, 0.190),
                   (0.014, 0.235), (0.0, 0.250)], segments=8,
              mat=MAT_BODY, matrix=FM @ Matrix.Translation(
                  (sgn * 0.292, 0.006, 0.0)))

    # vertical stabiliser (fuselage-local YZ plane, Y is the dorsal axis)
    prism(bm, [(-0.110, 0.520), (-0.110, 0.285), (-0.280, 0.220),
               (-0.272, 0.420)], 0.028, plane="YZ", mat=MAT_BODY,
          matrix=FM)
    prism(bm, [(-0.150, 0.470), (-0.150, 0.320), (-0.250, 0.268),
               (-0.246, 0.390)], 0.036, plane="YZ", mat=MAT_GLOW,
          matrix=FM)

    # twin engine bells with glowing cores
    for sgn in (-1, 1):
        lathe(bm, [(0.038, 0.110), (0.048, 0.045), (0.070, -0.024),
                   (0.072, -0.056)], segments=12, mat=MAT_BODY,
              matrix=FM @ Matrix.Translation((sgn * 0.090, 0.010, 0.0)))
        uvsphere(bm, 0.056, 10, 6, mat=MAT_GLOW,
                 matrix=FM @ M(loc=(sgn * 0.090, 0.010, -0.046),
                               scale=(1.0, 1.0, 0.7)))

    # dorsal canopy
    uvsphere(bm, 1.0, 12, 7, mat=MAT_GLOW,
             matrix=FM @ M(loc=(0.0, -0.100, 0.560),
                           scale=(0.048, 0.030, 0.130)))
    # nose sensor
    uvsphere(bm, 0.030, 8, 6, mat=MAT_GLOW,
             matrix=FM @ M(loc=(0.0, 0.0, 0.744)))
    return finish_piece(bm, "knight", HEIGHTS["knight"], mat_body, mat_glow)


def fed_rook(mat_body, mat_glow):
    bm = bmesh.new()
    fed_plinth(bm, 0.325, 0.245, 0.190, 0.098)

    # drydock spire
    lathe(bm, [(0.146, 0.090), (0.142, 0.300), (0.135, 0.550),
               (0.126, 0.800), (0.119, 0.905)], segments=24)

    # glowing window bands between the docking rings
    for z0, z1 in ((0.150, 0.212), (0.372, 0.434), (0.594, 0.652),
                   (0.812, 0.862)):
        lathe(bm, [(0.158, z0), (0.158, z1)], segments=24, mat=MAT_GLOW)
        for zz in (z0 - 0.012, z1 + 0.012):
            torus(bm, 0.156, 0.011, 24, 5, mat=MAT_BODY,
                  matrix=Matrix.Translation((0, 0, zz)))

    # three stacked docking rings
    for z, ro in ((0.290, 0.325), (0.512, 0.305), (0.734, 0.285)):
        lathe(bm, [(0.140, z - 0.026), (ro * 0.94, z - 0.030),
                   (ro, z - 0.013), (ro, z + 0.013),
                   (ro * 0.94, z + 0.030), (0.140, z + 0.026)],
              segments=28)
        torus(bm, ro * 0.995, 0.0075, 28, 5, mat=MAT_GLOW,
              matrix=Matrix.Translation((0, 0, z)))

    # vertical strut ribs
    for i in range(8):
        a = math.degrees(TAU * i / 8)
        taper_box(bm, (0.0, 0.152, 0.105), 0.017, 0.020,
                  (0.0, 0.130, 0.872), 0.013, 0.018,
                  mat=MAT_BODY, matrix=M(rot=(0, 0, a)))

    # command cap + antenna mast
    lathe(bm, [(0.119, 0.902), (0.140, 0.918), (0.140, 0.936),
               (0.092, 0.952), (0.048, 0.962)], segments=24)
    lathe(bm, [(0.020, 0.955), (0.013, 0.998)], segments=10)
    uvsphere(bm, 0.024, 10, 7, mat=MAT_GLOW,
             matrix=M(loc=(0, 0, 0.982), scale=(1.0, 1.0, 0.85)))
    return finish_piece(bm, "rook", HEIGHTS["rook"], mat_body, mat_glow)


def fed_bishop(mat_body, mat_glow):
    bm = bmesh.new()
    fed_plinth(bm, 0.285, 0.205, 0.150, 0.090)

    # slender tapered sensor column with sensor collars
    lathe(bm, [(0.132, 0.084), (0.118, 0.200), (0.098, 0.400),
               (0.080, 0.600), (0.068, 0.735), (0.078, 0.788)],
          segments=24)
    for z, R in ((0.220, 0.126), (0.460, 0.100), (0.700, 0.080)):
        lathe(bm, [(R + 0.006, z - 0.028), (R + 0.030, z - 0.020),
                   (R + 0.030, z + 0.020), (R + 0.006, z + 0.028)],
              segments=24, mat=MAT_BODY)
        torus(bm, R + 0.034, 0.013, 24, 6, mat=MAT_GLOW,
              matrix=Matrix.Translation((0, 0, z)))
    # vertical waveguide ribs
    for i in range(6):
        taper_box(bm, (0.0, 0.126, 0.130), 0.016, 0.014,
                  (0.0, 0.076, 0.760), 0.011, 0.012,
                  mat=MAT_BODY, matrix=M(rot=(0, 0, 60.0 * i)))

    # dish yoke
    sweep(bm, [(0.0, -0.010 * FRONT_Y, 0.760),
               (0.0, -0.020 * FRONT_Y, 0.845),
               (0.0, -0.016 * FRONT_Y, 0.908)],
          [(0.052, 0.058), (0.042, 0.048), (0.038, 0.044)],
          sections=10, mat=MAT_BODY)
    for sgn in (-1, 1):
        taper_box(bm, (sgn * 0.030, -0.016 * FRONT_Y, 0.800), 0.014, 0.030,
                  (sgn * 0.104, 0.026 * FRONT_Y, 0.900), 0.010, 0.022,
                  mat=MAT_BODY)

    # concave parabolic deflector dish, tipped forward
    DM = M(loc=(0.0, -0.010 * FRONT_Y, 0.905), rot=(-30 * FRONT_Y, 0, 0))
    dish = [
        (0.000, 0.000),
        (0.055, 0.008), (0.110, 0.030), (0.165, 0.066),
        (0.212, 0.110), (0.243, 0.152),
        (0.252, 0.160), (0.252, 0.140),
        (0.238, 0.118), (0.200, 0.078), (0.150, 0.040),
        (0.095, 0.012), (0.045, -0.006), (0.000, -0.014),
    ]
    lathe(bm, dish, segments=32, mat=MAT_BODY, matrix=DM,
          cap_bottom=False, cap_top=False,
          bands={0: MAT_GLOW, 1: MAT_GLOW, 2: MAT_GLOW, 3: MAT_GLOW,
                 4: MAT_GLOW})
    # emitter at the focus
    uvsphere(bm, 0.030, 10, 7, mat=MAT_GLOW,
             matrix=DM @ M(loc=(0.0, 0.0, 0.088)))
    lathe(bm, [(0.020, 0.006), (0.016, 0.070)], segments=8, mat=MAT_BODY,
          matrix=DM)
    return finish_piece(bm, "bishop", HEIGHTS["bishop"], mat_body, mat_glow)


def fed_queen(mat_body, mat_glow):
    bm = bmesh.new()
    fed_plinth(bm, 0.300, 0.225, 0.165, 0.098)

    # engineering hull: a slim spindle low and slightly aft
    lathe(bm, [(0.078, 0.090), (0.106, 0.160), (0.112, 0.270),
               (0.104, 0.380), (0.086, 0.470)],
          segments=22, matrix=M(loc=(0.0, -0.056 * FRONT_Y, 0.0)))
    torus(bm, 0.112, 0.012, 22, 6, mat=MAT_GLOW,
          matrix=M(loc=(0.0, -0.056 * FRONT_Y, 0.272)))
    uvsphere(bm, 1.0, 14, 8, mat=MAT_GLOW,
             matrix=M(loc=(0.0, 0.052 * FRONT_Y, 0.250),
                      scale=(0.046, 0.040, 0.044)))

    # graceful curved neck rising to the saucer
    neck = [(0.0, -0.058 * FRONT_Y, 0.400), (0.0, -0.052 * FRONT_Y, 0.620),
            (0.0, -0.018 * FRONT_Y, 0.850), (0.0, 0.026 * FRONT_Y, 1.050),
            (0.0, 0.046 * FRONT_Y, 1.215)]
    sweep(bm, neck,
          [(0.060, 0.096), (0.052, 0.090), (0.046, 0.086),
           (0.042, 0.082), (0.040, 0.076)],
          sections=14, mat=MAT_BODY)
    for i, (yy, z) in enumerate(((-0.056, 0.520), (-0.036, 0.740),
                                 (0.004, 0.960))):
        torus(bm, 0.055 - i * 0.004, 0.009, 16, 5, mat=MAT_GLOW,
              matrix=M(loc=(0.0, yy * FRONT_Y, z), rot=(12, 0, 0)))

    # saucer section
    SM = M(loc=(0.0, 0.018 * FRONT_Y, 1.258), rot=(-7 * FRONT_Y, 0, 0))
    saucer(bm, 0.288, 0.336, 0.088, 0.076, segments=34, matrix=SM)
    # bridge dome
    uvsphere(bm, 1.0, 14, 8, mat=MAT_BODY,
             matrix=SM @ M(loc=(0.0, -0.040, 0.080),
                           scale=(0.062, 0.070, 0.032)))
    # main deflector on the leading underside
    uvsphere(bm, 1.0, 14, 8, mat=MAT_GLOW,
             matrix=SM @ M(loc=(0.0, 0.256, -0.030),
                           scale=(0.075, 0.048, 0.036)))

    # warp nacelles: horizontal, high and wide, on slim blade pylons
    for sgn in (-1, 1):
        NM = M(loc=(sgn * 0.206, -0.226 * FRONT_Y, 0.905),
               rot=(-84 * FRONT_Y, 0, 0))
        nacelle(bm, 0.420, 0.058, matrix=NM, segments=18)
        sweep(bm, [(sgn * 0.046, -0.054 * FRONT_Y, 0.560),
                   (sgn * 0.108, -0.128 * FRONT_Y, 0.700),
                   (sgn * 0.168, -0.186 * FRONT_Y, 0.840),
                   (sgn * 0.200, -0.212 * FRONT_Y, 0.930)],
              [(0.020, 0.066), (0.017, 0.058), (0.014, 0.048),
               (0.012, 0.038)], sections=8, mat=MAT_BODY, square=True)
    return finish_piece(bm, "queen", HEIGHTS["queen"], mat_body, mat_glow)


def fed_king(mat_body, mat_glow):
    bm = bmesh.new()

    # broad ceremonial base with LCARS accent bands
    lathe(bm, [(0.360, 0.000), (0.360, 0.030), (0.346, 0.052),
               (0.300, 0.066), (0.300, 0.086), (0.256, 0.104),
               (0.256, 0.126), (0.202, 0.146)], segments=34)
    torus(bm, 0.322, 0.011, 34, 6, mat=MAT_GLOW,
          matrix=Matrix.Translation((0, 0, 0.0665)))
    torus(bm, 0.272, 0.010, 30, 6, mat=MAT_GLOW,
          matrix=Matrix.Translation((0, 0, 0.1065)))
    for i in range(10):
        a = math.degrees(TAU * i / 10)
        taper_box(bm, (0.0, 0.303, 0.078), 0.030, 0.012,
                  (0.0, 0.303, 0.094), 0.030, 0.012,
                  mat=MAT_GLOW, matrix=M(rot=(0, 0, a)))

    # tapered command spire
    lathe(bm, [(0.200, 0.140), (0.186, 0.280), (0.166, 0.500),
               (0.146, 0.720), (0.129, 0.920), (0.119, 1.050)],
          segments=28)
    # vertical window slots, standing proud of the tower wall
    for i in range(8):
        a = 22.5 + 45.0 * i
        taper_box(bm, (0.0, 0.196, 0.220), 0.026, 0.016,
                  (0.0, 0.130, 0.960), 0.018, 0.016,
                  mat=MAT_GLOW, matrix=M(rot=(0, 0, a)))
    for i in range(8):
        a = 45.0 * i
        taper_box(bm, (0.0, 0.198, 0.200), 0.020, 0.022,
                  (0.0, 0.132, 0.980), 0.014, 0.020,
                  mat=MAT_BODY, matrix=M(rot=(0, 0, a)))

    # cornice
    lathe(bm, [(0.119, 1.046), (0.184, 1.078), (0.184, 1.104),
               (0.124, 1.132)], segments=28)
    lathe(bm, [(0.124, 1.128), (0.132, 1.150), (0.118, 1.176)], segments=28)

    # crown collar the emblem rises out of
    torus(bm, 0.232, 0.015, 36, 7, mat=MAT_GLOW,
          matrix=Matrix.Translation((0.0, 0.0, 1.042)))
    torus(bm, 0.258, 0.011, 36, 5, mat=MAT_BODY,
          matrix=Matrix.Translation((0.0, 0.0, 1.042)))
    for i in range(8):
        a = 22.5 + 45.0 * i
        taper_box(bm, (0.0, 0.136, 1.010), 0.013, 0.013,
                  (0.0, 0.244, 1.042), 0.009, 0.009,
                  mat=MAT_BODY, matrix=M(rot=(0, 0, a)))

    # Starfleet delta emblem: solid plate haloed by an emissive rim
    right = [(0.000, 1.500), (0.058, 1.404), (0.110, 1.310),
             (0.156, 1.220), (0.192, 1.144), (0.212, 1.092),
             (0.178, 1.106), (0.134, 1.162), (0.086, 1.216),
             (0.040, 1.256), (0.000, 1.278)]
    delta = right + [(-x, z) for (x, z) in reversed(right[1:-1])]
    cz = 1.246
    rim = [(x * 1.085, cz + (z - cz) * 1.085) for (x, z) in delta]
    prism(bm, rim, 0.044, plane="XZ", mat=MAT_GLOW)
    prism(bm, delta, 0.066, plane="XZ", mat=MAT_BODY)
    # spine so the emblem still reads edge-on
    prism(bm, [(-0.032, 1.100), (0.032, 1.100), (0.022, 1.320),
               (0.000, 1.430), (-0.022, 1.320)], 0.054, plane="XZ",
          mat=MAT_BODY, matrix=M(rot=(0, 0, 90)))
    return finish_piece(bm, "king", HEIGHTS["king"], mat_body, mat_glow)


FEDERATION = {
    "pawn": fed_pawn, "knight": fed_knight, "bishop": fed_bishop,
    "rook": fed_rook, "queen": fed_queen, "king": fed_king,
}


# ==========================================================================
#  IMPERIUM  --  40k gothic: blackened iron, brass filigree, verticality
# ==========================================================================

def imp_plinth_square(bm, half, top_z, steps=2, rivets=3):
    """Chunky riveted square plinth."""
    z = 0.0
    h = half
    for i in range(steps):
        z1 = top_z * (i + 1) / steps
        h1 = half * (1.0 - 0.085 * (i + 1))
        taper_box(bm, (0, 0, z), h, h, (0, 0, z1 - 0.012), h, h)
        taper_box(bm, (0, 0, z1 - 0.012), h, h, (0, 0, z1), h1, h1)
        z, h = z1, h1
    if rivets:
        rivet_square(bm, half * 0.955, top_z * 0.30, per_side=rivets,
                     r=half * 0.048)
    return h


def imp_plinth_octo(bm, r0, r1, r2, top_z):
    """Stepped octagonal plinth."""
    lathe(bm, [(r0, 0.0), (r0, top_z * 0.30), (r0 * 0.955, top_z * 0.48),
               (r1, top_z * 0.60), (r1, top_z * 0.82), (r2, top_z)],
          segments=8)


def imp_pawn(mat_body, mat_glow):
    bm = bmesh.new()
    imp_plinth_square(bm, 0.228, 0.078, steps=2, rivets=2)

    # armoured conical body
    lathe(bm, [(0.206, 0.072), (0.194, 0.170), (0.166, 0.300),
               (0.136, 0.410), (0.114, 0.470)], segments=8)
    # front chest plate
    prism(bm, [(-0.085, 0.150), (0.085, 0.150), (0.070, 0.430),
               (-0.070, 0.430)], 0.060, plane="XZ",
          offset=0.170 * FRONT_Y, mat=MAT_BODY)
    prism(bm, [(-0.030, 0.220), (0.030, 0.220), (0.024, 0.380),
               (-0.024, 0.380)], 0.070, plane="XZ",
          offset=0.170 * FRONT_Y, mat=MAT_GLOW)

    # pauldrons + shoulder spikes
    for sgn in (-1, 1):
        taper_box(bm, (sgn * 0.150, -0.008, 0.396), 0.062, 0.078,
                  (sgn * 0.176, -0.008, 0.470), 0.050, 0.062,
                  mat=MAT_BODY)
        lathe(bm, [(0.032, 0.462), (0.024, 0.500), (0.0, 0.560)],
              segments=6, mat=MAT_BODY,
              matrix=Matrix.Translation((sgn * 0.176, -0.008, 0.0)))
    # gorget
    lathe(bm, [(0.126, 0.462), (0.146, 0.492), (0.116, 0.522)], segments=8)

    # hood, pulled back so the helm face stands proud of it
    lathe(bm, [(0.104, 0.508), (0.126, 0.560), (0.126, 0.630),
               (0.106, 0.686), (0.066, 0.726)], segments=10,
          matrix=Matrix.Translation((0.0, -0.056 * FRONT_Y, 0.0)))
    for sgn in (-1, 1):
        prism(bm, [(-0.070 * FRONT_Y, 0.520), (0.060 * FRONT_Y, 0.560),
                   (0.070 * FRONT_Y, 0.660), (-0.060 * FRONT_Y, 0.700)],
              0.022, plane="XZ", mat=MAT_BODY,
              matrix=M(rot=(0, 0, 90 + sgn * 52))
              @ Matrix.Translation((0.0, 0.098, 0.0)))

    # skull-faced helm
    skull(bm, size=0.210, matrix=M(loc=(0.0, 0.052 * FRONT_Y, 0.612)))

    # helm crest spike
    lathe(bm, [(0.052, 0.700), (0.038, 0.756), (0.018, 0.812),
               (0.0, 0.858)], segments=6,
          matrix=M(loc=(0.0, -0.040 * FRONT_Y, 0.0),
                   rot=(-8 * FRONT_Y, 0, 0)))
    # stubby bayonet raised over the right shoulder
    prism(bm, [(0.026 * FRONT_Y, 0.470), (0.086 * FRONT_Y, 0.470),
               (0.180 * FRONT_Y, 0.790), (0.118 * FRONT_Y, 0.802)],
          0.022, plane="YZ", offset=0.168, mat=MAT_BODY)
    taper_box(bm, (0.168, 0.056 * FRONT_Y, 0.452), 0.030, 0.052,
              (0.168, 0.056 * FRONT_Y, 0.500), 0.030, 0.052,
              mat=MAT_BODY)
    taper_box(bm, (0.168, 0.056 * FRONT_Y, 0.468), 0.036, 0.020,
              (0.168, 0.056 * FRONT_Y, 0.488), 0.036, 0.020,
              mat=MAT_GLOW)
    return finish_piece(bm, "pawn", HEIGHTS["pawn"], mat_body, mat_glow)


def imp_knight(mat_body, mat_glow):
    bm = bmesh.new()
    imp_plinth_square(bm, 0.216, 0.074, steps=2, rivets=2)

    # hunched armoured body: narrow hips broadening to heavy shoulders
    taper_box(bm, (0.0, -0.004 * FRONT_Y, 0.068), 0.128, 0.118,
              (0.0, -0.024 * FRONT_Y, 0.270), 0.116, 0.108)
    taper_box(bm, (0.0, -0.024 * FRONT_Y, 0.270), 0.116, 0.108,
              (0.0, -0.050 * FRONT_Y, 0.510), 0.140, 0.098)
    # hip armour skirt
    for sgn in (-1, 1):
        prism(bm, [(-0.128, 0.070), (0.128, 0.070), (0.092, 0.250),
                   (-0.092, 0.250)], 0.026, plane="XZ",
              offset=sgn * 0.116 * FRONT_Y, mat=MAT_BODY)
        prism(bm, [(-0.108, 0.078), (0.108, 0.078), (0.084, 0.262),
                   (-0.084, 0.262)], 0.026, plane="YZ",
              offset=sgn * 0.132, mat=MAT_BODY)
    # angled chest plate with a plasma vent
    prism(bm, [(-0.098, 0.300), (0.098, 0.300), (0.084, 0.480),
               (-0.084, 0.480)], 0.034, plane="XZ",
          offset=0.098 * FRONT_Y, mat=MAT_BODY)
    taper_box(bm, (0.0, 0.108 * FRONT_Y, 0.336), 0.058, 0.016,
              (0.0, 0.104 * FRONT_Y, 0.446), 0.048, 0.016, mat=MAT_GLOW)
    # sloped back plate
    prism(bm, [(-0.132, 0.270), (0.132, 0.270), (0.112, 0.560),
               (-0.112, 0.560)], 0.048, plane="XZ",
          offset=-0.128 * FRONT_Y, mat=MAT_BODY)
    # exhaust stacks
    for sgn in (-1, 1):
        taper_box(bm, (sgn * 0.070, -0.148 * FRONT_Y, 0.330), 0.030, 0.020,
                  (sgn * 0.070, -0.156 * FRONT_Y, 0.548), 0.024, 0.018,
                  mat=MAT_BODY)
        taper_box(bm, (sgn * 0.070, -0.164 * FRONT_Y, 0.360), 0.019, 0.014,
                  (sgn * 0.070, -0.170 * FRONT_Y, 0.520), 0.015, 0.014,
                  mat=MAT_GLOW)
    # pauldrons
    for sgn in (-1, 1):
        taper_box(bm, (sgn * 0.128, -0.040 * FRONT_Y, 0.430), 0.060, 0.098,
                  (sgn * 0.196, -0.050 * FRONT_Y, 0.560), 0.050, 0.080,
                  mat=MAT_BODY)
        taper_box(bm, (sgn * 0.196, -0.050 * FRONT_Y, 0.556), 0.050, 0.080,
                  (sgn * 0.208, -0.050 * FRONT_Y, 0.606), 0.026, 0.042,
                  mat=MAT_BODY)
        lathe(bm, [(0.030, 0.600), (0.020, 0.646), (0.0, 0.702)],
              segments=6, mat=MAT_BODY,
              matrix=Matrix.Translation((sgn * 0.208, -0.050 * FRONT_Y, 0.0)))

    # neck, pitched forward
    taper_box(bm, (0.0, 0.002 * FRONT_Y, 0.480), 0.070, 0.066,
              (0.0, 0.052 * FRONT_Y, 0.750), 0.062, 0.078)
    taper_box(bm, (0.0, 0.058 * FRONT_Y, 0.660), 0.078, 0.020,
              (0.0, 0.070 * FRONT_Y, 0.730), 0.070, 0.020, mat=MAT_GLOW)

    # head: angular warhorse / dreadnought skull, muzzle down and forward
    HM = M(loc=(0.0, 0.050 * FRONT_Y, 0.808), rot=(-22 * FRONT_Y, 0, 0))
    # braincase
    taper_box(bm, (0.0, -0.070, -0.090), 0.098, 0.086,
              (0.0, -0.030, 0.090), 0.092, 0.112, mat=MAT_BODY, matrix=HM)
    # crown plate with a raised ridge
    taper_box(bm, (0.0, -0.030, 0.090), 0.092, 0.112,
              (0.0, 0.006, 0.164), 0.066, 0.084, mat=MAT_BODY, matrix=HM)
    prism(bm, [(-0.020, 0.050), (0.020, 0.050), (0.020, 0.196),
               (0.000, 0.214), (-0.020, 0.196)], 0.034, plane="YZ",
          offset=0.0, mat=MAT_BODY, matrix=HM)
    # brow band
    taper_box(bm, (0.0, 0.068, 0.098), 0.098, 0.038,
              (0.0, 0.098, 0.056), 0.088, 0.034, mat=MAT_BODY, matrix=HM)
    # long upper muzzle
    taper_box(bm, (0.0, 0.052, 0.036), 0.080, 0.096,
              (0.0, 0.250, -0.062), 0.054, 0.070, mat=MAT_BODY, matrix=HM)
    taper_box(bm, (0.0, 0.250, -0.062), 0.054, 0.070,
              (0.0, 0.318, -0.108), 0.044, 0.046, mat=MAT_BODY, matrix=HM)
    # lower jaw, hinged back under the braincase
    taper_box(bm, (0.0, 0.020, -0.106), 0.074, 0.086,
              (0.0, 0.232, -0.166), 0.048, 0.062, mat=MAT_BODY, matrix=HM)
    taper_box(bm, (0.0, 0.232, -0.166), 0.048, 0.062,
              (0.0, 0.300, -0.150), 0.040, 0.036, mat=MAT_BODY, matrix=HM)
    # toothed maw
    for i in range(6):
        x = ((i + 0.5) / 6 - 0.5) * 0.098
        t = abs((i + 0.5) / 6 - 0.5) * 2.0
        y = 0.150 + t * 0.115
        taper_box(bm, (x, y, -0.076), 0.011, 0.028,
                  (x, y + 0.008, -0.128), 0.006, 0.020,
                  mat=MAT_BODY, matrix=HM)
        taper_box(bm, (x, y - 0.010, -0.136), 0.011, 0.028,
                  (x, y - 0.002, -0.086), 0.006, 0.020,
                  mat=MAT_BODY, matrix=HM)
    # flared cheek plates
    for sgn in (-1, 1):
        taper_box(bm, (sgn * 0.094, -0.010, 0.040), 0.022, 0.086,
                  (sgn * 0.070, 0.170, -0.076), 0.014, 0.052,
                  mat=MAT_BODY, matrix=HM)
        prism(bm, [(-0.060, 0.030), (0.070, -0.010), (0.086, -0.098),
                   (-0.040, -0.060)], 0.020, plane="YZ",
              offset=sgn * 0.100, mat=MAT_BODY, matrix=HM)
    # nostril vents
    for sgn in (-1, 1):
        taper_box(bm, (sgn * 0.026, 0.286, -0.070), 0.014, 0.020,
                  (sgn * 0.026, 0.300, -0.096), 0.012, 0.018,
                  mat=MAT_GLOW, matrix=HM)
    # single glowing eye set in an armoured housing
    lathe(bm, [(0.060, 0.0), (0.060, 0.034), (0.044, 0.050)], segments=10,
          mat=MAT_BODY,
          matrix=HM @ M(loc=(0.0, 0.086, 0.096), rot=(-90, 0, 0)))
    uvsphere(bm, 0.044, 12, 8, mat=MAT_GLOW,
             matrix=HM @ M(loc=(0.0, 0.112, 0.096), scale=(1.0, 0.6, 0.9)))

    # swept mane of armour blades down the back of the neck
    blade = [(0.045, -0.060), (-0.055, -0.090), (-0.085, 0.262),
             (0.012, 0.330)]
    for i in range(4):
        k = 1.0 - 0.18 * i
        dy, dz = -0.040 * i, -0.070 * i
        poly = [((dy + x * k) * FRONT_Y, 0.700 + dz + z * k)
                for (x, z) in blade]
        prism(bm, poly, 0.032 - 0.005 * i, plane="XZ", mat=MAT_BODY,
              matrix=M(rot=(0, 0, 90)))
    return finish_piece(bm, "knight", HEIGHTS["knight"], mat_body, mat_glow)


def imp_rook(mat_body, mat_glow):
    bm = bmesh.new()
    imp_plinth_square(bm, 0.268, 0.105, steps=2, rivets=3)

    # bastion tower
    taper_box(bm, (0, 0, 0.100), 0.158, 0.158, (0, 0, 0.800), 0.136, 0.136)

    # corner buttresses
    for i in range(4):
        a = 45.0 + 90.0 * i
        rot = M(rot=(0, 0, a))
        taper_box(bm, (0.0, 0.222, 0.100), 0.086, 0.074,
                  (0.0, 0.190, 0.420), 0.064, 0.058,
                  mat=MAT_BODY, matrix=rot)
        taper_box(bm, (0.0, 0.190, 0.420), 0.064, 0.058,
                  (0.0, 0.148, 0.640), 0.042, 0.038,
                  mat=MAT_BODY, matrix=rot)
        taper_box(bm, (0.0, 0.148, 0.640), 0.042, 0.038,
                  (0.0, 0.132, 0.700), 0.036, 0.032,
                  mat=MAT_BODY, matrix=rot)
        lathe(bm, [(0.036, 0.696), (0.024, 0.744), (0.0, 0.806)],
              segments=6, mat=MAT_BODY,
              matrix=rot @ Matrix.Translation((0.0, 0.132, 0.0)))
        taper_box(bm, (0.0, 0.216, 0.240), 0.020, 0.020,
                  (0.0, 0.216, 0.360), 0.016, 0.016,
                  mat=MAT_GLOW, matrix=rot)

    # glowing arrow slits, three per face
    for i in range(4):
        a = 90.0 * i
        rot = M(rot=(0, 0, a))
        for (x, z0, z1) in ((-0.074, 0.300, 0.440), (0.0, 0.360, 0.560),
                            (0.074, 0.300, 0.440), (0.0, 0.620, 0.740)):
            taper_box(bm, (x, 0.152, z0), 0.015, 0.016,
                      (x, 0.144, z1), 0.012, 0.016,
                      mat=MAT_GLOW, matrix=rot)

    # gate arch on the front face
    gate = gothic_arch_poly(0.084, 0.115, 0.320, 0.292, steps=6)
    prism(bm, gate, 0.040, plane="XZ", offset=0.162 * FRONT_Y,
          mat=MAT_BODY)
    taper_box(bm, (0.0, 0.154 * FRONT_Y, 0.110), 0.078, 0.014,
              (0.0, 0.154 * FRONT_Y, 0.290), 0.066, 0.014, mat=MAT_GLOW)

    # cornice, machicolation corbels, crenellations
    taper_box(bm, (0, 0, 0.795), 0.136, 0.136, (0, 0, 0.828), 0.192, 0.192)
    taper_box(bm, (0, 0, 0.828), 0.192, 0.192, (0, 0, 0.864), 0.184, 0.184)
    for i in range(4):
        for j in range(3):
            s = (j - 1) * 0.104
            taper_box(bm, (s, 0.150, 0.790), 0.024, 0.026,
                      (s, 0.186, 0.828), 0.024, 0.020,
                      mat=MAT_BODY, matrix=M(rot=(0, 0, 90.0 * i)))
    crenellations(bm, 0.184, 0.860, 0.968, per_side=3, w=0.058, d=0.050)

    # central keep spike
    taper_box(bm, (0, 0, 0.858), 0.072, 0.072, (0, 0, 0.908), 0.060, 0.060)
    taper_box(bm, (0.0, 0.0, 0.874), 0.084, 0.020,
              (0.0, 0.0, 0.900), 0.084, 0.020, mat=MAT_GLOW)
    taper_box(bm, (0.0, 0.0, 0.874), 0.020, 0.084,
              (0.0, 0.0, 0.900), 0.020, 0.084, mat=MAT_GLOW)
    lathe(bm, [(0.060, 0.902), (0.042, 0.948), (0.0, 1.004)], segments=8)
    return finish_piece(bm, "rook", HEIGHTS["rook"], mat_body, mat_glow)


def imp_bishop(mat_body, mat_glow):
    bm = bmesh.new()
    imp_plinth_octo(bm, 0.300, 0.238, 0.190, 0.100)

    # fluted column
    def flutes(j, segs):
        return 1.0 + 0.055 * math.cos(8.0 * TAU * j / segs)

    lathe(bm, [(0.148, 0.096), (0.140, 0.220), (0.132, 0.380),
               (0.124, 0.520), (0.120, 0.600)], segments=32, rmod=flutes)
    lathe(bm, [(0.152, 0.096), (0.160, 0.116), (0.146, 0.136)], segments=16)

    # flying buttresses on the diagonals
    for i in range(4):
        buttress(bm, 45.0 + 90.0 * i, 0.262, 0.096, 0.330, 0.128, 0.520,
                 pier_hw=0.046, pier_hd=0.042, arc_r=(0.024, 0.036))

    # rose window at the front
    RW = M(loc=(0.0, 0.104 * FRONT_Y, 0.660), rot=(-90 * FRONT_Y, 0, 0))
    lathe(bm, [(0.078, -0.030), (0.078, 0.026)], segments=18, mat=MAT_GLOW,
          matrix=RW)
    torus(bm, 0.090, 0.020, 18, 6, mat=MAT_BODY, matrix=RW)
    # radial tracery across the window face
    for i in range(6):
        prism(bm, [(-0.0075, -0.090), (0.0075, -0.090),
                   (0.0075, 0.090), (-0.0075, 0.090)], 0.024,
              plane="XY", offset=0.016, mat=MAT_BODY,
              matrix=RW @ M(rot=(0, 0, 30.0 * i)))
    torus(bm, 0.040, 0.010, 12, 5, mat=MAT_BODY,
          matrix=RW @ Matrix.Translation((0, 0, 0.016)))
    lathe(bm, [(0.108, -0.026), (0.118, 0.000), (0.108, 0.022)],
          segments=18, mat=MAT_BODY, matrix=RW)

    # arcaded gallery
    gothic_arcade(bm, 0.128, 0.720, 0.836, count=8, col_hw=0.020,
                  col_hd=0.026, glow_r=0.100)

    # spire with crockets
    lathe(bm, [(0.140, 0.826), (0.132, 0.856), (0.112, 0.880),
               (0.086, 0.940), (0.050, 1.000), (0.020, 1.036)], segments=8)
    for i in range(4):
        a = 45.0 + 90.0 * i
        for t, s in ((0.00, 1.0), (0.36, 0.8), (0.70, 0.6)):
            z = 0.888 + t * 0.120
            r = 0.082 - t * 0.052
            prism(bm, [(0.0, z), (0.030 * s, z + 0.014),
                       (0.040 * s, z + 0.044), (0.0, z + 0.030)],
                  0.020 * s, plane="XZ", offset=r, mat=MAT_BODY,
                  matrix=M(rot=(0, 0, a)))

    # skull finial
    skull(bm, size=0.118, matrix=M(loc=(0.0, 0.0, 1.092)))
    return finish_piece(bm, "bishop", HEIGHTS["bishop"], mat_body, mat_glow)


def imp_queen(mat_body, mat_glow):
    bm = bmesh.new()
    imp_plinth_octo(bm, 0.292, 0.232, 0.186, 0.104)

    # throne spire: arcaded base, narrow waist exposing the plasma core
    lathe(bm, [(0.146, 0.100), (0.138, 0.160), (0.100, 0.196)], segments=12)
    lathe(bm, [(0.100, 0.192), (0.100, 0.408)], segments=12)
    lathe(bm, [(0.100, 0.404), (0.134, 0.442), (0.122, 0.478),
               (0.096, 0.510)], segments=12)
    lathe(bm, [(0.056, 0.500), (0.056, 0.700)], segments=12)
    lathe(bm, [(0.058, 0.692), (0.098, 0.740), (0.108, 0.800),
               (0.098, 0.950), (0.084, 1.090)], segments=12)
    gothic_arcade(bm, 0.142, 0.196, 0.406, count=6, col_hw=0.026,
                  col_hd=0.032, glow_r=0.118, arch_frac=0.46)
    # ribs up the upper spire
    for i in range(6):
        taper_box(bm, (0.0, 0.104, 0.770), 0.018, 0.020,
                  (0.0, 0.090, 1.062), 0.013, 0.017,
                  mat=MAT_BODY, matrix=M(rot=(0, 0, 60.0 * i)))

    # plasma core in its cage
    uvsphere(bm, 0.092, 16, 11, mat=MAT_GLOW,
             matrix=M(loc=(0.0, 0.0, 0.605)))
    torus(bm, 0.104, 0.017, 16, 6, matrix=Matrix.Translation((0, 0, 0.512)))
    torus(bm, 0.104, 0.017, 16, 6, matrix=Matrix.Translation((0, 0, 0.700)))
    for i in range(6):
        taper_box(bm, (0.0, 0.104, 0.500), 0.016, 0.014,
                  (0.0, 0.104, 0.712), 0.014, 0.012,
                  mat=MAT_BODY, matrix=M(rot=(0, 0, 60.0 * i)))

    # brass filigree wings sweeping up and out
    for mirror in (False, True):
        feathered_wing(bm,
                       root=(0.052, -0.020 * FRONT_Y, 0.760),
                       ctrl=(0.226, -0.040 * FRONT_Y, 0.858),
                       tip=(0.318, -0.026 * FRONT_Y, 1.062),
                       feathers=6, spar_r=(0.028, 0.042),
                       feather_len=0.230, feather_w=0.050,
                       thickness=0.020, sweep_back=0.16, taper=-0.12,
                       mirror=mirror, mat=MAT_BODY)
        # shoulder coverts
        prism(bm, [(-0.024, 0.740), (0.052, 0.756), (0.146, 0.820),
                   (0.128, 0.866), (0.030, 0.808), (-0.030, 0.790)],
              0.028, plane="XZ", offset=-0.022 * FRONT_Y, mat=MAT_BODY,
              matrix=Matrix.Diagonal(Vector(
                  (-1.0 if mirror else 1.0, 1.0, 1.0, 1.0))))

    # crowned peak
    lathe(bm, [(0.082, 1.080), (0.070, 1.118), (0.092, 1.180),
               (0.096, 1.212)], segments=8)
    lathe(bm, [(0.070, 1.176), (0.070, 1.212)], segments=8, mat=MAT_GLOW)
    for i in range(6):
        lathe(bm, [(0.024, 1.196), (0.016, 1.250), (0.0, 1.300)],
              segments=5, mat=MAT_BODY,
              matrix=M(rot=(0, 0, 60.0 * i))
              @ Matrix.Translation((0.0, 0.084, 0.0)))
    lathe(bm, [(0.036, 1.190), (0.026, 1.268), (0.0, 1.352)], segments=6)
    return finish_piece(bm, "queen", HEIGHTS["queen"], mat_body, mat_glow)


def imp_king(mat_body, mat_glow):
    bm = bmesh.new()

    # broad buttressed plinth
    lathe(bm, [(0.364, 0.000), (0.364, 0.036), (0.346, 0.060),
               (0.300, 0.078), (0.300, 0.100), (0.252, 0.122),
               (0.252, 0.144), (0.206, 0.164)], segments=8)

    # tower
    lathe(bm, [(0.202, 0.158), (0.192, 0.300), (0.178, 0.440),
               (0.170, 0.600), (0.164, 0.760), (0.156, 0.900),
               (0.152, 0.960)], segments=12)

    # gothic arcade with deep plasma glow behind it
    gothic_arcade(bm, 0.182, 0.430, 0.740, count=8, col_hw=0.030,
                  col_hd=0.036, glow_r=0.150)

    # flying buttresses
    for i in range(4):
        buttress(bm, 45.0 + 90.0 * i, 0.290, 0.158, 0.430, 0.180, 0.700,
                 pier_hw=0.058, pier_hd=0.050, arc_r=(0.030, 0.046))

    # skull keystone over the front bay
    skull(bm, size=0.110, matrix=M(loc=(0.0, 0.196 * FRONT_Y, 0.320)))

    # cornice
    lathe(bm, [(0.152, 0.952), (0.212, 0.988), (0.212, 1.014),
               (0.150, 1.044)], segments=12)

    # double-headed aquila
    taper_box(bm, (0.0, 0.0, 1.030), 0.062, 0.052,
              (0.0, 0.010 * FRONT_Y, 1.230), 0.078, 0.058)
    uvsphere(bm, 0.048, 12, 8, mat=MAT_GLOW,
             matrix=M(loc=(0.0, 0.058 * FRONT_Y, 1.150),
                      scale=(1.0, 0.55, 1.15)))
    torus(bm, 0.058, 0.014, 14, 5, mat=MAT_BODY,
          matrix=M(loc=(0.0, 0.066 * FRONT_Y, 1.150), rot=(90, 0, 0)))

    for mirror in (False, True):
        feathered_wing(bm,
                       root=(0.058, -0.008 * FRONT_Y, 1.108),
                       ctrl=(0.244, -0.018 * FRONT_Y, 1.168),
                       tip=(0.334, -0.008 * FRONT_Y, 1.318),
                       feathers=6, spar_r=(0.030, 0.046),
                       feather_len=0.215, feather_w=0.054,
                       thickness=0.022, sweep_back=0.14, taper=-0.14,
                       mirror=mirror, mat=MAT_BODY)
        sgn = -1.0 if mirror else 1.0
        # shoulder coverts
        prism(bm, [(-0.020, 1.090), (0.060, 1.106), (0.156, 1.166),
                   (0.140, 1.212), (0.038, 1.156), (-0.026, 1.140)],
              0.030, plane="XZ", offset=-0.010 * FRONT_Y, mat=MAT_BODY,
              matrix=Matrix.Diagonal(Vector((sgn, 1.0, 1.0, 1.0))))
        # arched neck + outward-facing head
        sweep(bm, [(sgn * 0.028, 0.006 * FRONT_Y, 1.212),
                   (sgn * 0.062, 0.010 * FRONT_Y, 1.286),
                   (sgn * 0.098, 0.012 * FRONT_Y, 1.326)],
              [(0.032, 0.034), (0.027, 0.029), (0.024, 0.026)],
              sections=8, mat=MAT_BODY)
        taper_box(bm, (sgn * 0.094, 0.010 * FRONT_Y, 1.310), 0.030, 0.032,
                  (sgn * 0.126, 0.014 * FRONT_Y, 1.348), 0.024, 0.026,
                  mat=MAT_BODY)
        lathe(bm, [(0.028, 0.0), (0.024, 0.028), (0.010, 0.066),
                   (0.0, 0.086)], segments=6, mat=MAT_BODY,
              matrix=M(loc=(sgn * 0.136, 0.014 * FRONT_Y, 1.330),
                       rot=(0, sgn * 78, 0)))
        uvsphere(bm, 0.014, 8, 6, mat=MAT_GLOW,
                 matrix=M(loc=(sgn * 0.124, 0.034 * FRONT_Y, 1.352)))
        # crest fin behind each head
        prism(bm, [(0.070, 1.318), (0.104, 1.334), (0.092, 1.402),
                   (0.056, 1.372)], 0.018, plane="XZ",
              offset=-0.006 * FRONT_Y, mat=MAT_BODY,
              matrix=Matrix.Diagonal(Vector((sgn, 1.0, 1.0, 1.0))))

    # crown spike rising between the heads
    lathe(bm, [(0.046, 1.252), (0.052, 1.290), (0.034, 1.372),
               (0.018, 1.442), (0.0, 1.502)], segments=8)
    torus(bm, 0.050, 0.011, 12, 5, mat=MAT_GLOW,
          matrix=Matrix.Translation((0.0, 0.0, 1.286)))
    return finish_piece(bm, "king", HEIGHTS["king"], mat_body, mat_glow)


IMPERIUM = {
    "pawn": imp_pawn, "knight": imp_knight, "bishop": imp_bishop,
    "rook": imp_rook, "queen": imp_queen, "king": imp_king,
}

FACTION_TABLES = {"federation": FEDERATION, "imperium": IMPERIUM}


# ==========================================================================
#  export / verification / preview
# ==========================================================================

def export_glb(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for obj in bpy.data.objects:
        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)

    wanted = {
        "filepath": path,
        "export_format": "GLB",
        "export_yup": True,
        "export_apply": True,
        "export_materials": "EXPORT",
        "export_cameras": False,
        "export_lights": False,
        "use_selection": False,
        "use_visible": False,
        "use_renderable": False,
        "use_active_collection": False,
        "export_normals": True,
        "export_tangents": False,
        "export_texcoords": False,
        "export_extras": False,
        "export_animations": False,
        "export_skins": False,
        "export_morph": False,
        "export_attributes": False,
        "export_draco_mesh_compression_enable": False,
    }
    valid = {p.identifier for p in
             bpy.ops.export_scene.gltf.get_rna_type().properties}
    kwargs = {k: v for k, v in wanted.items() if k in valid}
    bpy.ops.export_scene.gltf(**kwargs)
    size = os.path.getsize(path)
    print("  -> %s  (%.1f KB)" % (path, size / 1024.0))
    return size


def _read_glb_json(path):
    import json
    import struct
    with open(path, "rb") as fh:
        data = fh.read()
    magic, _version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("not a GLB: %s" % path)
    off = 12
    doc = None
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off)
        if ctype == 0x4E4F534A:
            doc = json.loads(data[off + 8: off + 8 + clen].decode("utf-8"))
        off += 8 + clen
    return doc


def verify_export(path, blender_bounds=None):
    """Re-read the GLB and check it against the contract, in glTF space."""
    doc = _read_glb_json(path)
    acc = doc.get("accessors", [])
    mats = [m.get("name") for m in doc.get("materials", [])]
    print("\n  verify %s" % os.path.basename(path))
    print("    materials: %s" % mats)

    roots = doc["scenes"][doc.get("scene", 0)]["nodes"]
    seen = []
    ok = True
    for ni in roots:
        node = doc["nodes"][ni]
        name = node.get("name")
        seen.append(name)
        if "mesh" not in node:
            print("    ! node %s has no mesh" % name)
            ok = False
            continue
        if any(k in node for k in ("translation", "rotation", "scale",
                                   "matrix")):
            print("    ! node %s carries a transform" % name)
            ok = False
        mesh = doc["meshes"][node["mesh"]]
        lo = [1e9, 1e9, 1e9]
        hi = [-1e9, -1e9, -1e9]
        prim_mats = []
        for prim in mesh["primitives"]:
            a = acc[prim["attributes"]["POSITION"]]
            for i in range(3):
                lo[i] = min(lo[i], a["min"][i])
                hi[i] = max(hi[i], a["max"][i])
            prim_mats.append(mats[prim["material"]]
                             if "material" in prim else None)
        target = HEIGHTS.get(name)
        radius = max(abs(lo[0]), abs(hi[0]), abs(lo[2]), abs(hi[2]))
        flag = ""
        if target is None:
            flag += " BADNAME"
        else:
            if abs(hi[1] - target) > 0.004:
                flag += " HEIGHT"
            if abs(lo[1]) > 0.004:
                flag += " BASE"
        if radius > FOOTPRINT_LIMIT + 1e-3:
            flag += " FOOTPRINT"
        if sorted(m for m in prim_mats if m) != ["body", "glow"]:
            flag += " MATSLOTS"
        if flag:
            ok = False
        print("    %-7s y=[%.3f %.3f] x=[%+.3f %+.3f] z=[%+.3f %+.3f] "
              "r=%.3f mats=%s%s"
              % (name, lo[1], hi[1], lo[0], hi[0], lo[2], hi[2],
                 radius, prim_mats, flag))

        if blender_bounds and name in blender_bounds:
            by0, by1 = blender_bounds[name]
            # Blender +Y must land on glTF -Z
            if abs(lo[2] + by1) > 0.004 or abs(hi[2] + by0) > 0.004:
                print("      ! axis mapping mismatch: blender y=[%.3f %.3f] "
                      "-> gltf z=[%.3f %.3f]" % (by0, by1, lo[2], hi[2]))
                ok = False

    missing = [n for n in ORDER if n not in seen]
    extra = [n for n in seen if n not in ORDER]
    if missing or extra:
        print("    ! root meshes missing=%s extra=%s" % (missing, extra))
        ok = False
    print("    %s" % ("OK" if ok else "*** PROBLEMS ***"))
    return ok


def build_faction(faction):
    table = FACTION_TABLES[faction]
    reset_scene()
    mat_body, mat_glow = make_materials(faction)
    print("\n== %s ==" % faction)
    bounds = {}
    for name in ORDER:
        obj = table[name](mat_body, mat_glow)
        ys = [v.co.y for v in obj.data.vertices]
        bounds[name] = (min(ys), max(ys))
    return bounds


def frame_viewport(center=(0.0, 0.0, 0.55), distance=4.2, azimuth=205.0,
                   elevation=72.0, shading="MATERIAL"):
    for area in bpy.context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        space = area.spaces[0]
        rv3d = space.region_3d
        rv3d.view_perspective = "PERSP"
        rv3d.view_location = Vector(center)
        rv3d.view_distance = distance
        rv3d.view_rotation = Euler(
            (math.radians(elevation), 0.0, math.radians(azimuth)),
            "XYZ").to_quaternion()
        space.shading.type = shading
        if shading == "SOLID":
            space.shading.color_type = "MATERIAL"
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
        space.overlay.show_cursor = False
        space.overlay.show_object_origins = False
        space.overlay.show_text = False
        area.tag_redraw()


def spread_row(gap=0.62):
    """Lay the current pieces out left-to-right for inspection only."""
    objs = [bpy.data.objects[n] for n in ORDER if n in bpy.data.objects]
    x = -gap * (len(objs) - 1) * 0.5
    for o in objs:
        o.location = (x, 0.0, 0.0)
        x += gap


def main(preview="federation", do_export=True):
    import time
    t0 = time.time()
    built = {}
    for faction in ("federation", "imperium"):
        built[faction] = build_faction(faction)
        if do_export:
            path = os.path.join(OUT_DIR, faction + ".glb").replace("\\", "/")
            export_glb(path)

    if do_export:
        for faction in ("federation", "imperium"):
            path = os.path.join(OUT_DIR, faction + ".glb").replace("\\", "/")
            verify_export(path, built[faction])

    # leave the requested faction in the scene, spread out for a screenshot
    build_faction(preview)
    spread_row()
    frame_viewport()
    print("\ndone in %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
