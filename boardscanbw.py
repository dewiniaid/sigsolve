import argparse
import logging
import pathlib
import hashlib
import re
import PIL.Image
import PIL.ImageChops
import pyscreenshot
import datetime

from collections import defaultdict

from board import Tileset
import imageutil


logging.basicConfig()
log = logging.getLogger()
pil_log = logging.getLogger('PIL')
pil_log.setLevel(logging.INFO)
log.setLevel(logging.DEBUG)


class State:
    datadir = None
    defaultdir = None
    screenshotdir = None
    blank_image = None
    opts = None
    board = None
    index = None
    done = set()  # Tile keys already refreshed this run.  (Or previous runs if no refresh)
    levels = 256  # Image processing generates this many light bands.  Max = 256.


def generate_index(refresh=False):
    if State.index is not None and not refresh:
        return

    State.index = {}

    log.debug('Indexing images...')

    for path in State.datadir.glob('*/tile.*.png'):
        name = path.name
        result = re.match(r'^tile\.(.+)\.png$', name.lower())
        key = result.group(1)
        if key in State.index:
            log.warning(f"Duplicate key '{key}' in index: {State.index[key]} vs {path}")
        State.index[key] = path

    ct = len(State.index)
    log.debug(f'Image index contains {ct} entries.')
    return State.index


def process_image(image, description):
    image = imageutil.convert(image, 'RGB')
    if State.blank_image is None:
        fn = (State.datadir / 'empty.png')
        log.debug(f'Loading reference image {fn}')
        State.blank_image = imageutil.convert(PIL.Image.open(fn), 'RGB')
        log.info('Loaded reference image (size {0.width}x{0.height})'.format(State.blank_image))

    if State.defaultdir is None:
        State.defaultdir = State.datadir / '_unsorted'
        State.defaultdir.mkdir(exist_ok=True)

    if State.board is None:
        State.board = Tileset()

    generate_index()

    log.info(f'Processing {description} (size {image.width}x{image.height})')
    if image.size != State.blank_image.size:
        log.error(f'Image does not match reference image dimensions!  '
                  f'({image.width}x{image.height} != {State.blank_image.width}x{State.blank_image.height})'
                  )
        return False

    # Create differencing image so we can identify empty cells
    difference = PIL.ImageChops.difference(image, State.blank_image)

    # Process tiles
    skipped = blank = processed = 0
    for tile in State.board.tiles:
        coords = tile.sample_rect.coords
        localdiff = difference.crop(coords)
        extrema = localdiff.getextrema()
        if all(x[1] < 2 for x in extrema):
            # log.debug(f'Tile ({tile.x},{tile.y}) is empty.')
            blank += 1
            continue

        cropped = image.crop(coords)
        key = hashlib.md5(cropped.tobytes()).hexdigest()[:16].lower()
        cropped = localdiff

        if key in State.done:
            skipped += 1
            continue
        State.done.add(key)
        path = State.index.get(key)
        if not path:
            path = State.defaultdir / f'tile.{key}.png'
            State.index[key] = path

        imageutil.equalize(localdiff, State.levels).save(path, optimize=True)
        processed += 1

    log.info(f'Tiles processed: {processed}; skipped: {skipped}; blank: {blank}')
    return


def generate_composite(outfile, sources, extrema=None):
    size = None
    data = None
    first_source = None

    minima = maxima = None

    for source in sources:
        image = imageutil.convert(PIL.Image.open(source), 'L')
        if first_source is None:
            log.debug(f'Starting composite {outfile} using {source}')
            first_source = source
            minima = maxima = image
            size = image.size
            data = list(int(x) for x in image.getdata(0))
            continue
        elif size != image.size:
            log.error(f"While compositing {outfile}: current image {source} dimensions of "
                      "({image.width}x{image.height}) differs from first image {first_source} dimensions of "
                      "({size[0]}x{size[1]}).  This image will be skipped."
                      )
            continue

        minima = PIL.ImageChops.darker(image, minima)
        maxima = PIL.ImageChops.lighter(image, maxima)
        log.debug(f'... adding {source} to composite')
        for ix, value in enumerate(image.getdata(0)):
            data[ix] += int(value)

    data = list(max(0, min(255, round(x/len(sources)))) for x in data)
    average = PIL.Image.new('L', size, None)
    average.putdata(data)


    # image = PIL.Image.merge('RGB', (average, minima, maxima))
    # image = imageutil.equalize(PIL.Image.eval(PIL.ImageChops.difference(minima, maxima), lambda x: 255-x))
    image = average

    image.save(outfile)
    log.info(f'Wrote {outfile}')

    if extrema is None:
        extrema = (image, image)
    else:
        darker, lighter = extrema
        extrema = (
            PIL.ImageChops.darker(darker, image),
            PIL.ImageChops.lighter(lighter, image)
        )
    return extrema

