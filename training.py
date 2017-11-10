"""
Does automatic vision training.
"""

from sigsolve import util
from sigsolve.vision import Vision
from sigsolve.board import Board
from sigsolve.geometry import Rect, Point, DEFAULT_GEOMETRY
from sigsolve.solver import Solver
import pyscreenshot
import logging
import PIL.ImageDraw
import PIL.Image
import PIL.ImageChops
import pyautogui
import hashlib
import time
import re

from pprint import pprint
from pathlib import Path

logging.basicConfig()  # level=logging.DEBUG)
log = logging.getLogger('training')
log.setLevel(logging.DEBUG)


def average_brightness(image, rect):
    return image.crop(rect.coords).resize((1, 1), resample=PIL.Image.BICUBIC).convert('L').getdata()[0]

def secondary_identify(image, rect):
    """
    Returns 'vitae', 'mors' or 'quicksilver' based on simple color guessing.  Only valid for legal tiles!
    :param image: Image to examine
    :param tile: Tile
    :return: 'vitae', 'mors' or 'quicksilver'

    Based on taking average pixel values of sample_rect, and observing:

    Mors: red < 140, otherwise...    (Observed: 95)
    Quicksilver: (red-green) < 20    (Observed: 9)
    Vitae: otherwise                 (Observed: 30-31)
    """
    r, g, b = image.crop(rect.coords).resize((1, 1), resample=PIL.Image.BICUBIC).getdata()[0]
    if r < 140:
        return 'mors'
    if r  - g < 20:
        return 'quicksilver'
    return 'vitae'


