import pathlib

import PIL.Image
import PIL.ImageChops
import pyscreenshot

from sigsolve import imageutil, geometry

class Vision:
    @staticmethod
    def _getimage(what):
        if isinstance(what, (str, bytes, pathlib.Path)):
            return PIL.Image.open(what)
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
        self.extents = extents
        self.offset = -self.extents.xy1
        self.composites = {}
        if composites is not None:
            for key, image in composites.items():
                self.add_composite(key, image)

        self.image = None

    def add_composite(self, key, image):
        self.composites[key] = self._getimage(image)

    def match(self, tile, exponent=2, executor=None):
        """Finds the composite that most closely matches the source tile's image."""
        coords = (tile.sample_rect + self.offset).coords
        if executor:
            return executor.submit(self._match_coords, coords, exponent)
        return self._match_coords(coords, exponent)

    def _match_coords(self, coords, exponent=2):
        image = self.image.crop(coords)
        diff = PIL.ImageChops.difference(image, self.baseline.crop(coords))
        if all(band[1] < 3 for band in diff.getextrema()):
            return None
        image = imageutil.equalize(image)
        best = None
        bestscore = None
        for key, composite in self.composites.items():
            score = imageutil.score(composite, image, exponent=exponent)
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