def main(*args, **kwargs):
    parser = argparse.ArgumentParser(description='Process tile images')

    parser.add_argument('files', metavar='FILE', nargs='*', type=str,
                        help='File(s) to process.  If omitted, screen will be captured instead.  Files can be a directory.')
    parser.add_argument('-d', '--path', action='store', default='data',
                        help='Path to store tile data')
    parser.add_argument('-s', '--screenshot', action='store_true',
                        help='Take screenshot even if other arguments are present.')
    parser.add_argument('-c', '--composite', action='store_true',
                        help='Generate composites.'
                        )
    parser.add_argument('-t', '--test', action='store_true',
                        help='Test existing data against composites.'
                        )
    parser.add_argument('-r', '--refresh', action='store_true',
                        help='Refresh existing images if present.'
                        )

    State.opts = opts = parser.parse_args(*args, **kwargs)

    log.debug(f'opts: {opts!r}')

    if not (opts.composite or opts.files or opts.test):
        opts.screenshot = True
        log.debug('Setting implicit grab option')

    State.datadir = pathlib.Path(opts.path).resolve()
    log.debug(f'Data directory: {State.datadir}')

    generate_index()
    if not opts.refresh:
        State.done.update(State.index)


    processed_paths = set()

    if opts.screenshot:
        screenshot = pyscreenshot.grab()
        if State.screenshotdir is None:
            State.screenshotdir = State.datadir / '_screenshots'
            State.screenshotdir.mkdir(exist_ok=True)

        timestamp = datetime.datetime.now().strftime('%Y.%m.%d-%H.%M.%S')

        fn = State.screenshotdir / f'screenshot.{timestamp}.png'
        screenshot.save(fn, optimize=True)
        log.info(f'Saved screenshot to {fn}')
        process_image(screenshot, 'acquired screenshot')
        processed_paths.add(fn)

    for filename in opts.files:
        path = pathlib.Path(filename).resolve()

        if path in processed_paths:
            continue

        if path.is_dir:
            files = path.glob('*.png')
        elif path.is_file:
            files = [path]
        else:
            log.error(f'No such file/directory: {path!r}')
            return False

        for file in files:
            if file in processed_paths:
                continue

            process_image(PIL.Image.open(file), file)
            processed_paths.add(file)

    if opts.composite:
        compositesdir = State.datadir / '_composites'
        compositesdir.mkdir(exist_ok=True)
        groups = defaultdict(list)
        for file in State.index.values():
            groups[file.parent].append(file)

        extrema = None

        for group, files in groups.items():
            fn = compositesdir / f'composite.{group.name}.png'
            extrema = generate_composite(fn, files, extrema)

        fn = compositesdir / 'weightings.png'
        imageutil.equalize(PIL.Image.eval(PIL.ImageChops.difference(*extrema), lambda x: 255-x)).save(fn)


    if opts.test:
        failed = []
        passed = []
        composites = {}
        maxnamelen = 0

        fn = compositesdir / 'weightings.png'
        weightings = PIL.Image.open(fn)
        # PIL.Image.eval(PIL.ImageChops.difference(*extrema), lambda x: 255-x).save(fn)


        for path in State.datadir.glob('*/composite.*.png'):
            result = re.match(r'^composite\.(.+)\.png$', path.name.lower())
            if not result:
                continue
            groupname = result.group(1)
            maxnamelen = max(maxnamelen, len(groupname))
            composites[groupname] = PIL.Image.open(path)

        fmt = "{1:" + str(maxnamelen) + "}: {0:6.3f}"

        for file in sorted(State.index.values()):
            expected = file.parent.name
            results = []
            image = imageutil.convert(PIL.Image.open(file), 'L')
            print(f'{file}: ')
            for group, composite in composites.items():
                results.append((imageutil.score(composite, image, weightings=weightings), group))
            results.sort(reverse=True)

            best = results[-1][1]
            if best == expected:
                print(f"Expected: {expected} -- Best: {results[-1][1]} (score: {results[-1][0]:6.3f} -- GOOD")
                passed.append(file)
            else:
                print(f"Expected: {expected} -- Best: {results[-1][1]} -- ****FAILED MATCHING****")
                failed.append(file)
                for result in results:
                    print(fmt.format(*result))

        numpassed = len(passed)
        numfailed = len(failed)

        print(f'{numpassed} images passed, {numfailed} failed.')

        if failed:
            print('Failed images: ')
            for file in failed:
                print(f'    {file}')


if __name__ == '__main__':
    main()