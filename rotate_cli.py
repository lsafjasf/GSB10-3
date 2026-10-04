"""Command-line interface: rotate a PGM image.

usage: python3 rotate_cli.py IN.pgm OUT.pgm ANGLE
           [--interp {nearest,bilinear}] [--mode {crop,expand}]
           [--border {constant,edge}] [--fill VALUE]
"""

import argparse

from imagerotate import read_pgm, rotate, write_pgm


def main():
    parser = argparse.ArgumentParser(description="Rotate a PGM image "
                                     "(pure standard library).")
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument("angle", type=float, help="degrees, counter-clockwise")
    parser.add_argument("--interp", choices=["nearest", "bilinear"],
                        default="bilinear")
    parser.add_argument("--mode", choices=["crop", "expand"], default="expand")
    parser.add_argument("--border", choices=["constant", "edge"],
                        default="constant")
    parser.add_argument("--fill", type=float, default=0.0)
    args = parser.parse_args()

    image = read_pgm(args.src)
    result = rotate(image, args.angle, interp=args.interp, mode=args.mode,
                    border=args.border, fill=args.fill)
    write_pgm(args.dst, result)
    print("%s: %dx%d -> %s: %dx%d (angle=%g, interp=%s, mode=%s, border=%s)"
          % (args.src, image.width, image.height, args.dst,
             result.width, result.height, args.angle, args.interp,
             args.mode, args.border))


if __name__ == "__main__":
    main()
