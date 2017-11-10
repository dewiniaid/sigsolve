from sigsolve.util import numpify, denumpify
from pathlib import Path
import numpy
from PIL import Image


def main():
    datadir = Path('./data').resolve()

    for subdir in datadir.glob('*'):
        if not subdir.is_dir():
            continue

        composite = None
        count = 0
        for file in subdir.glob('tile.*.png'):
            print(file)
            image = numpify(Image.open(file))
            if count:
                composite += image
            else:
                composite = image.astype(numpy.uint64)
            count += 1

        composite //= count
        composite = composite.astype(numpy.uint8)
        denumpify(composite).save(datadir / f"composite.{subdir.name}.png")


if __name__ == '__main__':
    main()