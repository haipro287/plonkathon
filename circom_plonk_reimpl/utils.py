import py_ecc.bn128 as b
from py_ecc.fields.field_elements import FQ as Field
from functools import cache
from Crypto.Hash import keccak
import py_ecc.bn128 as b
from py_ecc.fields.field_elements import FQ as Field

f = b.FQ
f2 = b.FQ2

class f_inner(Field):
    field_modulus = b.curve_order

primitive_root = 5

@cache
def get_root_of_unity(group_order):
    return f_inner(5) ** ((b.curve_order - 1) // group_order)

@cache
def get_roots_of_unity(group_order):
    o = [f_inner(1), get_root_of_unity(group_order)]
    while len(o) < group_order:
        o.append(o[-1] * o[1])
    return o

def keccak256(x):
    return keccak.new(digest_bits=256).update(x).digest()

def serialize_int(x):
    return x.n.to_bytes(32, 'big')

def serialize_point(pt):
    return pt[0].n.to_bytes(32, 'big') + pt[1].n.to_bytes(32, 'big')

def binhash_to_f_inner(h):
    return f_inner(int.from_bytes(h, 'big'))

def ec_mul(pt, coeff):
    if hasattr(coeff, 'n'):
        coeff = coeff.n
    return b.multiply(pt, coeff % b.curve_order)

def ec_lincomb(pairs):
    o = b.Z1
    for pt, coeff in pairs:
        o = b.add(o, ec_mul(pt, coeff))
    return o

# Encodes the KZG commitment to the given polynomial coeffs
def powers_to_point(setup, powers):
    if len(powers) > len(setup.G1_side):
        raise Exception("Not enough powers in setup")
    o = b.Z1
    for x, y in zip(powers, setup.G1_side):
        o = b.add(o, b.multiply(y, x.n))
    return o

# Encodes the KZG commitment that evaluates to the given values in the group
def evaluations_to_point(setup, group_order, evals):
    return powers_to_point(setup, f_inner_fft(evals, inv=True))

SETUP_FILE_G1_STARTPOS = 80
SETUP_FILE_POWERS_POS = 60

class Setup(object):

    def __init__(self, G1_side, X2):
        self.G1_side = G1_side
        self.X2 = X2

    @classmethod
    def from_file(cls, filename):
        contents = open(filename, 'rb').read()
        # Byte 60 gives you the base-2 log of how many powers there are
        powers = 2**contents[SETUP_FILE_POWERS_POS]
        # Extract G1 points, which start at byte 80
        values = [
            int.from_bytes(contents[i: i+32], 'little')
            for i in range(SETUP_FILE_G1_STARTPOS,
                           SETUP_FILE_G1_STARTPOS + 32 * powers * 2, 32)
        ]
        assert max(values) < b.field_modulus
        # The points are encoded in a weird encoding, where all x and y points
        # are multiplied by a factor (for montgomery optimization?). We can extract
        # the factor because we know that the first point is the generator.
        factor = f(values[0]) / b.G1[0]
        values = [f(x) / factor for x in values]
        G1_side = [(values[i*2], values[i*2+1]) for i in range(powers)]
        print("Extracted G1 side, X^1 point: {}".format(G1_side[1]))
        # Search for start of G2 points. We again know that the first point is
        # the generator.
        pos = SETUP_FILE_G1_STARTPOS + 32 * powers * 2
        target = (factor * b.G2[0].coeffs[0]).n
        while pos < len(contents):
            v = int.from_bytes(contents[pos: pos+32], 'little')
            if v == target:
                break
            pos += 1
        print("Detected start of G2 side at byte {}".format(pos))
        X2_encoding = contents[pos + 32 * 4: pos + 32 * 8]
        X2_values = [
            f(int.from_bytes(X2_encoding[i: i + 32], 'little')) / factor
            for i in range(0, 128, 32)
        ]
        X2 = (f2(X2_values[:2]), f2(X2_values[2:]))
        assert b.is_on_curve(X2, b.b2)
        print("Extracted G2 side, X^1 point: {}".format(X2))
        # assert b.pairing(b.G2, G1_side[1]) == b.pairing(X2, b.G1)
        # print("X^1 points checked consistent")
        return cls(G1_side, X2)

# Extracts a point from JSON in circom format
def interpret_json_point(p):
    if len(p) == 3 and isinstance(p[0], str) and p[2] == "1":
        return (f(int(p[0])), f(int(p[1])))
    elif len(p) == 3 and p == ["0", "1", "0"]:
        return b.Z1
    elif len(p) == 3 and isinstance(p[0], list) and p[2] == ["1", "0"]:
        return (
            f2([int(p[0][0]), int(p[0][1])]),
            f2([int(p[1][0]), int(p[1][1])]),
        )
    elif len(p) == 3 and p == [["0", "0"], ["1", "0"], ["0", "0"]]:
        return b.Z2
    raise Exception("cannot interpret that point: {}".format(p))

def _fft(vals, modulus, roots_of_unity):
    if len(vals) == 1:
        return vals
    L = _fft(vals[::2], modulus, roots_of_unity[::2])
    R = _fft(vals[1::2], modulus, roots_of_unity[::2])
    o = [0 for i in vals]
    for i, (x, y) in enumerate(zip(L, R)):
        y_times_root = y*roots_of_unity[i]
        o[i] = (x+y_times_root) % modulus 
        o[i+len(L)] = (x-y_times_root) % modulus 
    return o

def f_inner_fft(vals, inv=False):
    roots = [x.n for x in get_roots_of_unity(len(vals))]
    o, nvals = b.curve_order, [x.n for x in vals]
    if inv:
        # Inverse FFT
        invlen = f_inner(1) / len(vals)
        reversed_roots = [roots[0]] + roots[1:][::-1]
        return [f_inner(x) * invlen for x in _fft(nvals, o, reversed_roots)]
    else:
        # Regular FFT
        return [f_inner(x) for x in _fft(nvals, o, roots)]