class Program:
    UNKNOWN_ELEMENT = 'UNKNOWN'

    def __init__(self):
        self.geometry = DEFAULT_GEOMETRY
        self.board = Board()
        self.bounds = self.board.bounds()
        self.offset = -self.bounds.xy1
        self.vision = None
        self.has_quintessence = None

        self.image = None  # Current board snapshot.
        self.original = None  # Original board snapshot.

        self.datadir = None
        self.hashes = set()
        self.legal_tiles = None
        self.identified = None

    def reset_board(self):
        for tile in self.board.tiles:
            if self.vision.isempty(tile):
                tile.element = None
                tile.exists = False
            else:
                tile.element = self.UNKNOWN_ELEMENT
                tile.exists = True

        self.identified = {
            True: set(),
            False: set()
        }

    def detect_secondary_element(self, tile):
        """
        Guess at tiles we can't properly identify by the legend.  Only legal tiles.
        """
        r, g, b = util.numpy_crop(self.image, (tile.sample_rect + self.offset).coords).mean((0,1))  #  self.image.crop(rect.coords).resize((1, 1), resample=PIL.Image.BICUBIC).getdata()[0]
        if r < 140:
            tile.element = 'mors'
        elif r - g < 20:
            if not self.has_quintessence:
                log.error('Guessing at quintessence, but quintessence should already have been identified!')
            tile.element = 'quicksilver'
        else:
            tile.element = 'vitae'

        return tile.element

    def initial_scan(self):
        # Reset symbols
        self.symbols = {k: v.middle for k, v in self.geometry.legend.items()}

        screenshot = pyscreenshot.grab()

        # Redetect quintessence
        rect = Rect.bounds((self.geometry.legend['quicksilver'], self.geometry.legend['quintessence']))
        bright = screenshot.crop(rect.coords).resize((1, 1), resample=PIL.Image.BICUBIC).convert('L').getdata()[0]
        log.info(f"Quintessence test: Brightness level={bright}")
        # Observed: 120 = quicksilver, 70 = quintessence.  Set a threshold midway between
        self.has_quintessence = bright < (120+70)/2
        if self.has_quintessence:
            log.info("Quintessence detected")
            del self.symbols['quicksilver']
        else:
            log.info("Quintessence NOT detected")
            del self.symbols['quintessence']

        # Use the other portion of the screenshot for our initial board capture.
        image = screenshot.crop(self.bounds.coords)
        self.vision.set_image(image, True)
        self.original = self.image = util.numpify(image)

        self.reset_board()

        # Try to read tiles from legend.
        for symbol, where in self.symbols.items():
            log.info(f"Detecting symbol {symbol} -- click {where}")
            pyautogui.mouseDown(*where)
            current = util.numpify(pyscreenshot.grab(self.bounds.coords))
            pyautogui.mouseUp(*where)
            diffed = current == self.image
            for tile in self.board.tiles:
                coords = (tile.sample_rect + self.offset).coords
                cropped = util.numpy_crop(diffed, coords)
                if all(cropped.flat):
                    continue

                # Detected variation!
                if tile.element != self.UNKNOWN_ELEMENT:
                    log.warning(f"{tile}: Overwriting previous elemental assignment of {tile:%e}!")
                tile.element = symbol

        # Show the current board state
        for line in self.board.lines():
            log.debug(line)

        # Validate counts
        expected = {
            'air': 8, 'fire': 8, 'water': 8, 'earth': 8,
            'salt': 4,
            'mercury': 1, 'tin': 1, 'iron': 1, 'copper': 1, 'silver': 1, 'gold': 1,
        }
        if self.has_quintessence:
            expected.update({'quintessence': 2})
        else:
            expected.update({'quicksilver': 5})

        ok = True
        for element, count in expected.items():
            remaining = self.board.remaining(element)
            if remaining != count:
                log.error(f"Wrong number of detected instances for {element}.  Expected {count}, found {remaining}.")
                ok = False

        if ok:
            # Identify all of the images we could identify.
            for tile in self.board.tiles:
                if tile.element is None or tile.element == self.UNKNOWN_ELEMENT:
                    continue
                self.identify(tile, self.original)

        self.legal_tiles = set(self.board.legal_tiles())
        self.identify_tiles(first_run=True)


    def run(self, *args):
        self.datadir = Path("./data").resolve()
        log.info(f"Datadir: {self.datadir}")
        self.vision = Vision(self.datadir / "empty.png", bounds=self.bounds)

        # Scan existing hashes in datadir.
        self.hashes = set()
        files = self.datadir.glob("*/tile.*.png")
        for file in files:
            result = re.match(r'^tile\.([0-9a-f]{32})\.png$', file.name.lower())
            if not result:
                continue
            self.hashes.add(bytes.fromhex(result.group(1)))
        log.info(f"{len(self.hashes)} pre-existing hashes.")

        while True:
            # Initial scan.
            self.initial_scan()

            while True:
                first = True
                # Run the solver as long as we can to expose new legal tiles.
                solver = Solver(self.board)
                result = solver.solve()
                print("---")
                print(repr(solver.solution))
                if result:
                    break  # No need to actually complete the game for what we're doing and let's not skew statistics.
                if not solver.solution or not solver.solution[0]:
                    # No solution, not even a best move.
                    break

                # Execute the first move of the solution.  Undo the rest.
                first, *rest = solver.solution
                for tile in first:
                    pt = tile.sample_rect.middle
                    util.click(pt, down=0.001, up=0.001)
                    tile.exists = False
                for move in rest:
                    for tile in move:
                        tile.exists = True

                # Observe changes of legality.
                new_legal_tiles = set(self.board.legal_tiles())
                changed = new_legal_tiles - self.legal_tiles
                self.legal_tiles = new_legal_tiles

                # Take new screenshot.
                self.image = util.numpify(pyscreenshot.grab(self.bounds.coords))

                self.identify_tiles(changed)

            util.click(self.geometry.new_game, up=5)



    def identify_tiles(self, tiles=None, first_run=False):
        if first_run:
            self.identified = {
                True: set(),
                False: set()
            }
        if tiles is None:
            tiles = self.board.tiles

        for tile in tiles:
            if tile.element == self.UNKNOWN_ELEMENT and tile.legal:
                self.detect_secondary_element(tile)
            if tile.element is None or tile.element == self.UNKNOWN_ELEMENT:
                continue
            if tile not in self.identified[tile.legal]:
                self.identify(tile, self.image, tile.legal)
                self.identified[tile.legal].add(tile)
                if tile.legal and tile not in self.identified[False]:
                    if not first_run:
                        self.identify(tile, self.original, False)
                    self.identified[False].add(tile)


    def identify(self, tile, image=None, legal=None):
        if image is None:
            image = self.image
        cropped = util.numpy_crop(image, (tile.sample_rect + self.offset).coords)
        hasher = hashlib.md5(cropped.tobytes())
        bhash = hasher.digest()
        hash = hasher.hexdigest().lower()
        if bhash in self.hashes:
            log.info(f"Tile with hash {hash} is already known to us.")
            return

        element = tile.element
        if legal is None:
            legal = tile.legal
        subdir = self.datadir / (element + "." + ('0' if legal else '1'))

        try:
            subdir.mkdir()
            log.info(f"Created directory {subdir}")
        except FileExistsError:
            pass

        fn = subdir / f"raw.{hash}.png"
        image = util.denumpify(cropped)
        image.save(fn)
        log.info(f"Wrote {fn}")
        fn = subdir / f"tile.{hash}.png"
        image = util.equalize(image)
        image.save(fn)
        log.info(f"Wrote {fn}")
        self.hashes.add(bhash)


if __name__ == '__main__':
    Program().run()


