"""
Image utility functions.
"""
import PIL.Image
import PIL.ImageChops
import numpy


def equalize(image, levels=256, grayscale=False):
    """
    Equalizes an image such that the darkest pixels become black, the lightest become white, and others are based on
    their percentile.  If a pixel is brighter than 25% of the other pixels, it will be 25% grey in the output.

    If the image has multiple channels, they will be processed separately and merged into a new image.

    If the image only has one color, the return image will be 50% gray.

    :param image: Source image
    :param levels: Number of grayscale levels.  If this is less than 256, there will be different discrete bands in
    the output image.
    :param grayscale: If True, the image is forced to grayscale rather than splitting bands.

    :return: A new equalized image.
    """
    if image.mode != 'L':
        if not grayscale:
            # merge requires a list (not a generator), so this comprehension produces a list instead of a generator.
            return PIL.Image.merge(image.mode, [equalize(band, levels) for band in image.split()])
        image = image.convert('L')

    histogram = image.histogram()

    # Compute divisor
    divisor = (
                  (image.width * image.height)  # Total number of pixels
                  - next(filter(None, reversed(histogram)))
              # Minus the last nonzero amount, otherwise it won't turn out white
              ) / (levels - 1)  # Divided by levels, which effectively multiplies them in the rounding phase.

    if not divisor:
        return PIL.Image.new('L', image.size, 127)

    # Multiplier to scale back up after dividing.
    multiplier = 255 / (levels - 1)

    # Generate remap table
    remap = []
    pixels = 0
    for count in histogram:
        remap.append(max(0, min(255, round(round(pixels / divisor) * multiplier))))
        pixels += count

    # Apply to image.
    return PIL.Image.eval(image, remap.__getitem__)  # lambda x: remap[x] but faster


def convert(image, mode):
    """
    Equivalent to image.convert(mode), except returns the source image if already in that mode.

    :param image: Source image
    :param mode: Desired mode
    :return: Image in desired mode
    """
    if image.mode != mode:
        image = image.convert(mode)
    return image


def score(composite, image, exponent=1):
    """
    Determines how a particular image scores against a composite.  Lower scores indicate a closer match.

    :param composite: The composite reference
    :param image: The image being scored.
    :param exponent: Arbitrary exponent to make a large difference in a small area more significant than a small
        difference in a large one.
    :return: An arbitrary score value where 0 is a perfect match and (255**exponent)*numchannels is the theoretical
        upper bound.
    """
    diff = PIL.ImageChops.difference(composite, image)

    if composite.mode != 'L':
        return sum(sum(c**exponent for c in x) for x in diff.getdata()) / (diff.width * diff.height)

        # return
    return sum(x**exponent for x in diff.getdata(0)) / (diff.width * diff.height)


def numpify(image):
    # result = numpy.frombuffer(image.tobytes(), dtype=numpy.uint8)
    # return result.reshape((*image.size, 3))
    # return (
    #     numpy.array(image, dtype=numpy.uint8).reshape((image))
    #         # .frombuffer(image.tobytes(), dtype=numpy.uint8)
    #         # .reshape((image.size[0], image.size[1], -1))
    #         # .transpose((1, 0, 2))
    # )
    return numpy.array(image, dtype=numpy.uint8)
