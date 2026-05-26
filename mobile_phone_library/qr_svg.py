from __future__ import annotations

import base64


VERSION = 4
SIZE = 4 * VERSION + 17
DATA_CODEWORDS = 64
BLOCK_DATA_CODEWORDS = 32
ECC_CODEWORDS_PER_BLOCK = 18


def qr_svg_data_uri(text: str, *, scale: int = 8, border: int = 4) -> str:
    svg = qr_svg(text, scale=scale, border=border)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def qr_svg(text: str, *, scale: int = 8, border: int = 4) -> str:
    matrix = qr_matrix(text)
    view_size = len(matrix) + border * 2
    rects: list[str] = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                rects.append(f"M{x + border},{y + border}h1v1h-1z")
    path = " ".join(rects)
    pixel_size = view_size * scale
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{pixel_size}" height="{pixel_size}" '
        f'viewBox="0 0 {view_size} {view_size}" shape-rendering="crispEdges">'
        f'<rect width="{view_size}" height="{view_size}" fill="#fff"/>'
        f'<path d="{path}" fill="#111827"/>'
        "</svg>"
    )


def qr_matrix(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    if len(data) > 62:
        raise ValueError("QR 内容过长。")
    bits = encode_payload_bits(data)
    data_codewords = bits_to_padded_codewords(bits)
    blocks = [data_codewords[:BLOCK_DATA_CODEWORDS], data_codewords[BLOCK_DATA_CODEWORDS:]]
    ecc_blocks = [reed_solomon_remainder(block, ECC_CODEWORDS_PER_BLOCK) for block in blocks]
    final_codewords: list[int] = []
    for i in range(BLOCK_DATA_CODEWORDS):
        final_codewords.extend(block[i] for block in blocks)
    for i in range(ECC_CODEWORDS_PER_BLOCK):
        final_codewords.extend(block[i] for block in ecc_blocks)
    code_bits = [(word >> shift) & 1 for word in final_codewords for shift in range(7, -1, -1)]

    matrix = [[False for _ in range(SIZE)] for _ in range(SIZE)]
    function = [[False for _ in range(SIZE)] for _ in range(SIZE)]
    draw_function_patterns(matrix, function)
    draw_codewords(matrix, function, code_bits)
    draw_format_bits(matrix, function)
    return matrix


def encode_payload_bits(data: bytes) -> list[int]:
    bits: list[int] = []
    append_bits(bits, 0b0100, 4)
    append_bits(bits, len(data), 8)
    for byte in data:
        append_bits(bits, byte, 8)
    return bits


def bits_to_padded_codewords(bits: list[int]) -> list[int]:
    capacity_bits = DATA_CODEWORDS * 8
    append_bits(bits, 0, min(4, capacity_bits - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    codewords = [bits_to_int(bits[i : i + 8]) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < DATA_CODEWORDS:
        codewords.append(pad)
        pad = 0x11 if pad == 0xEC else 0xEC
    return codewords


def draw_function_patterns(matrix: list[list[bool]], function: list[list[bool]]) -> None:
    draw_finder(matrix, function, 0, 0)
    draw_finder(matrix, function, SIZE - 7, 0)
    draw_finder(matrix, function, 0, SIZE - 7)
    for i in range(SIZE):
        if not function[6][i]:
            set_function(matrix, function, i, 6, i % 2 == 0)
        if not function[i][6]:
            set_function(matrix, function, 6, i, i % 2 == 0)
    draw_alignment(matrix, function, 26, 26)
    reserve_format_bits(matrix, function)
    set_function(matrix, function, 8, SIZE - 8, True)


def draw_finder(matrix: list[list[bool]], function: list[list[bool]], left: int, top: int) -> None:
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            x = left + dx
            y = top + dy
            if not (0 <= x < SIZE and 0 <= y < SIZE):
                continue
            dark = 0 <= dx <= 6 and 0 <= dy <= 6 and (
                dx in {0, 6} or dy in {0, 6} or (2 <= dx <= 4 and 2 <= dy <= 4)
            )
            set_function(matrix, function, x, y, dark)


def draw_alignment(matrix: list[list[bool]], function: list[list[bool]], cx: int, cy: int) -> None:
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            dark = max(abs(dx), abs(dy)) == 2 or (dx == 0 and dy == 0)
            set_function(matrix, function, cx + dx, cy + dy, dark)


def reserve_format_bits(matrix: list[list[bool]], function: list[list[bool]]) -> None:
    coords = [(8, i) for i in range(6)]
    coords.extend([(8, 7), (8, 8), (7, 8)])
    coords.extend((14 - i, 8) for i in range(9, 15))
    coords.extend((SIZE - 1 - i, 8) for i in range(8))
    coords.extend((8, SIZE - 15 + i) for i in range(8, 15))
    for x, y in coords:
        set_function(matrix, function, x, y, False)


def draw_format_bits(matrix: list[list[bool]], function: list[list[bool]]) -> None:
    bits = compute_format_bits(error_correction_level=0b00, mask=0)
    for i in range(6):
        set_function(matrix, function, 8, i, get_bit(bits, i))
    set_function(matrix, function, 8, 7, get_bit(bits, 6))
    set_function(matrix, function, 8, 8, get_bit(bits, 7))
    set_function(matrix, function, 7, 8, get_bit(bits, 8))
    for i in range(9, 15):
        set_function(matrix, function, 14 - i, 8, get_bit(bits, i))
    for i in range(8):
        set_function(matrix, function, SIZE - 1 - i, 8, get_bit(bits, i))
    for i in range(8, 15):
        set_function(matrix, function, 8, SIZE - 15 + i, get_bit(bits, i))
    set_function(matrix, function, 8, SIZE - 8, True)


def draw_codewords(matrix: list[list[bool]], function: list[list[bool]], bits: list[int]) -> None:
    bit_index = 0
    upward = True
    right = SIZE - 1
    while right > 0:
        if right == 6:
            right -= 1
        for vertical in range(SIZE):
            y = SIZE - 1 - vertical if upward else vertical
            for x in (right, right - 1):
                if function[y][x]:
                    continue
                dark = bit_index < len(bits) and bits[bit_index] == 1
                bit_index += 1
                if (x + y) % 2 == 0:
                    dark = not dark
                matrix[y][x] = dark
        upward = not upward
        right -= 2


def set_function(matrix: list[list[bool]], function: list[list[bool]], x: int, y: int, dark: bool) -> None:
    matrix[y][x] = dark
    function[y][x] = True


def compute_format_bits(error_correction_level: int, mask: int) -> int:
    data = (error_correction_level << 3) | mask
    value = data << 10
    generator = 0x537
    for i in range(14, 9, -1):
        if (value >> i) & 1:
            value ^= generator << (i - 10)
    return ((data << 10) | value) ^ 0x5412


def append_bits(bits: list[int], value: int, count: int) -> None:
    for shift in range(count - 1, -1, -1):
        bits.append((value >> shift) & 1)


def bits_to_int(bits: list[int]) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


def get_bit(value: int, index: int) -> bool:
    return ((value >> index) & 1) != 0


def reed_solomon_remainder(data: list[int], degree: int) -> list[int]:
    generator = rs_generator_poly(degree)
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder.pop(0)
        remainder.append(0)
        for i in range(degree):
            remainder[i] ^= gf_multiply(generator[i + 1], factor)
    return remainder


def rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        poly = poly_multiply(poly, [1, gf_pow(i)])
    return poly


def poly_multiply(left: list[int], right: list[int]) -> list[int]:
    result = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            result[i + j] ^= gf_multiply(a, b)
    return result


def gf_pow(power: int) -> int:
    value = 1
    for _ in range(power):
        value = gf_multiply(value, 2)
    return value


def gf_multiply(left: int, right: int) -> int:
    result = 0
    a = left
    b = right
    while b:
        if b & 1:
            result ^= a
        b >>= 1
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
    return result & 0xFF
