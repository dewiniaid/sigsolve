import PIL.Image
import PIL.ImageChops

def equalize(image, levels=256):
    """
    Equalizes a grayscale image such that the darkest pixels become black, the lighest pixels become white, and others
    are changed based on %ile.  Uses the image histogram to determine percentile.

    If the image is all one brightness level, the result will be white.

    :param image: Source image.
    :param levels: Number of levels.  Max 256.
    :return:
    """

    if image.mode != 'L':
        image = image.convert('L')

    histogram = image.histogram()

    # Compute divisor
    divisor = (
                  (image.width * image.height)  # Total number of pixels
                  - next(filter(None, reversed(histogram)))
              # Minus the last nonzero amount, otherwise it won't turn out white
              ) / (levels - 1)  # Divided by levels, which effectively multiplies them in the rounding phase.

    if not divisor:
        return PIL.Image.new('L', image.size, 255)

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
    :param exponent: Arbitrary exponent to make wider variations more significant vs. more varying pixels.
    :return: An arbitrary score value.
    """
    compare = PIL.ImageChops.difference(composite, image)

    if composite.mode == 'RGB':
        return sum(sum(c**exponent for c in x) for x in compare.getdata()) / (compare.width * compare.height)

        # return
    return sum(x**exponent for x in compare.getdata(0)) / (compare.width * compare.height)

