import pathlib

import PIL.Image
import PIL.ImageChops
import pyscreenshot

from sigsolve import imageutil, geometry

import numpy


def rehydrate(array):
    # return PIL.Image.frombytes('RGB', array.shape[:2], array.astype(numpy.uint8).tobytes())
    return PIL.Image.fromarray(array, 'RGB')

class Vision:
    # How many light levels can a tile differ (in either direction) from the baseline before the tile is no longer
    # considered empty.  This relies on integer rollover to avoid needing an in16 over a uint8.
    MAX_EMPTY_TOLERANCE = 2

    @staticmethod
    def _getimage(what):
        if isinstance(what, (str, bytes, pathlib.Path)):
            what = PIL.Image.open(what)
        if what.mode != 'RGB':
            what = what.convert('RGB')
        return what

    def __init__(self, baseline=None, composites=None, extents=None):
        """
        Handles image processing state functionality.

        :param baseline: Baseline image.  If this is a string or Path object, it is assumed to be a filename and is
        loaded.
        :param composites: Optional dictionary of composite images (or image filenames), with IDs as keys.
        :param extents: Rectangle of the area we're interested in.  Default is the whole image.
        """
        self.baseline = self._getimage(baseline)
        if extents:
            self.baseline = self.baseline.crop(extents.coords)
        else:
            extents = geometry.Rect(geometry.Point.ORIGIN, self.baseline.size)
        self.baseline = imageutil.numpify(self.baseline)
        self.baseline.flags.writeable = True
        # Some processing.
        self.baseline += self.MAX_EMPTY_TOLERANCE
        self.baseline[self.baseline < self.MAX_EMPTY_TOLERANCE] = 255  # Cap off what just rolled over

        self.extents = extents
        self.offset = -self.extents.xy1
        self.composites = {}
        if composites is not None:
            for key, image in composites.items():
                self.add_composite(key, image)

        self.image = None

    def add_composite(self, key, image):
        self.composites[key] = imageutil.numpify(self._getimage(image)).astype(numpy.int16)

    def match(self, tile):
        """Finds the composite that most closely matches the source tile's image."""
        coords = (tile.sample_rect + self.offset).coords
        base = self.baseline[coords[1]:coords[3], coords[0]:coords[2], 0:3]
        cropped = self.image.crop(coords)
        if numpy.all(base - imageutil.numpify(cropped) < 2*self.MAX_EMPTY_TOLERANCE):
            return None

        data = imageutil.numpify(imageutil.equalize(cropped)).astype(numpy.int16)
        buf = numpy.ndarray(data.shape, data.dtype)
        unsigned = buf.view(numpy.uint16)

        best = None
        bestscore = None
        for key, composite in self.composites.items():
            numpy.subtract(data, composite, out=buf)  # Initialize buf with a difference between the two arrays

            # We casually convert between signed and unsigned here, and the math just happens to work out due to
            # sign extension and truncation.
            unsigned **= 2  # Raise all values to power of 2.
            score = numpy.sum(unsigned)
            if bestscore is None or score < bestscore:
                bestscore = score
                best = key
        return best

    def screenshot(self):
        """Sets the image to a screenshot"""
        self.set_image(
            pyscreenshot.grab(self.extents.coords), cropped=True
        )

    def set_image(self, image, cropped=False):
        """Sets the image"""
        image = self._getimage(image)
        if not cropped and (self.extents.xy1 != geometry.Point.ORIGIN or self.extents.xy2 != image.size):
            image = image.crop(self.extents.coords)
        self.image = image
